import pytest
from pydantic import ValidationError
from backend.models import Placement, Profile, State
from backend.seed import initial_state
from backend.scheduling import indexes, times, validate_schedule, solve
from backend.editing import EditRequest, edit, inspect_edit


def bell_fixture():
    s = initial_state('demo')
    s.workloads[0].profileId = 'primary'
    s.workloads[0].hours = s.workloads[1].hours = 1
    s.placements = [Placement(id='a', workloadId='w0', day=0, period=1, roomId='r0', locked=True),
                    Placement(id='b', workloadId='w1', day=0, period=1, roomId='r1', locked=True)]
    return s


def test_day_specific_bells_change_validation_and_solver_clock():
    s = bell_fixture()
    assert validate_schedule(s)['issueCount'] == 0
    periods = [p.model_copy() for p in s.profiles[0].periods]
    periods[1].start = '08:50'
    s.profiles[0].dayPeriods = {'0': periods}
    assert times(s.placements[0], indexes(s))[0] == 530
    assert validate_schedule(s)['issueCount'] == 1
    assert solve(s, ['w0', 'w1'], seconds=2)['status'] == 'infeasible'
    s.placements[0].day = s.placements[1].day = 1
    assert times(s.placements[0], indexes(s))[0] == 540
    assert validate_schedule(s)['issueCount'] == 0
    assert solve(s, ['w0', 'w1'], seconds=2)['status'] == 'success'


def test_bell_edit_preview_blocks_new_time_conflict_and_preserves_profile_order():
    s = bell_fixture()
    profile = s.profiles[0].model_copy(deep=True)
    profile.periods[1].start = '08:50'
    candidate = edit(s, EditRequest(revision=0, action='profile', profile=profile))
    assert [p.id for p in candidate.profiles] == [p.id for p in s.profiles]
    result = inspect_edit(s, candidate)
    assert not result['canApply']
    assert result['diff']['changes'][0]['table'] == 'profiles'
    assert s.profiles[0].periods[1].start == '09:00'


def test_invalid_or_overlapping_profiles_and_referenced_deletion_rejected():
    s = bell_fixture()
    profile = s.profiles[0].model_dump()
    profile['dayPeriods'] = {'0': profile['periods'][:1]}
    with pytest.raises(ValidationError):
        Profile.model_validate(profile)
    profile['dayPeriods'] = {}
    profile['periods'][2]['start'] = '09:20'
    with pytest.raises(ValidationError):
        Profile.model_validate(profile)
    with pytest.raises(ValueError):
        edit(s, EditRequest(revision=0, action='removeProfile', ids=['primary']))
    s.profiles[0].dayPeriods = {'6': s.profiles[0].periods}
    with pytest.raises(ValidationError):
        State.model_validate(s.model_dump())
