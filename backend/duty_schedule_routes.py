"""HTTP routes of the duty roster; every request rechecks the account, role, workspace and revision."""
from datetime import date, timedelta
from uuid import uuid4
import json
import sqlite3

from fastapi import HTTPException, Request
from .duty_schedule import (StoredTemplate, TemplateSave, TemplateArchive, GeneratePreview, GenerateApply, SettingsSave, local_now, stamp, preview_cutoff, settings,
                            labeller, starts_at, closure_on, plan_generation, roster, fairness, reason_of)
from .editing import inspect_edit
from .models import Commitment
from .notifications import encoded


def register(service, app, Workspace):
    access = service.access

    def check_template(state, t):
        """References to the timetable and the times the template resolves to; 422 with a reason otherwise."""
        if t.weekday >= len(state.settings.days): raise HTTPException(422, 'День недели вне учебной недели школы.')
        profile = next((p for p in state.profiles if p.id == t.profileId), None)
        if not profile: raise HTTPException(422, 'Профиль звонков не найден.')
        teachers = {x.id for x in state.teachers}
        if set(t.roomIds) - {r.id for r in state.rooms}: raise HTTPException(422, 'Выбран отсутствующий кабинет.')
        if set(t.eligibleTeacherIds + t.teacherIds) - teachers: raise HTTPException(422, 'Выбран отсутствующий сотрудник.')
        if t.buildingId and t.buildingId not in {b.id for b in state.campus.buildings}: raise HTTPException(422, 'Выбрано отсутствующее здание.')
        if set(t.cycleIds) - {p.id for p in state.cycles.patterns}: raise HTTPException(422, 'Выбрана отсутствующая неделя расписания.')
        if t.afterPeriod is not None:
            bells = profile.for_day(t.weekday)
            if t.afterPeriod + 1 >= len(bells) or bells[t.afterPeriod].end >= bells[t.afterPeriod + 1].start: raise HTTPException(422, 'После этого урока нет перемены.')
            begin, finish = bells[t.afterPeriod].end, bells[t.afterPeriod + 1].start
        else: begin, finish = t.startTime, t.endTime
        # The same place, profile and candidate rules as for any saved duty.
        try:
            Commitment(id='probe', name=t.name, kind='duty', onDate=state.settings.yearStart, startTime=begin, endTime=finish, roomIds=t.roomIds, teacherIds=t.teacherIds,
                       profileId=t.profileId, location=t.location, buildingId=t.buildingId, requiredTeachers=t.requiredTeachers, eligibleTeacherIds=t.eligibleTeacherIds, note=t.note)
        except ValueError as exc: raise HTTPException(422, reason_of(exc))

    def template_rows(db, workspace, ids=None, active=True):
        clause = ' AND active=1' if active else ''
        rows = db.execute(f'SELECT * FROM duty_templates WHERE workspace=?{clause} ORDER BY id', (workspace,)).fetchall()
        return [r for r in rows if ids is None or r['id'] in ids]

    def stored(row):
        return StoredTemplate.model_validate(dict(id=row['id']) | json.loads(row['document']))

    @app.get('/api/duties/options')
    def options(request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace) as (db, user, snap):
            state = snap.state
            base = dict(today=local_now().date().isoformat(), revision=state.revision, yearStart=state.settings.yearStart, yearEnd=state.settings.yearEnd, days=state.settings.days)
            if service.manager(user, workspace): return base | dict(role='manager', settings=settings(db, workspace))
            own = sorted(service.own_teachers(user, snap, workspace))
            if not own: raise HTTPException(403, 'Дежурства показываются учителям, связанным с расписанием.')
            # Names only: a teacher may name a colleague in a swap request; the server decides whether that colleague fits.
            colleagues = sorted((t for t in state.teachers if t.id not in own), key=lambda t: t.name.casefold())
            return base | dict(role='teacher', teachers=[dict(id=x, name=snap.entities['teacher'][x].name) for x in own],
                               colleagues=[dict(id=t.id, name=t.name) for t in colleagues])

    @app.get('/api/duties/templates')
    def templates(request: Request, workspace: Workspace = 'school', archived: bool = False):
        with service.context(request, workspace) as (db, user, snap):
            service.require_manager(user, workspace)
            return [service.public_template(r) for r in template_rows(db, workspace, active=not archived)]

    @app.post('/api/duties/templates')
    def save_template(body: TemplateSave, request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace, True) as (db, user, snap):
            service.require_manager(user, workspace)
            key, previous = service.replay(db, user, body, 'template')
            if previous: return previous
            check_template(snap.state, body.template)
            document = body.template.model_dump(mode='json')
            if body.id is None:
                id, version, before, created = uuid4().hex[:16], 1, None, stamp()
                db.execute('INSERT INTO duty_templates VALUES (?,?,?,1,?,?,?)', (workspace, id, version, created, created, encoded(document)))
                action = 'created'
            else:
                row = db.execute('SELECT * FROM duty_templates WHERE workspace=? AND id=?', (workspace, body.id)).fetchone()
                if not row: raise HTTPException(404, 'Шаблон не найден.')
                if not row['active'] or row['version'] != body.version: raise HTTPException(409, 'Шаблон изменился. Обновите список.')
                id, version, before, action = body.id, row['version'] + 1, json.loads(row['document']), 'updated'
                db.execute('UPDATE duty_templates SET version=?,updated=?,document=? WHERE workspace=? AND id=?', (version, stamp(), encoded(document), workspace, id))
            service.history(db, workspace, user, 'template', id, action, body.reason, dict(before=before, after=document))
            access.record(db, 'duties.template_saved', workspace, snap.state.revision, detail=body.template.name)
            return service.remember(db, user, body, key, dict(id=id, version=version, active=True, updated=stamp()) | document)

    @app.post('/api/duties/templates/{id}/archive')
    def archive_template(id: str, body: TemplateArchive, request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace, True) as (db, user, snap):
            service.require_manager(user, workspace)
            key, previous = service.replay(db, user, body, 'archive:' + id)
            if previous: return previous
            row = db.execute('SELECT * FROM duty_templates WHERE workspace=? AND id=?', (workspace, id)).fetchone()
            if not row: raise HTTPException(404, 'Шаблон не найден.')
            if not row['active'] or row['version'] != body.version: raise HTTPException(409, 'Шаблон изменился. Обновите список.')
            db.execute('UPDATE duty_templates SET active=0,version=?,updated=? WHERE workspace=? AND id=?', (row['version'] + 1, stamp(), workspace, id))
            document = json.loads(row['document'])
            service.history(db, workspace, user, 'template', id, 'archived', body.reason, dict(before=document))
            access.record(db, 'duties.template_archived', workspace, snap.state.revision, detail=document['name'])
            return service.remember(db, user, body, key, dict(id=id, version=row['version'] + 1, active=False, updated=stamp()) | document)

    @app.get('/api/duties/templates/{id}/history')
    def template_history(id: str, request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace) as (db, user, snap):
            service.require_manager(user, workspace)
            rows = db.execute("SELECT seq,created,actor_name,action,reason FROM duty_history WHERE workspace=? AND entity='template' AND entity_id=? ORDER BY seq DESC LIMIT 100", (workspace, id)).fetchall()
            return [dict(id=r['seq'], created=r['created'], actor=r['actor_name'], action=r['action'], reason=r['reason']) for r in rows]

    @app.put('/api/duties/settings')
    def put_settings(body: SettingsSave, request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace, True) as (db, user, snap):
            service.require_manager(user, workspace)
            current = settings(db, workspace)
            if current['version'] != body.version: raise HTTPException(409, 'Настройки изменились. Обновите страницу.')
            value = body.model_dump(mode='json', exclude={'version', 'reason'})
            db.execute('INSERT INTO duty_settings VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document', (workspace, body.version + 1, encoded(value)))
            service.history(db, workspace, user, 'settings', workspace, 'changed', body.reason, dict(before={k: current[k] for k in value}, after=value))
            access.record(db, 'duties.settings_saved', workspace, snap.state.revision)
            return value | dict(version=body.version + 1)

    @app.post('/api/duties/generate/preview')
    def generate_preview(body: GeneratePreview, request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace) as (db, user, snap):
            service.require_manager(user, workspace)
            state = snap.state
            if body.revision != state.revision: raise HTTPException(409, 'Данные изменились. Обновите страницу и повторите проверку.')
            ids = set(body.templateIds)
            rows = template_rows(db, workspace, ids)
            if len(rows) != len(ids): raise HTTPException(404, 'Шаблон не найден.')
            chosen = [stored(r) for r in rows]
            versions = {r['id']: r['version'] for r in rows}
            generated = {(r['template_id'], r['on_date']): dict(commitmentId=r['commitment_id'], fingerprint=r['fingerprint'])
                         for r in db.execute('SELECT * FROM duty_generated WHERE workspace=? AND on_date BETWEEN ? AND ? AND template_id IN (SELECT value FROM json_each(?))',
                                             (workspace, body.start.isoformat(), body.end.isoformat(), json.dumps(sorted(ids))))}
        try: plan = plan_generation(state, chosen, body.start, body.end, generated, body)
        except ValueError as exc: raise HTTPException(422, reason_of(exc))
        result = inspect_edit(state, plan['candidate'])
        label, names = labeller(state), {t.id: t.name for t in state.teachers}
        listing = [dict(id=c.id, date=c.onDate.isoformat(), start=c.startTime, end=c.endTime, name=c.name, place=label(c), teachers=[names[t] for t in c.teacherIds]) for c in plan['created']]
        counts = dict(created=len(plan['created']), updated=len(plan['updated']), existing=plan['existing'], removed=len(plan['removed']), duplicates=len(plan['duplicates']))
        token = str(uuid4())
        document = dict(revision=state.revision, templateVersions=versions, commitments=[c.model_dump(mode='json') for c in plan['candidate'].commitments],
                        keys=plan['keys'], canApply=result['canApply'], counts=counts)
        with access.lock, access.connect() as db:
            db.execute('DELETE FROM duty_previews WHERE created<?', (preview_cutoff(),))
            db.execute('INSERT INTO duty_previews VALUES (?,?,?,?)', (token, workspace, stamp(), encoded(document)))
            db.execute('DELETE FROM duty_previews WHERE workspace=? AND token NOT IN (SELECT token FROM duty_previews WHERE workspace=? ORDER BY created DESC LIMIT 30)', (workspace, workspace))
        return dict(token=token, revision=state.revision, created=listing[:500], counts=counts, skipped=plan['skipped'], removed=plan['removed'], duplicates=plan['duplicates'],
                    warnings=plan['warnings'], **result)

    @app.post('/api/duties/generate/apply')
    def generate_apply(body: GenerateApply, request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace) as (db, user, snap):
            service.require_manager(user, workspace)
            key, previous = service.replay(db, user, body, 'generate')
            if previous: return previous | dict(state=snap.state)
            row = db.execute('SELECT document,created FROM duty_previews WHERE token=? AND workspace=?', (body.token, workspace)).fetchone()
            if not row or row['created'] < preview_cutoff(): raise HTTPException(404, 'Предпросмотр устарел. Проверьте создание заново.')
            doc = json.loads(row['document'])
            if doc['revision'] != body.revision or snap.state.revision != doc['revision']: raise HTTPException(409, 'План изменился после проверки. Проверьте создание заново.')
            if not doc['canApply']: raise HTTPException(422, 'Создание дежурств добавляет нарушения. Исправьте шаблоны или даты.')
            # The preview was validated against this very revision, so only the duties are replaced.
            candidate = snap.state.model_copy(update=dict(commitments=[Commitment.model_validate(c) for c in doc['commitments']]))

        def accept(db, saved):
            service.recheck(db, user)
            for id, version in doc['templateVersions'].items():
                found = db.execute('SELECT version,active FROM duty_templates WHERE workspace=? AND id=?', (workspace, id)).fetchone()
                if not found or not found[1] or found[0] != version: raise HTTPException(409, 'Шаблон изменился после проверки. Проверьте создание заново.')
            for template_id, on_date, commitment_id, digest in doc['keys']:
                db.execute('INSERT OR REPLACE INTO duty_generated VALUES (?,?,?,?,?,?,?)', (workspace, template_id, on_date, commitment_id, digest, saved.revision, stamp()))
            db.execute('DELETE FROM duty_previews WHERE token=?', (body.token,))
            service.history(db, workspace, user, 'generation', body.token, 'generated', '', doc['counts'])
            access.record(db, 'duties.generated', workspace, saved.revision, detail=f"Создано {doc['counts']['created']}")
            try: service.remember(db, user, body, key, dict(status='applied', revision=saved.revision, counts=doc['counts']))
            except sqlite3.IntegrityError: raise HTTPException(409, 'Этот идентификатор запроса уже использован.')

        saved = service.save(workspace, candidate, after_save=accept)
        return dict(status='applied', revision=saved.revision, counts=doc['counts'], state=saved)

    @app.get('/api/duties/mine')
    def mine(request: Request, workspace: Workspace = 'school', start: date | None = None, end: date | None = None, teacherId: str = ''):
        with service.context(request, workspace) as (db, user, snap):
            state = snap.state
            if service.manager(user, workspace):
                if not teacherId: raise HTTPException(422, 'Выберите сотрудника.')
                if teacherId not in snap.entities['teacher']: raise HTTPException(404, 'Сотрудник не найден.')
                ids = {teacherId}
            elif user['role'] == 'teacher':
                own = service.own_teachers(user, snap, workspace)
                if not own: raise HTTPException(403, 'Дежурства показываются учителям, связанным с расписанием.')
                if teacherId and teacherId not in own: raise HTTPException(404, 'Сотрудник недоступен.')
                ids = {teacherId} if teacherId else own
            else: raise HTTPException(403, 'Раздел доступен администрации расписания и учителям.')
            current = local_now()
            first = start or current.date()
            last = end or first + timedelta(days=59)
            if last < first or (last - first).days > 366: raise HTTPException(422, 'Период не длиннее 366 дней.')
            first, last = max(first, state.settings.yearStart), min(last, state.settings.yearEnd)
            owner = '' if service.manager(user, workspace) else user['id']
            swaps = {}
            for r in db.execute('SELECT commitment_id,from_teacher,status FROM duty_swaps WHERE workspace=? AND on_date BETWEEN ? AND ?' + (' AND requester_id=?' if owner else '') + ' ORDER BY seq',
                                [workspace, first.isoformat(), last.isoformat()] + ([owner] if owner else [])):
                swaps[r['commitment_id'], r['from_teacher']] = r['status'] if r['status'] != 'cancelled' else None
            label, names, days = labeller(state), {t.id: t.name for t in state.teachers}, state.settings.days
            everyone = {c.id for c in state.classes}
            items = []
            for c in sorted(state.commitments, key=lambda c: (c.onDate, c.startTime, c.name, c.id)):
                if c.kind != 'duty' or not first <= c.onDate <= last or not set(c.teacherIds) & ids: continue
                teacher = sorted(set(c.teacherIds) & ids)[0]
                closure = closure_on(state, c.onDate, ['holiday', 'winter'], everyone)
                items.append(dict(id=c.id, date=c.onDate.isoformat(), weekday=days[c.onDate.weekday()] if c.onDate.weekday() < len(days) else '', start=c.startTime, end=c.endTime,
                                  name=c.name, place=label(c), teacherId=teacher, colleagues=[names[t] for t in c.teacherIds if t not in ids],
                                  required=c.requiredTeachers, assigned=len(c.teacherIds), closed=closure.name if closure else '',
                                  swap=swaps.get((c.id, teacher)), canSwap=user['role'] == 'teacher' and starts_at(c) > current and not c.locked))
            return dict(today=current.date().isoformat(), start=first.isoformat(), end=last.isoformat(), teachers=[dict(id=x, name=names[x]) for x in sorted(ids, key=lambda x: names[x])], items=items)

    @app.get('/api/duties/roster')
    def roster_data(start: date, end: date, request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace) as (db, user, snap):
            service.require_manager(user, workspace)
            if end < start: raise HTTPException(422, 'Конец периода раньше начала.')
            if (end - start).days > 61: raise HTTPException(422, 'Печатный график — не больше двух месяцев.')
            return roster(snap.state, start, end)

    @app.get('/api/duties/fairness')
    def fairness_data(start: date, end: date, request: Request, workspace: Workspace = 'school'):
        with service.context(request, workspace) as (db, user, snap):
            service.require_manager(user, workspace)
            try: return fairness(snap.state, start, end)
            except ValueError as exc: raise HTTPException(422, str(exc))
