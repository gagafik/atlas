from datetime import date
import pytest
from fastapi.testclient import TestClient
from backend import api
from backend.models import State, Commitment, Placement, DatedLesson
from backend.seed import initial_state
from backend.calendar_ops import day_report
from backend.commitments import CommitmentEdit, edit_commitments
from backend.commitments import DutyRequest, allocate_duties
from backend.editing import inspect_edit

MON=date(2026,8,17)


def fixture():
    s=initial_state('demo'); s.events=[]
    s.placements=[Placement(id='english', workloadId='w0', day=0, period=1, roomId='r0')]
    return s


def reservation(**values):
    return Commitment.model_validate(dict(id='book',name='Встреча',onDate=MON,startTime='08:00',endTime='12:00',roomIds=['r0']) | values)


def test_reservation_conflicts_with_room_and_cancellation_frees_it():
    s=fixture(); changed=s.model_copy(deep=True); changed.commitments=[reservation()]
    assert any(i['type']=='dated_collision' for i in day_report(changed,MON)['issues'])
    assert not inspect_edit(s,changed)['canApply']
    changed.datedLessons=[DatedLesson(id='c',name='Нет урока',kind='cancel',placementId='english',sourceDate=MON,onDate=MON)]
    assert not day_report(changed,MON)['issues']
    assert s.commitments==[]


def test_multiple_groups_keep_exact_members_and_teacher_busy_checks():
    s=fixture(); s.commitments=[reservation(roomIds=['r1'],groupIds=['g-math','g-core'])]
    report=day_report(s,MON)
    assert set(next(r for r in report['rows'] if r.get('commitmentId'))['studentIds'])=={'s1','s2','s3','s4'}
    assert set(report['issues'][0]['affectedStudentIds'])=={'s1','s2'}
    s.commitments=[reservation(roomIds=['r1'],teacherIds=['t2'],profileId='secondary')]
    assert any(i.get('teacherIds')==['t2'] for i in day_report(s,MON)['issues'])


def test_empty_duty_is_saved_as_unfilled_and_location_building_supports_travel():
    s=fixture(); s.commitments=[reservation(kind='duty',roomIds=[],location='Коридор',requiredTeachers=2,profileId='secondary')]
    report=day_report(s,MON)
    assert report['unfilledDuties']==2 and not report['completeChecks']
    s.commitments[0].teacherIds=['t0']
    assert day_report(s,MON)['unfilledDuties']==1


def test_repeated_creation_and_locks_are_atomic():
    s=fixture(); value=reservation(startTime='17:00',endTime='18:00')
    result=edit_commitments(s,CommitmentEdit(revision=0,value=value,copies=3,weekStep=2))
    assert [x.onDate.isoformat() for x in result.commitments]==['2026-08-17','2026-08-31','2026-09-14']
    assert len({x.id for x in result.commitments})==3
    result.commitments[0].locked=True
    with pytest.raises(ValueError,match='закрепление'): edit_commitments(result,CommitmentEdit(revision=0,removeIds=['book']))
    invalid=value.model_copy(update={'onDate':date(2027,6,10)})
    with pytest.raises(ValueError): edit_commitments(s,CommitmentEdit(revision=0,value=invalid,copies=2))
    assert not s.commitments


def test_reference_validation_and_eligibility():
    s=fixture(); s.commitments=[reservation(roomIds=['absent'])]
    with pytest.raises(ValueError): State.model_validate(s.model_dump())
    with pytest.raises(ValueError): reservation(kind='duty',requiredTeachers=1,profileId='secondary',teacherIds=['t0'],eligibleTeacherIds=['t1'])


def test_api_preview_does_not_save_and_rejects_new_collision(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'DB',tmp_path/'bookings.db'); client=TestClient(api.app)
    client.get('/api/state?workspace=demo'); s=api.save('demo',fixture())
    result=client.post('/api/commitments/preview?workspace=demo',json=dict(revision=s.revision,value=reservation().model_dump(mode='json'))).json()
    assert not result['canApply'] and result['diff']['added']==1
    assert not client.get('/api/state?workspace=demo').json()['commitments']
    result=client.post('/api/commitments/preview?workspace=demo',json=dict(revision=s.revision,value=reservation(startTime='17:00',endTime='18:00').model_dump(mode='json'))).json()
    assert result['canApply']
    applied=client.post('/api/editor/apply?workspace=demo',json=dict(revision=s.revision,token=result['token'])).json()
    assert len(applied['commitments'])==1 and applied['placements']==s.model_dump(mode='json')['placements']
    assert client.post('/api/commitments/preview?workspace=demo',json=dict(revision=s.revision,removeIds=['book'])).status_code==409


def duty(id, start='16:00', end='16:30', **values):
    return reservation(id=id,kind='duty',roomIds=[],location='Коридор',profileId='secondary',requiredTeachers=1,startTime=start,endTime=end,**values)


