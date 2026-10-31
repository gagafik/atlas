import csv
from datetime import datetime,timezone
from uuid import uuid4
from fractions import Fraction
import pytest
from backend import api
from backend.final_reports import gpa_of,nearest_band
from backend.models import Placement,Student,Workload
from test_access import client,enable,create,signed  # noqa: F401
from test_journal import entry,lesson,write,graded,DAY
from test_journal_periods import period,close,action,period_clock  # noqa: F401
from test_final_grades import BASE,prepare,sheet,batch,propose,seeded,scale,report,publish,opts,stop_gradebook_jobs  # noqa: F401
from test_final_table import pair,english,ENGLISH
from test_class_staff import assign
from test_behaviour import note as behaviour_note,url as behaviour_url

STAMP=datetime(2026,10,14,10,tzinfo=timezone.utc)
MATHS='Математика';A='c-demo-a';D='c-demo-d'
PRIVATE={'calculation','explanation','author','rawPercent','roundedPercent'}


def lead(teacher_id,class_id=A):
    school=api.read('demo')
    school.classes=[c.model_copy(update=dict(classTeacherId=teacher_id)) if c.id==class_id else c for c in school.classes]
    api.save('demo',school)


def points_scale(c,**extra):
    bands=[dict(label='3',minPercent=0,points=3),dict(label='4',minPercent=50,points=4),dict(label='5',minPercent=75,points=5)]
    return scale(c,**dict(name='Баллы',bands=bands)|extra)


def keys(value):
    """Every key that appears anywhere in a JSON document."""
    if isinstance(value,dict):return set(value)|set().union(*[keys(v) for v in value.values()])
    if isinstance(value,list):return set().union(*[keys(v) for v in value]) if value else set()
    return set()


def finish(c,row,s,**extra):
    """Propose, approve and publish one row as the administrator."""
    saved=propose(c,row,s,**extra);assert saved.status_code==200,saved.text
    return publish(c,saved.json())


def published_pair(c,accounts=lambda c:None):
    """s1 holds English '5' and Maths '4', both published on a scale with points."""
    english(c);create(c,'parent');create(c,'teacher');accounts(c)
    p=close(c,period(c));b=prepare(c,p);s=points_scale(c)
    first=finish(c,pair(c,b,'s1'),s,label='5')
    second=finish(c,pair(c,b,'s1',MATHS),s,label='4',explanation='Решение педсовета')
    return p,b,s,first,second


def class_report(c,p,class_id=A,**params):
    return c.get(BASE+'/class-report',params=dict(workspace='demo',periodId=p['id'],classId=class_id)|params)


def layout(c,p,body,class_id=A):
    return c.put(f'{BASE}/report-layouts/{p["id"]}/{class_id}?workspace=demo',json=body)


def test_old_database_gains_report_tables_on_first_read(client):
    enable(client);assert opts(client)['admin']
    with api.connect() as db:db.execute('DROP TABLE grading_report_settings')
    # An upgraded database still carries the older migration marker; the first read must create the missing table.
    assert client.get(BASE+'/options?workspace=demo').status_code==200
    with api.connect() as db:assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='grading_report_settings'").fetchone()
    saved=client.put(BASE+'/report-settings?workspace=demo',json=dict(version=0,phrases=['Молодец'],yearRule='works'))
    assert saved.status_code==200 and saved.json()['version']==1
    options=opts(client);assert options['phrases']==['Молодец'] and options['yearRule']=='works' and options['reportVersion']==1
    assert client.put(BASE+'/report-settings?workspace=demo',json=dict(version=0,phrases=[])).status_code==409
    create(client,'teacher');t=signed('teacher')
    assert t.put(BASE+'/report-settings?workspace=demo',json=dict(version=1,phrases=[])).status_code==403
    assert opts(t)['phrases']==['Молодец'] and 'classes' in opts(t)


