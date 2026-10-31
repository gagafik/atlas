"""Transactional personal inbox. Titles are prepared per recipient at write time and only name what that recipient already sees."""
from collections import defaultdict
from datetime import date, datetime, time as daytime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo
import hashlib
import json
import re
import time

from fastapi import HTTPException, Request
from pydantic import Field, model_validator
from .models import Model
from .access import LOCAL, now
from .exporting import ExportRequest, schedule_rows
from .resources import teacher_ids, room_ids


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def permission_key(user):
    # Stored with each entry: background push skips entries made under other rights; the inbox keeps the owner's history.
    return hashlib.sha256(encoded({k: user[k] for k in ['role', 'workspaces', 'grants']}).encode()).hexdigest()


ZONE = ZoneInfo('Asia/Qyzylorda')
SYSTEM = 'system'
# Ordered: this is the order of the settings list. `label` names the category in settings, `push` is the only text a push may show.
CATEGORIES = {
    'grades': dict(label='Оценки', push='Новая оценка'),
    'homework': dict(label='Домашние задания', push='Новое ДЗ'),
    'journal': dict(label='Посещаемость и записи уроков', push='Обновление журнала'),
    'absences': dict(label='Объяснительные', push='Объяснительная'),
    'quizzes': dict(label='Тесты', push='Тест'),
    'assessments': dict(label='Календарь контрольных', push='Контрольная работа'),
    'behaviour': dict(label='Похвалы и замечания', push='Запись о поведении'),
    'messages': dict(label='Сообщения', push='Новое сообщение'),
    'meetings': dict(label='Родительские встречи', push='Встреча'),
    'forms': dict(label='Формы и заявления', push='Форма или заявление'),
    'timetable': dict(label='Расписание', push='Изменение расписания'),
    'duty': dict(label='Дежурства', push='Дежурство'),
    'other': dict(label='Прочее', push='Обновление в Атласе'),
}
PUSH_LABELS = {id: value['push'] for id, value in CATEGORIES.items()} | {SYSTEM: 'Проверка уведомлений'}
FAMILY = ('student', 'parent')
PREFIXES = {'absence.': 'absences', 'quiz.': 'quizzes', 'assessment.': 'assessments', 'behaviour.': 'behaviour',
            'message.': 'messages', 'meeting.': 'meetings', 'forms.': 'forms', 'duty.': 'duty', 'timetable.': 'timetable'}


def category_of(action):
    """Settings category of an action; a new action without a prefix lands in 'other'."""
    if action in ('journal.grade', 'journal.finals_published', 'journal.finals_withdrawn'): return 'grades'
    if action == 'journal.homework' or action.startswith('homework.'): return 'homework'
    if action.startswith('journal.'): return 'journal'
    if action in ('saved', 'undo', 'redo'): return 'timetable'
    if action == 'push.test': return SYSTEM
    return next((category for prefix, category in PREFIXES.items() if action.startswith(prefix)), 'other')


def student_ids(user, workspace):
    return {id for g in user['grants'] if g['workspace'] == workspace and g['view'] == 'student' for id in g['entityIds']}


TITLE_SIZE = dict(duty=300)  # characters; a duty notice joins up to three sentences and has always kept 300


def clip(text, size=200):
    text = ' '.join(str(text).split())
    return text if len(text) <= size else text[:size-1].rstrip() + '…'


def safe_target(value):
    """Whitelist what an entry may keep: where it leads, which children it concerns and one short title."""
    if not isinstance(value, dict): return None
    result = {}
    if isinstance(value.get('module'), str) and value['module'].strip(): result['module'] = value['module'].strip()[:40]
    record = value.get('recordId')
    if isinstance(record, (str, int)) and not isinstance(record, bool) and str(record): result['recordId'] = str(record)[:100]
    students = [dict(id=s['id'][:100], name=clip(s.get('name') or '', 160)) for s in value.get('students') or []
                if isinstance(s, dict) and isinstance(s.get('id'), str) and s['id']][:10]
    if students: result['students'] = students
    day = value.get('day')
    if isinstance(day, date): day = day.isoformat()
    if isinstance(day, str) and re.fullmatch(r'\d{4}-\d\d-\d\d', day):
        try: date.fromisoformat(day); result['day'] = day
        except ValueError: pass
    title = clip(value['title'], TITLE_SIZE.get(result.get('module'), 200)) if isinstance(value.get('title'), str) else ''
    if title: result['title'] = title
    return result or None


