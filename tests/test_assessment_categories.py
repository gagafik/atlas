"""Assessment categories: catalog and policy API, backfill, exact share maths, defaults on save and the calendar link."""
from copy import deepcopy
from datetime import date
from fractions import Fraction
import json
from uuid import uuid4
import pytest
from backend import api
from backend.assessment_categories import DEFAULTS, tally, merge, dump, load, percent_of, policy_for, composite, calendar_category
from test_access import client, enable, create, signed
from test_journal import entry, lesson, write, graded, report
from test_assessment_calendar import url, ok, english, book, listing, D17, D24, D31

CAT = '/api/journal/categories?workspace=demo'


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr('backend.assessment_calendar_routes.today', lambda: date(2026, 8, 10))


def put_config(c, **changes):
    current = c.get(CAT).json()
    body = dict(version=current['version'], reason='Решение завуча', categories=current['categories'], defaultCategory=current['defaultCategory'], policy=current['policy'], rules=current['rules']) | changes
    return c.put(CAT, json=body)


def shares(*values):
    return dict(shares=[dict(categoryId=id, percent=p) for id, p in zip(['fo', 'sor', 'soch'], values)], method='average', missing='renormalize')


def work(id, maxScore=10, weight=1, category='fo', kind='points'):
    return dict(id=id, name=id, kind=kind, maxScore=maxScore, weight=weight, category=category)


def marks(*pairs):
    """(assessment, score) pairs as the lesson's works and one pupil's grades."""
    return [a for a, _ in pairs], [dict(assessmentId=a['id'], score=s, label='', comment='') for a, s in pairs]


def sums_of(*pairs):
    return tally(*marks(*pairs))


def test_defaults_catalog_and_family_view(client):
    enable(client); create(client, 'parent'); create(client, 'editor'); create(client, 'teacher')
    admin = ok(client.get(CAT))
    assert [c['id'] for c in admin['categories']] == ['fo', 'sor', 'soch'] and admin['defaultCategory'] == 'fo' and admin['version'] == 0 and admin['canEdit']
    assert [(s['categoryId'], s['percent']) for s in admin['policy']['shares']] == [('fo', '25'), ('sor', '25'), ('soch', '50')] and admin['rules'] == []
    assert {'sub0', 'sub2'} <= {s['id'] for s in admin['subjects']} and 'c-demo-a' in {c['id'] for c in admin['classes']}
    teacher = ok(signed('teacher').get(CAT))
    assert 'policy' in teacher and 'canEdit' not in teacher and 'subjects' not in teacher
    parent = signed('parent').get(CAT)
    assert parent.status_code == 200 and set(parent.json()) == {'version', 'categories', 'defaultCategory'}
    assert signed('editor').get(CAT).status_code == 403


def edited(current):
    categories = [dict(c, name='ФО1') if c['id'] == 'fo' else c for c in current['categories']] + [dict(id='project', name='Проект', title='Проектная работа', color='#16a34a', calendar='', active=True)]
    policy = dict(shares=[dict(categoryId=id, percent=p) for id, p in [('fo', 20), ('sor', 20), ('soch', 50), ('project', 10)]], method='points', missing='block')
    return categories, policy


def test_admin_saves_categories_with_a_new_version_and_an_audit_entry(client):
    enable(client); current = ok(client.get(CAT)); categories, policy = edited(current)
    saved = ok(put_config(client, categories=categories, policy=policy))
    assert saved['version'] == 1 and saved['categories'][0]['name'] == 'ФО1' and saved['policy']['method'] == 'points'
    assert [c['id'] for c in ok(client.get(CAT))['categories']] == ['fo', 'sor', 'soch', 'project']
    saves = [e for e in client.get('/api/access/audit').json() if e['action'] == 'journal.categories']
    assert [json.loads(e['detail']) for e in saves] == [dict(version=1)]
    # An inactive non-default category is allowed, and only active ones need a unique calendar type.
    ok(put_config(client, categories=[dict(c, active=False) if c['id'] == 'project' else c for c in categories], policy=policy))


def test_a_stale_version_or_a_teacher_cannot_save_the_categories(client):
    enable(client); create(client, 'teacher'); categories, policy = edited(ok(client.get(CAT)))
    ok(put_config(client, categories=categories, policy=policy))
    assert put_config(client, version=0, categories=categories, policy=policy).status_code == 409
    assert put_config(signed('teacher'), categories=categories, policy=policy).status_code == 403
    assert ok(client.get(CAT))['version'] == 1