def test_scale_points_all_or_none(client):
    enable(client);url=BASE+'/scales/'+uuid4().hex+'?workspace=demo'
    partial=[dict(label='Ниже',minPercent=0,points=3),dict(label='Выше',minPercent=75)]
    assert client.put(url,json=dict(version=0,name='Шкала',bands=partial)).status_code==422
    full=client.put(url,json=dict(version=0,name='Шкала',bands=[dict(label='Ниже',minPercent=0,points=3),dict(label='Выше',minPercent=75,points='4.5')]))
    assert full.status_code==200,full.text
    assert [Fraction(str(b['points'])) for b in full.json()['bands']]==[3,Fraction(9,2)]
    assert client.put(url,json=dict(version=0,name='Шкала',bands=[dict(label='Ниже',minPercent=0,points=-1),dict(label='Выше',minPercent=75,points=2)])).status_code==422
    plain=scale(client);assert all(b.get('points') is None for b in plain['bands'])


def test_gpa_ignores_exempt_and_is_exact(client):
    english(client);create(client,'parent')
    state=api.read('demo');state.placements.append(Placement(id='hist',workloadId='w2',day=0,period=3,roomId='r2'));api.save('demo',state)
    p=close(client,period(client));b=prepare(client,p);s=points_scale(client);plain=scale(client);parent=signed('parent')
    finish(client,pair(client,b,'s1'),s,label='5')
    finish(client,pair(client,b,'s1',MATHS),s,label='4',explanation='Решение педсовета')
    assert report(parent,p).json()['gpa']==dict(value='4.50',counted=2,skipped=0)
    finish(client,pair(client,b,'s1','История'),s,label='',outcome='exempt',explanation='Освобождён приказом')
    got=report(parent,p).json();assert got['gpa']==dict(value='4.50',counted=2,skipped=0) and len(got['items'])==3
    # A grade given on a scale without points is not counted, and the exact mean is rounded half up.
    assert finish(client,pair(client,b,'s2'),plain,label='Ниже')['status']=='published'
    assert gpa_of([dict(outcome='graded',label='Выше',scale=plain)])is None
    two=[dict(outcome='graded',label='5',scale=s),dict(outcome='graded',label='5',scale=s),dict(outcome='graded',label='4',scale=s)]
    assert gpa_of(two)['value']=='4.67' and gpa_of(two+[dict(outcome='graded',label='Выше',scale=plain)])['skipped']==1
    assert nearest_band(s,Fraction(9,2))=='5' and nearest_band(s,Fraction(44,10))=='4' and nearest_band(plain,Fraction(4)) is None


def two_days(c):
    """Pupil s1 has English on two Mondays: 80% on the first, 60% on the second."""
    enable(c);create(c,'parent');create(c,'teacher')
    write(c,graded(entry(c,lesson(c)['lessonKey'])),publish=True).raise_for_status()
    second=entry(c,lesson(c,day='2026-08-24')['lessonKey'],day='2026-08-24');graded(second)
    second['document']['content']['records'][0]['grades'][0]['score']=6
    write(c,second,publish=True).raise_for_status()


def test_year_grade_comes_from_period_finals(client):
    two_days(client);s=points_scale(client);parent=signed('parent')
    results=[]
    for name,day,label in [('1 четверть',DAY,'5'),('2 четверть','2026-08-24','4')]:
        p=close(client,period(client,name=name,start=day,end=day,kind='quarter'));b=prepare(client,p)
        results.append(finish(client,pair(client,b,'s1'),s,label=label))
    year=close(client,period(client,name='Год',start=DAY,end='2026-08-24',kind='year'));book=prepare(client,year)
    row=pair(client,book,'s1');evidence=client.get(BASE+'/rows/'+row['id']+'/evidence?workspace=demo').json()
    advice=evidence['recommendations'][s['id']+':'+str(s['version'])]
    # The works alone give 70% (label 4); the quarter finals 5 and 4 average 4.5 and a tie goes to the higher band.
    assert advice['recommended']=='5' and advice['basis']=='periods' and advice['points']=='4.50' and advice['missing']==[]
    assert [(x['name'],x['label'],x['current']) for x in advice['periods']]==[('1 четверть','5',True),('2 четверть','4',True)]
    listed=next(i for i in sheet(client,book,scaleId=s['id']).json()['items'] if i['id']==row['id'])
    assert listed['advice']['basis']=='periods' and listed['advice']['recommended']=='5'
    saved=propose(client,row,s,label='5',submit=False)
    assert saved.status_code==200,saved.text
    assert saved.json()['proposal']['calculation']['basis']=='periods'
    assert propose(client,saved.json(),s,label='3').status_code==422
    result=finish(client,saved.json(),s,label='5');assert result['status']=='published'
    shown=report(parent,year).json()['items']
    assert [i['label'] for i in shown]==['5'] and not keys(shown)&(PRIVATE|{'basis','periods','missing'})
    # The same book recommends from the works once the school asks for that rule.
    client.put(BASE+'/report-settings?workspace=demo',json=dict(version=0,phrases=[],yearRule='works')).raise_for_status()
    again=client.get(BASE+'/rows/'+row['id']+'/evidence?workspace=demo').json()['recommendations'][s['id']+':'+str(s['version'])]
    assert 'basis' not in again and again['recommended']=='4' and again['rawPercent']=='70'


