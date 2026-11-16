"""Move the marks of the current half-year from EduPage class journals into the Atlas journal.

The source is the JSON each class gradebook page of EduPage receives, one file per class
("<class>_P1.json.gz"; plain .json is read too). The tool signs in to a running Atlas server as an
administrator and writes only through its HTTP API (GET/PUT /api/journal/entry): it never opens the
database. Dry run by default; --apply writes. It is safe to run again while the school still works in
EduPage: an EduPage column is one Atlas work with a stable id ("ep-<UdalostID>"), so a second run finds
it and changes nothing unless the column or a mark changed.

What people changed in Atlas by hand is left alone, including a mark a teacher corrected or deleted and a work a teacher removed:
the importer reads the saved versions of a lesson (history) and tells its own saves from others by the account name, so use an
administrator account of its own. More than PUBLISH_LIMIT lessons are published only with --publish (every family gets notices).

Make a backup first (python -m tools.backup_school). The report holds counts only: no names of pupils
or teachers, no marks, no column titles. See GRADES_IMPORT.md.

The module has three parts: reading the files (Source), planning (Planner turns the journals and what
Atlas returned into one change per lesson and a report; it only reads) and the HTTP part (Http, Atlas,
save, main).
"""
import argparse
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import getpass
import gzip
from http.cookiejar import CookieJar
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib import error, parse, request as urlrequest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pydantic import ValidationError
from backend.invitation_registers import class_key, name_key, text_key
from backend.journal import Content

HEADERS = {'X-Atlas-Request': '1'}
PREFIX = 'ep-'
# Limits of the journal (backend/journal.py): works per lesson, name, label, comment, work id. A lesson also holds at most 10 marks
# per pupil, which the limit of works already guarantees.
MAX_WORKS, NAME_LIMIT, LABEL_LIMIT, COMMENT_LIMIT, ID_LIMIT = 10, 100, 30, 1000, 80
WEIGHT_UNIT = 20  # The EduPage weight "20" is one times.
SEARCH_DAYS = 7
RETRIES = 4  # Tries of one request when the connection drops.
ENOUGH = 0.8  # The share of a column's pupils a group must hold when no group holds them all.
PUBLISH_LIMIT = 20  # More lessons than this are published only when the operator says so (--publish): every family gets a notice per lesson.
MARKED = ('score', 'label', 'comment')
NUMBER = re.compile(r'\s*(-?\d+(?:[.,]\d+)?)')
UNRESOLVED = ['noSubject', 'noPupilMatch', 'noGroup', 'noDate', 'noLesson']


class Stop(Exception):
    """The run cannot go on: no sign-in, no connection, or a read every plan needs was refused."""

    def __init__(self, step, status, reason):
        super().__init__(f'{step}: {status} {reason}')
        self.step, self.status, self.reason = step, status, reason


class Refused(Exception):
    def __init__(self, status, reason):
        super().__init__(f'{status} {reason}')
        self.status, self.reason = status, reason


# ----------------------------------------------------------------------------- source files

@dataclass
class Mark:
    id: str
    pupil: str
    text: str
    datum: str
    day: date | None


@dataclass
class Column:
    uid: str
    subject: str = ''
    category: str = ''
    title: str = ''
    termin: date | None = None
    edited: date | None = None
    weight: float = 1
    maximum: float | None = None
    every: bool = False
    only: set = field(default_factory=set)
    classes: set = field(default_factory=set)
    marks: dict = field(default_factory=dict)


@dataclass
class Pupil:
    klass: str
    name: str
    ended: bool = False  # EduPage gives the record an end of study.


def number(value, fallback):
    try:
        result = float(str(value).replace(',', '.'))
    except ValueError:
        return fallback
    return result if 0 <= result < float('inf') else fallback


def weight_of(value):
    """The weight of a column in times: a missing or unreadable one is 1, a negative one is none."""
    try:
        result = float(str(value).replace(',', '.'))
    except ValueError:
        return 1.0
    return max(0.0, result / WEIGHT_UNIT) if abs(result) < float('inf') else 1.0


def day_of(text):
    try:
        return date.fromisoformat(str(text or '')[:10])
    except ValueError:
        return None


def termin_of(text):
    try:
        return datetime.strptime(str(text or '').strip(), '%d.%m.%Y').date()
    except ValueError:
        return None


class Source:
    def __init__(self):
        self.columns, self.pupils = {}, {}
        self.files = self.unreadable = 0
        self.not_subject = set()

    def add(self, data):
        klass = str((data.get('trieda') or {}).get('Meno') or '').strip()
        subjects = {str(k): str(v.get('p_meno') or '') for k, v in (data.get('predmetyVTriede') or {}).items()}
        categories = {str(k): str(v.get('p_meno') or '') for k, v in (data.get('kategorie') or {}).items()}
        for sid, person in (data.get('studenti') or {}).items():
            surname, first = str(person.get('p_priezvisko') or '').strip(), str(person.get('p_meno') or '').strip()
            if surname and not surname[0].isdigit():  # A row that starts with a digit is a service row.
                self.pupils.setdefault(str(sid), Pupil(klass, f'{surname} {first}'.strip(), bool(str(person.get('p_koniec_studia') or '').strip())))
        here = set()
        for key, raw in ((data.get('udalosti') or {}).get('edupage') or {}).items():
            uid, subject = str(raw.get('UdalostID') or key), raw.get('PredmetID')
            if not subject or subject == 'vsetky':
                self.not_subject.add(uid)
                continue
            column = self.columns.setdefault(uid, Column(uid))
            column.subject = column.subject or subjects.get(str(subject), '')
            column.category = column.category or categories.get(str(raw.get('KategoriaID') or ''), '')
            column.title = column.title or str(raw.get('p_meno') or '')
            column.termin = column.termin or termin_of(raw.get('p_termin'))
            column.edited = column.edited or day_of(raw.get('timestamp'))
            column.weight = weight_of(raw.get('p_vaha'))
            maximum = number(raw.get('p_vaha_body'), 0)
            column.maximum = maximum if 0 < maximum <= 10000 else None
            column.every = str(raw.get('p_najskor_priemer')) == '0'
            column.only |= {str(s) for s in raw.get('onlyStudentids') or []}
            column.classes.add(klass)
            here.add(uid)
        for raw in data.get('vsetkyZnamky') or []:
            uid, text = str(raw.get('udalostid')), str(raw.get('data') or '').strip()
            if uid not in here or raw.get('stav') != 'o' or not text:
                continue
            datum = str(raw.get('datum') or '')
            mark_id = str(raw.get('znamkaid') or hashlib.sha256(f'{uid}|{raw.get("studentid")}|{datum}|{text}'.encode('utf8')).hexdigest()[:16])
            self.columns[uid].marks.setdefault(mark_id, Mark(mark_id, str(raw.get('studentid')), text, datum, day_of(raw.get('datum_vytvorenia')) or day_of(datum)))
        self.files += 1


