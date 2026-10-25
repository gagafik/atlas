"""Assessment categories (ФО/СОР/СОЧ) of journal works: the school catalog, share policy, calendar link and exact category maths."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import json
import re
from typing import Literal
from fastapi import HTTPException, Request
from pydantic import Field, model_validator
from .models import Model
from .notifications import encoded
from .access import now
from .natural import natural

# A stored work without a category counts as ФО. This is the reading rule, never the configurable default.
LEGACY='fo'
GREY='#6b7280'
# The 25/25/50 split is the Kazakhstan rule for a term; the school must confirm it (an assumption, like assessment_calendar.DEFAULTS).
DEFAULTS=dict(version=0,categories=[
    dict(id='fo',name='ФО',title='Формативное оценивание',color='#2563eb',calendar='',active=True),
    dict(id='sor',name='СОР',title='Суммативное оценивание за раздел',color='#d97706',calendar='summative_unit',active=True),
    dict(id='soch',name='СОЧ',title='Суммативное оценивание за четверть',color='#dc2626',calendar='summative_term',active=True)],
    defaultCategory='fo',
    policy=dict(shares=[dict(categoryId='fo',percent='25'),dict(categoryId='sor',percent='25'),dict(categoryId='soch',percent='50')],method='average',missing='renormalize'),
    rules=[])
SHAPE=re.compile(r'([a-z0-9-]{1,40})?')


def tables(db):
    # Plain execute, never executescript: this runs inside journal write transactions.
    db.execute('CREATE TABLE IF NOT EXISTS assessment_category_config(workspace TEXT PRIMARY KEY,version INTEGER NOT NULL,document TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS assessment_category_history(id INTEGER PRIMARY KEY,workspace TEXT,version INTEGER,actor TEXT,created TEXT,reason TEXT,document TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS assessment_category_history_ws ON assessment_category_history(workspace,id)')
    backfill(db)


def lacking(column):
    return f"EXISTS(SELECT 1 FROM json_each({column},'$.content.assessments') a WHERE coalesce(json_extract(a.value,'$.category'),'')='')"


def filled(raw):
    """(text, changed): every work of the stored document gets the legacy category."""
    if raw is None:return raw,False
    document=json.loads(raw);changed=False
    for a in document.get('content',{}).get('assessments',[]):
        if not a.get('category'):a['category']=LEGACY;changed=True
    return (encoded(document) if changed else raw),changed


def backfill(db):
    """Once: draft and published copies get category 'fo'. History, closure copies and grade events keep their bytes (read through category_of)."""
    if db.execute("SELECT version FROM journal_projection_versions WHERE name='assessment_categories'").fetchone()==(1,):return
    db.execute('SAVEPOINT assessment_categories_migration')
    try:
        after=-1
        while True:
            # Batches by rowid, so no cursor stays open on the table being updated.
            rows=db.execute(f'SELECT rowid,workspace,lesson_key,document,published_document FROM journal_entries WHERE rowid>? AND ({lacking("document")} OR {lacking("published_document")}) ORDER BY rowid LIMIT 200',(after,)).fetchall()
            if not rows:break
            for rowid,workspace,key,draft,published in rows:
                db.execute('UPDATE journal_entries SET document=?,published_document=? WHERE rowid=?',(filled(draft)[0],filled(published)[0],rowid))
            after=rows[-1][0]
        db.execute("INSERT OR REPLACE INTO journal_projection_versions VALUES ('assessment_categories',1)")
        db.execute('RELEASE assessment_categories_migration')
    except Exception:
        db.execute('ROLLBACK TO assessment_categories_migration');db.execute('RELEASE assessment_categories_migration');raise


class Category(Model):
    id: str = Field(pattern=r'^[a-z0-9-]{1,40}$')
    name: str = Field(min_length=1,max_length=12)
    title: str = Field(min_length=1,max_length=80)
    color: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    calendar: Literal['','summative_unit','summative_term'] = ''
    active: bool = True


class Share(Model):
    categoryId: str = Field(min_length=1,max_length=40)
    percent: Decimal = Field(ge=0,le=100,max_digits=5,decimal_places=2)


class Policy(Model):
    shares: list[Share] = Field(min_length=1,max_length=20)
    method: Literal['average','points'] = 'average'
    missing: Literal['renormalize','block'] = 'renormalize'

    @model_validator(mode='after')
    def valid(self):
        if len({s.categoryId for s in self.shares})!=len(self.shares):raise ValueError('Категория указана в долях несколько раз.')
        if sum(s.percent for s in self.shares)!=100:raise ValueError('Доли категорий должны в сумме давать 100 %.')
        return self


class Rule(Policy):
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    name: str = Field(min_length=1,max_length=80)
    subjectIds: list[str] = Field(default_factory=list,max_length=300)
    classIds: list[str] = Field(default_factory=list,max_length=300)


class ConfigEdit(Model):
    version: int = Field(ge=0)
    reason: str = Field(min_length=3,max_length=500)
    categories: list[Category] = Field(min_length=1,max_length=20)
    defaultCategory: str = Field(min_length=1,max_length=40)
    policy: Policy
    rules: list[Rule] = Field(default_factory=list,max_length=50)

    @model_validator(mode='after')
    def valid(self):
        by_id={c.id:c for c in self.categories}
        if len(by_id)!=len(self.categories) or len({c.name.casefold() for c in self.categories})!=len(self.categories):raise ValueError('Коды и названия категорий не должны повторяться.')
        if not by_id.get(self.defaultCategory) or not by_id[self.defaultCategory].active:raise ValueError('Категория по умолчанию должна существовать и быть действующей.')
        for p in [self.policy,*self.rules]:
            if any(s.categoryId not in by_id for s in p.shares):raise ValueError('Доли ссылаются на неизвестную категорию.')
        if len({r.id for r in self.rules})!=len(self.rules):raise ValueError('Правила не должны повторяться.')
        linked=[c.calendar for c in self.categories if c.active and c.calendar]
        if len(set(linked))!=len(linked):raise ValueError('Тип календаря связан только с одной категорией.')
        return self


def config(db,workspace):
    """The stored configuration, or the defaults of a school that never edited it (also on a database without the table)."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assessment_category_config'").fetchone():
        row=db.execute('SELECT version,document FROM assessment_category_config WHERE workspace=?',(workspace,)).fetchone()
        if row:return json.loads(row[1])|dict(version=row[0])
    return deepcopy(DEFAULTS)