def year_advice(c,book,s,student='s1'):
    row=pair(c,book,student);return c.get(BASE+'/rows/'+row['id']+'/evidence?workspace=demo').json()['recommendations'][s['id']+':'+str(s['version'])]


def quarter(c,s,name,day,label,student='s1',**extra):
    """Close a one-day quarter and publish the pupil's English result in it (no result when the label is None)."""
    p=close(c,period(c,name=name,start=day,end=day,kind='quarter'));b=prepare(c,p)
    if label is not None:finish(c,pair(c,b,student),s,label=label,**dict(explanation='Решение педсовета')|extra)
    return p


def year(c):return prepare(c,close(c,period(c,name='Год',start=DAY,end='2026-08-24',kind='year')))


def test_year_grade_counts_the_quarters_and_not_a_semester_on_top(client):
    two_days(client);s=points_scale(client)
    quarter(client,s,'1 четверть',DAY,'5');quarter(client,s,'2 четверть','2026-08-24','4')
    semester=prepare(client,close(client,period(client,name='Полугодие',start=DAY,end='2026-08-24',kind='semester')))
    finish(client,pair(client,semester,'s1'),s,label='3',explanation='Решение педсовета')
    advice=year_advice(client,year(client),s)
    # The semester summary of 3 would pull the mean down to 4; the quarters alone average 4.5.
    assert [x['name'] for x in advice['periods']]==['1 четверть','2 четверть'] and advice['points']=='4.50' and advice['recommended']=='5'


def test_year_grade_names_the_period_without_a_result(client):
    two_days(client);s=points_scale(client)
    quarter(client,s,'1 четверть',DAY,'5');quarter(client,s,'2 четверть','2026-08-24',None)
    advice=year_advice(client,year(client),s)
    assert advice['missing']==['2 четверть'] and [x['name'] for x in advice['periods']]==['1 четверть'] and advice['points']=='5.00' and advice['recommended']=='5'


def test_year_grade_skips_an_exempt_quarter_without_calling_it_missing(client):
    two_days(client);s=points_scale(client)
    quarter(client,s,'1 четверть',DAY,'4');quarter(client,s,'2 четверть','2026-08-24','',outcome='exempt',explanation='Освобождён приказом')
    advice=year_advice(client,year(client),s)
    assert advice['missing']==[] and advice['points']=='4.00' and advice['recommended']=='4'
    assert [(x['label'],x['points']) for x in advice['periods']]==[('4','4'),('Освобождён',None)]


def test_year_grade_warns_when_a_quarter_was_reclosed_after_its_result(client):
    two_days(client);s=points_scale(client)
    first=quarter(client,s,'1 четверть',DAY,'5');quarter(client,s,'2 четверть','2026-08-24','4')
    close(client,action(client,first,'reopen').json())
    advice=year_advice(client,year(client),s)
    # The published result of the first quarter belongs to the earlier closure; staff are told, the grade still counts.
    assert [(x['name'],x['current']) for x in advice['periods']]==[('1 четверть',False),('2 четверть',True)] and advice['points']=='4.50'