INVALID_CONFIGS = {
    'shares do not add up': lambda categories, policy: dict(categories=categories, policy=dict(policy, shares=policy['shares'][:3] + [dict(categoryId='project', percent=9)])),
    # Shares are kept in hundredths: the editor refuses more decimals so that it never sends a value like this.
    'too many decimals': lambda categories, policy: dict(categories=categories, policy=dict(policy, shares=[dict(categoryId=id, percent=p) for id, p in [('fo', '20.004'), ('sor', '19.996'), ('soch', '50'), ('project', '10')]])),
    'category dropped': lambda categories, policy: dict(categories=[c for c in categories if c['id'] != 'sor'], policy=dict(shares=[dict(categoryId=id, percent=p) for id, p in [('fo', 50), ('soch', 40), ('project', 10)]], method='average', missing='renormalize')),
    'default category inactive': lambda categories, policy: dict(categories=[dict(c, active=False) if c['id'] == 'fo' else c for c in categories], policy=policy),
    'unknown default category': lambda categories, policy: dict(categories=categories, policy=policy, defaultCategory='nope'),
    'calendar type used twice': lambda categories, policy: dict(categories=[dict(c, calendar='summative_unit') if c['id'] == 'soch' else c for c in categories], policy=policy),
    'rule for an unknown subject': lambda categories, policy: dict(categories=categories, policy=policy, rules=[dict(id=uuid4().hex, name='Правило', subjectIds=['ghost'], classIds=[], **shares(25, 25, 50))]),
}


@pytest.mark.parametrize('case', INVALID_CONFIGS)
def test_an_invalid_category_configuration_is_refused_and_the_stored_one_stays(client, case):
    enable(client); before = ok(client.get(CAT)); categories, policy = edited(before)
    assert put_config(client, **INVALID_CONFIGS[case](categories, policy)).status_code == 422
    assert ok(client.get(CAT)) == before


def test_backfill_is_idempotent_and_marker_guarded(client):
    enable(client); create(client, 'parent'); parent = signed('parent')
    key = lesson(client)['lessonKey']
    record = graded(entry(client, key)); record['document']['content']['assessments'].append(work('second', category='sor'))
    for pupil in record['document']['content']['records']: pupil['grades'].append(dict(assessmentId='second', score=5))
    assert write(client, record, publish=True).status_code == 200
    history = lambda: client.get('/api/journal/history', params=dict(workspace='demo', lessonKey=key)).json()
    before = history()
    # An old database: the works carry no category and the backfill has not run yet.
    with api.connect() as db:
        for column in ['document', 'published_document']:
            document = json.loads(db.execute(f'SELECT {column} FROM journal_entries').fetchone()[0])
            for a in document['content']['assessments']:
                if a['id'] == 'work': a.pop('category')
            db.execute(f'UPDATE journal_entries SET {column}=?', (json.dumps(document, ensure_ascii=False),))
        db.execute("DELETE FROM journal_projection_versions WHERE name='assessment_categories'")
    categories = lambda content: {a['id']: a['category'] for a in content['assessments']}
    saved = entry(client, key)
    assert categories(saved['document']['content']) == {'work': 'fo', 'second': 'sor'}
    assert categories(report(parent).json()['rows'][0]['content']) == {'work': 'fo', 'second': 'sor'}
    assert history() == before
    assert entry(client, key)['document'] == saved['document'] and history() == before


def test_composite_average_vs_points_and_weights():
    sums = sums_of((work('a'), 9), (work('b', 20), 10))
    assert percent_of(sums['fo'], 'average') == 70 and percent_of(sums['fo'], 'points') == Fraction(190, 3)
    sums = sums_of((work('a'), 9), (work('b', 20, 2), 10))
    assert percent_of(sums['fo'], 'average') == Fraction(190, 3) and percent_of(sums['fo'], 'points') == 58
    # Text marks, missing marks and zero-weight works add nothing; fractional maxima stay exact.
    sums = sums_of((work('a', 0.5), 0.25), (work('t', kind='label'), None), (work('z', weight=0), 10), (work('m'), None))
    assert sums['fo']['numeric'] == 1 and percent_of(sums['fo'], 'average') == 50
    assert list(sums_of((work('z', weight=0), 10))) == []
    again = load(json.loads(json.dumps(dump(merge(merge({}, sums), sums_of((work('a', 0.5), 0.5))))))); assert again['fo']['numeric'] == 2 and percent_of(again['fo'], 'average') == 75


