from datetime import date
from backend.models import DatedLesson, Event, TeacherAbsence, TimetableRule, Placement
from backend.seed import initial_state
from backend.calendar_ops import day_report, substitute_options, SubstituteRequest
from backend.editing import inspect_edit

MON = date(2026,8,17)
TUE = date(2026,8,18)


def fixture():
    s = initial_state('demo'); s.events = []
    s.placements = [Placement(id='english', workloadId='w0', day=0, period=1, roomId='r0'),
                    Placement(id='math', workloadId='w4', day=1, period=2, roomId='r1'),
                    Placement(id='physics', workloadId='w5', day=1, period=2, roomId='r2')]
    return s


def added(s, day=MON, **values):
    return DatedLesson.model_validate(dict(id='extra', name='Разовый', kind='add', onDate=day,
        groupId='g-a', subjectId='sub3', profileId='secondary', startTime='15:00', endTime='15:40',
        teacherIds=['t3'], roomIds=['r0'], **values))


def test_daily_class_limits_include_actual_groups_and_only_affected_pupils():
    s = fixture(); s.rules = [TimetableRule(id='max', name='Не больше 1', kind='daily_load', scope='class', targetIds=['c-demo-a'], maxLessons=1)]
    assert not day_report(s,TUE)['issues']
    changed=s.model_copy(deep=True); changed.datedLessons = [added(s,TUE)]
    result=day_report(changed,TUE)
    assert len(result['issues']) == 2
    assert {id for i in result['issues'] for id in i.get('affectedStudentIds', [])} == {'s1','s2'}
    assert not inspect_edit(s,changed)['canApply']


def test_empty_day_minima_respect_class_holidays_and_teacher_absence_but_not_manual_cancellation():
    s=fixture(); teacher=s.workloads[0].teacherId
    s.rules = [TimetableRule(id='min', name='Минимум', kind='daily_load', scope='class', targetIds=['c-demo-a'], minLessons=1, includeEmptyDays=True),
               TimetableRule(id='teacher', name='Учитель минимум', kind='daily_load', scope='teacher', targetIds=[teacher], minLessons=1, includeEmptyDays=True)]
    s.datedLessons = [DatedLesson(id='cancel', name='Отмена', kind='cancel', onDate=MON, sourceDate=MON, placementId='english')]
    assert len(day_report(s,MON)['issues'])==3
    s.events = [Event(id='holiday', name='Каникулы', start=MON, end=MON, kind='holiday', effect='cancel', classIds=['c-demo-a'])]
    assert not day_report(s,MON)['issues']
    s.events=[]; s.rules=s.rules[1:]
    s.absences=[TeacherAbsence(id='abs', name='Нет', teacherId=teacher, start=MON, end=MON)]
    assert not day_report(s,MON)['issues']


def test_shape_uses_actual_clock_and_soft_penalties_without_blocking_preview():
    s=fixture(); s.rules=[TimetableRule(id='shape', name='Ранний конец', kind='day_shape', scope='class', targetIds=['c-demo-a'], latestEnd='14:00', maxIdleMinutes=60, mode='soft', weight=3)]
    changed=s.model_copy(deep=True); changed.datedLessons=[added(s)]
    result=day_report(changed,MON)
    assert result['warningCount']==4 and result['qualityPenalty']>0
    assert inspect_edit(s,changed)['canApply']
    changed.rules[0].mode='hard'
    assert any('позже' in i['message'] for i in day_report(changed,MON)['issues'])


def test_lunch_and_gap_periods_reuse_reference_bells():
    s=fixture(); s.rules=[TimetableRule(id='lunch', name='Обед', kind='day_shape', scope='teacher', targetIds=['t3'], lunchStart='12:00', lunchEnd='13:00', lunchMinutes=30)]
    extra=added(s); extra.startTime='12:00'; extra.endTime='13:00'; s.datedLessons=[extra]
    assert any('нет перерыва' in i['message'] for i in day_report(s,MON)['issues'])
    s.rules=[TimetableRule(id='gaps', name='Без окон', kind='day_shape', scope='class', targetIds=['c-demo-a'], maxIdlePeriods=0, gapProfileId='secondary')]
    extra.startTime='15:00'; extra.endTime='15:40'
    assert any('пустых учебных периодов' in i['message'] for i in day_report(s,MON)['issues'])


def test_subject_relations_and_workload_scope_survive_dated_replacement():
    s=fixture(); extra=added(s); s.datedLessons=[extra]
    s.rules=[TimetableRule(id='order', name='История раньше английского', kind='relation', scope='class', targetIds=['c-demo-a'], subjectIds=['sub3'], otherSubjectIds=['sub2'], relation='before')]
    assert len([i for i in day_report(s,MON)['issues'] if i['type']=='dated_rule'])==2
    s.rules=[TimetableRule(id='load', name='Нагрузка позже', kind='day_shape', scope='workload', targetIds=['w0'], earliestStart='10:00')]
    assert any(i['ruleId']=='load' for i in day_report(s,MON)['issues'])


def test_added_subject_without_weekly_workload_and_weekend_empty_minima():
    s=fixture(); s.rules=[TimetableRule(id='subject', name='Не больше 0 историй', kind='daily_load', scope='subject', targetIds=['sub3'], maxLessons=0)]
    # Existing History workloads are removed to test a new subject/group pair.
    s.workloads=[w for w in s.workloads if w.subjectId!='sub3']
    s.datedLessons=[added(s)]
    assert len([i for i in day_report(s,MON)['issues'] if i['type']=='dated_rule'])==2
    s.rules=[TimetableRule(id='minimum', name='Минимум', kind='daily_load', scope='class', minLessons=1, includeEmptyDays=True)]
    assert not day_report(s,date(2026,8,22))['issues']


def test_substitute_candidates_respect_teacher_daily_maximum():
    s=fixture(); s.rules=[TimetableRule(id='max', name='Не назначать', kind='daily_load', scope='teacher', targetIds=['t3'], maxLessons=0)]
    options=substitute_options(s,SubstituteRequest(revision=0,day=MON,rowId=f'{MON}:english',teacherId='t2'))
    assert not next(c for c in options['candidates'] if c['id']=='t3')['available']
