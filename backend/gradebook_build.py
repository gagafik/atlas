"""Read an immutable closure into an owned staging DB, never write the school DB."""
from contextlib import closing
from datetime import date,timedelta
from fractions import Fraction
import json
import multiprocessing as mp
import os
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4
from .models import State
from .calendar_ops import DayContext,resolve_day
from .notifications import encoded


class Cancelled(Exception):pass


def compile_book(path,job,stage,cancel,progress):
    def check():
        if cancel.is_set():raise Cancelled()
    rows={};last=0
    def report(phase,done,total):
        nonlocal last
        check()
        if time.monotonic()-last>.25 or done==total:
            progress(dict(phase=phase,completed=done,total=total));last=time.monotonic()
    def target(pupil,subject_id,subject):
        key=(pupil['id'],subject_id)
        if key not in rows:rows[key]=dict(id=uuid4().hex,student=pupil,subject=subject,expected=0,published=0,numeric=0,text=0,missing=0,zeroWeight=0,incompleteRoster=0,weightedSum=Fraction(0),weight=Fraction(0))
        return rows[key]
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(stage)) as out,out:
        source.execute('BEGIN')
        closure=source.execute('SELECT state_document,published_count FROM journal_period_closures WHERE workspace=? AND period_id=? AND version=?',(job['workspace'],job['periodId'],job['closureVersion'])).fetchone()
        if not closure:raise ValueError('Missing immutable closure')
        state=State.model_validate_json(closure[0]);ctx=DayContext(state);students={s.id:s for s in state.students};classes={c.id:c.name for c in state.classes}
        out.execute('CREATE TABLE facts(lesson_key TEXT,version INTEGER,student_id TEXT,subject_id TEXT,day TEXT,start TEXT,summary TEXT,works TEXT)')
        out.execute('CREATE TABLE projected(lesson_key TEXT,version INTEGER)')
        out.execute('CREATE TABLE rows(id TEXT,student_id TEXT,subject_id TEXT,class_id TEXT,source TEXT)')
        start=date.fromisoformat(job['start']);end=date.fromisoformat(job['end']);total=(end-start).days+1
        for n in range(total):
            check();day=start+timedelta(days=n)
            for lesson in resolve_day(state,day,ctx)[0]:
                if lesson['activity']!='lesson' or lesson['status']=='cancelled':continue
                for sid in lesson['studentIds']:
                    s=students[sid];row=target(dict(id=sid,name=s.name,classId=s.classId,className=classes.get(s.classId,'')),lesson['subjectId'],lesson['subject'])
                    row['expected']+=1;row['incompleteRoster']+=int(not lesson['rosterComplete'])
            report('schedule',n+1,total)
        lessons=source.execute('''SELECT s.lesson_key,s.published_version,CASE WHEN p.lesson_key IS NULL THEN d.document END
            FROM journal_period_snapshots s JOIN journal_period_documents d ON d.workspace=s.workspace AND d.lesson_key=s.lesson_key AND d.published_version=s.published_version
            LEFT JOIN grading_projected p ON p.workspace=s.workspace AND p.lesson_key=s.lesson_key AND p.version=s.published_version
            WHERE s.workspace=? AND s.period_id=? AND s.closure_version=? ORDER BY s.day,s.start,s.lesson_key''',(job['workspace'],job['periodId'],job['closureVersion']))
        for n,(key,version,raw) in enumerate(lessons):
            check()
            if raw is None:
                summaries=[(r[0],json.loads(r[1])) for r in source.execute('SELECT subject_id,summary FROM grading_facts WHERE workspace=? AND lesson_key=? AND version=?',(job['workspace'],key,version))]
            else:
                document=json.loads(raw);lesson=document['lesson'];records={r['studentId']:r for r in document['content']['records']};summaries=[];facts=[]
                for pupil in lesson['students']:
                    check();pupil={k:pupil[k] for k in ['id','name','classId','className']}
                    s=dict(student=pupil,subject=lesson['subject'],numeric=0,text=0,missing=0,zeroWeight=0,weightedSum=Fraction(0),weight=Fraction(0))
                    grades={g['assessmentId']:g for g in records.get(pupil['id'],{}).get('grades',[])};works=[]
                    for a in document['content']['assessments']:
                        grade=grades.get(a['id'],{});score=grade.get('score');label=grade.get('label','')
                        if score is not None and a['weight']>0:
                            weight=Fraction(str(a['weight']));s['numeric']+=1;s['weight']+=weight;s['weightedSum']+=Fraction(str(score))/Fraction(str(a['maxScore']))*100*weight
                        elif score is not None:s['zeroWeight']+=1
                        elif label:s['text']+=1
                        else:s['missing']+=1
                        works.append(dict(assessment=a['name'],kind=a['kind'],score=score,label=label,maxScore=a['maxScore'],weight=a['weight']))
                    s['weightedSum']=str(s['weightedSum']);s['weight']=str(s['weight']);summaries.append((lesson['subjectId'],s))
                    facts.append((key,version,pupil['id'],lesson['subjectId'],lesson['date'],lesson['start'],encoded(s),encoded(works)))
                out.executemany('INSERT INTO facts VALUES (?,?,?,?,?,?,?,?)',facts)
                out.execute('INSERT INTO projected VALUES (?,?)',(key,version))
            for subject,s in summaries:
                row=target(s['student'],subject,s['subject']);row['published']+=1
                for k in ['numeric','text','missing','zeroWeight']:row[k]+=s[k]
                for k in ['weightedSum','weight']:row[k]+=Fraction(s[k])
            report('journals',n+1,closure[1])
        for n,((student,subject),row) in enumerate(rows.items()):
            check();id=row.pop('id');row['weightedSum']=str(row['weightedSum']);row['weight']=str(row['weight'])
            out.execute('INSERT INTO rows VALUES (?,?,?,?,?)',(id,student,subject,row['student']['classId'],encoded(row)))
            report('rows',n+1,len(rows))
    return dict(status='built',rows=len(rows))