def read_json(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)


def load_sources(paths):
    """Every *.json.gz and *.json of the folders and files given."""
    source, files = Source(), []
    for item in map(Path, paths):
        files += sorted(f for f in item.iterdir() if f.name.endswith(('.json', '.json.gz'))) if item.is_dir() else [item]
    for path in files:
        try:
            source.add(read_json(path))
        except (OSError, ValueError, AttributeError):
            source.unreadable += 1
    return source


# ----------------------------------------------------------------------------- marks

def fit(text, limit):
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def unbracket(text):
    text = text.strip()
    return text[1:-1].strip() if text.startswith('(') and text.endswith(')') else text


def parse_mark(text):
    """(score or None, remark): a number at the start is the score; a negative number is a remark only."""
    found = NUMBER.match(text)
    if found and not found.group(1).startswith('-'):
        return float(found.group(1).replace(',', '.')), unbracket(text[found.end():])
    return None, unbracket(text)


PLAIN = re.compile(r'[A-Za-z0-9_-]{1,%d}' % (ID_LIMIT - 20))  # A uid kept as it is in a work id; any other is replaced by its hash.


def work_id(uid, attempt=1):
    """The id of the work of a column (attempt 1) or of a further attempt of it. It depends on the uid alone, never on the other columns.

    The base is "ep-<uid>" for a plain uid (letters, digits, "_" and "-", short) and "ep-~h<hash>" for any other. "~" occurs in no
    base id except right after the prefix of a hashed one, and a further attempt appends "~<k>": "ep-<uid>~<k>" or "ep-~h<hash>~<k>".
    So an attempt id (one "~" past the prefix, or two) is never the base id of any column, and different columns never share an id."""
    base = PREFIX + uid if PLAIN.fullmatch(uid) else f'{PREFIX}~h{hashlib.sha256(uid.encode("utf8")).hexdigest()[:32]}'
    return base if attempt == 1 else f'{base}~{attempt}'


def make_grade(text, work, counts):
    """The Atlas grade (without assessmentId) of one EduPage mark text on a work with these attributes."""
    text = ' '.join(text.split())
    if work['kind'] == 'label':
        return dict(score=None, label=text[:LABEL_LIMIT].strip(), comment=fit(text[LABEL_LIMIT:], COMMENT_LIMIT))
    score, remark = parse_mark(text)
    if score is None:
        counts['textOnly'] += 1
        return dict(score=None, label='', comment=fit(remark, COMMENT_LIMIT))
    if score > work['maxScore']:
        counts['scoresCut'] += 1
        remark = fit(f'В EduPage стояло {score:g} при максимуме {work["maxScore"]:g}.' + (f' {remark}' if remark else ''), COMMENT_LIMIT)
        score = work['maxScore']
    return dict(score=score, label='', comment=fit(remark, COMMENT_LIMIT))


def order_key(mark):
    return mark.datum, len(mark.id), mark.id


# ----------------------------------------------------------------------------- Atlas reads

def reason_of(data):
    detail = data.get('detail') if isinstance(data, dict) else None
    if isinstance(detail, list):
        detail = '; '.join(str(item.get('msg', '')) for item in detail if isinstance(item, dict))
    return fit(str(detail or ''), 300)


class Http:
    """The real server: JSON over urllib with the session cookie. The tests use a TestClient wrapper with the same call()."""

    def __init__(self, base):
        self.base = base.rstrip('/')
        self.opener = urlrequest.build_opener(urlrequest.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, params=None, body=None):
        url = self.base + path + ('?' + parse.urlencode(params) if params else '')
        data = json.dumps(body).encode('utf8') if body is not None else None
        request = urlrequest.Request(url, data=data, method=method, headers=HEADERS | ({'Content-Type': 'application/json'} if data else {}))
        # A dropped connection is tried again. A repeated PUT that had gone through meets the newer version and is read again.
        for attempt in range(RETRIES):
            try:
                with self.opener.open(request, timeout=120) as response:
                    status, raw = response.status, response.read()
                break
            except error.HTTPError as exc:
                status, raw = exc.code, exc.read()
                break
            except OSError as exc:
                if attempt == RETRIES - 1:
                    raise Stop('connection', 0, f'нет связи с сервером: {exc}') from None
                time.sleep(0.5)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, None