def test_duty_allocation_balances_minutes_and_preserves_unselected_and_locked():
    s=fixture(); s.commitments=[duty('fixed','12:00','13:00',teacherIds=['t0'],locked=True),duty('a'),duty('b','16:30','17:00')]
    before=s.model_dump()
    result=allocate_duties(s,DutyRequest(revision=0,commitmentIds=['fixed','a','b'],seconds=2))
    assert result['status']=='success' and result['assigned']==2 and result['locked']==1
    candidate=result['candidate']; assert candidate.commitments[0]==s.commitments[0]
    new=[x.teacherIds[0] for x in candidate.commitments[1:]]
    assert len(set(new))==2 and 't0' not in new
    assert s.model_dump()==before and candidate.placements==s.placements


def test_duty_allocation_uses_actual_occupancy_absences_and_eligibility():
    from backend.models import TeacherAbsence
    s=fixture(); s.commitments=[duty('a','08:00','12:00',eligibleTeacherIds=['t0','t2'])]
    # t2 teaches English; the remaining eligible teacher is absent.
    s.absences=[TeacherAbsence(id='absence',name='Нет',teacherId='t0',start=MON,end=MON)]
    result=allocate_duties(s,DutyRequest(revision=0,commitmentIds=['a'],seconds=1))
    assert result['status']=='infeasible'
    s.absences=[]
    result=allocate_duties(s,DutyRequest(revision=0,commitmentIds=['a'],seconds=1))
    assert result['candidate'].commitments[0].teacherIds==['t0']


def test_overlapping_duties_and_minutes_limit_make_insufficient_staff_infeasible():
    s=fixture(); s.commitments=[duty('a',eligibleTeacherIds=['t0']),duty('b',eligibleTeacherIds=['t0'])]
    assert allocate_duties(s,DutyRequest(revision=0,commitmentIds=['a','b'],seconds=1))['status']=='infeasible'
    s.commitments[1].startTime='16:30'; s.commitments[1].endTime='17:00'
    assert allocate_duties(s,DutyRequest(revision=0,commitmentIds=['a','b'],seconds=1,maxMinutesPerTeacher=30))['status']=='infeasible'
    assert allocate_duties(s,DutyRequest(revision=0,commitmentIds=['a','b'],seconds=1,maxMinutesPerTeacher=60))['status']=='success'


def test_duty_capacity_is_independently_checked_with_known_staff_lower_bound():
    s=fixture(); s.rooms[0].capacity=1
    s.commitments=[reservation(id='a',kind='duty',profileId='secondary',requiredTeachers=2,startTime='16:00',endTime='16:30')]
    result=allocate_duties(s,DutyRequest(revision=0,commitmentIds=['a'],seconds=1))
    assert result['status']=='blocked' and any('вместимость' in d for d in result['details'])
    assert not s.commitments[0].teacherIds


def test_duty_travel_allows_a_real_intermediate_assignment():
    s=fixture()
    s.campus=s.campus.model_validate(dict(buildings=[dict(id='a',name='A'),dict(id='b',name='B'),dict(id='c',name='C')],
        transfers=[dict(fromId='a',toId='c',minutes=60)],defaultTravelMinutes=0))
    s.rooms[0].buildingId='a'
    p=s.placements[0]; w=s.workloads[0]
    end=next(x for x in s.profiles if x.id==w.profileId).for_day(0)[p.period].end
    from backend.rules import minutes
    def clock(n): return f'{n//60:02}:{n%60:02}'
    finish=minutes(end)
    s.commitments=[duty('target',clock(finish+15),clock(finish+30),buildingId='c',eligibleTeacherIds=['t2'])]
    assert allocate_duties(s,DutyRequest(revision=0,commitmentIds=['target'],seconds=1))['status']=='infeasible'
    s.commitments.append(duty('between',clock(finish+5),clock(finish+10),buildingId='b',eligibleTeacherIds=['t2']))
    result=allocate_duties(s,DutyRequest(revision=0,commitmentIds=['target','between'],seconds=1))
    assert result['status']=='success' and not result['issues']


def test_duty_api_returns_preview_only_and_checks_revision(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'DB',tmp_path/'duties.db'); client=TestClient(api.app)
    client.get('/api/state?workspace=demo'); s=fixture(); s.commitments=[duty('a')]; s=api.save('demo',s)
    body=dict(revision=s.revision,commitmentIds=['a'],seconds=1)
    result=client.post('/api/commitments/allocate?workspace=demo',json=body).json()
    assert result['status']=='success' and result['preview']['canApply']
    assert not client.get('/api/state?workspace=demo').json()['commitments'][0]['teacherIds']
    applied=client.post('/api/editor/apply?workspace=demo',json=dict(revision=s.revision,token=result['preview']['token'])).json()
    assert len(applied['commitments'][0]['teacherIds'])==1
    assert client.post('/api/commitments/allocate?workspace=demo',json=body).status_code==409