def test_year_rule_works_and_no_finals_fall_back(client):
    two_days(client);s=points_scale(client)
    for day in [DAY,'2026-08-24']:close(client,period(client,name='Четверть '+day,start=day,end=day,kind='quarter'))
    book=prepare(client,close(client,period(client,name='Год',start=DAY,end='2026-08-24',kind='year')));row=pair(client,book,'s1')
    advice=client.get(BASE+'/rows/'+row['id']+'/evidence?workspace=demo').json()['recommendations'][s['id']+':'+str(s['version'])]
    assert 'basis' not in advice and advice['recommended']=='4' and advice['rawPercent']=='70'
    plain=scale(client)
    assert sheet(client,book,scaleId=plain['id']).json()['items'][0]['advice']['recommended']=='Ниже'


def test_layout_chooses_and_orders_subjects(client):
    p,b,s,first,second=published_pair(client);parent=signed('parent');t=signed('teacher')
    order=lambda:[i['subject'] for i in report(parent,p).json()['items']]
    assert order()==[ENGLISH,MATHS] and report(parent,p).json()['gpa']['counted']==2
    url=f'{BASE}/report-layouts/{p["id"]}/{A}?workspace=demo'
    assert client.get(url).json()['version']==0 and {x['id'] for x in client.get(url).json()['subjects']}=={'sub0','sub2'}
    assert layout(client,p,dict(version=0,order=['sub0','sub2'],hidden=[])).status_code==200
    assert order()==[MATHS,ENGLISH]
    assert layout(client,p,dict(version=0,order=[],hidden=[])).status_code==409
    assert t.put(url,json=dict(version=1,order=[],hidden=[])).status_code==403 and t.get(url).status_code==403
    assert layout(client,p,dict(version=1,order=['sub0','sub2'],hidden=['sub0'])).status_code==200
    shown=report(parent,p).json();assert [i['subject'] for i in shown['items']]==[ENGLISH] and shown['gpa']['counted']==1
    full=class_report(client,p).json();s1=next(x for x in full['pupils'] if x['id']=='s1')
    assert [i['subject'] for i in s1['items']]==[ENGLISH] and s1['gpa']['counted']==1 and [x['name'] for x in full['subjects']]==[ENGLISH]
    # Hiding changes what is shown, never what was published.
    with api.connect() as db:assert db.execute('SELECT count(*) FROM final_results').fetchone()[0]==2
    assert layout(client,p,dict(version=2,order=[],hidden=[])).status_code==200 and len(report(parent,p).json()['items'])==2
    assert layout(client,dict(p,id='missing'),dict(version=0)).status_code==404
    assert client.put(f'{BASE}/report-layouts/{p["id"]}/missing?workspace=demo',json=dict(version=0)).status_code==404
    assert layout(client,p,dict(version=3,order=['sub0','sub0'])).status_code==422


def test_layout_survives_reclose(client):
    p,b,s,first,second=published_pair(client);parent=signed('parent')
    assert layout(client,p,dict(version=0,order=['sub0','sub2'],hidden=[])).status_code==200
    reopened=action(client,p,'reopen').json();b2=prepare(client,close(client,reopened))
    assert b2['id']!=b['id']
    got=report(parent,p).json()
    assert [i['subject'] for i in got['items']]==[MATHS,ENGLISH] and not got['items'][0]['current']


def test_class_report_access(client):
    p,b,s,first,second=published_pair(client);lead('t2')
    create(client,'teacher',login='other',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])]);create(client,'student')
    t,other,parent,pupil=signed('teacher'),signed('other'),signed('parent'),signed('student')
    full=class_report(client,p);assert full.status_code==200,full.text
    assert [x['id'] for x in full.json()['pupils']]==['s1','s2'] and full.json()['className']=='10A' and full.json()['school']
    s1=next(x for x in full.json()['pupils'] if x['id']=='s1')
    assert [i['label'] for i in s1['items']]==['5','4'] and s1['gpa']['value']=='4.50' and next(x for x in full.json()['pupils'] if x['id']=='s2')['items']==[]
    mine=class_report(t,p);assert mine.status_code==200 and mine.json()==full.json()
    assert not keys(mine.json())&PRIVATE
    assert class_report(t,p,D).status_code==403 and class_report(other,p).status_code==403
    assert class_report(parent,p).status_code==403 and class_report(pupil,p).status_code==403
    assert class_report(client,p,'missing').status_code==404 and class_report(t,p,'missing').status_code==403
    assert class_report(client,dict(p,id='missing')).status_code==404
    # A pupil who left the class disappears from its report but not from the administrator's personal report.
    school=api.read('demo');school.students=[x.model_copy(update=dict(classId=D)) if x.id=='s2' else x for x in school.students];api.save('demo',school)
    assert [x['id'] for x in class_report(t,p).json()['pupils']]==['s1']