class Atlas:
    """Reads and writes of the journal through the API. Reads are cached for the run; a refusal raises Refused."""

    def __init__(self, http, workspace):
        self.http, self.workspace = http, workspace
        self.days, self.entries, self.tables, self.rows, self.contents = {}, {}, {}, {}, {}

    def call(self, method, path, params=None, body=None):
        status, data = self.http.call(method, path, dict(params or {}, workspace=self.workspace), body)
        if status >= 400:
            raise Refused(status, reason_of(data))
        return data

    def need(self, step, path):
        """A read the whole plan depends on: when it is refused the run stops."""
        try:
            return self.call('GET', path)
        except Refused as exc:
            raise Stop(step, exc.status, exc.reason) from None

    def day(self, day):
        if day not in self.days:
            self.days[day] = self.call('GET', '/api/journal/day', dict(day=day))['lessons']
        return self.days[day]

    def entry(self, key, day, fresh=False):
        if fresh or key not in self.entries:
            self.rows.pop(key, None)  # The lesson may have been saved since: its list of saves is read again too.
            data = self.call('GET', '/api/journal/entry', dict(lessonKey=key, day=day))
            if data.get('attendanceDefaulted'):
                # The editor only shows everybody present; nothing of it is stored. Saving it back would mark the pupils.
                for record in data['document']['content']['records']:
                    record['attendance'] = 'unmarked'
            self.entries[key] = data
        return self.entries[key]

    def works(self, group, subject, start, end):
        """{work id: (lessonKey, day)} of the lessons of a course that hold works; the course table lists them in one read."""
        if (group, subject) not in self.tables:
            found = {}
            if start <= end:
                table = self.call('GET', '/api/journal/course-table', dict(groupId=group, subjectId=subject, start=start.isoformat(), end=end.isoformat()))
                for work in table['works']:
                    lesson = table['lessons'][work['lesson']]
                    found.setdefault(work['id'], (lesson['lessonKey'], lesson['day']))
            self.tables[group, subject] = found
        return self.tables[group, subject]

    def history(self, key):
        """The saves of a lesson, newest first: [{version, actor, action}] (the API lists 50); None when it cannot be read."""
        if key not in self.rows:
            try:
                self.rows[key] = self.call('GET', '/api/journal/history', dict(lessonKey=key))
            except Refused:
                self.rows[key] = None
        return self.rows[key]

    def content(self, key, version):
        """The content saved as one version of a lesson (a version never changes, so it is read once); None when it cannot be read."""
        if (key, version) not in self.contents:
            try:
                rows = self.call('GET', '/api/journal/history', dict(lessonKey=key, version=version))
                self.contents[key, version] = rows[0]['document']['content'] if rows else None
            except Refused:
                self.contents[key, version] = None
        return self.contents[key, version]

    def withdrawn(self, key):
        """Whether the teacher hid this lesson from the families (the latest history lines are read)."""
        rows = self.history(key)
        return rows is None or any(line['action'] == 'journal.withdrawn' for line in rows)


# ----------------------------------------------------------------------------- planning

@dataclass
class Work:
    """One work to put in Atlas: id, assessment (name, kind, maxScore, weight, category) and a grade for each Atlas pupil."""
    id: str
    assessment: dict
    grades: dict


@dataclass
class LessonPlan:
    key: str
    day: str
    works: dict = field(default_factory=dict)
    merged: object = None


@dataclass
class Prepared:
    column: Column
    group: dict
    subject: dict
    day: date
    works: list


@dataclass
class Merged:
    content: dict
    states: dict
    written: int = 0
    only_here: int = 0
    adjusted: int = 0
    kept_marks: int = 0
    kept_works: int = 0
    removed_works: int = 0
    changed: bool = False
    publish: bool = False
    kept: bool = False
    problem: str = ''


class Past:
    """What the saved versions of one lesson say about who changed what, to leave the teachers' own changes alone.

    The history is read only when something differs from what the importer would write. Only the importer's own saves are
    "ours": a lesson no one else has saved needs no reading at all. Without a signed-in name (accounts off) nobody can be told
    from the importer, so every difference is taken for a hand edit."""

    def __init__(self, atlas, key, me):
        self.atlas, self.key, self.me = atlas, key, me

    def rows(self):
        return self.atlas.history(self.key)

    def foreign(self):
        rows = self.rows()
        if rows is None:
            return True
        return any(r['actor'] != self.me for r in rows) if self.me else bool(rows)

    def removed(self, pick, ours=False):
        """Whether the value (a work, a grade) was in some saved version but is not in the lesson now."""
        if not self.foreign():
            return False
        for row in self.rows() or []:
            if ours and self.me and row['actor'] != self.me:
                continue
            content = self.atlas.content(self.key, row['version'])
            if content and pick(content) is not None:
                return True
        return False

    def by_hand(self, pick):
        """Whether the value in the lesson now was last set by someone other than the importer (or it cannot be told)."""
        if not self.foreign():
            return False
        rows = self.rows() or []
        for i, row in enumerate(rows):
            later = self.atlas.content(self.key, row['version'])
            earlier = self.atlas.content(self.key, rows[i + 1]['version']) if i + 1 < len(rows) else {}
            if later is None or earlier is None:
                break
            if pick(later) != (pick(earlier) if earlier else None):
                return not self.me or row['actor'] != self.me
        return True

    def pending(self, since):
        """Whether the importer has a save of its own that is not published yet (saved after version `since`). Without a name any save may be its own."""
        return any(r['version'] > since and (not self.me or r['actor'] == self.me) for r in self.rows() or [])

    def hand_edited(self, since):
        """Whether someone other than the importer saved, after version `since` (0: from the start), a change to an imported work or to a mark of one.

        Each such save is compared with the save before it; what cannot be read counts as a change."""
        rows = self.rows()
        if rows is None:
            return True
        for i, row in enumerate(rows):
            if row['version'] <= since:
                return False
            if self.me and row['actor'] == self.me:
                continue
            later = self.atlas.content(self.key, row['version'])
            if i + 1 < len(rows):
                earlier = self.atlas.content(self.key, rows[i + 1]['version'])
            else:
                earlier = {} if row['version'] == 1 else None  # The list holds 50 saves; the one before this is out of sight.
            current, before = (imported_part(later), imported_part(earlier)) if later is not None and earlier is not None else (None, None)
            if current is None or before is None or current != before:
                return True
        return False


def work_pick(work_id, keys):
    def pick(content):
        found = next((a for a in content['assessments'] if a['id'] == work_id), None)
        return {k: found.get(k) for k in keys} if found else None
    return pick


