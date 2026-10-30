import multiprocessing as mp
import os
import threading
import time

from backend.optimization import Optimization
from backend.seed import initial_state
from backend.scheduling import validate_schedule
from backend.solver_process import solve_isolated, configured_workers


def stuck_child(document, ids, seconds, options, cancel, channel):
    channel.send(('progress',{'phase':'searching','pid':os.getpid()}))
    while True: time.sleep(.1)  # Ignores cooperative cancellation on purpose.


def crashed_child(document, ids, seconds, options, cancel, channel):
    os._exit(23)


def test_real_child_solves_and_preserves_input_with_progress():
    state=initial_state('demo'); before=state.model_dump_json(); progress=[]
    result=solve_isolated(state,seconds=2,progress=progress.append,optimization=Optimization(early=0,stability=0,preferences=0))
    assert result['status']=='success',result
    assert any(p.get('execution')=='process' for p in progress)
    assert state.model_dump_json()==before
    candidate=state.model_copy(update={'placements':result['placements']})
    assert validate_schedule(candidate)['issues']==[]
    assert not [p for p in mp.active_children() if p.name=='atlas-generator']


def test_pre_cancel_and_force_cancel_reap_the_owned_child():
    event=threading.Event(); event.set()
    assert solve_isolated(initial_state('demo'),cancel=event)['status']=='cancelled'
    event.clear(); pids=[]
    def progress(value): pids.append(value['pid']); event.set()
    result=solve_isolated(initial_state('demo'),cancel=event,progress=progress,_target=stuck_child,_cancel_grace=.1)
    assert result['status']=='cancelled' and pids[0]!=os.getpid()
    assert not [p for p in mp.active_children() if p.pid in pids]


def test_wall_timeout_and_abrupt_crash_leave_no_running_child():
    result=solve_isolated(initial_state('demo'),_target=stuck_child,_wall_seconds=.2)
    assert result['status']=='timeout'
    result=solve_isolated(initial_state('demo'),_target=crashed_child)
    assert result['status']=='failed'
    assert not [p for p in mp.active_children() if p.name=='atlas-generator']


def test_worker_config_is_bounded_and_invalid_value_uses_safe_default(monkeypatch):
    monkeypatch.setattr(os,'cpu_count',lambda:8)
    monkeypatch.delenv('ATLAS_SOLVER_WORKERS',raising=False)
    assert configured_workers()==2
    monkeypatch.setenv('ATLAS_SOLVER_WORKERS','1000')
    assert configured_workers()==8
    monkeypatch.setenv('ATLAS_SOLVER_WORKERS','invalid')
    assert configured_workers()==2
    monkeypatch.setenv('ATLAS_SOLVER_WORKERS','0')
    assert configured_workers()==1