def _child(path,job,stage,cancel,channel):
    from .solver_process import _lower_priority
    _lower_priority();done=threading.Event()
    def watch():
        parent=mp.parent_process()
        while not done.wait(.5):
            if parent is not None and not parent.is_alive():os._exit(0)
    threading.Thread(target=watch,daemon=True).start()
    try:channel.send(('result',compile_book(path,job,stage,cancel,lambda p:channel.send(('progress',p)))))
    except Cancelled:channel.send(('result',dict(status='cancelled')))
    except (BrokenPipeError,EOFError):pass
    except Exception:
        # Do not echo pupil documents, SQL values or filesystem paths to clients.
        channel.send(('result',dict(status='failed',message='Не удалось подготовить ведомость. Можно повторить задание.')))
    finally:done.set();channel.close()


def build_isolated(path,job,stage,cancel,progress,*,target=None,wall_seconds=1800):
    ctx=mp.get_context('spawn');receiver,sender=ctx.Pipe(duplex=False);child_cancel=ctx.Event()
    process=ctx.Process(target=target or _child,args=(str(path),job,str(stage),child_cancel,sender),name='atlas-gradebook',daemon=True)
    started=False;deadline=time.monotonic()+wall_seconds;cancelling=None
    try:
        process.start();started=True;sender.close()
        while True:
            if cancel.is_set():
                child_cancel.set();cancelling=cancelling or time.monotonic()
                if time.monotonic()-cancelling>=2:return dict(status='cancelled')
            if time.monotonic()>=deadline:return dict(status='failed',message='Превышено время подготовки ведомости. Можно повторить задание.')
            if receiver.poll(.1):
                try:kind,value=receiver.recv()
                except EOFError:break
                if kind=='progress':progress(value)
                elif kind=='result':return dict(status='cancelled') if cancel.is_set() else value
            elif not process.is_alive():break
        return dict(status='failed',message='Исполнитель остановился без результата. Можно повторить задание.')
    finally:
        child_cancel.set();sender.close();receiver.close()
        if started:
            process.join(timeout=.5)
            if process.is_alive():process.terminate();process.join(timeout=3)
            if process.is_alive():process.kill();process.join(timeout=3)
            if process.is_alive():raise RuntimeError('Gradebook worker did not stop')
            process.close()