def grade_pick(work_id, pupil):
    def pick(content):
        record = next((r for r in content['records'] if r['studentId'] == pupil), None)
        found = next((g for g in record['grades'] if g['assessmentId'] == work_id), None) if record else None
        return {k: found.get(k) for k in MARKED} if found else None
    return pick


def foreign(content):
    """Whether the draft holds anything that is not an imported work: publishing the draft would show it too."""
    if any((content.get(k) or '').strip() for k in ['topic', 'homework']) or any(content.get(k) for k in ['dueDate', 'homeworkFiles', 'materials', 'curriculum']):
        return True
    if any(not a['id'].startswith(PREFIX) for a in content['assessments']):
        return True
    return any(r['attendance'] != 'unmarked' or r['lateMinutes'] or r['comment'] or r['absenceCode'] or r['leftAt']
               or any(not g['assessmentId'].startswith(PREFIX) for g in r['grades']) for r in content['records'])


def without_imported(content):
    """The lesson content without the imported works and their marks, in a form that compares: what a family sees besides them."""
    try:
        result = Content.model_validate(content).model_dump(mode='json')
    except ValidationError:
        return None
    result['assessments'] = [a for a in result['assessments'] if not a['id'].startswith(PREFIX)]
    result['records'] = {r['studentId']: dict(r, grades=[g for g in r['grades'] if not g['assessmentId'].startswith(PREFIX)]) for r in result['records']}
    return result


def imported_part(content):
    """The imported works and their marks of a lesson content, in a form that compares (None when the content is not valid)."""
    if not content:
        return {}, {}
    try:
        data = Content.model_validate(content).model_dump(mode='json')
    except ValidationError:
        return None
    return ({a['id']: a for a in data['assessments'] if a['id'].startswith(PREFIX)},
            {(r['studentId'], g['assessmentId']): g for r in data['records'] for g in r['grades'] if g['assessmentId'].startswith(PREFIX)})


def adjust(grade, work):
    """Make a mark that no longer fits its work valid (the maximum was lowered, or the work became text): keep what it said. True when changed."""
    if work['kind'] == 'label':
        if grade['score'] is None:
            return False
        grade.update(label=fit(f'{grade["score"]:g}', LABEL_LIMIT), score=None)
        return True
    if grade['label']:
        grade.update(label='', comment=fit(f'{grade["label"]} {grade["comment"]}'.strip(), COMMENT_LIMIT))
        return True
    if grade['score'] is not None and grade['score'] > work['maxScore']:
        grade.update(score=work['maxScore'], comment=fit(f'В Атласе стояло {grade["score"]:g} при максимуме {work["maxScore"]:g}. {grade["comment"]}'.strip(), COMMENT_LIMIT))
        return True
    return False


def merge(entry, plan, publish, withdrawn, past, published):
    """The lesson content with the planned works and marks in it: everything already there stays, imported works are updated in place.

    A value that someone else set in Atlas after the import (a corrected mark, a work's weight, a deleted work or mark) is not set again.
    The server publishes the whole draft, so the lesson is published only when that shows nothing the teacher has not published yet.
    published() gives the content of the version that is published now."""
    original = entry['document']['content']
    content = deepcopy(original)
    records = {r['studentId']: r for r in content['records']}
    result = Merged(content, {})
    saved = entry['version'] > 0  # A lesson never saved has no history and nobody's changes in it.
    for work in plan.works.values():
        found = next((a for a in content['assessments'] if a['id'] == work.id), None)
        if found is None:
            if saved and past.removed(work_pick(work.id, ['name']), ours=True):
                result.removed_works += 1
                continue
            content['assessments'].append(dict(id=work.id, **work.assessment))
            state = 'new'
        else:
            state = 'unchanged' if all(found.get(k) == v for k, v in work.assessment.items()) else 'updated'
            if state == 'updated' and saved and past.by_hand(work_pick(work.id, work.assessment)):
                state = 'unchanged'
                result.kept_works += 1
            else:
                found.update(work.assessment)
            result.only_here += sum(1 for pupil, r in records.items() if pupil not in work.grades
                                    and any(g['assessmentId'] == work.id and (g['score'] is not None or g['label'] or g['comment']) for g in r['grades']))
        for pupil, grade in work.grades.items():
            record = records.get(pupil)
            if record is None:
                continue
            current = next((g for g in record['grades'] if g['assessmentId'] == work.id), None)
            if current is None:
                if found is not None and saved and past.removed(grade_pick(work.id, pupil)):
                    result.kept_marks += 1
                    continue
                record['grades'].append(dict(assessmentId=work.id, **grade))
            elif all(current.get(k) == v for k, v in grade.items()):
                continue
            elif saved and past.by_hand(grade_pick(work.id, pupil)):
                result.kept_marks += 1
                continue
            else:
                current.update(grade)
            result.written += 1
            if state == 'unchanged':
                state = 'updated'
        result.states[work.id] = state
        placed = next(a for a in content['assessments'] if a['id'] == work.id)
        result.adjusted += sum(adjust(g, placed) for r in content['records'] for g in r['grades'] if g['assessmentId'] == work.id)
    try:
        Content.model_validate(content)
    except ValidationError as exc:
        result.problem = fit('; '.join(str(e['msg']) for e in exc.errors()), 300)
    result.changed = content != original
    version, public = entry['version'], entry.get('publishedVersion')
    marked = any(g['assessmentId'] in plan.works and (g['score'] is not None or g['label'] or g['comment']) for r in content['records'] for g in r['grades'])
    if publish and marked:
        if public is None:
            # Never published: only when the draft holds nothing of its own and the teacher has not hidden it.
            safe = version == 0 or not foreign(original) and not withdrawn() and not past.hand_edited(0)
        elif public == version:
            safe = True
        else:
            # The draft is ahead of the publication: publishing it shows only the imported works when nothing else differs.
            # A change someone else saved to an imported work or mark counts as a difference too: it is not the importer's to publish.
            before = published()
            base = without_imported(before) if before else None
            safe = base is not None and base == without_imported(original) and not past.hand_edited(public)
        # A lesson stays a draft only when the importer has something of its own there: a write now, or marks it saved and nobody published.
        result.kept = not safe and (result.changed or past.pending(public or 0))
        result.publish = safe and (result.changed or public != version)
    return result