def category_of(a):return a.get('category') or LEGACY


def names(cfg):return {c['id']:c for c in cfg['categories']}


def calendar_category(cfg,calendar):
    """The journal category of a calendar type (summative_unit/summative_term); 'other' and unmapped types take the school default."""
    return next((c['id'] for c in cfg['categories'] if c['active'] and calendar and c['calendar']==calendar),cfg['defaultCategory'])


# Pure maths: exact Fractions up to the final rounding.
F0=Fraction(0)
KEYS=['weight','weightedSum','points','maxPoints']


def empty():return dict(numeric=0,weight=F0,weightedSum=F0,points=F0,maxPoints=F0)


def tally(assessments,grades):
    """Per-category sums of one pupil's numeric marks in one lesson. Labels, missing marks and zero-weight works add nothing."""
    by={g['assessmentId']:g for g in grades}
    sums={}
    for a in assessments:
        g=by.get(a['id'])
        if not g or g['score'] is None or a['kind']!='points' or not a['weight']>0:continue
        w,s,m=Fraction(str(a['weight'])),Fraction(str(g['score'])),Fraction(str(a['maxScore']))
        c=sums.setdefault(category_of(a),empty())
        c['numeric']+=1;c['weight']+=w;c['weightedSum']+=s/m*100*w;c['points']+=s*w;c['maxPoints']+=m*w
    return sums


def merge(into,other):
    for id,c in other.items():
        t=into.setdefault(id,empty());t['numeric']+=c['numeric']
        for k in KEYS:t[k]+=c[k]
    return into


def dump(sums):return {id:{k:(str(v) if k in KEYS else v) for k,v in c.items()} for id,c in sums.items()}


def load(sums):return {id:{k:(Fraction(v) if k in KEYS else v) for k,v in c.items()} for id,c in sums.items()}


def percent_text(f):return str(Decimal(f.numerator)/Decimal(f.denominator))


def percent_of(c,method):
    """The category's percentage by the policy method, or None when it has no weighted marks."""
    if not c or not c['weight']>0:return None
    return c['weightedSum']/c['weight'] if method=='average' else c['points']/c['maxPoints']*100