def preference_map(db, ids):
    """Settings of many accounts in one query; an account without a row mutes nothing and has no quiet hours."""
    result = {id: dict(muted=set(), quietStart=None, quietEnd=None) for id in ids}
    if result:
        for id, enabled, muted, start, end in db.execute(
                'SELECT user_id,enabled,muted,quiet_start,quiet_end FROM notification_preferences WHERE user_id IN (SELECT value FROM json_each(?))',
                (encoded(list(result)),)):
            # A row written before categories existed carries only the old switch.
            everything = set(CATEGORIES) if muted is None and enabled == 0 else set()
            result[id] = dict(muted=(set(json.loads(muted)) if muted else everything) & set(CATEGORIES), quietStart=start, quietEnd=end)
    return result


def quiet_until(pref, instant=None):
    """Epoch second when the quiet window that contains `instant` (default now) ends, else None. Windows may cross midnight."""
    start, end = pref.get('quietStart'), pref.get('quietEnd')
    if not start or not end: return None
    moment = datetime.fromtimestamp(time.time() if instant is None else instant, ZONE)
    first, last = daytime.fromisoformat(start), daytime.fromisoformat(end)
    clock = moment.time()
    if not (first <= clock < last if first < last else clock >= first or clock < last): return None
    until = moment.replace(hour=last.hour, minute=last.minute, second=0, microsecond=0)
    return (until if until > moment else until + timedelta(days=1)).timestamp()


def personal_index(state, views):
    """Compare visible weekly rows exactly; dated records conservatively by audience.

    Date intervals are not expanded into hundreds of full school days on every save.
    A changed closure/absence may notify an affected audience even if its day is empty.
    Private reasons, notes, rosters and solver-only settings are never message content.
    """
    result = defaultdict(list)
    groups = {g.id: g for g in state.groups}
    loads = {w.id: w for w in state.workloads}
    placements = {p.id: p for p in state.placements}
    members = {g.id: {s.id for s in state.students if s.active and
               (s.classId in g.classIds if g.kind == 'whole' else s.id in g.studentIds)} for g in state.groups}
    classes = {s.id: s.classId for s in state.students}
    names = {key: {r.id: r.name for r in getattr(state,key)} for key in ['subjects','groups','rooms','teachers']}

    def audience(group_ids=(), teachers=(), rooms=()):
        selected = [groups[g] for g in group_ids if g in groups]
        students = {s for g in selected for s in members[g.id]}
        class_ids = {classes[s] for s in students} | {c for g in selected for c in g.classIds
                    if g.kind == 'whole' or g.rosterStatus != 'complete'}
        return dict(student=students, class_=class_ids, group=set(group_ids), teacher=set(teachers), room=set(rooms))

    def add(targets, value):
        fingerprint = hashlib.sha256(encoded(value).encode()).hexdigest()
        for view in views:
            for id in targets.get('class_' if view == 'class' else view, ()):
                result[(view, id)].append(fingerprint)

    for view in views:
        _, _, rows = schedule_rows(state, ExportRequest(revision=state.revision, view=view))
        for row in rows:
            add({('class_' if view == 'class' else view): [row['entityId']]},
                {k: row[k] for k in ['cycleId', 'cycle', 'day', 'dayName', 'start', 'end', 'subject', 'group', 'teachers', 'rooms']})

    lessons = [audience([loads[p.workloadId].groupId], teacher_ids(loads[p.workloadId]), room_ids(loads[p.workloadId],p.roomId)) for p in state.placements]
    for item in state.datedLessons:
        p = placements.get(item.placementId)
        w = loads.get(p.workloadId) if p else None
        group_id = w.groupId if w else item.groupId
        teachers = set(teacher_ids(w) if w else ()) | set(item.teacherIds or ())
        rooms = set(room_ids(w, p.roomId) if w else ()) | set(item.roomIds or ())
        value = item.model_dump(mode='json', exclude={'name', 'note', 'id'})
        targets = audience([group_id] if group_id else [], teachers, rooms)
        lessons.append(targets)
        labels = [names['groups'].get(group_id), names['subjects'].get(item.subjectId or (w.subjectId if w else None)),
                  sorted(names['teachers'][id] for id in teachers), sorted(names['rooms'][id] for id in rooms)]
        add(targets, ['dated', value, labels])
    for item in state.commitments:
        value = item.model_dump(mode='json', exclude={'note', 'locked', 'eligibleTeacherIds', 'studentCount', 'buildingId', 'id'})
        labels = [sorted(names['groups'][id] for id in item.groupIds), sorted(names['teachers'][id] for id in item.teacherIds), sorted(names['rooms'][id] for id in item.roomIds)]
        add(audience(item.groupIds, item.teacherIds, item.roomIds), ['commitment', value, labels])
    for item in state.absences:
        targets = audience(teachers=[item.teacherId])
        for lesson in lessons:
            if item.teacherId in lesson['teacher']:
                for key, ids in lesson.items():
                    targets[key].update(ids)
        add(targets, ['absence', item.model_dump(mode='json', exclude={'name', 'note', 'id'})])
    for item in state.events:
        if item.effect != 'cancel': continue
        selected_classes = set(item.classIds or [c.id for c in state.classes])
        targets = dict(student={s.id for s in state.students if s.active and s.classId in selected_classes},
                       class_=selected_classes, group=set(), teacher=set(), room=set())
        for lesson in lessons:
            if lesson['class_'] & selected_classes:
                for key in ['group','teacher','room']: targets[key].update(lesson[key])
        add(targets, ['closure', item.model_dump(mode='json', exclude={'name', 'grades', 'kind', 'id'})])
    return {key: sorted(values) for key, values in result.items()}


