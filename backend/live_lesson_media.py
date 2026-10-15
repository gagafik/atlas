"""Pinned library editions and a projection view without personal responses."""
import hashlib
import json
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from fastapi import HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from . import homework_files, teaching_material_files, teaching_materials
from .notifications import encoded
from .quiz_models import public_question
from .live_lessons import stamp


def bind_materials(db, user, workspace, refs, retained):
    previous = {(m['id'], m['version']): m for m in retained}
    result = []
    for ref in refs:
        if (ref.id, ref.version) in previous:
            result.append(previous[ref.id, ref.version])
            continue
        row = teaching_materials.row_for(db, workspace, ref.id)
        if row['archived']:
            raise HTTPException(409, 'Архивный материал нельзя добавить к новому вопросу.')
        teaching_materials.require_version_access(db, user, row, ref.version)
        # Immutable material editions already retain their files through file_links.
        # Store the canonical snapshot, never client-supplied text/file metadata.
        result.append(teaching_materials.snapshot(db, ref.id, ref.version))
    return result


def register(s, app, Workspace):
    @app.get('/api/live-lessons/{id}/projection')
    def projection(id: str, request: Request, workspace: Workspace = 'school',
                   questionIndex: int | None = Query(default=None, ge=0, le=49)):
        with s.context(request, workspace) as (db, user, snap):
            row = s.access_row(db, user, snap, workspace, id, staff=True, metadata=True)
            rounds = s.rounds(db, row)
            selected = next((r for r in rounds if r['question_index'] == questionIndex), None) if questionIndex is not None else (rounds[-1] if rounds else None)
            if questionIndex is not None and selected is None:
                raise HTTPException(404, 'Вопрос ещё не открывали.')
            etag = '"' + hashlib.sha256(encoded([row['version'], rounds, questionIndex, user, snap.state.revision]).encode()).hexdigest() + '"'
            headers = {'ETag': etag, 'Cache-Control': 'no-store', 'Vary': 'Cookie', 'X-Atlas-Server-Time': stamp()}
            if request.headers.get('if-none-match') == etag:
                return Response(status_code=304, headers=headers)
            row = s.row(db, workspace, id)
            doc = json.loads(row['document'])
            result = dict(title=row['title'], instructions=doc['instructions'], status=row['status'],
                          questionCount=len(doc['questions']), opened=[r['question_index'] for r in rounds], round=None)
            if selected:
                index = selected['question_index']; q = doc['questions'][index]
                item = dict(index=index, phase=selected['phase'], deadline=selected['deadline'],
                            question=public_question(q), materials=q.get('materials', []))
                if selected['phase'] == 'revealed':
                    item.update(solution=q['question'], solutionMaterials=q.get('solutionMaterials', []))
                result['round'] = item
            return Response(encoded(result), media_type='application/json', headers=headers)

    @app.get('/api/live-lessons/{id}/files/{file_id}')
    def file(id: str, file_id: str, request: Request, workspace: Workspace = 'school',
             questionIndex: int = Query(ge=0, le=49),
             part: Literal['materials', 'solutionMaterials'] = 'materials', projection: bool = False):
        with s.context(request, workspace) as (db, user, snap):
            row = s.access_row(db, user, snap, workspace, id, staff=projection)
            manage = s.manage(user, snap, workspace, row)
            if projection or not manage:
                r = next((r for r in s.rounds(db, row) if r['question_index'] == questionIndex), None)
                if not r or (part == 'solutionMaterials' and r['phase'] != 'revealed'):
                    raise HTTPException(404, 'Материал ещё не открыт.')
            questions = json.loads(row['document'])['questions']
            if questionIndex >= len(questions): raise HTTPException(404, 'Вопрос не найден.')
            pinned = [f for m in questions[questionIndex].get(part, []) for f in m['files'] if f['id'] == file_id]
            if not pinned: raise HTTPException(404, 'Файл не относится к этому вопросу.')
            f = db.execute("SELECT * FROM teaching_material_files WHERE id=? AND workspace=? AND status='ready'", (file_id, workspace)).fetchone()
            if not f: raise HTTPException(404, 'Файл не найден.')
            path = homework_files.file_path(teaching_material_files.root_for(db), file_id)
            if not path.is_file(): raise HTTPException(404, 'Файл отсутствует в хранилище.')
            mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(Path(f['filename']).suffix.lower())
            disposition = 'inline' if mime else 'attachment'
            headers = {'Content-Disposition': disposition + "; filename*=UTF-8''" + quote(f['filename']),
                       'Cache-Control': 'no-store', 'Vary': 'Cookie', 'X-Content-Type-Options': 'nosniff',
                       'Content-Security-Policy': "sandbox; default-src 'none'"}
            return FileResponse(path, media_type=mime or 'application/octet-stream', headers=headers)
