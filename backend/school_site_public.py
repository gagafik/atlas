"""Small script-free public pages. Only explicitly published fields are rendered."""
from datetime import datetime
from html import escape
import json
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from . import homework_files as storage

LABELS={'page':'Страница','news':'Новость','event':'Событие','gallery':'Галерея'}
CSS='''*{box-sizing:border-box}body{margin:0;background:#f5f7f4;color:#19382f;font:17px/1.6 system-ui,sans-serif}a{color:#075e46;text-underline-offset:3px}a:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid #b65b0b;outline-offset:3px}header,main,footer{max-width:1100px;margin:auto;padding:24px}header{border-bottom:1px solid #ccdad1}header>a{font-size:24px;font-weight:700;text-decoration:none}nav{display:flex;flex-wrap:wrap;gap:12px 24px;margin-top:16px}.intro{font-size:21px;max-width:800px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr));gap:24px}.card,article{background:white;border:1px solid #d9e2dc;border-radius:16px;padding:24px;min-width:0}h1{font-size:clamp(30px,5vw,48px);line-height:1.18}h2{line-height:1.3}p,h1,h2,a,figcaption{overflow-wrap:anywhere}.text{white-space:pre-wrap}small{color:#506a5f}figure{margin:0}img{display:block;width:100%;height:auto;border-radius:10px}figcaption{padding:8px 0 20px}form{display:flex;flex-wrap:wrap;gap:10px;margin:24px 0}input,button,select{font:inherit;padding:10px;border:1px solid #8da99b;border-radius:8px;max-width:100%}button{background:#e5f2e9;color:#19382f;cursor:pointer}.notice{background:#fff1cf;padding:12px;border-radius:8px}.skip{position:absolute;left:-9999px}.skip:focus{left:10px;top:10px;background:white;padding:12px}footer{border-top:1px solid #ccdad1;margin-top:32px}article{max-width:900px;margin:auto}.event{padding:14px;background:#edf5ed;border-radius:10px}.pages{display:flex;gap:20px;margin:24px 0}@media(max-width:600px){header,main,footer{padding:16px}.card,article{padding:18px}}'''

