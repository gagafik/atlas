"""Opt-in single-instance backup worker. Private files never enter API responses or logs."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from uuid import uuid4

from .backup_school import create_backup, digest, reject_link
from .restore_school import restore_backup, check_runtime

ROOT=Path(__file__).resolve().parents[1]
RUN=re.compile(r'backup-\d{14}-[a-f0-9]{16}')
RETRY_SECONDS=900
HEARTBEAT_SECONDS=5
CHILD_ERRORS={3:'permission',4:'database',5:'verification',6:'missing_file',7:'unexpected',8:'copy_integrity'}


class ChildFailure(RuntimeError):
    def __init__(self,code):self.code=CHILD_ERRORS.get(code,'process')


def timestamp():return datetime.now(timezone.utc).isoformat()


def number(name,default,minimum,maximum):
    value=int(os.environ.get(name,str(default)))
    if not minimum<=value<=maximum:raise ValueError(f'{name} is outside its supported range.')
    return value


@dataclass(frozen=True)
class Config:
    database:Path
    destination:Path
    interval:int=86400
    keep:int=7
    snapshot_timeout:int=30
    verify_timeout:int=600
    reserve_bytes:int=256*1024*1024

    @property
    def control(self):return self.database.with_suffix('.backup-agent')

    @property
    def identity(self):
        return hashlib.sha256(str(self.database.resolve()).encode()).hexdigest()

    @classmethod
    def environment(cls,database=None):
        target=os.environ.get('ATLAS_BACKUP_DIR','').strip()
        if not target:return None
        return cls(Path(database or os.environ.get('TIMETABLE_DB',ROOT/'data/timetable.db')).resolve(),Path(target).absolute(),
                   number('ATLAS_BACKUP_INTERVAL_HOURS',24,1,168)*3600,
                   number('ATLAS_BACKUP_KEEP',7,2,90),
                   number('ATLAS_BACKUP_SNAPSHOT_TIMEOUT_SECONDS',30,1,300),
                   number('ATLAS_BACKUP_VERIFY_TIMEOUT_SECONDS',600,10,7200))


def read_json(path):
    reject_link(path)
    if path.stat().st_size>128*1024:raise ValueError('Oversized operations state.')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('Invalid operations state.')
    return value


def atomic_json(path,value):
    reject_link(path)
    pending=path.with_name(path.name+'.pending-'+uuid4().hex)
    with pending.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
    pending.replace(path)


@contextmanager
def worker_lock(control):
    reject_link(control)
    control.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=control/'worker.lock';reject_link(path)
    with path.open('a+b') as stream:
        if os.name=='nt':
            import msvcrt
            stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise ValueError('A backup worker already owns this database.') from None
        else:
            import fcntl
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise ValueError('A backup worker already owns this database.') from None
        try:yield
        finally:
            if os.name=='nt':
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def managed_remove(root,path):
    """Only direct, recorded run directories inside the exact managed root."""
    reject_link(root);reject_link(path)
    root=root.resolve(strict=True)
    if path.parent.resolve()!=root or not RUN.fullmatch(path.name) or path.resolve().parent!=root:
        raise ValueError('Refusing cleanup outside the managed root.')
    if not path.exists():return
    for child in path.rglob('*'):reject_link(child)
    shutil.rmtree(path)


class Worker:
    def __init__(self,config):
        self.config=config
        self.state={}
        self.stop=False
        self.child=None

    def initialize(self):
        check_runtime()
        c=self.config
        reject_link(c.control);reject_link(c.destination)
        target=c.destination.resolve()
        if (c.database.is_relative_to(target) or target.is_relative_to(c.control.resolve())
                or target.is_relative_to(c.database.with_suffix('.files'))):
            raise ValueError('Backup destination overlaps school data or worker staging.')
        c.destination.mkdir(mode=0o700,parents=True,exist_ok=True)
        owner=c.destination/'atlas-backup-owner.json'
        if owner.exists():
            if read_json(owner)!={'database':c.identity,'format':'atlas-backup-owner-v1'}:
                raise ValueError('Backup destination belongs to another database.')
        else:
            # An existing nonempty directory is never adopted for retention.
            if any(c.destination.iterdir()):raise ValueError('A new, empty backup destination is required.')
            with owner.open('x',encoding='utf-8') as stream:
                json.dump({'database':c.identity,'format':'atlas-backup-owner-v1'},stream)
        status=c.control/'status.json'
        if status.exists():
            self.state=read_json(status)
            if self.state.get('database')!=c.identity or self.state.get('destination')!=str(target):
                raise ValueError('Worker state belongs to a different configuration.')
            if (not isinstance(self.state.get('copies'),list) or len(self.state['copies'])>1000
                    or any(not isinstance(row,dict) or not RUN.fullmatch(row.get('id','')) for row in self.state['copies'])
                    or self.state.get('runId') and not RUN.fullmatch(self.state['runId'])):
                raise ValueError('Invalid backup retention state.')
        else:
            self.state=dict(database=c.identity,destination=str(target),copies=[],lastSuccess=None,lastFailure=None,nextRun=0)
        # Child processes enforce their own deadlines even after a hard parent
        # crash. Do not clean or reuse their paths until that bound has expired.
        if self.state.get('runId') and self.state.get('childDeadline',0)>time.time():
            raise ValueError('An interrupted backup child may still be active; retry after its deadline.')
        # Only a previously recorded interrupted run may be removed. A committed
        # successful destination remains available even after a cleanup interruption.
        old=self.state.get('runId')
        if old:
            managed_remove(c.control, c.control/old)
            pending=c.destination/old
            if pending.exists() and not any(row['id']==old for row in self.state['copies']):
                managed_remove(c.destination,pending)
            self.state['lastFailure']=dict(at=timestamp(),code='interrupted')
            self.state['nextRun']=0
        self.state.pop('runId',None)
        self.publish('waiting')

    def publish(self,phase=None,**values):
        if phase:self.state['phase']=phase
        self.state.update(values,heartbeat=time.time(),intervalHours=self.config.interval//3600,keep=self.config.keep)
        atomic_json(self.config.control/'status.json',self.state)

    def run_child(self,phase,arguments,timeout):
        self.publish(phase,childDeadline=time.time()+timeout+10)
        # Separate child makes writer-lock and verification deadlines enforceable.
        # Capture neither private exception messages nor school paths in logs.
        self.child=subprocess.Popen([sys.executable,'-m','tools.backup_service',*arguments,'--deadline',str(timeout)],cwd=ROOT,
                                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        started=time.monotonic()
        try:
            while self.child.poll() is None:
                if self.stop:raise InterruptedError('Worker stopped.')
                if time.monotonic()-started>=timeout:raise TimeoutError('Backup phase exceeded its deadline.')
                try:self.child.wait(timeout=min(HEARTBEAT_SECONDS,timeout))
                except subprocess.TimeoutExpired:self.publish()
            if self.child.returncode==124:raise TimeoutError('Backup phase exceeded its deadline.')
            if self.child.returncode:raise ChildFailure(self.child.returncode)
        finally:
            if self.child.poll() is None:self.child.kill();self.child.wait()
            self.child=None
            self.state.pop('childDeadline',None)
        return round(time.monotonic()-started,3)

    def cycle(self):
        c=self.config
        id='backup-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')+'-'+uuid4().hex[:16]
        stage=c.control/id;target=c.destination/id
        started=time.monotonic()
        try:
            if len(self.state['copies'])>=1000:raise ValueError('Retention requires operator attention.')
            if not c.database.is_file():raise FileNotFoundError('School database is missing.')
            # Estimate before taking the source writer lock. Include every stored
            # attachment (also uploading ones) and enough space for snapshot+drill.
            size=c.database.stat().st_size
            for suffix in ['-wal','-journal']:
                journal=Path(str(c.database)+suffix);reject_link(journal)
                if journal.is_file():size+=journal.stat().st_size
            files=c.database.with_suffix('.files');reject_link(files)
            if files.exists():
                for file in files.rglob('*'):
                    reject_link(file)
                    if file.is_file():size+=file.stat().st_size
            if (shutil.disk_usage(c.control).free<3*size+c.reserve_bytes
                    or shutil.disk_usage(c.destination).free<3*size+c.reserve_bytes):
                raise OSError('Insufficient free space.')
            stage.mkdir(mode=0o700)
            self.publish('snapshot',runId=id,lastAttempt=timestamp())
            seconds=self.run_child('snapshot',['snapshot','--database',str(c.database),'--output',str(stage/'snapshot')],c.snapshot_timeout)
            self.publish(snapshotSeconds=seconds)
            self.run_child('verifying',['verify','--source',str(stage/'snapshot'),'--output',str(target),
                                       '--drill',str(stage/'restore')],c.verify_timeout)
            # The child writes only non-sensitive aggregate evidence after both checks.
            result=read_json(stage/'result.json')
            completed=dict(id=id,at=timestamp(),files=result['files'],bytes=result['bytes'],
                           snapshotSeconds=seconds,durationSeconds=round(time.monotonic()-started,3),
                           writerLockSeconds=result['writerLockSeconds'],
                           manifestSha256=digest(target/'manifest.json'),restoreVerified=True)
            self.state['copies'].append(completed)
            self.state['lastSuccess']=completed
            self.state['lastFailure']=None
            self.state['nextRun']=time.time()+c.interval
            # Persist success BEFORE retention. A failed pruning operation must not
            # hide a verified backup, nor remove the most recent successful copy.
            self.publish('retention')
            self.prune()
            return True
        except (OSError,ValueError,RuntimeError) as exc:
            code='timeout' if isinstance(exc,TimeoutError) else 'stopped' if isinstance(exc,InterruptedError) else 'failed'
            self.state['lastFailure']=dict(at=timestamp(),code=code,phase=self.state.get('phase'),
                                           reason=exc.code if isinstance(exc,ChildFailure) else type(exc).__name__)
            self.state['nextRun']=time.time()+RETRY_SECONDS
            return False
        finally:
            if stage.exists():
                try:managed_remove(c.control,stage)
                except (OSError,ValueError):self.state['cleanupWarning']=True
            if target.exists() and not any(row['id']==id for row in self.state['copies']):
                try:managed_remove(c.destination,target)
                except (OSError,ValueError):self.state['cleanupWarning']=True
            self.state.pop('runId',None)
            self.publish('stopped' if self.stop else 'waiting')

    def prune(self):
        while len(self.state['copies'])>self.config.keep:
            oldest=self.state['copies'][0];path=self.config.destination/oldest['id']
            try:
                if path.exists():
                    if digest(path/'manifest.json')!=oldest['manifestSha256']:
                        raise ValueError('Retained backup was changed outside this worker.')
                    managed_remove(self.config.destination,path)
                self.state['copies'].pop(0)
                self.publish()
            except (OSError,ValueError):
                self.state['cleanupWarning']=True
                break

    def run(self,once=False):
        with worker_lock(self.config.control):
            self.initialize()
            if once:
                ok=self.cycle();self.publish('stopped');return ok
            try:
                while not self.stop:
                    if time.time()>=self.state['nextRun']:self.cycle()
                    self.publish()
                    for _ in range(HEARTBEAT_SECONDS):
                        if self.stop:break
                        time.sleep(1)
            finally:self.publish('stopped')
        return True


def public_status(database):
    """Allowlist only; never expose paths, IDs, file names or source metadata."""
    try:config=Config.environment(database)
    except (ValueError,OSError):return dict(state='configuration_error')
    if not config:return dict(state='disabled')
    try:
        reject_link(config.control)
        state=read_json(config.control/'status.json')
        if state.get('database')!=config.identity or state.get('destination')!=str(config.destination.resolve()):
            return dict(state='configuration_error')
        heartbeat=state.get('heartbeat',0)
        alive=isinstance(heartbeat,(int,float)) and 0<=time.time()-heartbeat<30 and state.get('phase')!='stopped'
        last=state.get('lastSuccess')
        failure=state.get('lastFailure')
        result=dict(state='running' if alive else 'stopped',phase=state.get('phase') if state.get('phase') in ['waiting','snapshot','verifying','retention','stopped'] else None,
                    intervalHours=config.interval//3600,keep=config.keep,nextRun=state.get('nextRun'),
                    lastFailure={key:failure[key] for key in ['at','code']} if failure else None,cleanupWarning=bool(state.get('cleanupWarning')),
                    lastSuccess={key:last.get(key) for key in ['at','files','bytes','snapshotSeconds','writerLockSeconds','durationSeconds','restoreVerified']} if last else None)
        result['overdue']=not last or time.time()-datetime.fromisoformat(last['at']).timestamp()>config.interval+RETRY_SECONDS
        return result
    except FileNotFoundError:return dict(state='not_started')
    except (ValueError,OSError,TypeError,KeyError):return dict(state='unreadable')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['run','snapshot','verify'],nargs='?',default='run')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--database',type=Path)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--drill',type=Path)
    parser.add_argument('--deadline',type=int)
    args=parser.parse_args()
    if args.deadline:
        # A crashed parent cannot leave an orphan holding the school writer lock.
        timer=threading.Timer(args.deadline,lambda:os._exit(124));timer.daemon=True;timer.start()
    try:
        if args.action=='snapshot':create_backup(args.database,args.output)
        elif args.action=='verify':
            restore_backup(args.source,args.output)
            result=restore_backup(args.output,args.drill)
            atomic_json(args.drill.parent/'result.json',dict(files=result['files'],bytes=result['bytes'],writerLockSeconds=result['manifest']['writerLockSeconds']))
        else:
            config=Config.environment(args.database)
            if not config:raise ValueError('Set ATLAS_BACKUP_DIR to enable the worker.')
            worker=Worker(config)
            def stop(*_):worker.stop=True
            signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
            if not worker.run(args.once):
                print(json.dumps(dict(verified=False,failure=worker.state.get('lastFailure'))),file=sys.stderr)
                return 2
            if args.once:
                last=worker.state['lastSuccess']
                print(json.dumps(dict(verified=True,restoreVerified=True,files=last['files'],bytes=last['bytes'])))
    except Exception as exc:
        print(f'Backup worker failed ({type(exc).__name__}); private details omitted.',file=sys.stderr)
        if args.action!='run':
            if isinstance(exc,PermissionError):return 3
            if isinstance(exc,sqlite3.Error):return 4
            if isinstance(exc,ValueError):return 5
            if isinstance(exc,FileNotFoundError):return 6
            if isinstance(exc,RuntimeError):return 8
            return 7
        return 2
    return 0


if __name__=='__main__':sys.exit(main())
