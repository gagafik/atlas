"""Swap requests for duties. A teacher asks, administration decides; approval repeats the conflict checks of any duty edit."""
from uuid import uuid4
import json
import sqlite3

from fastapi import HTTPException, Request
from pydantic import Field
from .duty_schedule import REQUEST, local_now, stamp, starts_at, duty_text, labeller
from .editing import inspect_edit
from .models import Model, Commitment
from .notifications import encoded


class SwapCreate(Model):
    requestId: str = Field(pattern=REQUEST)
    commitmentId: str = Field(min_length=1, max_length=100)
    fromTeacherId: str = Field(min_length=1, max_length=100)
    toTeacherId: str | None = Field(default=None, max_length=100)
    reason: str = Field(default='', max_length=500)


class SwapDecision(Model):
    requestId: str = Field(pattern=REQUEST)
    revision: int
    version: int = Field(ge=1)
    approve: bool
    toTeacherId: str | None = Field(default=None, max_length=100)
    reason: str = Field(default='', max_length=500)


class SwapCancel(Model):
    version: int = Field(ge=1)


def public(row):
    """Swap as shown to its requester and to administration: the duty facts, never its note."""
    doc = json.loads(row['document'])
    return dict(id=row['id'], version=row['version'], status=row['status'], commitmentId=row['commitment_id'], date=row['on_date'],
                name=doc['dutyName'], start=doc['start'], end=doc['end'], place=doc['place'],
                fromTeacher=dict(id=row['from_teacher'], name=doc['fromName']),
                toTeacher=dict(id=row['to_teacher'], name=doc['toName']) if row['to_teacher'] else None,
                requester=doc['requesterName'], reason=doc['reason'], decision=doc.get('decision', ''), decidedBy=doc.get('decidedBy', ''),
                created=row['created'], updated=row['updated'])