def policy_for(cfg,subject_id,class_id):
    """A self-contained snapshot of the first rule that matches the subject and class, else of the default policy."""
    rule=next((r for r in cfg['rules'] if (not r['subjectIds'] or subject_id in r['subjectIds']) and (not r['classIds'] or class_id in r['classIds'])),None)
    source=rule or cfg['policy'];catalog=names(cfg)
    def share(s):
        c=catalog.get(s['categoryId']) or dict(name=s['categoryId'],title='',color=GREY)
        return dict(id=s['categoryId'],name=c['name'],title=c['title'],color=c['color'],share=str(s['percent']))
    return dict(ruleId=rule['id'] if rule else None,ruleName=rule['name'] if rule else 'Правило школы по умолчанию',method=source['method'],missing=source['missing'],shares=[share(s) for s in source['shares']])


def composite(categories,policy):
    """The share-weighted percentage over the categories that have marks; a missing category is never a zero."""
    shares=[(s,Fraction(s['share'])) for s in policy['shares']]
    positive={s['id'] for s,f in shares if f>0}
    entries,present,missing=[],[],[]
    for s,f in shares:
        if f<=0:continue
        c=categories.get(s['id']);pct=percent_of(c,policy['method'])
        entries.append(dict(id=s['id'],name=s['name'],color=s['color'],share=s['share'],numeric=c['numeric'] if c else 0,percent=percent_text(pct) if pct is not None else None))
        if pct is None:missing.append(s['id'])
        else:present.append((f,pct,c['numeric']))
    blocked=policy['missing']=='block' and bool(missing)
    exact=None if blocked or not present else sum(f*p for f,p,_ in present)/sum(f for f,_,_ in present)
    return dict(exact=exact,categories=entries,missing=missing,excluded=sorted(id for id,c in categories.items() if c['numeric'] and id not in positive),
                counted=sum(n for _,_,n in present),blocked=blocked)


def breakdown(sums,cfg,method):
    """Per-category percentages of a pupil's subject, in catalog order, for the family report."""
    catalog=names(cfg);order=list(catalog)
    result=[]
    for id in sorted(sums,key=lambda i:(order.index(i) if i in order else len(order),i)):
        pct=percent_of(sums[id],method)
        if pct is None:continue
        c=catalog.get(id) or dict(name=id,color=GREY)
        result.append(dict(id=id,name=c['name'],color=c['color'],numeric=sums[id]['numeric'],percentage=round(float(pct),2)))
    return result


# Linking a journal work to a booking of the assessment calendar.
BOOKING_PROBLEM='Работа календаря не относится к этому уроку или отменена.'


def has_calendar(db):
    """The calendar creates its tables lazily, so a school that never opened it has none."""
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assessment_bookings'").fetchone())


def linked_to(raw):return (json.loads(raw).get('journal') or {}).get('assessmentId')


def lesson_bookings(db,workspace,key,cfg):
    """Active calendar works on this lesson, for the journal editor (staff only)."""
    if not has_calendar(db):return []
    result=[]
    for id,status,type_id,category,raw in db.execute("SELECT id,status,type_id,category,document FROM assessment_bookings WHERE workspace=? AND lesson_key=? AND status!='cancelled' ORDER BY seq",(workspace,key)):
        data=json.loads(raw)
        result.append(dict(id=id,status=status,typeId=type_id,type=data.get('type',''),short=data.get('short',''),topic=data.get('topic',''),calendarCategory=category,
                           journalCategory=calendar_category(cfg,category),assessmentId=linked_to(raw)))
    return result


def booking_category(db,workspace,key,id):
    """The calendar category of a live booking of this lesson, else 422."""
    row=db.execute('SELECT status,category,lesson_key FROM assessment_bookings WHERE workspace=? AND id=?',(workspace,id)).fetchone() if has_calendar(db) else None
    if not row or row[0]=='cancelled' or row[2]!=key:raise HTTPException(422,BOOKING_PROBLEM)
    return row[1]