def test_class_report_lists_only_active_pupils(client):
    p,b,s,first,second=published_pair(client)
    school=api.read('demo');school.students=[x.model_copy(update=dict(active=False)) if x.id=='s1' else x for x in school.students];api.save('demo',school)
    got=class_report(client,p);assert got.status_code==200
    # The pupil who left has published grades, but the class report only reads the current pupils.
    assert [x['id'] for x in got.json()['pupils']]==['s2'] and got.json()['subjects']==[] and 'Ученик 1' not in got.text


def test_class_report_preview_is_admin_only_and_has_no_drafts(client):
    english(client);create(client,'parent');create(client,'teacher');lead('t2')
    p=close(client,period(client));b=prepare(client,p);s=points_scale(client);t=signed('teacher')
    approved=pair(client,b,'s1');submitted=pair(client,b,'s1',MATHS);draft=pair(client,b,'s2')
    batch(client,[propose(client,approved,s,label='5').json()],'approve').raise_for_status()
    assert propose(client,submitted,s,label='4',explanation='Решение педсовета').status_code==200
    assert propose(client,draft,s,label='4',submit=False).status_code==200
    shown=class_report(client,p,preview='true').json();s1=next(x for x in shown['pupils'] if x['id']=='s1')
    assert shown['preview'] is True and [(i['subject'],i['label'],i['preview']) for i in s1['items']]==[(ENGLISH,'5',True)]
    assert next(x for x in shown['pupils'] if x['id']=='s2')['items']==[] and not keys(shown)&PRIVATE
    assert class_report(t,p,preview='true').status_code==403 and class_report(signed('parent'),p,preview='true').status_code==403
    # The final copy has nothing until publication, and a family never reads an approved-only grade.
    assert next(x for x in class_report(client,p).json()['pupils'] if x['id']=='s1')['items']==[] and class_report(t,p).json()['preview'] is False
    assert report(signed('parent'),p).json()['items']==[]
    action(client,p,'reopen').raise_for_status()
    assert class_report(client,p,preview='true').status_code==409


def test_class_report_preview_after_reclose_ignores_results_of_the_earlier_closure(client):
    p,b,s,first,second=published_pair(client)
    reopened=action(client,p,'reopen').json();b2=prepare(client,close(client,reopened));assert b2['id']!=b['id']
    items=lambda body:next(x for x in body['pupils'] if x['id']=='s1')['items']
    # The new book only holds drafts: the grades of the earlier closure are not part of it.
    assert items(class_report(client,p,preview='true').json())==[]
    # The final copy keeps showing them, marked as belonging to the earlier closure.
    final=items(class_report(client,p).json());assert [(i['label'],i['current']) for i in final]==[('5',False),('4',False)]
    row=pair(client,b2,'s1');batch(client,[propose(client,row,s,label='4',explanation='Пересмотр').json()],'approve').raise_for_status()
    shown=class_report(client,p,preview='true').json()
    assert shown['preview'] is True and [(i['subject'],i['label'],i['preview'],i['current']) for i in items(shown)]==[(ENGLISH,'4',True,True)]
    assert [(i['label'],i['current']) for i in items(class_report(client,p).json())]==[('5',False),('4',False)]


