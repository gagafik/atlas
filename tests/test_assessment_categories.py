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
from test_assessment_calendar import url, ok, english, book, D17, D24

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


def stored(db, column='document'):
    return {a['id']: a for a in json.loads(db.execute(f'SELECT {column} FROM journal_entries').fetchone()[0])['content']['assessments']}


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


def test_admin_edit_is_versioned_and_validated(client):
    enable(client); create(client, 'teacher'); current = ok(client.get(CAT))
    categories = [dict(c, name='ФО1') if c['id'] == 'fo' else c for c in current['categories']] + [dict(id='project', name='Проект', title='Проектная работа', color='#16a34a', calendar='', active=True)]
    policy = dict(shares=[dict(categoryId=id, percent=p) for id, p in [('fo', 20), ('sor', 20), ('soch', 50), ('project', 10)]], method='points', missing='block')
    saved = ok(put_config(client, categories=categories, policy=policy))
    assert saved['version'] == 1 and saved['categories'][0]['name'] == 'ФО1' and saved['policy']['method'] == 'points'
    assert [c['id'] for c in ok(client.get(CAT))['categories']] == ['fo', 'sor', 'soch', 'project']
    with api.connect() as db:
        assert db.execute('SELECT version,actor,reason FROM assessment_category_history').fetchall() == [(1, 'Администратор', 'Решение завуча')]
        assert db.execute("SELECT count(*) FROM audit WHERE action='journal.categories'").fetchone()[0] == 1
    stale = put_config(client, version=0, categories=categories, policy=policy)
    assert stale.status_code == 409
    current = ok(client.get(CAT))
    wrong = put_config(client, categories=categories, policy=dict(policy, shares=policy['shares'][:3] + [dict(categoryId='project', percent=9)]))
    assert wrong.status_code == 422
    dropped = put_config(client, categories=[c for c in categories if c['id'] != 'sor'], policy=dict(shares=[dict(categoryId=id, percent=p) for id, p in [('fo', 50), ('soch', 40), ('project', 10)]], method='average', missing='renormalize'))
    assert dropped.status_code == 422 and 'нельзя удалить' in dropped.text
    inactive = [dict(c, active=False) if c['id'] == 'fo' else c for c in categories]
    assert put_config(client, categories=inactive, policy=policy).status_code == 422
    assert put_config(client, categories=categories, policy=policy, defaultCategory='nope').status_code == 422
    twice = [dict(c, calendar='summative_unit') if c['id'] == 'soch' else c for c in categories]
    assert put_config(client, categories=twice, policy=policy).status_code == 422
    assert put_config(client, categories=categories, policy=policy, rules=[dict(id=uuid4().hex, name='Правило', subjectIds=['ghost'], classIds=[], **shares(25, 25, 50))]).status_code == 422
    assert put_config(signed('teacher'), categories=categories, policy=policy).status_code == 403
    assert ok(client.get(CAT))['version'] == current['version']
    # An inactive non-default category is allowed, and only active ones need a unique calendar type.
    ok(put_config(client, categories=[dict(c, active=False) if c['id'] == 'project' else c for c in categories], policy=policy))


def test_backfill_is_idempotent_and_marker_guarded(client):
    key = lesson(client)['lessonKey']
    record = graded(entry(client, key)); record['document']['content']['assessments'].append(work('second', category='sor'))
    for pupil in record['document']['content']['records']: pupil['grades'].append(dict(assessmentId='second', score=5))
    assert write(client, record, publish=True).status_code == 200
    with api.connect() as db:
        history = db.execute('SELECT document FROM journal_history ORDER BY id').fetchall()
        for column in ['document', 'published_document']:
            document = json.loads(db.execute(f'SELECT {column} FROM journal_entries').fetchone()[0])
            for a in document['content']['assessments']:
                if a['id'] == 'work': a.pop('category')
            db.execute(f'UPDATE journal_entries SET {column}=?', (json.dumps(document, ensure_ascii=False),))
        db.execute("DELETE FROM journal_projection_versions WHERE name='assessment_categories'")
    saved = entry(client, key)
    assert {a['id']: a['category'] for a in saved['document']['content']['assessments']} == {'work': 'fo', 'second': 'sor'}
    with api.connect() as db:
        assert stored(db, 'published_document')['work']['category'] == 'fo' and stored(db, 'published_document')['second']['category'] == 'sor'
        assert db.execute('SELECT document FROM journal_history ORDER BY id').fetchall() == history
        assert db.execute("SELECT version FROM journal_projection_versions WHERE name='assessment_categories'").fetchone() == (1,)
        text = db.execute('SELECT document,published_document FROM journal_entries').fetchone()
        db.commit(); db.execute('BEGIN IMMEDIATE'); api.journal.prepare(db)
        assert db.execute('SELECT document,published_document FROM journal_entries').fetchone() == text
        # With the marker present the backfill never runs again, whatever the documents hold.
        document = json.loads(text[0]); document['content']['assessments'][0].pop('category')
        db.execute('UPDATE journal_entries SET document=?', (json.dumps(document, ensure_ascii=False),)); api.journal.prepare(db)
        assert 'category' not in stored(db)['work']


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
    assert policy_for(cfg, 'sub2', 'c-demo-a')['ruleName'] == 'Правило A'
    b = policy_for(cfg, 'sub0', 'c-demo-a')
    assert b['ruleName'] == 'Правило B' and b['ruleId'] == cfg['rules'][1]['id'] and b['method'] == 'points' and b['missing'] == 'block'
    assert b['shares'][0] == dict(id='fo', name='ФО', title='Формативное оценивание', color='#2563eb', share='60') and b['shares'][1]['name'] == 'ghost'
    default = policy_for(cfg, 'sub0', 'other')
    assert default['ruleId'] is None and default['ruleName'] == 'Правило школы по умолчанию' and [s['share'] for s in default['shares']] == ['25', '25', '50']
    assert policy_for(cfg, 'sub0', None)['ruleId'] is None