def test_composite_shares_missing_and_block():
    policy = policy_for(DEFAULTS, 'sub0', 'c-demo-a')
    full = merge(merge(sums_of((work('f'), 8)), sums_of((work('s', 20, category='sor'), 14))), sums_of((work('c', category='soch'), 6.1)))
    result = composite(full, policy)
    assert result['exact'] == 68 and result['missing'] == [] and result['counted'] == 3 and result['blocked'] is False
    assert [(c['id'], c['percent']) for c in result['categories']] == [('fo', '80'), ('sor', '70'), ('soch', '61')]
    without = {k: v for k, v in full.items() if k != 'soch'}
    result = composite(without, policy)
    assert result['exact'] == 75 and result['missing'] == ['soch'] and result['counted'] == 2
    result = composite(without, dict(policy, missing='block'))
    assert result['exact'] is None and result['blocked'] is True
    assert composite({}, policy)['exact'] is None
    zero_only = merge(sums_of((work('s', 20, category='sor'), 14)), sums_of((work('c', category='soch', weight=0), 6)))
    assert composite(zero_only, policy)['missing'] == ['fo', 'soch'] and composite(zero_only, policy)['exact'] == 70
    # A category without a share is listed and ignored.
    zero_share = dict(policy, shares=[dict(s, share='0') if s['id'] == 'fo' else dict(s, share='50') for s in policy['shares']])
    result = composite(full, zero_share)
    assert result['excluded'] == ['fo'] and result['exact'] == Fraction(131, 2) and result['counted'] == 2
    assert composite(sums_of((work('x', category='gone'), 5)), policy)['excluded'] == ['gone']


def test_policy_for_first_matching_rule():
    cfg = deepcopy(DEFAULTS)
    cfg['categories'].append(dict(id='old', name='Старая', title='Старая', color='#000000', calendar='', active=False))
    rule = lambda name, **ids: dict(id=uuid4().hex, name=name, subjectIds=[], classIds=[], method='points', missing='block', shares=[dict(categoryId='fo', percent='60'), dict(categoryId='ghost', percent='40')]) | ids
    cfg['rules'] = [rule('Правило A', subjectIds=['sub2']), rule('Правило B', classIds=['c-demo-a'])]
    assert policy_for(cfg, 'sub2', 'c-demo-a')['ruleId'] == cfg['rules'][0]['id']
    b = policy_for(cfg, 'sub0', 'c-demo-a')
    assert b['ruleId'] == cfg['rules'][1]['id'] and b['method'] == 'points' and b['missing'] == 'block'
    assert [(s['id'], s['share']) for s in b['shares']] == [('fo', '60'), ('ghost', '40')]
    default = policy_for(cfg, 'sub0', 'other')
    assert default['ruleId'] is None and [(s['id'], s['share']) for s in default['shares']] == [('fo', '25'), ('sor', '25'), ('soch', '50')]
    assert policy_for(cfg, 'sub0', None)['ruleId'] is None


def test_calendar_category_maps_active_types_only():
    cfg = deepcopy(DEFAULTS)
    assert [calendar_category(cfg, c) for c in ['summative_unit', 'summative_term', 'other', '']] == ['sor', 'soch', 'fo', 'fo']
    cfg['categories'][1]['active'] = False
    assert calendar_category(cfg, 'summative_unit') == 'fo'


def saved_with_a_second_work(client, category='sor'):
    record = graded(entry(client, lesson(client)['lessonKey']))
    saved = write(client, record, publish=True); assert saved.status_code == 200, saved.text
    saved = saved.json(); saved['document']['content']['assessments'].append(work('second', category=category))
    for pupil in saved['document']['content']['records']: pupil['grades'].append(dict(assessmentId='second', score=5))
    saved = write(client, saved, publish=True); assert saved.status_code == 200, saved.text
    return saved.json()


def test_a_work_saved_without_a_category_gets_the_default_one(client):
    enable(client); create(client, 'parent')
    record = graded(entry(client, lesson(client)['lessonKey']))
    assert 'category' not in record['document']['content']['assessments'][0]
    saved = write(client, record, publish=True); assert saved.status_code == 200, saved.text
    first = saved.json()['document']['content']['assessments'][0]
    assert first['category'] == 'fo' and 'bookingId' not in first
    assert [a['category'] for a in report(signed('parent')).json()['rows'][0]['content']['assessments']] == ['fo']


@pytest.mark.parametrize('category', ['nope', 'Bad Id'])
def test_an_unknown_or_malformed_category_is_refused_and_the_lesson_stays(client, category):
    enable(client)
    saved = write(client, graded(entry(client, lesson(client)['lessonKey'])), publish=True).json()
    bad = deepcopy(saved); bad['document']['content']['assessments'][0]['category'] = category
    assert write(client, bad).status_code == 422
    assert entry(client, saved['lessonKey'])['document'] == saved['document']