def test_class_teacher_without_rows_gets_the_panel(client):
    english(client);create(client,'teacher');create(client,'teacher',login='leader',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])])
    create(client,'teacher',login='other',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])]);create(client,'parent')
    p=close(client,period(client));b=prepare(client,p);s=scale(client);lead('t1')
    panel=lambda login:signed(login).get('/api/journal/options?workspace=demo').json()['panels']['finalGrades']
    assert opts(signed('leader'))['classTeacher'] is True and sheet(signed('leader'),b).status_code==404
    assert panel('leader') is False and panel('other') is False and panel('teacher') is True
    finish(client,pair(client,b,'s1'),s)
    assert panel('leader') is True and panel('other') is False
    assert [x['id'] for x in opts(signed('leader'))['classes']]==[A]


def test_published_notice_names_subject_and_opens_report(client):
    p,b,s,first,second=published_pair(client,lambda c:create(c,'parent',login='second',grants=[dict(workspace='demo',view='student',entityIds=['s3'])]));parent=signed('parent')
    notice=[i for i in parent.get('/api/notifications/inbox').json()['items'] if i['action']=='journal.finals_published']
    assert len(notice)==1,notice
    entry_=notice[0];assert 'Английский язык — 5' in entry_['title'] and 'Математика — 4' in entry_['title'] and p['name'] in entry_['title']
    assert [x['id'] for x in entry_['students']]==['s1'] and entry_['link'] is True and entry_['category']=='grades'
    opened=parent.post(f"/api/notifications/{entry_['id']}/open");assert opened.status_code==200,opened.text
    assert opened.json()['module']=='finals' and opened.json()['recordId']==p['id'] and opened.json()['studentId']=='s1'
    assert [i for i in signed('second').get('/api/notifications/inbox').json()['items'] if i['action']=='journal.finals_published']==[]
    assert 'rawPercent' not in str(entry_) and 'Решение' not in str(entry_)


def test_published_notice_names_three_subjects_and_counts_the_rest(client):
    english(client);create(client,'parent');create(client,'teacher')
    # Physics belongs to the other half of the class in the seed, so 10A gets its own physics lesson: four subjects for s1.
    school=api.read('demo');school.workloads.append(Workload(id='w-phys',groupId='g-a',subjectId='sub1',teacherId='t1',hours=1,profileId='secondary',roomIds=['r2']))
    school.placements+=[Placement(id='hist',workloadId='w2',day=0,period=3,roomId='r2'),Placement(id='phys',workloadId='w-phys',day=0,period=4,roomId='r2')];api.save('demo',school)
    p=close(client,period(client));b=prepare(client,p);s=points_scale(client)
    for subject in [ENGLISH,MATHS,'История','Физика']:finish(client,pair(client,b,'s1',subject),s,label='5',explanation='Решение педсовета')
    notices=[i for i in signed('parent').get('/api/notifications/inbox').json()['items'] if i['action']=='journal.finals_published']
    assert len(notices)==1
    title=notices[0]['title']
    assert title.endswith('Английский язык — 5, История — 5, Математика — 5 и ещё 1') and 'Физика' not in title


def test_finals_notice_stops_opening_when_the_child_has_no_result_in_that_period(client):
    p,b,s,first,second=published_pair(client);parent=signed('parent')
    notice=next(i for i in parent.get('/api/notifications/inbox').json()['items'] if i['action']=='journal.finals_published')
    # Results of the same child in another period do not open this period's report.
    with api.connect() as db:db.execute("UPDATE final_results SET period_id='elsewhere' WHERE period_id=?",(p['id'],))
    assert parent.post(f"/api/notifications/{notice['id']}/open").status_code==404


def test_withdrawn_notice_has_no_grade_and_retracts_old_title(client):
    p,b,s,first,second=published_pair(client);parent=signed('parent')
    inbox=lambda action:[i for i in parent.get('/api/notifications/inbox').json()['items'] if i['action']==action]
    published=inbox('journal.finals_published')[0]
    # Reading the entry first keeps it in the inbox after it is retracted.
    parent.post(f"/api/notifications/{published['id']}/open").raise_for_status()
    gone=batch(client,[first],'withdraw',reason='Ошибочная публикация');assert gone.status_code==200,gone.text
    withdrawn=inbox('journal.finals_withdrawn');assert len(withdrawn)==1
    assert 'Английский язык' in withdrawn[0]['title'] and '5' not in withdrawn[0]['title'].split(':')[1] and 'Ошибочная' not in withdrawn[0]['title']
    assert 'link' not in withdrawn[0]
    old=[i for i in inbox('journal.finals_published')]
    assert all('title' not in i and 'students' not in i and 'link' not in i for i in old)