def has_table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def exists(db, table, sql, params):
    """Does a row of the module's own table still allow the opener? A module that never ran has no table."""
    return has_table(db, table) and bool(db.execute(sql, params).fetchone())


# Openers answer {} (or the extra id the screen needs) when the record is still open to this account, None otherwise.
# `kids` are the entry's children the account may still see; staff accounts get all listed children.
def open_journal(db, user, workspace, data, kids):
    key = data.get('recordId')
    if kids: return {} if any(exists(db, 'journal_members', "SELECT 1 FROM journal_members WHERE workspace=? AND lesson_key=? AND student_id=? AND kind='published'", (workspace, key, kid)) for kid in kids) else None
    return {} if user['role'] == 'admin' and exists(db, 'journal_entries', 'SELECT 1 FROM journal_entries WHERE workspace=? AND lesson_key=? AND published_document IS NOT NULL', (workspace, key)) else None


def open_quizzes(db, user, workspace, data, kids):
    key = data.get('recordId')
    if not exists(db, 'quizzes', "SELECT 1 FROM quizzes WHERE workspace=? AND id=? AND status NOT IN ('withdrawn','draft')", (workspace, key)): return None
    if user['role'] in FAMILY: return {} if any(exists(db, 'quiz_members', 'SELECT 1 FROM quiz_members WHERE workspace=? AND quiz_id=? AND student_id=?', (workspace, key, kid)) for kid in kids) else None
    return {}


def open_behaviour(db, user, workspace, data, kids):
    row = db.execute('SELECT student_id,status,visible FROM behaviour_notes WHERE workspace=? AND id=?', (workspace, data.get('recordId'))).fetchone() if has_table(db, 'behaviour_notes') else None
    if not row: return None
    if user['role'] in FAMILY: return {} if row[1] == 'published' and row[2] and row[0] in kids else None
    return {} if user['role'] == 'admin' else None


def open_finals(db, user, workspace, data, kids):
    # The report card of the period opens for a family that still has a published result of the child in it.
    return {} if user['role'] in FAMILY and any(exists(db, 'final_results', 'SELECT 1 FROM final_results WHERE workspace=? AND period_id=? AND student_id=?', (workspace, data.get('recordId'), kid)) for kid in kids) else None


def open_assessments(db, user, workspace, data, kids):
    row = db.execute('SELECT status FROM assessment_bookings WHERE workspace=? AND id=?', (workspace, data.get('recordId'))).fetchone() if has_table(db, 'assessment_bookings') else None
    return {} if row and (row[0] == 'published' and kids or user['role'] not in FAMILY) else None


