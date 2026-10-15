"""Shared version references in plans; no private text is embedded in plan history."""
from fastapi import HTTPException
from . import teaching_materials as library


def references(content):
    return {(r['id'],r['version']) for t in content['topics'] for r in t.get('materials',[])}


def status(db, workspace, refs):
    refs=sorted(set(refs))
    if not refs: return {}
    library.tables(db)
    clauses=' OR '.join('(m.id=? AND v.version=?)' for _ in refs)
    rows=db.execute(f'''SELECT m.id,v.version,json_extract(v.document,'$.title')
        FROM teaching_materials m JOIN teaching_material_versions v ON v.material_id=m.id
        WHERE m.workspace=? AND m.visibility='staff' AND m.archived=0 AND v.visibility='staff'
        AND ({clauses})''',(workspace,*[value for pair in refs for value in pair])).fetchall()
    result={f'{id}:{version}':dict(id=id,version=version,available=False) for id,version in refs}
    for id,version,title in rows: result[f'{id}:{version}'].update(available=True,title=title)
    return result


def validate(db, workspace, content, previous=None):
    states=status(db,workspace,references(content))
    retained={(t['id'],r['id'],r['version']) for t in (previous or {}).get('topics',[]) for r in t.get('materials',[])}
    for topic in content['topics']:
        for ref in topic.get('materials',[]):
            if not states[f"{ref['id']}:{ref['version']}"]['available'] and (topic['id'],ref['id'],ref['version']) not in retained:
                raise HTTPException(409,'Для новой связи в плане выберите материал и редакцию, открытые сотрудникам. Приватные, архивные и недоступные редакции не добавляются.')


def resolve(db, workspace, topic):
    refs=topic.get('materials',[])
    states=status(db,workspace,[(r['id'],r['version']) for r in refs])
    return dict(materials=[library.snapshot(db,r['id'],r['version']) for r in refs if states[f"{r['id']}:{r['version']}"]['available']],
                unavailable=[dict(id=r['id'],version=r['version']) for r in refs if not states[f"{r['id']}:{r['version']}"]['available']])


def copy_content(db, workspace, content, omit_unavailable):
    states=status(db,workspace,references(content));omitted=0
    for topic in content['topics']:
        refs=topic.get('materials',[])
        unavailable=[r for r in refs if not states[f"{r['id']}:{r['version']}"]['available']]
        if unavailable and not omit_unavailable:
            raise HTTPException(409,'В плане есть недоступные материалы. Восстановите общий доступ или явно выберите копирование без недоступных материалов.')
        omitted+=len(unavailable)
        topic['materials']=[r for r in refs if states[f"{r['id']}:{r['version']}"]['available']]
    return omitted
