"""Inventory observations are an audit, never an automatic write-off."""
import json
from uuid import uuid4
from fastapi import HTTPException,Query,Request
from .library_models import AuditStart,AuditMark,AuditClose
from .library import stamp,version
from .notifications import encoded


def register(s,app,Workspace):
    def audit(db,workspace,id):
        row=db.execute('SELECT * FROM library_audits WHERE workspace=? AND id=?',(workspace,id)).fetchone()
        if not row:raise HTTPException(404,'Сверка не найдена.')
        return dict(row)
    def public(db,row):
        counts={r['result']:r['n'] for r in db.execute('SELECT result,count(*) AS n FROM library_audit_items WHERE audit_id=? GROUP BY result',(row['id'],))}
        changed=db.execute('SELECT count(*) FROM library_audit_items a JOIN library_copies c ON c.id=a.copy_id WHERE a.audit_id=? AND c.version!=a.copy_version',(row['id'],)).fetchone()[0] if row['status']=='open' else 0
        return row|dict(counts=counts,changed=changed)
    @app.get('/api/library/audits')
    def listing(request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1)):
        with s.context(request,workspace) as (db,user,snap):
            s.catalog_manager(db,user,snap,workspace);rows=db.execute('SELECT * FROM library_audits WHERE workspace=? AND seq<? ORDER BY seq DESC LIMIT 21',(workspace,before)).fetchall()
            return dict(items=[public(db,dict(r)) for r in rows[:20]],nextBefore=rows[19]['seq'] if len(rows)>20 else None)
    @app.post('/api/library/audits')
    def start(body:AuditStart,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.catalog_manager(db,user,snap,workspace);key,old=s.replay(db,user,body,'audit-start:'+workspace)
            if old:return old
            if db.execute("SELECT 1 FROM library_audits WHERE workspace=? AND status='open'",(workspace,)).fetchone():raise HTTPException(409,'Сначала завершите открытую сверку.')
            count=db.execute("SELECT count(*) FROM library_copies WHERE workspace=? AND status!='withdrawn'",(workspace,)).fetchone()[0]
            if not count or count>100000:raise HTTPException(422,'Для сверки нужно от 1 до 100000 экземпляров. Списанные не входят.')
            id=str(uuid4());db.execute('INSERT INTO library_audits(id,workspace,version,title,status,created,closed) VALUES (?,?,?,?,?,?,?)',(id,workspace,1,body.title,'open',stamp(),None))
            for r in db.execute("SELECT id FROM library_copies WHERE workspace=? AND status!='withdrawn' ORDER BY seq",(workspace,)):
                c=s.copy(db,workspace,r['id']);result='loaned' if c['onLoan'] else 'pending'
                db.execute('INSERT INTO library_audit_items(audit_id,copy_id,version,copy_version,result,note,document) VALUES (?,?,?,?,?,?,?)',(id,c['id'],1,c['version'],result,'',encoded(c)))
            result=public(db,audit(db,workspace,id));s.history(db,workspace,user,'audit',id,'audit_started','',result)
            return s.remember(db,user,body,key,result)
    @app.get('/api/library/audits/{id}')
    def detail(id:str,request:Request,workspace:Workspace='school',before:int=Query(default=9223372036854775807,ge=1),result:str=Query(default='',pattern=r'^(pending|seen|missing|loaned)?$')):
        with s.context(request,workspace) as (db,user,snap):
            s.catalog_manager(db,user,snap,workspace);a=audit(db,workspace,id)
            sql='SELECT a.*,c.version AS current_version FROM library_audit_items a JOIN library_copies c ON c.id=a.copy_id WHERE audit_id=? AND a.seq<?';args=[id,before]
            if result:sql+=' AND result=?';args.append(result)
            rows=db.execute(sql+' ORDER BY a.seq DESC LIMIT 31',args).fetchall()
            items=[dict(copyId=r['copy_id'],version=r['version'],result=r['result'],note=r['note'],copy=json.loads(r['document']),changed=a['status']=='open' and r['copy_version']!=r['current_version']) for r in rows[:30]]
            return dict(audit=public(db,a),items=items,nextBefore=rows[29]['seq'] if len(rows)>30 else None)
    @app.post('/api/library/audits/{id}/copies/{copyid}')
    def mark(id:str,copyid:str,body:AuditMark,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.catalog_manager(db,user,snap,workspace);a=audit(db,workspace,id);key,old=s.replay(db,user,body,'audit-mark:'+workspace+':'+id+':'+copyid)
            if old:return old
            if a['status']!='open':raise HTTPException(409,'Сверка закрыта.')
            row=db.execute('SELECT * FROM library_audit_items WHERE audit_id=? AND copy_id=?',(id,copyid)).fetchone()
            if not row:raise HTTPException(404,'Экземпляр не входит в эту сверку.')
            version(row,body.version);c=s.copy(db,workspace,copyid);result=body.result;doc=row['document'];copy_version=row['copy_version']
            if result=='refresh':
                if not body.note:raise HTTPException(422,'Укажите причину повторной проверки.')
                copy_version=c['version'];doc=encoded(c);result='loaned' if c['onLoan'] else 'pending'
            else:
                if c['version']!=copy_version:raise HTTPException(409,'После начала сверки экземпляр изменился. Сначала обновите его состояние.')
                if c['onLoan'] and result!='loaned' or not c['onLoan'] and result=='loaned':raise HTTPException(409,'Проверьте фактическую выдачу или возврат экземпляра.')
                if result=='missing' and not body.note:raise HTTPException(422,'Опишите результат поиска экземпляра.')
            db.execute('UPDATE library_audit_items SET version=version+1,copy_version=?,result=?,note=?,document=? WHERE audit_id=? AND copy_id=?',(copy_version,result,body.note,doc,id,copyid));db.execute('UPDATE library_audits SET version=version+1 WHERE id=?',(id,))
            result=dict(copyId=copyid,version=body.version+1,result=result,note=body.note,copy=json.loads(doc));s.history(db,workspace,user,'audit',id,'audit_checked',body.note,result)
            return s.remember(db,user,body,key,result)
    @app.post('/api/library/audits/{id}/action')
    def finish(id:str,body:AuditClose,request:Request,workspace:Workspace='school'):
        with s.context(request,workspace,True) as (db,user,snap):
            s.catalog_manager(db,user,snap,workspace);a=audit(db,workspace,id);key,old=s.replay(db,user,body,'audit-close:'+workspace+':'+id)
            if old:return old
            version(a,body.version)
            if a['status']!='open':raise HTTPException(409,'Сверка уже завершена.')
            current=public(db,a)
            if body.action=='close' and (current['changed'] or current['counts'].get('pending',0)):raise HTTPException(409,'Остались непроверенные или изменившиеся экземпляры.')
            db.execute('UPDATE library_audits SET version=version+1,status=?,closed=? WHERE id=?',('closed' if body.action=='close' else 'cancelled',stamp(),id));result=public(db,audit(db,workspace,id));s.history(db,workspace,user,'audit',id,'audit_'+body.action,body.reason,result)
            return s.remember(db,user,body,key,result)