def merge_in(atlas, entry, plan, publish, me):
    """merge() with what it asks of Atlas: the saved versions of the lesson (history) and the publication."""
    key = plan.key
    return merge(entry, plan, publish, lambda: atlas.withdrawn(key), Past(atlas, key, me), lambda: atlas.content(key, entry.get('publishedVersion')))


@dataclass
class Rules:
    subjects: dict = field(default_factory=dict)
    skip: set = field(default_factory=set)
    categories: dict = field(default_factory=dict)

    @classmethod
    def read(cls, path):
        if not path:
            return cls()
        data = json.loads(Path(path).read_text(encoding='utf8'))
        return cls({text_key(k): [v] if isinstance(v, str) else list(v) for k, v in (data.get('subjects') or {}).items()},
                   {text_key(s) for s in data.get('skipSubjects') or []},
                   {text_key(k): v for k, v in (data.get('categories') or {}).items()})


class Planner:
    def __init__(self, atlas, source, rules, publish, minute, me=None):
        self.atlas, self.source, self.rules, self.publish, self.minute, self.me = atlas, source, rules, publish, minute, me
        self.lessons, self.matches, self.planned, self.sizes = {}, {}, set(), {}
        self.unresolved = defaultdict(lambda: defaultdict(int))
        self.report = dict(
            files=source.files, filesUnreadable=source.unreadable,
            columns=dict(total=0, notSubject=len(source.not_subject), skippedByRules=0, withoutPupils=0, planned=0, unresolved={}),
            works=dict(new=0, updated=0, unchanged=0, companion=0),
            marks=dict(written=0, onlyInAtlas=0, textOnly=0, scoresCut=0, adjusted=0, attemptsKept=0,
                       leftOut=dict(outsideGroup=0, notInLesson=0, noPupilMatch=0, noLesson=0)),
            titlesShortened=0, moved=dict(anotherDay=0, forCapacity=0),
            lessons=dict(toSave=0, toPublish=0, blocked=0, keptAsDraft=0, saved=0, refused=[]),
            keptAsInAtlas=dict(editedMarks=0, editedWorks=0, removedWorks=0),
            pupilsWithoutMatch={}, categoriesUnmapped={}, goneFromEdupage=0)
        self.counts = self.report['marks']

    # -- what Atlas knows
    def load(self):
        state = self.atlas.need('state', '/api/state')
        options = self.atlas.need('options', '/api/journal/options')
        self.catalog = self.atlas.need('categories', '/api/journal/categories')
        self.today = date.fromisoformat(options['today'])
        self.year = date.fromisoformat(state['settings']['yearStart']), date.fromisoformat(state['settings']['yearEnd'])
        self.classes = {c['id']: c['name'] for c in state['classes']}
        self.students = {s['id']: s for s in state['students']}
        self.names = [s['name'] for s in state['students']] + [t['name'] for t in state['teachers']] + [p.name for p in self.source.pupils.values()]
        self.pupils = defaultdict(list)
        for s in state['students']:
            if s.get('active', True):
                self.pupils[class_key(self.classes.get(s['classId'], '')), name_key(s['name'])].append(s['id'])
        self.found = {sid: self.pupils.get((class_key(p.klass), name_key(p.name)), []) for sid, p in self.source.pupils.items()}
        self.claims = defaultdict(list)
        for sid, found in self.found.items():
            if len(found) == 1:
                self.claims[found[0]].append(sid)
        self.subjects = defaultdict(list)
        for s in state['subjects']:
            self.subjects[text_key(s['name'])].append(s)
        self.groups = {g['id']: g for g in state['groups']}
        self.courses = defaultdict(set)
        for w in state['workloads']:
            self.courses[w['subjectId']].add(w['groupId'])
        end = min(self.today, self.year[1])
        self.span = max(self.year[0], end - timedelta(days=365)), end

    def members(self, group):
        """The pupils of a group: its own list when filled, else the active pupils of its classes."""
        if group['id'] not in self.sizes:
            self.sizes[group['id']] = set(group['studentIds']) or {s['id'] for s in self.students.values() if s.get('active', True) and s['classId'] in group['classIds']}
        return self.sizes[group['id']]

    def match(self, sid):
        """The Atlas pupil of an EduPage pupil: the same class and name among the active pupils, exactly one.

        When several EduPage records (a pupil enrolled again keeps an old one) name the same Atlas pupil, the one without an end of
        study is taken if it is the only one; otherwise none of them is, as with two Atlas pupils of one name."""
        if sid not in self.matches:
            found = self.found.get(sid, [])
            pupil, why = (found[0] if len(found) == 1 else None), ('ambiguous' if len(found) > 1 else 'none')
            if pupil:
                twins = self.claims[pupil]
                if len(twins) > 1 and [t for t in twins if not self.source.pupils[t].ended] != [sid]:
                    pupil, why = None, 'ambiguous'
            self.matches[sid] = pupil, why
        return self.matches[sid][0]

    def category(self, name):
        """The id of the Atlas category of an EduPage category name: the rules file, then name or title, then the kind of calendar work."""
        active = [c for c in self.catalog['categories'] if c['active']]

        def find(text):
            key = text_key(text)
            return next((c for c in active if key in {text_key(c['id']), text_key(c['name']), text_key(c['title'])}), None)

        key = text_key(name)
        found = find(self.rules.categories[key]) if key in self.rules.categories else None
        found = found or find(name)
        if not found and 'суммат' in key:
            kind = 'summative_unit' if 'раздел' in key else 'summative_term' if 'семестр' in key or 'четверт' in key else None
            found = next((c for c in active if kind and c['calendar'] == kind), None)
        if not found and 'формат' in key:
            found = next((c for c in active if c['id'] == 'fo'), None) or next((c for c in active if not c['calendar']), None)
        return found['id'] if found else None

    # -- one column
    def unresolve(self, column, reason):
        self.unresolved[reason][column.subject, tuple(sorted(column.classes))] += 1

    def prepare(self, column):
        report = self.report['columns']
        report['total'] += 1
        subject_key = text_key(column.subject)
        if subject_key in self.rules.skip:
            report['skippedByRules'] += 1
            return None
        sids = set(column.only) | {m.pupil for m in column.marks.values()}
        if not sids:
            report['withoutPupils'] += 1
            return None
        subjects = self.subjects.get(subject_key) or [s for name in self.rules.subjects.get(subject_key, []) for s in self.subjects.get(text_key(name), [])]
        if not subjects:
            return self.unresolve(column, 'noSubject')
        people = {sid: self.match(sid) for sid in sids}
        pupils = {p for p in people.values() if p}
        if not pupils:
            return self.unresolve(column, 'noPupilMatch')
        day = self.work_date(column)
        if day is None:
            return self.unresolve(column, 'noDate')
        chosen = self.choose_group(column, subjects, pupils)
        if chosen is None:
            return self.unresolve(column, 'noGroup')
        return Prepared(column, *chosen, day, self.works_of(column, chosen[0], people))

    @staticmethod
    def work_date(column):
        """p_termin when it is not later than the first mark; else the day of the first mark; else the day of the last edit."""
        first = min((m.day for m in column.marks.values() if m.day), default=None)
        if column.termin and (first is None or column.termin <= first):
            return column.termin
        return first or column.edited

    def choose_group(self, column, subjects, pupils):
        """(group, subject) of a column: where its work is already placed; else the smallest group holding every pupil; else one holding most."""
        pairs = sorted({(g, s['id']): (self.groups[g], s) for s in subjects for g in self.courses[s['id']] if pupils & self.members(self.groups[g])}.values(),
                       key=lambda p: (len(self.members(p[0])), p[0]['id'], p[1]['id']))
        for group, subject in pairs:
            if any(self.slot_of(i, column.uid) for i in self.atlas.works(group['id'], subject['id'], *self.span)):
                return group, subject
        whole = [p for p in pairs if pupils <= self.members(p[0])]
        if whole:
            return whole[0]
        best = max(pairs, key=lambda p: (len(pupils & self.members(p[0])), -len(self.members(p[0]))), default=None)
        return best if best and len(pupils & self.members(best[0])) >= ENOUGH * len(pupils) else None

    @staticmethod
    def slot_of(work, uid):
        """Whether an Atlas work id is the work of this column or one of its attempt works (never another column's work)."""
        base = work_id(uid)
        return work == base or re.fullmatch(re.escape(base) + r'~\d+', work) is not None

    def works_of(self, column, group, people):
        """The base work and, when every mark counts, one more work for each further attempt."""
        report = self.report
        category = self.category(column.category) if column.category else None
        if column.category and category is None:
            report['categoriesUnmapped'][column.category] = report['categoriesUnmapped'].get(column.category, 0) + 1
        category = category or self.catalog['defaultCategory']
        title = ' '.join(column.title.split()) or next(c['title'] for c in self.catalog['categories'] if c['id'] == category)
        report['titlesShortened'] += len(title) > NAME_LIMIT
        assessment = dict(name=fit(title, NAME_LIMIT), kind='points' if column.maximum else 'label', maxScore=column.maximum or 10,
                          weight=min(100.0, column.weight) if column.category else 0, category=category)
        members, attempts, grades = self.members(group), defaultdict(list), defaultdict(dict)
        for mark in sorted(column.marks.values(), key=order_key):
            attempts[mark.pupil].append(mark)
        for sid, marks in attempts.items():
            if column.every:
                chosen = list(enumerate(marks, 1))
            else:
                scored = [(parse_mark(m.text)[0], m) for m in marks]
                chosen = [(1, max((s for s in scored if s[0] is not None), key=lambda s: s[0], default=scored[0])[1])]
            pupil = people.get(sid)
            if pupil is None or pupil not in members:
                self.counts['leftOut']['noPupilMatch' if pupil is None else 'outsideGroup'] += len(chosen)
                continue
            for k, mark in chosen:
                if pupil in grades[k]:  # Two EduPage records of one pupil: the later never replaces the earlier unseen.
                    self.counts['leftOut']['noPupilMatch'] += 1
                    continue
                grade = make_grade(mark.text, assessment, self.counts)
                if not column.every and len(marks) > 1:
                    grade['comment'] = fit(f'{grade["comment"]} Другие попытки: ' + '; '.join(m.text for m in marks if m is not mark), COMMENT_LIMIT)
                grades[k][pupil] = grade
        works = [Work(work_id(column.uid), assessment, grades.get(1, {}))]
        for k in sorted(k for k in grades if k > 1):
            suffix = f' · попытка {k}'
            works.append(Work(work_id(column.uid, k), dict(assessment, name=fit(assessment['name'], NAME_LIMIT - len(suffix)) + suffix), grades[k]))
            report['works']['companion'] += 1
        return works

    def guard_attempts(self, prepared):
        """Attempts are numbered by their order, so an attempt deleted in EduPage would shift the later ones onto the wrong works and
        leave a mark twice. A pupil who holds more attempts in Atlas than EduPage has keeps what Atlas holds, and it is counted."""
        held = defaultdict(int)
        for found, where in self.atlas.works(prepared.group['id'], prepared.subject['id'], *self.span).items():
            entry = self.slot_of(found, prepared.column.uid) and self.entry(*where)
            for record in (entry['document']['content']['records'] if entry else []):
                held[record['studentId']] += any(g['assessmentId'] == found and any(g[k] not in (None, '') for k in MARKED) for g in record['grades'])
        for pupil, count in held.items():
            planned = sum(1 for work in prepared.works if pupil in work.grades)
            if planned and count > planned:
                for work in prepared.works:
                    work.grades.pop(pupil, None)
                self.counts['attemptsKept'] += 1

    # -- lessons
    def entry(self, key, day):
        try:
            return self.atlas.entry(key, day)
        except Refused:
            return None

    def lessons_of(self, group, subject, day):
        """The lessons of a course on a day that have begun and are not cancelled, earliest first."""
        if not self.year[0] <= day <= min(self.year[1], self.today):
            return []
        names = {self.classes.get(c) for c in group['classIds']}
        found = []
        for item in self.atlas.day(day.isoformat()):
            lesson = item['lesson']
            if item.get('archived') or lesson['status'] == 'cancelled' or lesson['subject'] != subject['name'] or lesson['group'] != group['name']:
                continue
            if item['classes'] and not set(item['classes']) <= names or day == self.today and lesson['start'] > self.minute:
                continue
            found.append(item)
        return sorted(found, key=lambda i: (i['lesson']['start'], i['lessonKey']))

    def candidates(self, group, subject, day):
        """The entries of the course's lessons in the order a work is offered: the day itself, then up to 7 days back, then up to 7 forward."""
        for offset in [0, *range(-1, -SEARCH_DAYS - 1, -1), *range(1, SEARCH_DAYS + 1)]:
            for item in self.lessons_of(group, subject, day + timedelta(days=offset)):
                entry = self.entry(item['lessonKey'], item['lesson']['date'])
                lesson = entry and entry['document']['lesson']
                # The day list matches by names; the ids settle it.
                if lesson and (lesson['subjectId'], lesson['groupId']) == (subject['id'], group['id']):
                    yield entry

    def plan_of(self, entry):
        return self.lessons.setdefault(entry['lessonKey'], LessonPlan(entry['lessonKey'], entry['document']['lesson']['date']))

    @staticmethod
    def roster(entry):
        return {s['id'] for s in entry['document']['lesson']['students'] if s.get('enrolled', True)}

    def fits(self, entry, work):
        """Room for one more work, counting what the lesson has and what is planned for it.
        A pupil can hold no more marks than the lesson has works and both limits are 10, so the work limit covers the pupils too."""
        return len({a['id'] for a in entry['document']['content']['assessments']} | set(self.plan_of(entry).works)) < MAX_WORKS

    def add(self, entry, work):
        roster = self.roster(entry)
        grades = {p: g for p, g in work.grades.items() if p in roster}
        self.counts['leftOut']['notInLesson'] += len(work.grades) - len(grades)
        self.plan_of(entry).works[work.id] = Work(work.id, work.assessment, grades)
        self.planned.add(work.id)

    def place(self, prepared, work):
        """A work Atlas already has stays where it is; a new one goes to the first lesson with room."""
        known = self.atlas.works(prepared.group['id'], prepared.subject['id'], *self.span).get(work.id)
        if known:
            entry = self.entry(*known)
            if entry:
                self.add(entry, work)
            return bool(entry)
        first = None
        for entry in self.candidates(prepared.group, prepared.subject, prepared.day):
            first = first or entry
            if self.fits(entry, work):
                self.add(entry, work)
                self.report['moved']['anotherDay'] += entry['document']['lesson']['date'] != prepared.day.isoformat()
                self.report['moved']['forCapacity'] += entry is not first
                return True
        return False

    def run(self):
        self.load()
        prepared = [p for p in (self.prepare(c) for c in self.source.columns.values()) if p]
        for p in sorted(prepared, key=lambda p: (p.day, p.column.uid)):
            if p.column.every:
                self.guard_attempts(p)
            placed = [self.place(p, work) for work in p.works]
            if all(placed):
                self.report['columns']['planned'] += 1
                continue
            self.unresolve(p.column, 'noLesson')
            self.counts['leftOut']['noLesson'] += sum(len(w.grades) for w, ok in zip(p.works, placed) if not ok)
        self.finish()
        return self.report

    def finish(self):
        report, lessons = self.report, self.report['lessons']
        for plan in self.lessons.values():
            entry = self.atlas.entry(plan.key, plan.day)
            plan.merged = merged = merge_in(self.atlas, entry, plan, self.publish, self.me)
            if merged.problem:  # Nothing of this lesson goes in, so none of it is counted as written.
                lessons['refused'].append(self.refusal(entry, 422, merged.problem))
                continue
            for state in merged.states.values():
                report['works'][state] += 1
            self.counts['written'] += merged.written
            self.counts['onlyInAtlas'] += merged.only_here
            self.counts['adjusted'] += merged.adjusted
            lessons['keptAsDraft'] += merged.kept
            lessons['toPublish'] += merged.publish
            kept = report['keptAsInAtlas']
            kept['editedMarks'] += merged.kept_marks
            kept['editedWorks'] += merged.kept_works
            kept['removedWorks'] += merged.removed_works
            if merged.changed or merged.publish:
                lessons['toSave'] += 1
                lessons['blocked'] += not entry['writable'] or entry['needsSync']
        for reason in UNRESOLVED:
            if reason in self.unresolved:
                report['columns']['unresolved'][reason] = [dict(subject=s, classes=list(c), columns=n) for (s, c), n in sorted(self.unresolved[reason].items())]
        for sid in sorted({m.pupil for c in self.source.columns.values() for m in c.marks.values()}):
            if self.match(sid) is None:
                person = self.source.pupils.get(sid)
                counter = report['pupilsWithoutMatch'].setdefault(person.klass if person else '?', dict(none=0, ambiguous=0))
                counter[self.matches[sid][1]] += 1
        known = {work_id(c.uid) for c in self.source.columns.values()}
        loaded = {i for found in self.atlas.tables.values() for i in found if i.startswith(PREFIX)}

        def base(i):
            found = re.fullmatch(r'(.+)~\d+', i)
            return i if i in known or not found else found.group(1)

        # A column that is still in EduPage but could not be planned is not gone; one that is no longer there (or one attempt of it) is.
        report['goneFromEdupage'] = sum(1 for i in loaded - self.planned if base(i) not in known or base(i) in self.planned)

    def refusal(self, entry, status, reason):
        lesson = entry['document']['lesson']
        for name in sorted(self.names, key=len, reverse=True):
            reason = re.sub(re.escape(name), '…', reason, flags=re.I)
        return dict(day=lesson['date'], subject=lesson['subject'], group=lesson['group'], status=status, reason=reason)


