import pytest
from pydantic import ValidationError
from backend.seed import initial_state
from backend.models import Placement, TimetableRule as Rule
from backend.optimization import Optimization
from backend.scheduling import solve, validate_schedule
from test_editing import client


def single():
    state = initial_state('demo')
    state.workloads = state.workloads[:1]
    state.workloads[0].hours = 1
    state.workloads[0].allowedPeriods = []
    state.placements = [Placement(id='existing',workloadId='w0',day=0,period=8,roomId='r0')]
    return state


def test_search_priorities_choose_stability_or_earlier_period_without_mutation():
    state = single(); original = state.model_dump_json()
    stable = solve(state,seconds=2,optimization=dict(early=0,stability=100,preferences=1))
    early = solve(state,seconds=2,optimization=dict(early=100,stability=0,preferences=1))
    assert stable['status']==early['status']=='success'
    assert stable['placements']==state.placements
    assert early['placements'][0].period==0
    assert early['placements'][0].id=='existing'
    assert stable['optimization']['after']['stability']==0
    assert early['optimization']['after']['stability']==1
    assert early['optimization']['before']['total']==800
    assert early['optimization']['after']['total']==0
    assert state.model_dump_json()==original


def test_preference_multiplier_and_zero_weights_do_not_disable_hard_rules():
    state = single(); profile=state.profiles[0]
    rule=Rule(id='morning',name='Не первый урок',kind='availability',scope='workload',targetIds=['w0'],mode='soft',weight=1,
              unavailable=[dict(day=d,start=profile.periods[0].start,end=profile.periods[0].end) for d in range(5)])
    state.rules=[rule]
    prioritized=solve(state,seconds=2,optimization=dict(early=1,stability=0,preferences=100))
    assert prioritized['status']=='success', prioritized
    assert prioritized['placements'][0].period==1
    ignored=solve(state,seconds=2,optimization=dict(early=1,stability=0,preferences=0))
    assert ignored['placements'][0].period==0
    rule.mode='hard'
    feasible=solve(state,seconds=2,optimization=dict(early=0,stability=0,preferences=0))
    assert feasible['status']=='success' and feasible['placements'][0].period!=0
    state.placements=feasible['placements']
    assert validate_schedule(state)['issueCount']==0


def test_priority_bounds_and_api_preview_record_the_selected_weights(client):
    for invalid in [dict(early=-1),dict(preferences=1001),dict(stability=1.5),dict(unknown=1)]:
        with pytest.raises(ValidationError): Optimization(**invalid)
    weights=dict(early=0,stability=80,preferences=2)
    before=client.get('/api/state?workspace=demo').json()
    result=client.post('/api/generate?workspace=demo',json=dict(revision=1,preview=True,seconds=2,optimization=weights))
    assert result.status_code==200,result.text
    assert result.json()['optimization']['weights']==weights
    assert client.get('/api/state?workspace=demo').json()==before
    assert client.post('/api/generation-jobs?workspace=demo',json=dict(revision=1,optimization=dict(early=-1))).status_code==422
