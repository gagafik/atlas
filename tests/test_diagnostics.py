import threading
from ortools.sat.python import cp_model
from backend.diagnostics import Constraints
from backend.seed import initial_state
from backend.models import Placement
from backend.scheduling import solve


def test_resource_core_names_two_locked_lessons_and_the_shared_teacher():
    state=initial_state('demo');state.workloads=state.workloads[:2]
    for w in state.workloads: w.hours=1
    state.placements=[Placement(id='a',workloadId='w0',day=0,period=1,roomId='r0',locked=True),
                      Placement(id='b',workloadId='w1',day=0,period=1,roomId='r1',locked=True)]
    original=state.model_dump_json()
    result=solve(state,seconds=2)
    assert result['status']=='infeasible' and result['diagnosis']['status']=='explained'
    core={(r['kind'],r['id']) for r in result['diagnosis']['reasons']}
    assert {('resource','teacher:t2'),('lock','a'),('lock','b')} <= core
    assert state.model_dump_json()==original
    state.placements[1].locked=False
    assert solve(state,seconds=2)['status']=='success'


def test_empty_availability_domain_explains_weekly_demand():
    state=initial_state('demo');state.workloads=state.workloads[:1];state.placements=[]
    teacher=next(t for t in state.teachers if t.id==state.workloads[0].teacherId)
    teacher.unavailable=[f'{d}:{p}' for d in range(5) for p in range(11)]
    result=solve(state,seconds=2)
    assert result['status']=='infeasible'
    reason=next(r for r in result['diagnosis']['reasons'] if r['kind']=='hours')
    assert reason['workloadIds']==['w0'] and '0 доступных позиций' in reason['name']


def test_synchronous_groups_with_one_shared_pupil_report_block_and_pupil():
    state=initial_state('demo');state.groups[-1].studentIds.append('s1')
    result=solve(state,seconds=2)
    assert result['status']=='infeasible'
    core=result['diagnosis']['reasons']
    assert any(r['kind']=='block' for r in core)
    assert any(r.get('resource')=='student:s1' for r in core)


def test_diagnostic_clone_preserves_constraint_model_and_cancellation():
    model=cp_model.CpModel(); named=Constraints()
    a=model.new_bool_var('a');b=model.new_bool_var('b')
    i=model.new_optional_fixed_size_interval_var(0,40,a,'a')
    j=model.new_optional_fixed_size_interval_var(0,40,b,'b')
    named.add(model.add(a==1),'lock','a','A')
    named.add(model.add(b==1),'lock','b','B')
    named.add(model.add_no_overlap([i,j]),'resource','room','Room')
    before=str(model.proto)
    result=named.explain(model,{},2)
    assert {r['id'] for r in result['reasons']}=={'a','b','room'}
    assert str(model.proto)==before
    cancel=threading.Event();cancel.set()
    assert named.explain(model,{},2,cancel)['status']=='cancelled'


def test_cancel_during_explanation_returns_cancelled_without_solution():
    state=initial_state('demo');state.groups[-1].studentIds.append('s1')
    cancel=threading.Event()
    def progress(value):
        if value['phase']=='diagnosing': cancel.set()
    result=solve(state,seconds=2,cancel=cancel,progress=progress)
    assert result['status']=='cancelled' and 'placements' not in result


def test_identical_pupil_calendars_share_a_constraint_without_weakening_it():
    state=initial_state('demo');state.workloads=[state.workloads[0],state.workloads[2]]
    for w in state.workloads: w.hours=1
    state.placements=[Placement(id='a',workloadId='w0',day=0,period=1,roomId='r0',locked=True),
                      Placement(id='b',workloadId='w2',day=0,period=1,roomId='r1',locked=True)]
    result=solve(state,seconds=2)
    assert result['status']=='infeasible'
    pupils=next(r for r in result['diagnosis']['reasons'] if r.get('resource')=='student:s1')
    assert {'student:s1','student:s2'}<=set(pupils['equivalentResources'])
    state.placements[1].locked=False
    assert solve(state,seconds=2)['status']=='success'