def test_republish_replaces_unread_entry(client):
    p,b,s,first,second=published_pair(client);parent=signed('parent')
    reopened=batch(client,[first],'reopen',reason='Проверка итога').json()['items'][0]
    finish(client,reopened,s,label='4',explanation='Пересмотр')
    notices=[i for i in parent.get('/api/notifications/inbox').json()['items'] if i['action']=='journal.finals_published']
    assert len(notices)==1 and 'Английский язык — 4' in notices[0]['title']


def test_measures_print_only_when_flagged_and_visible(client,monkeypatch):
    monkeypatch.setattr('backend.behaviour.now',lambda:STAMP)
    p,b,s,first,second=published_pair(client);parent=signed('parent')
    flagged=behaviour_note(client,['s1'],'director-remark','Личный текст замечания',day=DAY);assert flagged.status_code==200,flagged.text
    assert behaviour_note(client,['s1'],'discipline','Не печатать',day=DAY).status_code==200
    assert behaviour_note(client,['s1'],'director-remark','Скрытая',day=DAY,visible=False).status_code==200
    assert behaviour_note(client,['s1'],'director-remark','Вне периода',day='2026-08-18').status_code==200
    got=report(parent,p).json()['measures']
    assert len(got)==1 and got[0]['kind']=='remark' and got[0]['category']=='Замечание директора' and got[0]['day']==DAY and got[0]['letterNumber']
    assert set(got[0])=={'kind','kindTitle','category','day','letterNumber','levelTitle'} and 'Личный' not in str(report(parent,p).json())
    card=next(x for x in class_report(client,p).json()['pupils'] if x['id']=='s1');assert card['measures']==got
    note_id=flagged.json()['ids'][0];current=client.get(behaviour_url('notes')).json()['items']
    version=next(n['version'] for n in current if n['id']==note_id)
    assert client.post(behaviour_url(f'notes/{note_id}/withdraw'),json=dict(version=version,reason='Ошибка')).status_code==200
    assert report(parent,p).json()['measures']==[]


def test_measure_default_comes_from_category(client,monkeypatch):
    monkeypatch.setattr('backend.behaviour.now',lambda:STAMP)
    enable(client)
    listed=lambda:{n['category']:n['printed'] for n in client.get(behaviour_url('notes')).json()['items']}
    assert behaviour_note(client,['s1'],'director-remark','Одно',day=DAY).status_code==200
    assert behaviour_note(client,['s1'],'discipline','Два',day=DAY).status_code==200
    assert behaviour_note(client,['s1'],'director-praise','Три',day=DAY,printed=False).status_code==200
    assert behaviour_note(client,['s1'],'activity','Четыре',day=DAY,printed=True).status_code==200
    assert listed()=={'Замечание директора':True,'Нарушение дисциплины':False,'Благодарность директора':False,'Активная работа на уроке':True}
    flagged=next(n for n in client.get(behaviour_url('notes')).json()['items'] if n['category']=='Нарушение дисциплины')
    edited=client.post(behaviour_url(f"notes/{flagged['id']}/edit"),json=dict(version=flagged['version'],categoryId='discipline',text='Два',reason='Печать',printed=True))
    assert edited.status_code==200 and edited.json()['printed'] is True
    kept=client.post(behaviour_url(f"notes/{flagged['id']}/edit"),json=dict(version=edited.json()['version'],categoryId='discipline',text='Два',reason='Без изменения флага'))
    assert kept.json()['printed'] is True


