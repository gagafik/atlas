from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from tools import backup_service as service
from tools.backup_school import digest
from tools.restore_school import verify_backup
from test_backup_school import fixture_database


def config(tmp_path,**values):
    source,_,_=fixture_database(tmp_path)
    return service.Config(source,tmp_path/'private-backups',**values)


def test_real_worker_snapshots_verifies_restores_and_retains_only_its_copies(tmp_path):
    c=config(tmp_path,keep=2)
    original=digest(c.database)
    for _ in range(3):assert service.Worker(c).run(once=True)
    state=service.read_json(c.control/'status.json')
    assert state['phase']=='stopped' and len(state['copies'])==2
    assert len(list(c.destination.glob('backup-*')))==2
    assert not list(c.control.glob('backup-*'))
    assert state['lastSuccess']['restoreVerified'] and state['lastSuccess']['files']==1
    assert 0<state['lastSuccess']['writerLockSeconds']<state['lastSuccess']['snapshotSeconds']
    for row in state['copies']:assert verify_backup(c.destination/row['id'])['files']==1
    assert digest(c.database)==original
    with closing(sqlite3.connect(c.database)) as db:
        assert db.execute("SELECT count(*) FROM homework_files WHERE status='uploading'").fetchone()[0]==1


def test_failure_does_not_replace_last_good_copy_and_retry_is_scheduled(tmp_path,monkeypatch):
    c=config(tmp_path)
    assert service.Worker(c).run(once=True)
    old=service.read_json(c.control/'status.json')['lastSuccess']
    (c.database.with_suffix('.files')/'homework'/('a'*32+'.bin')).write_bytes(b'broken')
    assert not service.Worker(c).run(once=True)
    state=service.read_json(c.control/'status.json')
    assert state['lastSuccess']==old and state['lastFailure']['code']=='failed'
    assert state['nextRun']>time.time()+890
    assert verify_backup(c.destination/old['id'])['files']==1
    assert len(list(c.destination.glob('backup-*')))==1


def test_restore_drill_failure_does_not_report_success_or_leave_partial_destination(tmp_path,monkeypatch):
    c=config(tmp_path)
    original=service.Worker.run_child
    def fail(self,phase,args,timeout):
        result=original(self,phase,args,timeout)
        if phase=='verifying':raise RuntimeError('Verification failed')
        return result
    monkeypatch.setattr(service.Worker,'run_child',fail)
    assert not service.Worker(c).run(once=True)
    state=service.read_json(c.control/'status.json')
    assert state['lastSuccess'] is None and not state['copies']
    assert not list(c.destination.glob('backup-*'))


def test_snapshot_timeout_terminates_child_and_does_not_keep_writer_reserved(tmp_path):
    c=config(tmp_path,snapshot_timeout=1)
    with closing(sqlite3.connect(c.database)) as lock:
        lock.execute('BEGIN IMMEDIATE')
        worker=service.Worker(c)
        assert not worker.run(once=True)
        assert worker.child is None
        lock.rollback()
    assert service.read_json(c.control/'status.json')['lastFailure']['code']=='timeout'
    with closing(sqlite3.connect(c.database,timeout=0)) as db:
        db.execute('BEGIN IMMEDIATE');db.rollback()


def test_one_worker_per_database_across_processes_and_release_after_exit(tmp_path):
    c=config(tmp_path)
    code='from pathlib import Path; from tools.backup_service import worker_lock; import sys\nwith worker_lock(Path(sys.argv[1])): print("acquired")'
    with service.worker_lock(c.control):
        result=subprocess.run([sys.executable,'-c',code,str(c.control)],capture_output=True,timeout=10)
        assert result.returncode!=0
    result=subprocess.run([sys.executable,'-c',code,str(c.control)],capture_output=True,timeout=10)
    assert result.returncode==0


@pytest.mark.parametrize('kind',['source_parent','attachments','control','foreign_nonempty','different_owner'])
def test_destination_never_adopts_source_or_unowned_directories(tmp_path,kind):
    c=config(tmp_path)
    targets={'source_parent':c.database.parent,'attachments':c.database.with_suffix('.files'),
             'control':c.control/'backup','foreign_nonempty':c.destination,'different_owner':c.destination}
    target=targets[kind]
    if kind=='foreign_nonempty':target.mkdir();(target/'keep.txt').write_text('private')
    if kind=='different_owner':
        target.mkdir();(target/'atlas-backup-owner.json').write_text('{"database":"other"}')
    with pytest.raises(ValueError):service.Worker(service.Config(c.database,target)).run(once=True)
    assert c.database.is_file()
    if kind=='foreign_nonempty':assert (target/'keep.txt').read_text()=='private'