def test_duties_preserve_teacher_lunch_and_allocator_finds_another_person():
    from backend.models import TimetableRule
    s=fixture(); s.rules=[TimetableRule(id='lunch',name='Обед',kind='day_shape',scope='teacher',targetIds=['t0'],lunchStart='12:00',lunchEnd='13:00',lunchMinutes=30)]
    s.commitments=[duty('a','12:00','13:00',teacherIds=['t0'],eligibleTeacherIds=['t0','t1'])]
    assert any(i['type']=='dated_lunch' and i['teacherIds']==['t0'] for i in day_report(s,MON)['issues'])
    s.commitments[0].teacherIds=[]
    result=allocate_duties(s,DutyRequest(revision=0,commitmentIds=['a'],seconds=1))
    assert result['status']=='success' and result['candidate'].commitments[0].teacherIds==['t1']
    assert not result['issues']
    s.commitments[0].eligibleTeacherIds=['t0']
    assert allocate_duties(s,DutyRequest(revision=0,commitmentIds=['a'],seconds=1))['status']=='infeasible'


def test_two_duties_cannot_jointly_consume_required_break_and_soft_rule_is_visible():
    from backend.models import TimetableRule
    s=fixture(); s.rules=[TimetableRule(id='lunch',name='Обед',kind='day_shape',scope='teacher',targetIds=['t0'],lunchStart='12:00',lunchEnd='13:00',lunchMinutes=30)]
    s.commitments=[duty('a','12:00','12:20',eligibleTeacherIds=['t0']),duty('b','12:40','13:00',eligibleTeacherIds=['t0'])]
    assert allocate_duties(s,DutyRequest(revision=0,commitmentIds=['a','b'],seconds=1))['status']=='infeasible'
    s.rules[0].mode='soft';s.rules[0].weight=7
    result=allocate_duties(s,DutyRequest(revision=0,commitmentIds=['a','b'],seconds=1))
    assert result['status']=='success'
    report=day_report(result['candidate'],MON)
    assert report['warningCount']==1 and report['qualityPenalty']==7


def test_group_booking_respects_individual_lunch_and_is_not_counted_as_a_lesson():
    from backend.models import TimetableRule
    s=fixture();s.rules=[TimetableRule(id='lunch',name='Обед',kind='day_shape',scope='class',targetIds=['c-demo-a'],lunchStart='12:00',lunchEnd='13:00',lunchMinutes=30),
                         TimetableRule(id='load',name='Один урок',kind='daily_load',scope='class',targetIds=['c-demo-a'],maxLessons=1)]
    s.commitments=[reservation(startTime='12:00',endTime='13:00',roomIds=['r1'],groupIds=['g-math'])]
    report=day_report(s,MON)
    assert len(report['issues'])==1 and report['issues'][0]['affectedStudentIds']==['s1']
    assert report['issues'][0]['type']=='dated_lunch'


def test_complete_mixed_group_only_lists_actual_pupils_classes():
    s=fixture();next(g for g in s.groups if g.id=='g-math').studentIds=['s1']
    s.placements=[Placement(id='math',workloadId='w4',day=0,period=1,roomId='r0')]
    row=day_report(s,MON)['rows'][0]
    assert row['originalClassIds']==row['classIds']==['c-demo-a']


def test_manual_edit_requires_unlock_and_commitments_roundtrip():
    from backend.exporting import state_xml, read_state_xml, workbook, ExportRequest, TABLES
    from backend.tabular import parse_file
    from backend.importing import mapped_state, MappingRequest
    s=fixture();s.commitments=[duty('a',teacherIds=['t0'],locked=True),reservation(startTime='17:00',endTime='18:00',groupIds=['g-math','g-core'],studentCount=10,note='Текст')]
    value=s.commitments[0].model_copy(update={'name':'Новое название'})
    with pytest.raises(ValueError,match='закрепление'): edit_commitments(s,CommitmentEdit(revision=0,value=value))
    value.locked=False
    assert edit_commitments(s,CommitmentEdit(revision=0,value=value)).commitments[-1].name=='Новое название'
    assert read_state_xml(state_xml(s))==s
    book=parse_file('book.xlsx',workbook(s,ExportRequest(revision=0,format='xlsx_data')))
    number=next(i for i,x in enumerate(book['sheets']) if x['name']==TABLES['commitments'])
    sheet=book['sheets'][number]
    request=MappingRequest(revision=0,token='test',table='commitments',sheet=number,mapping={h:i for i,h in enumerate(sheet['rows'][0])},skipBlanks=False)
    result=mapped_state(s,book,request)
    assert result['status']=='success' and result['candidate']==s