def test_a_disabled_category_keeps_its_old_works_but_is_refused_for_new_ones(client):
    enable(client); saved = saved_with_a_second_work(client)
    current = ok(client.get(CAT)); ok(put_config(client, categories=[dict(c, active=False) if c['id'] == 'sor' else c for c in current['categories']]))
    assert write(client, saved).status_code == 200
    stored = entry(client, saved['lessonKey']); again = deepcopy(stored); again['document']['content']['assessments'].append(work('third', category='sor'))
    assert write(client, again).status_code == 422
    assert entry(client, saved['lessonKey'])['document'] == stored['document']


def test_an_old_client_without_categories_keeps_the_stored_ones_and_new_works_get_the_default(client):
    enable(client); saved = saved_with_a_second_work(client)
    again = entry(client, saved['lessonKey']); again['document']['content']['assessments'].append(work('third', category='sor'))
    again['document']['content']['assessments'][-1].pop('category'); again['document']['content']['assessments'][1].pop('category')
    saved = write(client, again); assert saved.status_code == 200, saved.text
    assert [a['category'] for a in saved.json()['document']['content']['assessments']] == ['fo', 'sor', 'fo']


def test_a_new_work_takes_the_default_category_chosen_by_the_school(client):
    enable(client); saved = saved_with_a_second_work(client)
    ok(put_config(client, defaultCategory='sor'))
    fresh = entry(client, saved['lessonKey']); fresh['document']['content']['assessments'].append(dict(work('fourth'), category=''))
    assert write(client, fresh).json()['document']['content']['assessments'][-1]['category'] == 'sor'


def test_family_report_hides_booking_and_shows_category_summary(client, clock):
    enable(client); create(client, 'parent'); parent = signed('parent')
    booking = ok(english(client, type='soch'))
    record = graded(entry(client, lesson(client)['lessonKey']))
    content = record['document']['content']
    content['assessments'].append(dict(id='term', name='СОЧ', kind='points', maxScore=10, weight=1, bookingId=booking['id']))
    for pupil in content['records']: pupil['grades'].append(dict(assessmentId='term', score=6))
    assert write(client, record, publish=True).status_code == 200
    summary = report(parent).json()
    assert {a['id']: a['category'] for a in summary['rows'][0]['content']['assessments']} == {'work': 'fo', 'term': 'soch'}
    assert 'bookingId' not in json.dumps(summary) and booking['id'] not in json.dumps(summary)
    subject = summary['summary']['subjects'][0]
    assert subject['percentage'] == 70 and subject['sharePercentage'] == 66.67 and subject['rule'] == 'Правило школы по умолчанию'
    assert [(c['id'], c['numeric'], c['percentage']) for c in subject['categories']] == [('fo', 1, 80), ('soch', 1, 60)]
    # The staff entry still carries the link.
    assert [a.get('bookingId') for a in entry(client, record['lessonKey'])['document']['content']['assessments']] == [None, booking['id']]


def journal_link(client, id, **query):
    return next(b for b in ok(client.get(url('bookings', start=D17, end=D31, **query)))['items'] if b['id'] == id)['journalAssessmentId']


def history_of(client, id): return ok(client.get(url(f'bookings/{id}/history')))['items']


def link_work(client, id, work_id='cal'):
    # Saves the English lesson of the booked work with one journal work linked to the booking; returns the saved entry.
    record = entry(client, lesson(client)['lessonKey'])
    record['document']['content']['assessments'] = [dict(id=work_id, name='СОР по календарю', kind='points', maxScore=20, weight=2, bookingId=id)]
    saved = write(client, record); assert saved.status_code == 200, saved.text
    return saved.json()


def booked(client):
    enable(client)
    return ok(english(client))['id']


def test_a_journal_work_linked_to_a_booking_takes_its_category(client, clock):
    id = booked(client)
    booking, = entry(client, lesson(client)['lessonKey'])['bookings']
    assert booking['id'] == id and booking['typeId'] == 'sor' and booking['journalCategory'] == 'sor' and booking['assessmentId'] is None
    saved = link_work(client, id)
    work_ = saved['document']['content']['assessments'][0]
    assert work_['category'] == 'sor' and work_['bookingId'] == id and saved['bookings'][0]['assessmentId'] == 'cal'
    assert journal_link(client, id) == 'cal' and history_of(client, id)[-1]['action'] == 'journal_linked'
    assert next(b for b in listing(client) if b['id'] == id)['version'] == 1


def test_saving_the_linked_lesson_again_writes_no_new_history(client, clock):
    id = booked(client); saved = link_work(client, id); count = len(history_of(client, id))
    assert write(client, saved).status_code == 200
    assert len(history_of(client, id)) == count