def test_class_teacher_measures_follow_the_duty(client,monkeypatch):
    monkeypatch.setattr('backend.behaviour.now',lambda:STAMP)
    p,b,s,first,second=published_pair(client);teacher=create(client,'teacher',login='lead',grants=[dict(workspace='demo',view='teacher',entityIds=['t1'])]);lead('t1');t=signed('lead')
    assert behaviour_note(client,['s1'],'director-remark','Текст',day=DAY).status_code==200
    card=lambda:next(x for x in class_report(t,p).json()['pupils'] if x['id']=='s1')
    assert len(card()['measures'])==1 and len(report(t,p).json()['measures'])==1
    assign(client,{A:[dict(accountId=teacher['id'],capabilities=[],revoked=['behaviour_notes'])]})
    assert card()['measures']==[] and len(card()['items'])==2 and report(t,p).json()['measures']==[] and len(report(t,p).json()['items'])==2
    assert len(report(client,p).json()['measures'])==1


def test_report_without_behaviour_tables(client):
    p,b,s,first,second=published_pair(client)
    with api.connect() as db:
        for name in ['behaviour_notes','behaviour_history','behaviour_requests']:db.execute('DROP TABLE IF EXISTS '+name)
    got=report(signed('parent'),p);assert got.status_code==200 and got.json()['measures']==[]
    with api.connect() as db:assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='behaviour_notes'").fetchone()


def test_export_is_not_limited_to_one_page_of_the_sheet(client):
    enable(client)
    # 32 pupils in 10A take English and history: more rows than the 50 of one sheet page.
    school=api.read('demo');school.students+=[Student(id=f'x{i}',name=f'Ученик Х{i}',classId=A) for i in range(30)]
    school.placements.append(Placement(id='hist',workloadId='w2',day=0,period=3,roomId='r2'));api.save('demo',school)
    b=prepare(client,close(client,period(client)));total=sheet(client,b).json()['total']
    assert total>50
    response=client.get(BASE+'/books/'+b['id']+'/export',params=dict(workspace='demo'));assert response.status_code==200,response.text
    rows=list(csv.reader(response.content.decode('utf-8-sig').splitlines(),delimiter=';'))
    assert len(rows)-1==total


def test_export_contains_every_row_and_respects_scope(client):
    english(client);create(client,'parent');create(client,'teacher')
    school=api.read('demo');school.students=[x.model_copy(update=dict(name='=1+1')) if x.id=='s1' else x for x in school.students];api.save('demo',school)
    p=close(client,period(client));b=prepare(client,p);s=scale(client);t=signed('teacher')
    finish(client,pair(client,b,'s2'),s,label='Ниже',explanation='Решение')
    url=BASE+'/books/'+b['id']+'/export'
    def table(c,**params):
        response=c.get(url,params=dict(workspace='demo')|params);assert response.status_code==200,response.text
        assert response.headers['content-disposition']=='attachment; filename="final-grades.csv"'
        text=response.content.decode('utf-8-sig');return list(csv.reader(text.splitlines(),delimiter=';'))
    rows=table(client);total=sheet(client,b).json()['total']
    assert rows[0][:5]==['Ученик','Класс','Предмет','Статус','Итог'] and len(rows)-1==total and total>=4
    assert len(table(client,classId=A))-1<total and len(table(client,classId=A,subjectId='sub2'))-1==2 and len(table(client,status='published'))-1==1
    # Formula-like names are protected, and the published pupil carries the grade and the mark of the card.
    assert any(r[0].startswith("'=") for r in rows[1:]) and not any(r[0].startswith('=') for r in rows[1:])
    done=next(r for r in rows[1:] if r[3]=='Опубликовано');assert done[4]=='Ниже' and done[-1]=='Да' and done[13]=='Сотрудник · teacher'
    mine=table(t);assigned=[x for x in sheet(client,b).json()['items'] if x['assigned']]
    assert len(mine)-1==len(assigned)>0 and {r[2] for r in mine[1:]}=={ENGLISH}
    assert signed('parent').get(url,params=dict(workspace='demo')).status_code==403
    create(client,'teacher',login='idle',grants=[dict(workspace='demo',view='teacher',entityIds=['t3'])])
    assert signed('idle').get(url,params=dict(workspace='demo')).status_code==404