# ----------------------------------------------------------------------------- writing

def save(atlas, plan, publish, me=None):
    """GET then PUT one lesson; a version conflict is read again and tried once more. Returns the marks written; a refusal raises Refused."""
    for attempt in (0, 1):
        entry = atlas.entry(plan.key, plan.day, fresh=True)
        merged = merge_in(atlas, entry, plan, publish, me)
        if merged.problem:
            raise Refused(422, merged.problem)
        if not (merged.changed or merged.publish):
            return 0
        body = dict(lessonKey=plan.key, day=entry['document']['lesson']['date'], revision=entry['revision'], version=entry['version'],
                    content=merged.content, publish=merged.publish)
        try:
            atlas.call('PUT', '/api/journal/entry', body=body)
            return merged.written
        except Refused as exc:
            if exc.status != 409 or attempt:
                raise


def run(http, workspace, source, rules=None, apply=False, publish=True, minute=None, me=None, confirmed=False):
    """Plan the import and, with apply, save it. The result is the report: counts only.

    me is the name the importer is signed in under: the saves under another name are the teachers'. A run that publishes more than
    PUBLISH_LIMIT lessons writes nothing unless confirmed."""
    atlas = Atlas(http, workspace)
    minute = minute or datetime.now(ZoneInfo('Asia/Qyzylorda')).strftime('%H:%M')
    planner = Planner(atlas, source, rules or Rules(), publish, minute, me)
    stopped, planned = None, False
    try:
        report = planner.run()
        planned = True
        if apply:
            lessons = report['lessons']
            if lessons['toPublish'] > PUBLISH_LIMIT and not confirmed:
                raise Stop('publish', 0, f'будет опубликовано уроков: {lessons["toPublish"]}, и каждая семья получит уведомления; '
                                         'запустите с --draft, чтобы сохранить оценки неопубликованными, или с --publish, чтобы опубликовать')
            for plan in sorted(planner.lessons.values(), key=lambda p: (p.day, p.key)):
                if plan.merged.problem or not (plan.merged.changed or plan.merged.publish):
                    continue
                try:
                    save(atlas, plan, publish, me)
                    lessons['saved'] += 1
                except Refused as exc:
                    lessons['refused'].append(planner.refusal(atlas.entry(plan.key, plan.day), exc.status, exc.reason))
    except Stop as stop:
        stopped = dict(step=stop.step, status=stop.status, reason=stop.reason)
    except Refused as exc:  # A read the plan needs, refused while planning.
        stopped = dict(step='read', status=exc.status, reason=exc.reason)
    if stopped:
        # What the run did before it stopped stays in the report: lessons saved before a drop of the connection are in Atlas.
        return dict(aborted=stopped, **(dict(mode='apply', publish=publish, **planner.report) if planned else {}))
    return dict(mode='apply' if apply else 'dry-run', publish=publish, **planner.report)


