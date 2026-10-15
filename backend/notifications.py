"""Transactional, permission-scoped inbox. No pupil data in notification text."""
from collections import defaultdict
from datetime import timedelta
import hashlib
import json

from fastapi import HTTPException, Request
from pydantic import Field
from .models import Model
from .access import LOCAL, now
from .exporting import ExportRequest, schedule_rows
from .resources import teacher_ids, room_ids


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def permission_key(user):
    # Password changes keep the inbox; changes of role/grants revoke old entries.
    return hashlib.sha256(encoded({k: user[k] for k in ['role', 'workspaces', 'grants']}).encode()).hexdigest()


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


class Preferences(Model):
    enabled: bool = True


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
        db.execute('CREATE INDEX IF NOT EXISTS notification_age ON notifications(created)')
        db.execute('CREATE TABLE IF NOT EXISTS notification_preferences (user_id TEXT PRIMARY KEY,enabled INTEGER)')
        from .web_push import tables
        tables(db)

    def capture(self, db, workspace, before, after, action='saved'):
        self.tables(db)
        self.access.tables(db)
        users = [self.access.public(row) for row in db.execute('SELECT * FROM accounts WHERE active=1')]
        if not db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone(): users = [LOCAL]
        muted = {r[0] for r in db.execute('SELECT user_id FROM notification_preferences WHERE enabled=0')}
        relevant = [u for u in users if u['id'] not in muted and
                    (u['role'] == 'admin' or u['role'] == 'editor' and workspace in u['workspaces'] or
                     u['role'] not in ['admin', 'editor'] and any(g['workspace'] == workspace for g in u['grants']))]
        personal = [u for u in relevant if u['role'] not in ['admin', 'editor']]
        views = {g['view'] for u in personal for g in u['grants'] if g['workspace'] == workspace}
        old = personal_index(before, views) if views else {}
        new = personal_index(after, views) if views else {}
        calendar_changed = (before.cycles != after.cycles or
                            (before.settings.yearStart, before.settings.yearEnd) != (after.settings.yearStart, after.settings.yearEnd))
        recipients = []
        for user in relevant:
            changed = user['role'] in ['admin', 'editor'] or calendar_changed or any(
                old.get((g['view'], id), []) != new.get((g['view'], id), [])
                for g in user['grants'] if g['workspace'] == workspace for id in g['entityIds'])
            if not changed: continue
            recipients.append(user)
        self.deliver(db,recipients,workspace,after.revision,action)

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

    def deliver(self, db, users, workspace, revision, action):
        self.tables(db)
        muted = {r[0] for r in db.execute('SELECT user_id FROM notification_preferences WHERE enabled=0')}
        created = now().isoformat()
        for user in users:
            if user['id'] in muted: continue
            notice=db.execute('INSERT INTO notifications (user_id,permissions,workspace,revision,created,action) VALUES (?,?,?,?,?,?)',
                       (user['id'], permission_key(user), workspace, revision, created, action)).lastrowid
            from .web_push import enqueue
            enqueue(db,user,notice)
            db.execute('DELETE FROM notifications WHERE user_id=? AND id NOT IN (SELECT id FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 500)', (user['id'], user['id']))
        db.execute('DELETE FROM notifications WHERE created<?', ((now()-timedelta(days=90)).isoformat(),))

    def register(self, app):
        def owner(request):
            user = request.state.user or LOCAL
            return user['id'], permission_key(user)

        @app.get('/api/notifications/inbox')
        def inbox(request: Request, beforeId: int | None = None, unreadOnly: bool = False):
            user_id, permissions = owner(request)
            with self.access.lock, self.access.connect() as db:
                self.tables(db)
                cutoff = (now()-timedelta(days=90)).isoformat()
                clause = 'user_id=? AND permissions=? AND created>=?'
                params = [user_id, permissions, cutoff]
                count, latest = db.execute(f'SELECT count(CASE WHEN read_at IS NULL THEN 1 END),coalesce(max(id),0) FROM notifications WHERE {clause}', params).fetchone()
                if beforeId is not None: clause += ' AND id<?'; params.append(beforeId)
                if unreadOnly: clause += ' AND read_at IS NULL'
                rows = db.execute(f'SELECT id,workspace,revision,created,action,read_at FROM notifications WHERE {clause} ORDER BY id DESC LIMIT 51', params).fetchall()
                preference = db.execute('SELECT enabled FROM notification_preferences WHERE user_id=?', (user_id,)).fetchone()
            items = [dict(id=r[0],workspace=r[1],revision=r[2],created=r[3],action=r[4],read=bool(r[5])) for r in rows[:50]]
            return dict(items=items, unread=count, latestId=latest, nextBeforeId=items[-1]['id'] if len(rows)>50 else None, enabled=not preference or bool(preference[0]))

        @app.put('/api/notifications/preferences')
        def preferences(body: Preferences, request: Request):
            user_id, _ = owner(request)
            with self.access.lock, self.access.connect() as db:
                self.tables(db)
                db.execute('INSERT INTO notification_preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled', (user_id, int(body.enabled)))
            return body

        @app.post('/api/notifications/read')
        def mark(body: ReadItems, request: Request):
            if bool(body.ids) == (body.throughId is not None): raise HTTPException(422, 'Укажите записи или границу списка.')
            user_id, permissions = owner(request)
            selection = 'id<=?' if body.throughId is not None else 'id IN (' + ','.join('?' for _ in body.ids) + ')'
            values = [body.throughId] if body.throughId is not None else body.ids
            with self.access.lock, self.access.connect() as db:
                self.tables(db)
                db.execute(f'UPDATE notifications SET read_at=? WHERE user_id=? AND permissions=? AND {selection}',
                           [now().isoformat() if body.read else None, user_id, permissions, *values])
            return dict(status='updated')