def resolve_content(db,workspace,key,content,previous,user):
    """Inside the save transaction: default and validate each work's category and keep the calendar links in step.

    Returns a new content dict. A link is checked only when it is new, so a booking that is cancelled or moved later never blocks the lesson.
    """
    cfg=config(db,workspace);catalog=names(cfg)
    drafts={a['id']:a for a in previous['document']['content']['assessments']} if previous else {}
    stored={}
    for document in [previous['published'],previous['document']] if previous else []:
        for a in (document['content']['assessments'] if document else []):stored.setdefault(a['id'],set()).add(category_of(a))
    works,linked={},[]
    for a in content['assessments']:
        a=dict(a)
        if not a.get('bookingId'):a.pop('bookingId',None)
        booking=a.get('bookingId');mapped=None
        if booking and drafts.get(a['id'],{}).get('bookingId')!=booking:
            mapped=calendar_category(cfg,booking_category(db,workspace,key,booking));linked.append((booking,a['id']))
        # An old client may leave the category out of a work that already exists: keep the stored one.
        if not a.get('category'):a['category']=mapped or (category_of(drafts[a['id']]) if a['id'] in drafts else cfg['defaultCategory'])
        found=catalog.get(a['category'])
        if not found:raise HTTPException(422,'Выберите категорию работы из списка школы.')
        if not found['active'] and a['category'] not in stored.get(a['id'],set()):
            raise HTTPException(422,f"Категория «{found['name']}» отключена. Выберите действующую категорию.")
        works[a['id']]=a
    if linked or any(a.get('bookingId') for a in drafts.values()):
        from .assessment_calendar import link_journal
        for booking,id in linked:link_journal(db,workspace,booking,id,user,key)
        for id,a in drafts.items():
            booking=a.get('bookingId')
            if booking and works.get(id,{}).get('bookingId')!=booking and has_calendar(db):
                # Only a link that still points at this work is dropped; a booking that was linked elsewhere is left alone.
                row=db.execute('SELECT document FROM assessment_bookings WHERE workspace=? AND id=?',(workspace,booking)).fetchone()
                if row and linked_to(row[0])==id:link_journal(db,workspace,booking,None,user,key,id)
    return dict(content,assessments=list(works.values()))


def register(journal,app,Workspace):
    def admin(user,workspace):
        if user['role']!='admin' or workspace not in user['workspaces']:raise HTTPException(403,'Категории работ настраивает администратор.')

    def view(cfg,user,workspace):
        result=dict(version=cfg['version'],categories=cfg['categories'],defaultCategory=cfg['defaultCategory'])
        if user['role'] in ['admin','teacher']:result|=dict(policy=cfg['policy'],rules=cfg['rules'])
        if user['role']=='admin' and workspace in user['workspaces']:
            state=journal.snapshots.get(workspace).state
            result|=dict(canEdit=True,subjects=[dict(id=s.id,name=s.name) for s in sorted(state.subjects,key=lambda s:natural(s.name))],
                         classes=[dict(id=c.id,name=c.name) for c in sorted(state.classes,key=lambda c:natural(c.name))])
        return result

    @app.get('/api/journal/categories')
    def read_categories(request: Request,workspace: Workspace='school'):
        user=journal.scope(request,workspace)
        cfg=journal.reading(lambda db:(journal.recheck_account(db,user),config(db,workspace))[1])
        return view(cfg,user,workspace)

    @app.put('/api/journal/categories')
    def edit_categories(body: ConfigEdit,request: Request,workspace: Workspace='school'):
        user=journal.scope(request,workspace,staff=True);admin(user,workspace)
        state=journal.snapshots.get(workspace).state
        subjects,classes={s.id for s in state.subjects},{c.id for c in state.classes}
        with journal.access.lock,journal.access.connect() as db:
            journal.tables(db)
            if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
            journal.recheck_account(db,user)
            current=config(db,workspace)
            # The version check makes a repeated request harmless, as for final-grade scales.
            if current['version']!=body.version:raise HTTPException(409,'Настройки категорий изменились. Обновите страницу.')
            if {c['id'] for c in current['categories']}-{c.id for c in body.categories}:raise HTTPException(422,'Категорию нельзя удалить — её можно отключить.')
            old={r['id']:r for r in current['rules']}
            for r in body.rules:
                # Ids that left the timetable after the rule was saved stay allowed; only new references are checked.
                known=old.get(r.id,{})
                if set(r.subjectIds)-subjects-set(known.get('subjectIds',[])) or set(r.classIds)-classes-set(known.get('classIds',[])):
                    raise HTTPException(422,'Правило ссылается на неизвестный предмет или класс.')
            value=body.model_dump(mode='json',exclude={'version','reason'});version=current['version']+1
            db.execute('INSERT INTO assessment_category_config VALUES (?,?,?) ON CONFLICT(workspace) DO UPDATE SET version=excluded.version,document=excluded.document',(workspace,version,encoded(value)))
            db.execute('INSERT INTO assessment_category_history(workspace,version,actor,created,reason,document) VALUES (?,?,?,?,?,?)',(workspace,version,user['name'],now().isoformat(),body.reason,encoded(value)))
            journal.access.record(db,'journal.categories',workspace,detail=encoded(dict(version=version)))
        return view(value|dict(version=version),user,workspace)