def headers(indexable=False):
    return {'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff',
            'X-Robots-Tag':'index, follow' if indexable else 'noindex, nofollow',
            'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"}

def navigation(db,workspace,site):
    return [dict(id=r['id'],slug=r['slug'],title=json.loads(r['published'])['title']) for id in site['menuIds'] for r in db.execute('SELECT id,slug,published FROM school_site_entries WHERE id=? AND workspace=? AND published IS NOT NULL',(id,workspace))]

def event(data):
    if data['kind']!='event':return ''
    def local(value):return datetime.fromisoformat(value).astimezone(ZoneInfo('Asia/Qyzylorda')).strftime('%d.%m.%Y %H:%M')
    return '<p class="event">'+escape(local(data['startsAt'])+' — '+local(data['endsAt'])+' · время школы (UTC+5)'+(' · '+data['place'] if data['place'] else ''))+'</p>'

def render(site,workspace,nav,entry=None,items=None,preview=False,q='',kind='',offset=0,more=False):
    base='/school/'+workspace
    title=(entry['title']+' · ' if entry else '')+site['title']
    links=''.join('<a href="'+base+'/p/'+escape(n['slug'],quote=True)+'">'+escape(n['title'])+'</a>' for n in nav)
    content='<p class="notice">Предпросмотр. Черновик ещё не опубликован.</p>' if preview else ''
    if entry:
        content+='<article><small>'+LABELS[entry['kind']]+'</small><h1>'+escape(entry['title'])+'</h1><p class="intro text">'+escape(entry['summary'])+'</p>'+event(entry)
        for block in entry['blocks']:
            if block['kind']=='heading':content+='<h2>'+escape(block['text'])+'</h2>'
            elif block['kind']=='link':content+='<p><a rel="noopener noreferrer" href="'+escape(block['url'],quote=True)+'">'+escape(block['text'])+'</a></p>'
            else:content+='<p class="text">'+escape(block['text'])+'</p>'
        content+='<div class="grid">'
        for photo in entry['photos']:
            src=('/api/school-site/photos/'+photo['id']+'?workspace='+workspace) if preview else base+'/photos/'+photo['id']
            content+='<figure><img loading="lazy" src="'+src+'" alt="'+escape(photo['alt'],quote=True)+'"><figcaption>'+escape(photo['caption'])+'</figcaption></figure>'
        content+='</div></article>'
    else:
        content+='<h1>'+escape(site['title'])+'</h1><p class="intro text">'+escape(site['intro'])+'</p>'
        if not preview:
            content+='<form method="get" action="'+base+'"><label>Поиск <input name="q" maxlength="100" value="'+escape(q,quote=True)+'"></label><label>Раздел <select name="kind"><option value="">Все материалы</option>'
            content+=''.join('<option value="'+k+'"'+(' selected' if kind==k else '')+'>'+v+'</option>' for k,v in LABELS.items())+'</select></label><button>Найти</button></form>'
        content+='<div class="grid">'
        for item in items or []:
            content+='<section class="card"><small>'+LABELS[item['kind']]+'</small><h2><a href="'+base+'/p/'+escape(item['slug'],quote=True)+'">'+escape(item['title'])+'</a></h2><p>'+escape(item['summary'])+'</p>'+event(item)+'</section>'
        content+='</div>'
        if not items and not preview:content+='<p>Опубликованных материалов по этому запросу нет.</p>'
        if more or offset:
            content+='<nav class="pages" aria-label="Страницы материалов">'
            if offset:content+='<a href="'+base+'?'+escape(urlencode(dict(q=q,kind=kind,offset=max(0,offset-24))),quote=True)+'">Назад</a>'
            if more:content+='<a href="'+base+'?'+escape(urlencode(dict(q=q,kind=kind,offset=offset+24)),quote=True)+'">Далее</a>'
            content+='</nav>'
    return '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="'+escape(entry['summary'] if entry else site['intro'][:300],quote=True)+'"><title>'+escape(title)+'</title><style>'+CSS+'</style></head><body><a class="skip" href="#content">К содержимому</a><header><a href="'+base+'">'+escape(site['title'])+'</a><nav aria-label="Меню сайта">'+links+'</nav></header><main id="content">'+content+'</main><footer><p class="text">'+escape(site['contact'])+'</p><p class="text">'+escape(site['footer'])+'</p></footer></body></html>'

def register(s,app,Workspace):
    @app.middleware('http')
    async def public_headers(request,call_next):
        response=await call_next(request)
        if request.url.path.startswith('/school/'):
            if response.status_code==404 and '/photos/' not in request.url.path:
                response=HTMLResponse('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Страница недоступна</title><style>'+CSS+'</style></head><body><main><h1>Страница недоступна</h1><p>Материал ещё не опубликован, снят с сайта или адрес указан неверно.</p></main></body></html>',status_code=404)
            for key,value in headers().items():
                if key not in response.headers:response.headers[key]=value
        return response

    @app.get('/school/{workspace}',response_class=HTMLResponse)
    def home(workspace:Workspace,q:str=Query(default='',max_length=100),kind:str=Query(default='',pattern='^(|page|news|event|gallery)$'),offset:int=Query(default=0,ge=0,le=3000)):
        with s.public_context(workspace) as (db,site):
            where='workspace=? AND published IS NOT NULL';args=[workspace]
            if q:where+=' AND instr(published_search,?)>0';args.append(q.casefold())
            if kind:where+=" AND json_extract(published,'$.kind')=?";args.append(kind)
            rows=db.execute('SELECT published FROM school_site_entries WHERE '+where+' ORDER BY published_at DESC,seq DESC LIMIT 25 OFFSET ?',[*args,offset]).fetchall()
            items=[json.loads(r['published']) for r in rows[:24]]
            return HTMLResponse(render(site,workspace,navigation(db,workspace,site),items=items,q=q,kind=kind,offset=offset,more=len(rows)>24),headers=headers(site['indexable'] and not q))

    @app.get('/school/{workspace}/p/{slug}',response_class=HTMLResponse)
    def page(workspace:Workspace,slug:str):
        with s.public_context(workspace) as (db,site):
            row=db.execute('SELECT published FROM school_site_entries WHERE workspace=? AND slug=? AND published IS NOT NULL',(workspace,slug)).fetchone()
            if not row:raise HTTPException(404,'Материал недоступен.')
            return HTMLResponse(render(site,workspace,navigation(db,workspace,site),entry=json.loads(row['published'])),headers=headers(site['indexable']))

    @app.get('/school/{workspace}/photos/{id}')
    def photo(workspace:Workspace,id:str):
        from .school_site_files import root_for
        with s.public_context(workspace) as (db,site):
            row=db.execute("SELECT f.*,e.published FROM school_site_files f JOIN school_site_entries e ON e.id=f.item_id AND e.workspace=f.workspace WHERE f.id=? AND f.workspace=? AND f.status='ready' AND e.published IS NOT NULL",(id,workspace)).fetchone()
            if not row or id not in [p['id'] for p in json.loads(row['published'])['photos']]:raise HTTPException(404,'Фото недоступно.')
            path=storage.file_path(root_for(db),id)
            if not path.is_file():raise HTTPException(404,'Фото недоступно.')
            # A bounded, already re-encoded image is read inside the authorization
            # snapshot. Retained history images are never removed by draft GC.
            return Response(path.read_bytes(),media_type='image/jpeg',headers=headers())