def test_the_teacher_may_override_the_category_mapped_from_the_booking(client, clock):
    id = booked(client); saved = link_work(client, id)
    saved['document']['content']['assessments'][0]['category'] = 'soch'
    saved = write(client, saved).json()
    assert saved['document']['content']['assessments'][0]['category'] == 'soch'


def test_removing_the_linked_work_unlinks_the_booking_and_it_can_be_linked_again(client, clock):
    id = booked(client); saved = link_work(client, id)
    removed = deepcopy(saved); removed['document']['content']['assessments'] = []
    for pupil in removed['document']['content']['records']: pupil['grades'] = []
    removed = write(client, removed); assert removed.status_code == 200, removed.text
    assert journal_link(client, id) is None and removed.json()['bookings'][0]['assessmentId'] is None
    assert [h['action'] for h in history_of(client, id)][-2:] == ['journal_linked', 'journal_unlinked']
    relink = deepcopy(removed.json()); relink['document']['content']['assessments'] = [dict(id='cal2', name='СОР', kind='points', maxScore=10, weight=1, bookingId=id)]
    assert write(client, relink).status_code == 200
    assert journal_link(client, id) == 'cal2'


def test_moving_the_booking_to_another_lesson_drops_the_link(client, clock):
    id = booked(client); link_work(client, id); key = lesson(client)['lessonKey']
    moved = client.post(url(f'bookings/{id}/move'), json=dict(requestId=str(uuid4()), version=1, day=D24, lessonId=f'{D24}:eng', reason='Перенос'))
    assert moved.status_code == 200, moved.text
    assert journal_link(client, id) is None
    assert next(h for h in history_of(client, id) if h['action'] == 'moved')['data']['after']['day'] == D24
    stale = entry(client, key); assert stale['bookings'] == [] and stale['document']['content']['assessments'][0]['bookingId'] == id
    # A stale link is not checked again: the lesson stays savable and removing the work leaves the booking alone.
    assert write(client, stale).status_code == 200


def test_a_cancelled_booking_cannot_be_linked_again(client, clock):
    id = booked(client); link_work(client, id); key = lesson(client)['lessonKey']
    cancelled = client.post(url(f'bookings/{id}/cancel'), json=dict(version=1, reason='Отмена')); assert cancelled.status_code == 200, cancelled.text
    before = journal_link(client, id, status='cancelled')
    current = entry(client, key); assert current['bookings'] == []
    # The work keeps its category and bookingId; only a new link to the cancelled booking is refused.
    current = write(client, current); assert current.status_code == 200
    swap = deepcopy(current.json()); first, = swap['document']['content']['assessments']; first.pop('bookingId')
    swap['document']['content']['assessments'].append(dict(id='again', name='Ещё', kind='points', maxScore=10, weight=1, bookingId=id))
    assert write(client, swap).status_code == 422
    assert journal_link(client, id, status='cancelled') == before


def test_a_work_cannot_link_to_a_booking_of_another_lesson(client, clock):
    id = booked(client)
    math = entry(client, lesson(client, 'Математика')['lessonKey'])
    math['document']['content']['assessments'] = [dict(id='alien', name='Чужая', kind='points', maxScore=10, weight=1, bookingId=id)]
    assert write(client, math).status_code == 422 and journal_link(client, id) is None
    math['document']['content']['assessments'] = [dict(id='ok', name='Своя', kind='points', maxScore=10, weight=1)]
    assert write(client, math).status_code == 200


def test_two_works_cannot_link_to_one_booking(client, clock):
    id = booked(client)
    english_lesson = entry(client, lesson(client)['lessonKey'])
    english_lesson['document']['content']['assessments'] = [dict(id='one', name='Раз', kind='points', maxScore=10, weight=1, bookingId=id), dict(id='two', name='Два', kind='points', maxScore=10, weight=1, bookingId=id)]
    assert write(client, english_lesson).status_code == 422 and journal_link(client, id) is None
    english_lesson['document']['content']['assessments'][1].pop('bookingId')
    assert write(client, english_lesson).status_code == 200
    assert journal_link(client, id) == 'one'


def test_a_link_to_an_unknown_booking_is_refused_and_the_old_link_stays(client, clock):
    id = booked(client); saved = link_work(client, id)
    saved['document']['content']['assessments'].append(dict(id='two', name='Два', kind='points', maxScore=10, weight=1, bookingId='0' * 32))
    assert write(client, saved).status_code == 422
    assert journal_link(client, id) == 'cal'