def register(service, app, Workspace):
    access, notifications = service.access, service.notifications

    def find(db, workspace, id):
        row = db.execute('SELECT * FROM duty_swaps WHERE workspace=? AND id=?', (workspace, id)).fetchone()
        if not row: raise HTTPException(404, 'Заявка не найдена.')
        return dict(row)

    def duty_of(state, id):
        return next((c for c in state.commitments if c.id == id and c.kind == 'duty'), None)

    def check_replacement(state, c, to, origin):
        if to not in {t.id for t in state.teachers}: raise HTTPException(422, 'Заменяющий сотрудник не найден.')
        if to == origin or to in c.teacherIds: raise HTTPException(422, 'Заменяющий уже назначен на это дежурство.')
        if c.eligibleTeacherIds and to not in c.eligibleTeacherIds: raise HTTPException(422, 'Заменяющий не входит в список кандидатов этого дежурства.')

    def accounts(db, sql, params=()):
        return [service.access.public(r) for r in db.execute(sql, params).fetchall()]

    def tell_managers(db, workspace, revision, detail):
        users = [u for u in accounts(db, "SELECT * FROM accounts WHERE active=1 AND role IN ('admin','editor')") if u['role'] == 'admin' or workspace in u['workspaces']]
        notifications.deliver(db, users, workspace, revision, 'duty.swap_requested', topic=f'duty-swaps:{workspace}', detail=detail)

    def tell_requester(db, workspace, swap, revision, detail):
        notifications.deliver(db, accounts(db, 'SELECT * FROM accounts WHERE id=? AND active=1', (swap['requester_id'],)), workspace, revision, 'duty.swap_decided', detail=detail)

    @app.post('/api/duties/swaps')
    def create(body: SwapCreate, request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace, True) as (db, user, snap):
            own = service.own_teachers(user, snap, workspace)
            if not own: raise HTTPException(403, 'Заявки на замену подают учителя, связанные с расписанием.')
            key, previous = service.replay(db, user, body, 'swap')
            if previous: return previous
            state = snap.state
            c = duty_of(state, body.commitmentId)
            if not c or body.fromTeacherId not in own or body.fromTeacherId not in c.teacherIds: raise HTTPException(404, 'Дежурство не найдено.')
            current = local_now()
            if starts_at(c) <= current: raise HTTPException(422, 'Дежурство уже началось.')
            if c.locked: raise HTTPException(422, 'Дежурство закреплено. Обратитесь к администрации расписания.')
            if db.execute("SELECT 1 FROM duty_swaps WHERE workspace=? AND commitment_id=? AND from_teacher=? AND status='pending'", (workspace, c.id, body.fromTeacherId)).fetchone():
                raise HTTPException(409, 'Заявка уже подана.')
            if body.toTeacherId: check_replacement(state, c, body.toTeacherId, body.fromTeacherId)
            names, label = {t.id: t.name for t in state.teachers}, labeller(state)
            doc = dict(dutyName=c.name, start=c.startTime, end=c.endTime, place=label(c), requesterName=user['name'], fromName=names[body.fromTeacherId],
                       toName=names[body.toTeacherId] if body.toTeacherId else '', reason=body.reason)
            id, created = uuid4().hex, stamp()
            db.execute('INSERT INTO duty_swaps(id,workspace,commitment_id,on_date,from_teacher,to_teacher,requester_id,status,version,created,updated,document) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (id, workspace, c.id, c.onDate.isoformat(), body.fromTeacherId, body.toTeacherId, user['id'], 'pending', 1, created, created, encoded(doc)))
            service.history(db, workspace, user, 'swap', id, 'requested', body.reason, dict(commitmentId=c.id, fromTeacherId=body.fromTeacherId, toTeacherId=body.toTeacherId))
            tell_managers(db, workspace, state.revision, duty_text(state, c, current, label) + ' · ' + user['name'])
            return service.remember(db, user, body, key, public(find(db, workspace, id)))

    @app.get('/api/duties/swaps')
    def listing(request: Request, workspace: Workspace = 'school', status: str | None = None):
        if status is not None and status not in ['pending', 'approved', 'rejected', 'cancelled']: raise HTTPException(422, 'Неизвестный статус заявки.')
        with service.context(request, workspace) as (db, user, snap):
            manager = service.manager(user, workspace)
            if not manager and not service.own_teachers(user, snap, workspace): raise HTTPException(403, 'Заявки на замену доступны учителям и администрации расписания.')
            clause, params = 'workspace=?', [workspace]
            if not manager: clause += ' AND requester_id=?'; params.append(user['id'])
            if status: clause += ' AND status=?'; params.append(status)
            return [public(r) for r in db.execute(f'SELECT * FROM duty_swaps WHERE {clause} ORDER BY seq DESC LIMIT 100', params)]

    @app.post('/api/duties/swaps/{id}/cancel')
    def cancel(id: str, body: SwapCancel, request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace, True) as (db, user, snap):
            row = find(db, workspace, id)
            if row['requester_id'] != user['id']: raise HTTPException(404, 'Заявка не найдена.')
            if row['version'] != body.version or row['status'] != 'pending': raise HTTPException(409, 'Заявка уже рассмотрена или изменилась. Обновите список.')
            db.execute("UPDATE duty_swaps SET status='cancelled',version=version+1,updated=? WHERE workspace=? AND id=?", (stamp(), workspace, id))
            service.history(db, workspace, user, 'swap', id, 'cancelled', '', dict(version=body.version))
            return public(find(db, workspace, id))

    def reject(db, user, snap, workspace, id, body, key):
        row = find(db, workspace, id)
        if row['version'] != body.version or row['status'] != 'pending': raise HTTPException(409, 'Заявка уже рассмотрена или изменилась. Обновите список.')
        doc = json.loads(row['document']) | dict(decision=body.reason, decidedBy=user['name'])
        db.execute("UPDATE duty_swaps SET status='rejected',version=?,updated=?,document=? WHERE workspace=? AND id=?", (row['version'] + 1, stamp(), encoded(doc), workspace, id))
        service.history(db, workspace, user, 'swap', id, 'rejected', body.reason, dict(version=body.version))
        access.record(db, 'duties.swap_rejected', workspace, snap.state.revision, detail=doc['dutyName'])
        tell_requester(db, workspace, row, snap.state.revision, 'Отклонено: ' + (body.reason or doc['dutyName']))
        return service.remember(db, user, body, key, dict(swap=public(find(db, workspace, id))))

    @app.post('/api/duties/swaps/{id}/decide')
    def decide(id: str, body: SwapDecision, request: Request, workspace: Workspace = 'school'):
        if not body.approve:
            with service.context(request, workspace, True) as (db, user, snap):
                service.require_manager(user, workspace)
                key, previous = service.replay(db, user, body, 'decide:' + id)
                if previous: return previous | dict(state=snap.state)
                return reject(db, user, snap, workspace, id, body, key) | dict(state=snap.state)
        with service.context(request, workspace) as (db, user, snap):
            service.require_manager(user, workspace)
            key, previous = service.replay(db, user, body, 'decide:' + id)
            if previous: return previous | dict(state=snap.state)
            row, state = find(db, workspace, id), snap.state
            if row['version'] != body.version or row['status'] != 'pending': raise HTTPException(409, 'Заявка уже рассмотрена или изменилась. Обновите список.')
            if state.revision != body.revision: raise HTTPException(409, 'Данные изменились. Обновите страницу и повторите действие.')
            c = duty_of(state, row['commitment_id'])
            if not c or row['from_teacher'] not in c.teacherIds: raise HTTPException(409, 'Назначение уже изменилось. Отклоните заявку или обновите список.')
            if c.locked: raise HTTPException(409, 'Дежурство закреплено. Снимите закрепление в разделе «Бронирования и дежурства».')
            if starts_at(c) <= local_now(): raise HTTPException(409, 'Дежурство уже началось.')
            to = body.toTeacherId or row['to_teacher']
            if not to: raise HTTPException(422, 'Выберите заменяющего.')
            check_replacement(state, c, to, row['from_teacher'])
        from .calendar_ops import DayContext, resolve_day
        from .commitments import duty_conflicts
        names = {t.id: t.name for t in state.teachers}
        replacement = Commitment.model_validate(c.model_copy(update=dict(teacherIds=sorted((set(c.teacherIds) - {row['from_teacher']}) | {to}))).model_dump())
        ctx = DayContext(state)
        rows = [r for r in resolve_day(state, c.onDate, ctx)[0] if r['status'] != 'cancelled' and r.get('commitmentId') != c.id]
        reasons = duty_conflicts(state, ctx, rows, replacement, to)
        if reasons: raise HTTPException(409, f'{names[to]} не может заменить: {", ".join(reasons)}.')
        candidate = state.model_copy(update=dict(commitments=[replacement if x.id == c.id else x for x in state.commitments]))
        found = inspect_edit(state, candidate)
        if not found['canApply']: raise HTTPException(409, 'Замена создаёт нарушения в расписании: ' + '; '.join(i['message'] for i in found['newIssues'][:3]))
        outcome = {}

        def accept(db, saved):
            service.recheck(db, user)
            now_row = db.execute('SELECT version,status,document FROM duty_swaps WHERE workspace=? AND id=?', (workspace, id)).fetchone()
            if not now_row or now_row[0] != row['version'] or now_row[1] != 'pending': raise HTTPException(409, 'Заявка уже рассмотрена или изменилась. Обновите список.')
            doc = json.loads(now_row[2]) | dict(toName=names[to], decision=body.reason, decidedBy=user['name'])
            changed = row | dict(status='approved', to_teacher=to, version=row['version'] + 1, updated=stamp(), document=encoded(doc))
            db.execute("UPDATE duty_swaps SET status='approved',to_teacher=?,version=?,updated=?,document=? WHERE workspace=? AND id=?", (to, changed['version'], changed['updated'], changed['document'], workspace, id))
            service.history(db, workspace, user, 'swap', id, 'approved', body.reason, dict(fromTeacherId=row['from_teacher'], toTeacherId=to))
            access.record(db, 'duties.swap_approved', workspace, saved.revision, detail=doc['dutyName'])
            outcome.update(swap=public(changed))
            try: service.remember(db, user, body, key, dict(swap=outcome['swap']))
            except sqlite3.IntegrityError: raise HTTPException(409, 'Этот идентификатор запроса уже использован.')
            tell_requester(db, workspace, row, saved.revision, f'Одобрено: замена на {names[to]}. ' + duty_text(saved, replacement, local_now()))

        saved = service.save(workspace, candidate, after_save=accept)
        return outcome | dict(state=saved)
