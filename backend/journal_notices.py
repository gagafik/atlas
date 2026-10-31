"""What a family sees about a published lesson, prepared per recipient at publication.

A title names the subject, the date and this pupil's own marks and homework. It never holds the
private lesson comment, topic text, homework text, file names or another pupil's name.
"""
from datetime import date

from .grade_feed import graded
from .notifications import clip, student_ids

ATTENDANCE = {'present': 'присутствие', 'absent': 'отсутствие', 'late': 'опоздание', 'excused': 'отсутствие по уважительной причине', 'left': 'ранний уход'}
HOMEWORK = {'new': 'Новое ДЗ', 'changed': 'Изменено ДЗ', 'removed': 'Снято ДЗ'}


def mark(grade, assessment):
    if assessment['kind'] == 'label': return grade['label']
    return f"{grade['score']:g} из {assessment['maxScore']:g}".replace('.', ',')


def short(text, size=80):
    return clip(text, size)


def day_label(iso):
    return date.fromisoformat(iso).strftime('%d.%m')


def projection(document, student):
    from .journal import pupil_projection
    return pupil_projection(document, student) if document else None


def grade_parts(old, new):
    """One phrase per assessment whose mark or comment this pupil's family has not seen yet."""
    before, before_assessments = graded(old)
    after, after_assessments = graded(new)
    parts = []
    for id in sorted(set(before) | set(after)):
        a, b = before.get(id), after.get(id)
        if a and not b:
            parts.append(f"{before_assessments[id]['name']}: оценка снята")
            continue
        assessment = after_assessments[id]
        seen = lambda g, x: (g['score'], g['label'], g['comment'], x['maxScore'], x['name'])
        if a and seen(a, before_assessments[id]) == seen(b, assessment): continue
        comment = f", {short(b['comment'])}" if b['comment'] else ''
        parts.append(f"{assessment['name']} теперь {mark(b, assessment)}{comment}" if a else f"{assessment['name']} {mark(b, assessment)}{comment}")
    return parts


def attendance_mark(projection):
    return (projection['record']['attendance'], projection['record']['lateMinutes'], projection['record']['leftAt']) if projection else ('unmarked', 0, None)


def beside_other_news(old, new):
    """Is a changed attendance mark worth its own entry next to a grade or homework entry? Not the usual first 'present'."""
    first, last = attendance_mark(old), attendance_mark(new)
    return first != last and not (first[0] == 'unmarked' and last[0] == 'present')


def plain(projection):
    """The same lesson with nothing a family hears about besides the topic, the homework (it has its own entry) and the mark."""
    return projection | dict(content=projection['content'] | dict(assessments=[], homeworkFiles=[], materials=[], curriculum=None),
                             record=projection['record'] | dict(comment='', grades=[]))


def routine(old, new):
    """A plain first 'present' (or no mark yet) with nothing else changed for this pupil: a lesson held as usual, not news for the family.
    A first publication is measured against the plain lesson: a planned work, materials or a personal comment are news."""
    first, last = attendance_mark(old), attendance_mark(new)
    if new is None or first[0] != 'unmarked' or last[0] not in ('unmarked', 'present'): return False
    old = old or plain(new)
    without = lambda p: {k: v for k, v in p['record'].items() if k != 'attendance'}
    return old | dict(record=None) == new | dict(record=None) and without(old) == without(new)


def attendance_title(old, new, subject, day):
    """Text for a changed attendance mark, else None."""
    first, last = attendance_mark(old), attendance_mark(new)
    if first == last or last[0] == 'unmarked': return None
    return f"{subject}, {day_label(day)}: отмечено {ATTENDANCE[last[0]]}" + (f" ({last[1]} мин)" if last[0] == 'late' and last[1] else f" (в {last[2]})" if last[0] == 'left' else '')


def homework_state(document):
    content = document['content'] if document else {}
    return (content.get('homework') or '').strip(), content.get('dueDate')


