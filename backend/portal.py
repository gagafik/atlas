"""A projected personal timetable: no raw state, pupil rosters or private notes."""
from datetime import date
from typing import Literal
from fastapi import Request, HTTPException
from .exporting import VIEWS
from .portal_cache import PortalSnapshots


def grants(user, workspace, state):
    if not user: raise HTTPException(401,'Войдите в учётную запись.')
    if user['role']=='admin' or user['role']=='editor' and workspace in user['workspaces']:
        return [dict(view=view,entityIds=[x.id for x in getattr(state,table)]) for view,table in VIEWS.items() if view!='class_'] + [dict(view='class',entityIds=[x.id for x in state.classes])]
    return [g for g in user['grants'] if g['workspace']==workspace]


def register_portal(app, Workspace, read, stamp):
    snapshots = PortalSnapshots(read, stamp)
    @app.get('/api/portal/options')
    def options(request: Request):
        user=request.state.user
        if not user: raise HTTPException(401,'Войдите в учётную запись.')
        spaces=user['workspaces'] if user['role'] in ['admin','editor'] else sorted({g['workspace'] for g in user['grants']})
        result=[]
        for workspace in spaces:
            snapshot=snapshots.get(workspace); state=snapshot.state; records=[]
            for grant in grants(user,workspace,state):
                entities=snapshot.entities['class_' if grant['view']=='class' else grant['view']]
                records += [dict(view=grant['view'],id=id,name=entities[id].name) for id in grant['entityIds'] if id in entities]
            result.append(dict(id=workspace,name=state.settings.name,revision=state.revision,records=records,cycles=state.cycles.patterns,
                               yearStart=state.settings.yearStart,yearEnd=state.settings.yearEnd))
        return result

    @app.get('/api/portal/schedule')
    def schedule(request: Request, view: Literal['class','teacher','room','group','student'], entityId: str,
                 workspace: Workspace='school', cycleId: str | None=None, day: date | None=None):
        user=request.state.user
        if not user: raise HTTPException(401,'Войдите в учётную запись.')
        staff=user['role']=='admin' or user['role']=='editor' and workspace in user['workspaces']
        if not staff and not any(g['view']==view and entityId in g['entityIds'] for g in grants(user,workspace,None)):
            raise HTTPException(403,'Это расписание не назначено вашей учётной записи.')
        snapshot=snapshots.get(workspace); state=snapshot.state
        kind='class_' if view=='class' else view
        entity=snapshot.entities[kind].get(entityId)
        if not entity: raise HTTPException(404,'Запись удалена из расписания. Обратитесь к администратору.')
        if day:
            if not state.settings.yearStart <= day <= state.settings.yearEnd: raise HTTPException(422,'Дата вне учебного года.')
            projected=snapshot.day_rows(day,kind,entityId)
        else:
            cycle=cycleId or state.cycles.patterns[0].id
            try: projected=snapshot.week_rows(cycle,kind,entityId)
            except ValueError as exc: raise HTTPException(422,str(exc))
        return dict(revision=state.revision,name=entity.name,school=state.settings.name,rows=projected,day=day,source='working')