def sign_in(http, ask):
    """Sign in when the server asks for it (ask() gives login and password). Returns (name signed in under or None, whether a session was opened)."""
    status, data = http.call('GET', '/api/auth/status')
    if status != 200 or not (data or {}).get('enabled'):
        return None, False
    if data.get('user'):
        return data['user'].get('name'), False
    status, data = http.call('POST', '/api/auth/login', None, dict(zip(['login', 'password'], ask())))
    if status != 200:
        raise Stop('sign-in', status, reason_of(data))
    return (data.get('user') or {}).get('name'), True


def credentials(path):
    if path:
        lines = Path(path).read_text(encoding='utf8').split(chr(10))
        if len(lines) < 2 or not lines[0].strip() or not lines[1].rstrip(chr(13)):
            raise Stop('sign-in', 0, 'в файле учётной записи нужны логин в первой строке и пароль во второй')
        return lines[0].strip(), lines[1].rstrip(chr(13))
    return input('Логин администратора: ').strip(), getpass.getpass('Пароль: ')


def main(argv=None, http=None, minute=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', nargs='+', help='folders or files with <class>_P1.json.gz')
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--workspace', default='school', choices=['school', 'demo'])
    parser.add_argument('--account', help='file: login on the first line, password on the second (outside the repository)')
    parser.add_argument('--rules', help='JSON kept outside the repository: subjects, skipSubjects, categories')
    parser.add_argument('--apply', action='store_true', help='write; without it nothing is changed')
    publishing = parser.add_mutually_exclusive_group()
    publishing.add_argument('--draft', action='store_true', help='save marks unpublished')
    publishing.add_argument('--publish', action='store_true', help=f'publish even when more than {PUBLISH_LIMIT} lessons are published: every family gets notices')
    parser.add_argument('--report', help='write the report as JSON to this file')
    args = parser.parse_args(argv)
    http, signed = http or Http(args.url), False
    try:
        source = load_sources(args.source)
        if not source.files:
            raise Stop('files', 0, 'не найдено ни одного читаемого файла журнала')
        rules = Rules.read(args.rules)
        me, signed = sign_in(http, lambda: credentials(args.account))
        report = run(http, args.workspace, source, rules, args.apply, not args.draft, minute, me, args.publish)
    except Stop as stop:
        report = dict(aborted=dict(step=stop.step, status=stop.status, reason=stop.reason))
    finally:
        if signed:
            http.call('POST', '/api/auth/logout')
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        Path(args.report).write_text(text + '\n', encoding='utf8')
    return 2 if 'aborted' in report else 0


if __name__ == '__main__':
    sys.exit(main())