def test_calendar_category_maps_active_types_only():
    cfg = deepcopy(DEFAULTS)
    assert [calendar_category(cfg, c) for c in ['summative_unit', 'summative_term', 'other', '']] == ['sor', 'soch', 'fo', 'fo']
    cfg['categories'][1]['active'] = False
    assert calendar_category(cfg, 'summative_unit') == 'fo'


def test_save_fills_default_and_rejects_unknown_and_inactive(client):
    enable(client)
    record = graded(entry(client, lesson(client)['lessonKey']))
    assert 'category' not in record['document']['content']['assessments'][0]
    saved = write(client, record, publish=True); assert saved.status_code == 200, saved.text
    saved = saved.json(); first = saved['document']['content']['assessments'][0]
    assert first['category'] == 'fo' and 'bookingId' not in first
    with api.connect() as db: assert stored(db, 'published_document')['work']['category'] == 'fo'
    unknown = deepcopy(saved); unknown['document']['content']['assessments'][0]['category'] = 'nope'
    refused = write(client, unknown); assert refused.status_code == 422 and 'Выберите категорию' in refused.text
    malformed = deepcopy(saved); malformed['document']['content']['assessments'][0]['category'] = 'Bad Id'
    assert write(client, malformed).status_code == 422
    saved['document']['content']['assessments'].append(work('second', category='sor'))
    for pupil in saved['document']['content']['records']: pupil['grades'].append(dict(assessmentId='second', score=5))
    saved = write(client, saved, publish=True); assert saved.status_code == 200, saved.text
    current = ok(client.get(CAT)); ok(put_config(client, categories=[dict(c, active=False) if c['id'] == 'sor' else c for c in current['categories']]))
    again = saved.json(); assert write(client, again).status_code == 200
    again = entry(client, again['lessonKey']); again['document']['content']['assessments'].append(work('third', category='sor'))
    refused = write(client, again); assert refused.status_code == 422 and 'отключена' in refused.text
    # An old client that leaves the category out keeps the stored one for an existing work and gets the default for a new one.
    again['document']['content']['assessments'][-1].pop('category'); again['document']['content']['assessments'][1].pop('category')
    saved = write(client, again); assert saved.status_code == 200, saved.text
    assert [a['category'] for a in saved.json()['document']['content']['assessments']] == ['fo', 'sor', 'fo']
    ok(put_config(client, categories=[dict(c, active=True) if c['id'] == 'sor' else c for c in ok(client.get(CAT))['categories']], defaultCategory='sor'))
    fresh = entry(client, again['lessonKey']); fresh['document']['content']['assessments'].append(dict(work('fourth'), category=''))
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


def linked(db, id):
    return json.loads(db.execute('SELECT document FROM assessment_bookings WHERE id=?', (id,)).fetchone()[0])['journal']['assessmentId']


def actions(db, id):
    return [r[0] for r in db.execute('SELECT action FROM assessment_history WHERE booking_id=? ORDER BY seq', (id,))]