def test_low_space_fails_the_run_and_preserves_existing_data(tmp_path):
    c=config(tmp_path)
    assert service.Worker(c).run(once=True)
    old=service.read_json(c.control/'status.json')['lastSuccess']
    # A reserve no disk can satisfy leaves too little free space for another copy.
    tight=service.Config(c.database,c.destination,reserve_bytes=1<<60)
    assert not service.Worker(tight).run(once=True)
    state=service.read_json(c.control/'status.json')
    assert state['lastFailure']['code']=='failed' and state['lastSuccess']==old
    assert [row['id'] for row in state['copies']]==[old['id']]
    assert [path.name for path in c.destination.glob('backup-*')]==[old['id']]
    assert verify_backup(c.destination/old['id'])['files']==1


def test_crash_recovery_cleans_only_recorded_incomplete_run_and_retries(tmp_path):
    c=config(tmp_path)
    assert service.Worker(c).run(once=True)
    state=service.read_json(c.control/'status.json');last=state['lastSuccess']
    id='backup-20260101000000-'+'a'*16
    (c.control/id).mkdir();(c.control/id/'partial.bin').write_bytes(b'partial')
    (c.destination/id).mkdir();(c.destination/id/'partial.bin').write_bytes(b'partial')
    state.update(runId=id,phase='snapshot',childDeadline=time.time()-1)
    service.atomic_json(c.control/'status.json',state)
    with service.worker_lock(c.control):
        worker=service.Worker(c);worker.initialize()
        assert worker.state['lastSuccess']==last and worker.state['lastFailure']['code']=='interrupted'
        assert not (c.control/id).exists() and not (c.destination/id).exists()
        assert (c.destination/last['id']).is_dir() and worker.state['nextRun']==0


def test_possible_orphan_deadline_prevents_cleanup(tmp_path):
    c=config(tmp_path);assert service.Worker(c).run(once=True)
    state=service.read_json(c.control/'status.json')
    id='backup-20260101000000-'+'a'*16
    (c.control/id).mkdir()
    state.update(runId=id,childDeadline=time.time()+120)
    service.atomic_json(c.control/'status.json',state)
    with pytest.raises(ValueError,match='child'):service.Worker(c).run(once=True)
    assert (c.control/id).is_dir()


def test_cleanup_refuses_outside_paths_and_symlinks(tmp_path):
    root=tmp_path/'managed';root.mkdir()
    outside=tmp_path/'outside';outside.mkdir();(outside/'keep').write_text('keep')
    with pytest.raises(ValueError):service.managed_remove(root,outside)
    target=root/('backup-20260101000000-'+'b'*16)
    try:target.symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('Symlink creation not available')
    with pytest.raises(ValueError):service.managed_remove(root,target)
    assert (outside/'keep').read_text()=='keep'


def test_tampered_retained_copy_is_not_deleted(tmp_path):
    c=config(tmp_path,keep=2)
    for _ in range(2):assert service.Worker(c).run(once=True)
    state=service.read_json(c.control/'status.json');old=c.destination/state['copies'][0]['id']
    (old/'manifest.json').write_text('{}')
    assert service.Worker(c).run(once=True)
    state=service.read_json(c.control/'status.json')
    assert old.exists() and len(state['copies'])==3 and state['cleanupWarning']
    assert verify_backup(c.destination/state['lastSuccess']['id'])['files']==1


def test_status_is_disabled_or_stale_and_never_exposes_paths(tmp_path,monkeypatch):
    c=config(tmp_path)
    monkeypatch.delenv('ATLAS_BACKUP_DIR',raising=False)
    assert service.public_status(c.database)==dict(state='disabled')
    monkeypatch.setenv('ATLAS_BACKUP_DIR',str(c.destination))
    assert service.public_status(c.database)==dict(state='not_started')
    assert service.Worker(c).run(once=True)
    public=service.public_status(c.database)
    assert public['state']=='stopped' and not public['overdue']
    encoded=json.dumps(public)
    assert str(tmp_path) not in encoded and 'manifestSha256' not in encoded and 'backup-' not in encoded
    state=service.read_json(c.control/'status.json');state.update(phase='waiting',heartbeat=time.time())
    service.atomic_json(c.control/'status.json',state)
    assert service.public_status(c.database)['state']=='running'
    state['heartbeat']=time.time()-60;service.atomic_json(c.control/'status.json',state)
    assert service.public_status(c.database)['state']=='stopped'