def deliver_journal(notifications, db, workspace, version, key, before, after):
    document = after or before
    lesson = document['lesson']
    subject, day = lesson['subject'], lesson['date']
    names = {s['id']: s['name'] for d in (before, after) if d for s in d['lesson']['students']}
    ids = {r['studentId'] for d in (before, after) if d for r in d['content']['records']}
    old = {id: projection(before, id) for id in ids}
    new = {id: projection(after, id) for id in ids}
    changed = {id for id in ids if old[id] != new[id]}
    if not changed: return
    users = notifications.granted_users(db, workspace, 'student', changed, ['student', 'parent'])
    base = f'journal:{workspace}:{key}:'

    def pupils(ids):
        return [dict(id=id, name=names.get(id, '')) for id in ids]

    if after is None:
        # Withdrawn from pupils and parents: nothing this lesson told them stays readable.
        for user in users:
            kids = sorted(student_ids(user, workspace) & changed)
            notifications.redact(db, [user['id']], base, base + '￿')
            notifications.deliver(db, [user], workspace, version, 'journal.withdrawn', topic=base + 'withdrawn',
                                  target=dict(recordId=key, day=day, students=pupils(kids), title=f"{subject}, {day_label(day)}: запись урока скрыта"))
        return
    # Published again after a withdrawal: the notice that the lesson was hidden no longer holds.
    if before is None: notifications.retract(db, [user['id'] for user in users], base + 'withdrawn')
    parts = {id: grade_parts(old[id], new[id]) for id in changed}
    before_hw, after_hw = homework_state(before), homework_state(after)
    kind = 'new' if after_hw[0] and not before_hw[0] else 'changed' if after_hw[0] and after_hw != before_hw else 'removed' if before_hw[0] and not after_hw[0] else None
    homework_ids = {id for id in changed if (old if kind == 'removed' else new)[id]} if kind else set()
    due = after_hw[1]
    for user in users:
        kids = sorted(student_ids(user, workspace) & changed)
        covered = set()
        for kid in kids:
            if not parts[kid]: continue
            covered.add(kid)
            notifications.deliver(db, [user], workspace, version, 'journal.grade', topic=f'{base}{kid}:grade', supersede=True,
                                  target=dict(module='journal', recordId=key, day=day, students=pupils([kid]), title=f"{subject}: " + '; '.join(parts[kid])))
        family = [kid for kid in kids if kid in homework_ids]
        if family:
            covered.update(family)
            title = f"{HOMEWORK[kind]}: {subject}" + (f", до {day_label(due)}" if due and kind != 'removed' else '')
            notifications.deliver(db, [user], workspace, version, 'journal.homework', topic=base + 'homework', supersede=True,
                                  target=dict(module='journal', recordId=key, day=day, students=pupils(family), title=title))
        for kid in kids:
            told = attendance_title(old[kid], new[kid], subject, day)
            # A mark the teacher cleared is no longer true: what was told about it goes. A replaced mark is superseded below.
            if attendance_mark(old[kid])[0] != 'unmarked' and attendance_mark(new[kid])[0] == 'unmarked':
                notifications.retract(db, [user['id']], f'{base}{kid}:attendance')
            if kid in covered and not beside_other_news(old[kid], new[kid]): continue
            # A lesson recorded as held, with everybody present, is not news: no entry for it, also when it is the only change.
            if routine(old[kid], new[kid]): continue
            # An absence, a lateness or a correction is told and replaces the older mark, also beside a grade or homework entry.
            # It has a topic of its own: a later plain update of the lesson must not push the unread absence out of the inbox.
            if told: notifications.deliver(db, [user], workspace, version, 'journal.updated', topic=f'{base}{kid}:attendance', supersede=True,
                                           target=dict(module='journal', recordId=key, day=day, students=pupils([kid]), title=told))
            elif kid not in covered:
                notifications.deliver(db, [user], workspace, version, 'journal.updated', topic=f'{base}{kid}:lesson', supersede=True,
                                      target=dict(module='journal', recordId=key, day=day, students=pupils([kid]), title=f"{subject}, {day_label(day)}: обновлена запись урока"))