def open_meetings(db, user, workspace, data, kids):
    row = db.execute('SELECT event_id,parent_id,student_id,host_id FROM meeting_bookings WHERE workspace=? AND id=?', (workspace, data.get('recordId'))).fetchone() if has_table(db, 'meeting_bookings') else None
    if not row: return None
    if user['role'] == 'admin' or row[3] == user['id'] or row[1] == user['id'] and row[2] in kids: return dict(eventId=row[0])
    # A guardian also opens a consultation that someone else of the family booked for their child.
    consultation = db.execute("SELECT json_extract(document,'$.kind') FROM meeting_events WHERE id=?", (row[0],)).fetchone()
    return dict(eventId=row[0]) if user['role'] == 'parent' and row[2] in kids and consultation and consultation[0] == 'consultation' else None


def open_class_meetings(db, user, workspace, data, kids):
    # The class teacher's panel re-checks the meeting server-side; a guardian opens it while a child of theirs is still invited.
    key = data.get('recordId')
    if not exists(db, 'class_meetings', 'SELECT 1 FROM class_meetings WHERE workspace=? AND id=?', (workspace, key)): return None
    if user['role'] in ('admin', 'teacher'): return {}
    return {} if user['role'] == 'parent' and any(exists(db, 'class_meeting_invitees', 'SELECT 1 FROM class_meeting_invitees WHERE meeting_id=? AND student_id=?', (key, kid)) for kid in kids) else None


def open_forms(db, user, workspace, data, kids):
    row = db.execute('SELECT user_id,form_id FROM school_form_answers WHERE id=?', (data.get('recordId'),)).fetchone() if has_table(db, 'school_form_answers') else None
    return dict(formId=row[1]) if row and (row[0] == user['id'] or user['role'] == 'admin') else None


def open_timetable(db, user, workspace, data, kids):
    return {} if kids or user['role'] not in FAMILY else None


OPENERS = dict(journal=open_journal, quizzes=open_quizzes, behaviour=open_behaviour, assessments=open_assessments, meetings=open_meetings, class_meetings=open_class_meetings,
               forms=open_forms, timetable=open_timetable, finals=open_finals, duty=lambda *args: {})
# Duty screens live in the teacher portal: the administrator and class-staff shells have no section to open.
NO_DUTY_SCREEN = ('admin', 'editor', 'staff')


def linkable(user, module):
    return module in OPENERS and not (module == 'duty' and user['role'] in NO_DUTY_SCREEN)


def navigation(db, user, workspace, data):
    """Where an entry leads for this account right now, or None. Only ids and a day: never the record's content."""
    if not linkable(user, data['module']): return None
    listed = [s['id'] for s in data.get('students') or []]
    kids = [id for id in listed if id in student_ids(user, workspace)] if user['role'] in FAMILY else listed
    # Whatever the module: an entry about children is shut once the family account has none of them left (a form of no child stays the owner's).
    if user['role'] in FAMILY and listed and not kids: return None
    found = OPENERS[data['module']](db, user, workspace, data, kids)
    if found is None: return None
    return dict(module=data['module'], workspace=workspace, recordId=data.get('recordId'), studentId=kids[0] if kids else None,
                day=data.get('day')) | {k: v for k, v in found.items() if k in ('formId', 'eventId')}


QUIET = r'^([01]\d|2[0-3]):[0-5]\d$'


class Preferences(Model):
    """Either the legacy all-or-nothing `enabled` or a list of muted categories; quiet hours only delay the push."""
    enabled: bool | None = None
    muted: list[Literal[tuple(CATEGORIES)]] | None = Field(default=None, max_length=len(CATEGORIES))
    quietStart: str | None = Field(default=None, pattern=QUIET)
    quietEnd: str | None = Field(default=None, pattern=QUIET)

    @model_validator(mode='after')
    def valid_choice(self):
        given = self.model_fields_set
        quiet = bool(given & {'quietStart', 'quietEnd'})
        if self.enabled is None and self.muted is None and not quiet: raise ValueError('Укажите настройки уведомлений.')
        if self.enabled is not None and self.muted is not None: raise ValueError('Укажите либо переключатель, либо список категорий.')
        if quiet:
            if not {'quietStart', 'quietEnd'} <= given or (self.quietStart is None) != (self.quietEnd is None):
                raise ValueError('Укажите начало и конец тихих часов.')
            if self.quietStart is not None and self.quietStart == self.quietEnd: raise ValueError('Начало и конец тихих часов совпадают.')
        return self