def test_booking_link_category_and_unlink(client, clock):
    enable(client)
    created = ok(english(client)); id = created['id']
    key = lesson(client)['lessonKey']
    record = entry(client, key)
    assert record['bookings'] == [dict(id=id, status=created['status'], typeId='sor', type='Суммативное оценивание за раздел', short='СОР', topic='Дроби',
                                       calendarCategory='summative_unit', journalCategory='sor', assessmentId=None)]
    record['document']['content']['assessments'] = [dict(id='cal', name='СОР по календарю', kind='points', maxScore=20, weight=2, bookingId=id)]
    saved = write(client, record); assert saved.status_code == 200, saved.text
    work_ = saved.json()['document']['content']['assessments'][0]
    assert work_['category'] == 'sor' and work_['bookingId'] == id and saved.json()['bookings'][0]['assessmentId'] == 'cal'
    with api.connect() as db:
        assert linked(db, id) == 'cal' and actions(db, id)[-1] == 'journal_linked'
        count = len(actions(db, id)); version = db.execute('SELECT version FROM assessment_bookings WHERE id=?', (id,)).fetchone()[0]
    assert version == 1
    assert ok(client.get(url('bookings', start=D17, end=D17)))['items'][0]['journalAssessmentId'] == 'cal'
    same = write(client, saved.json()); assert same.status_code == 200
    with api.connect() as db: assert len(actions(db, id)) == count
    # The teacher may override the mapped category of the work.
    saved = same.json(); saved['document']['content']['assessments'][0]['category'] = 'soch'
    saved = write(client, saved).json(); assert saved['document']['content']['assessments'][0]['category'] == 'soch'
    removed = deepcopy(saved); removed['document']['content']['assessments'] = []
    for pupil in removed['document']['content']['records']: pupil['grades'] = []
    removed = write(client, removed); assert removed.status_code == 200, removed.text
    with api.connect() as db: assert linked(db, id) is None and actions(db, id)[-2:] == ['journal_linked', 'journal_unlinked']
    assert removed.json()['bookings'][0]['assessmentId'] is None
    # Relink, then move the booking to another lesson: the link goes.
    relink = deepcopy(removed.json()); relink['document']['content']['assessments'] = [dict(id='cal2', name='СОР', kind='points', maxScore=10, weight=1, bookingId=id)]
    relinked = write(client, relink); assert relinked.status_code == 200, relinked.text
    with api.connect() as db: assert linked(db, id) == 'cal2'
    moved = client.post(url(f'bookings/{id}/move'), json=dict(requestId=str(uuid4()), version=1, day=D24, lessonId=f'{D24}:eng', reason='Перенос'))
    assert moved.status_code == 200, moved.text
    with api.connect() as db:
        assert linked(db, id) is None and json.loads(db.execute("SELECT document FROM assessment_history WHERE booking_id=? AND action='moved'", (id,)).fetchone()[0])['after']['day'] == D24
    assert ok(client.get(url('bookings', start=D24, end=D24)))['items'][0]['journalAssessmentId'] is None
    stale = entry(client, key); assert stale['bookings'] == [] and stale['document']['content']['assessments'][0]['bookingId'] == id
    # A stale link is not checked again: the lesson stays savable and removing the work leaves the booking alone.
    assert write(client, stale).status_code == 200
    # Move back and cancel: the work keeps its category and bookingId, a new link to it is refused.
    back = client.post(url(f'bookings/{id}/move'), json=dict(requestId=str(uuid4()), version=2, day=D17, lessonId=f'{D17}:eng', reason='Назад'))
    assert back.status_code == 200, back.text
    cancelled = client.post(url(f'bookings/{id}/cancel'), json=dict(version=3, reason='Отмена')); assert cancelled.status_code == 200, cancelled.text
    current = entry(client, key); assert current['bookings'] == []
    current = write(client, current); assert current.status_code == 200
    swap = deepcopy(current.json()); first, = swap['document']['content']['assessments']; first.pop('bookingId')
    swap['document']['content']['assessments'].append(dict(id='again', name='Ещё', kind='points', maxScore=10, weight=1, bookingId=id))
    refused = write(client, swap); assert refused.status_code == 422 and 'календаря' in refused.text


def test_booking_link_conflict(client, clock):
    enable(client)
    id = ok(english(client))['id']
    math = entry(client, lesson(client, 'Математика')['lessonKey'])
    math['document']['content']['assessments'] = [dict(id='alien', name='Чужая', kind='points', maxScore=10, weight=1, bookingId=id)]
    refused = write(client, math); assert refused.status_code == 422 and 'не относится к этому уроку' in refused.text
    math['document']['content']['assessments'] = [dict(id='ok', name='Своя', kind='points', maxScore=10, weight=1)]
    assert write(client, math).status_code == 200
    english_lesson = entry(client, lesson(client)['lessonKey'])
    english_lesson['document']['content']['assessments'] = [dict(id='one', name='Раз', kind='points', maxScore=10, weight=1, bookingId=id), dict(id='two', name='Два', kind='points', maxScore=10, weight=1, bookingId=id)]
    duplicate = write(client, english_lesson); assert duplicate.status_code == 422 and 'Одна работа календаря' in duplicate.text
    english_lesson['document']['content']['assessments'][1].pop('bookingId')
    assert write(client, english_lesson).status_code == 200
    with api.connect() as db: assert linked(db, id) == 'one'
    unknown = entry(client, english_lesson['lessonKey']); unknown['document']['content']['assessments'][1]['bookingId'] = '0' * 32
    assert write(client, unknown).status_code == 422