class ReadItems(Model):
    ids: list[int] = Field(default_factory=list, max_length=100)
    throughId: int | None = Field(default=None, ge=1)
    read: bool = True


class Notifications:
    def __init__(self, access):
        self.access = access

    def tables(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,permissions TEXT,workspace TEXT,revision INTEGER,created TEXT,action TEXT,read_at TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS notification_owner ON notifications(user_id,permissions,id)')
        db.execute('CREATE INDEX IF NOT EXISTS notification_user ON notifications(user_id,id)')
        db.execute('CREATE INDEX IF NOT EXISTS notification_age ON notifications(created)')
        cols = {r[1] for r in db.execute('PRAGMA table_info(notifications)')}
        if 'topic' not in cols:
            db.execute('ALTER TABLE notifications ADD COLUMN topic TEXT')
        if 'detail' not in cols:
            db.execute('ALTER TABLE notifications ADD COLUMN detail TEXT')
        # Each backfill runs only in the branch that adds its column; tables() runs on every inbox read.
        if 'category' not in cols:
            db.execute('ALTER TABLE notifications ADD COLUMN category TEXT')
            for (action,) in db.execute('SELECT DISTINCT action FROM notifications WHERE category IS NULL').fetchall():
                db.execute('UPDATE notifications SET category=? WHERE category IS NULL AND action=?', (category_of(action), action))
        if 'target' not in cols:
            db.execute('ALTER TABLE notifications ADD COLUMN target TEXT')
            db.execute("UPDATE notifications SET target=json_object('module','duty','title',detail) WHERE detail IS NOT NULL AND target IS NULL")
        db.execute('CREATE INDEX IF NOT EXISTS notification_topic ON notifications(user_id,topic)')
        db.execute('CREATE TABLE IF NOT EXISTS notification_preferences (user_id TEXT PRIMARY KEY,enabled INTEGER)')
        cols = {r[1] for r in db.execute('PRAGMA table_info(notification_preferences)')}
        if 'muted' not in cols:
            db.execute('ALTER TABLE notification_preferences ADD COLUMN muted TEXT')
            # The old switch muted everything: keep each opt-out as all categories.
            db.execute('UPDATE notification_preferences SET muted=? WHERE enabled=0', (encoded(list(CATEGORIES)),))
        for column in ['quiet_start', 'quiet_end']:
            if column not in cols: db.execute(f'ALTER TABLE notification_preferences ADD COLUMN {column} TEXT')
        from .web_push import tables
        tables(db)

    def capture(self, db, workspace, before, after, action='saved'):
        self.tables(db)
        self.access.tables(db)
        from .timetable_announcements import ensure_baseline
        ensure_baseline(db, workspace, before)
        users = [self.access.public(row) for row in db.execute('SELECT * FROM accounts WHERE active=1')]
        if not db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone(): users = [LOCAL]
        prefs = preference_map(db, [u['id'] for u in users])
        # Families learn about timetable changes only from an explicit announcement (timetable_announcements).
        relevant = [u for u in users if 'timetable' not in prefs[u['id']]['muted'] and u['role'] not in FAMILY and
                    (u['role'] == 'admin' or u['role'] == 'editor' and workspace in u['workspaces'] or
                     u['role'] not in ['admin', 'editor'] and any(g['workspace'] == workspace for g in u['grants']))]
        personal = [u for u in relevant if u['role'] not in ['admin', 'editor']]
        views = {g['view'] for u in personal for g in u['grants'] if g['workspace'] == workspace}
        old = personal_index(before, views) if views else {}
        new = personal_index(after, views) if views else {}
        calendar_changed = (before.cycles != after.cycles or
                            (before.settings.yearStart, before.settings.yearEnd) != (after.settings.yearStart, after.settings.yearEnd) or
                            before.scheduleDays != after.scheduleDays)
        recipients = []
        for user in relevant:
            changed = user['role'] in ['admin', 'editor'] or calendar_changed or any(
                old.get((g['view'], id), []) != new.get((g['view'], id), [])
                for g in user['grants'] if g['workspace'] == workspace for id in g['entityIds'])
            if not changed: continue
            recipients.append(user)
        self.deliver(db,recipients,workspace,after.revision,action)
        from .duty_schedule import notify_changes
        notify_changes(self, db, workspace, before, after)

    def granted_users(self, db, workspace, view, entity_ids, roles):
        """Select indexed candidates, then enforce effective grants without caching them.

        Stored parent grants may have expired or not started yet. Access.public
        remains authoritative; the SQL predicate only narrows the candidate set.
        """
        ids=set(entity_ids)
        if not ids:return []
        from .account_grants import candidates
        rows=candidates(db,workspace,view,ids,roles)
        result=[]
        for row in rows:
            user=self.access.public(row)
            if any(g['workspace']==workspace and g['view']==view and ids.intersection(g['entityIds']) for g in user['grants']):
                result.append(user)
        return result

    def redact(self, db, user_ids, first, last):
        """Blank title, children and module of the unread and read entries whose topic lies in [first, last]; a prefix p is p..p+U+FFFF.

        Without its module an entry leads nowhere: opening it cannot answer for a record the account lost or that was corrected.
        """
        db.execute("UPDATE notifications SET target=json_remove(target,'$.title','$.students','$.module') WHERE user_id IN (SELECT value FROM json_each(?)) AND topic>=? AND topic<=? AND target IS NOT NULL",
                   (encoded(list(user_ids)), first, last))

    def retract(self, db, user_ids, topic):
        """The event this topic told about no longer holds: older entries lose their text, the unread ones disappear."""
        self.redact(db, user_ids, topic, topic)
        db.execute('DELETE FROM notifications WHERE user_id IN (SELECT value FROM json_each(?)) AND topic=? AND read_at IS NULL', (encoded(list(user_ids)), topic))

    def deliver(self, db, users, workspace, revision, action, topic=None, target=None, supersede=False):
        """topic: at most one unread entry per user and topic; a newer event replaces it.

        target: where the entry leads and its short title, a dict or a function of the recipient (never private text).
        supersede: the older entries of the topic, read ones too, lose their title and children: only the newest stays readable.
        An account that muted the category gets no new entry, but its older entries are still retracted.
        """
        self.tables(db)
        category = category_of(action)
        prefs = preference_map(db, [u['id'] for u in users])
        created = now().isoformat()
        from .web_push import enqueue
        for user in users:
            pref = prefs[user['id']]
            muted = category != SYSTEM and category in pref['muted']
            if topic and supersede: self.retract(db, [user['id']], topic)
            elif topic and not muted: db.execute('DELETE FROM notifications WHERE user_id=? AND topic=? AND read_at IS NULL', (user['id'], topic))
            if muted: continue
            safe = safe_target(target(user) if callable(target) else target)
            notice = db.execute('INSERT INTO notifications (user_id,permissions,workspace,revision,created,action,topic,category,target) VALUES (?,?,?,?,?,?,?,?,?)',
                       (user['id'], permission_key(user), workspace, revision, created, action, topic, category, encoded(safe) if safe else None)).lastrowid
            enqueue(db, user, notice, quiet_until=quiet_until(pref))
            db.execute('DELETE FROM notifications WHERE user_id=? AND id NOT IN (SELECT id FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 500)', (user['id'], user['id']))
        db.execute('DELETE FROM notifications WHERE created<?', ((now()-timedelta(days=90)).isoformat(),))

    def register(self, app):
        def owner(request):
            user = request.state.user or LOCAL
            return user['id'], permission_key(user)

        def settings(muted, start, end):
            return dict(categories=[dict(id=id, label=value['label'], enabled=id not in muted) for id, value in CATEGORIES.items()],
                        quietStart=start, quietEnd=end, zone=str(ZONE))

        def entry(row, user, granted):
            id, workspace, revision, created, action, read_at, detail, target, category = row
            item = dict(id=id, workspace=workspace, revision=revision, created=created, action=action, read=bool(read_at), category=category or category_of(action))
            data = json.loads(target) if target else None
            if not data:
                if detail: item['title'] = detail
                return item
            students = data.get('students') or []
            if students and user['role'] in FAMILY:
                # The account's current grants decide, not the ones at write time.
                if workspace not in granted: granted[workspace] = student_ids(user, workspace)
                students = [s for s in students if s['id'] in granted[workspace]]
                if not students: return item | dict(restricted=True)
            if data.get('title'): item['title'] = data['title']
            if students: item['students'] = students
            if linkable(user, data.get('module')) and data.get('title'): item['link'] = True
            return item

        @app.get('/api/notifications/inbox')
        def inbox(request: Request, beforeId: int | None = None, unreadOnly: bool = False):
            user_id, _ = owner(request)
            user = request.state.user or LOCAL
            with self.access.lock, self.access.connect() as db:
                self.tables(db)
                cutoff = (now()-timedelta(days=90)).isoformat()
                clause = 'user_id=? AND created>=?'
                params = [user_id, cutoff]
                count, latest = db.execute(f'SELECT count(CASE WHEN read_at IS NULL THEN 1 END),coalesce(max(id),0) FROM notifications WHERE {clause}', params).fetchone()
                if beforeId is not None: clause += ' AND id<?'; params.append(beforeId)
                if unreadOnly: clause += ' AND read_at IS NULL'
                rows = db.execute(f'SELECT id,workspace,revision,created,action,read_at,detail,target,category FROM notifications WHERE {clause} ORDER BY id DESC LIMIT 51', params).fetchall()
                muted = preference_map(db, [user_id])[user_id]['muted']
            granted = {}
            items = [entry(r, user, granted) for r in rows[:50]]
            return dict(items=items, unread=count, latestId=latest, nextBeforeId=items[-1]['id'] if len(rows)>50 else None, enabled=len(muted) < len(CATEGORIES))

        @app.get('/api/notifications/preferences')
        def read_preferences(request: Request):
            user_id, _ = owner(request)
            with self.access.lock, self.access.connect() as db:
                self.tables(db)
                pref = preference_map(db, [user_id])[user_id]
            return settings(pref['muted'], pref['quietStart'], pref['quietEnd'])

        @app.put('/api/notifications/preferences')
        def preferences(body: Preferences, request: Request):
            user_id, _ = owner(request)
            with self.access.lock, self.access.connect() as db:
                self.tables(db)
                if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
                old = preference_map(db, [user_id])[user_id]
                muted = set(CATEGORIES) if body.enabled is False else set() if body.enabled else set(body.muted) if body.muted is not None else old['muted']
                start, end = (body.quietStart, body.quietEnd) if 'quietStart' in body.model_fields_set else (old['quietStart'], old['quietEnd'])
                db.execute('INSERT INTO notification_preferences (user_id,enabled,muted,quiet_start,quiet_end) VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET '
                           'enabled=excluded.enabled,muted=excluded.muted,quiet_start=excluded.quiet_start,quiet_end=excluded.quiet_end',
                           (user_id, int(len(muted) < len(CATEGORIES)), encoded([id for id in CATEGORIES if id in muted]), start, end))
            return settings(muted, start, end)

        @app.post('/api/notifications/read')
        def mark(body: ReadItems, request: Request):
            if bool(body.ids) == (body.throughId is not None): raise HTTPException(422, 'Укажите записи или границу списка.')
            user_id, _ = owner(request)
            selection = 'id<=?' if body.throughId is not None else 'id IN (' + ','.join('?' for _ in body.ids) + ')'
            values = [body.throughId] if body.throughId is not None else body.ids
            with self.access.lock, self.access.connect() as db:
                self.tables(db)
                db.execute(f'UPDATE notifications SET read_at=? WHERE user_id=? AND {selection}',
                           [now().isoformat() if body.read else None, user_id, *values])
            return dict(status='updated')

        from .timetable_announcements import register as announcements
        announcements(app, self)

        @app.post('/api/notifications/{id}/open')
        def open_notice(id: int, request: Request):
            """Re-check access to the record behind an entry and answer only where it lives; never its content."""
            user = request.state.user or LOCAL
            result = None
            with self.access.lock, self.access.connect() as db:
                self.tables(db)
                if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT workspace,target FROM notifications WHERE id=? AND user_id=?', (id, user['id'])).fetchone()
                if row:
                    # Read even when refused, so a dead entry cannot stay unread for ever.
                    db.execute('UPDATE notifications SET read_at=coalesce(read_at,?) WHERE id=? AND user_id=?', (now().isoformat(), id, user['id']))
                    data = json.loads(row[1]) if row[1] else {}
                    if data.get('module') in OPENERS: result = navigation(db, user, row[0], data)
            if result is None: raise HTTPException(404, 'Запись больше недоступна.')
            return result
