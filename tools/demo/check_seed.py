"""Check a seeded demo database through the API at the demo clock.

    python -m tools.demo.check_seed --workdir DIR

Reads DIR/demo.db and DIR/meta.json only; nothing is written (the API's own
bookkeeping aside). Exits non-zero when the school does not look as intended.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import names


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', required=True)
    work = Path(parser.parse_args(argv).workdir).resolve()
    os.environ.update(TIMETABLE_DB=str(work / 'demo.db'), ATLAS_WEB_PUSH_ENABLED='0', ATLAS_BACKUP_DIR='')
    from . import clock
    clock.forget_sessions()
    meta = json.loads((work / 'meta.json').read_text(encoding='utf-8'))
    from . import boot
    api = boot.boot(work, meta['now'])
    clients = {}
    for role, credentials in meta['roles'].items():
        client = boot.client(api)
        response = client.post('/api/auth/login', json=credentials)
        assert response.status_code == 200, (role, response.text)
        clients[role] = client
    failures = []

    def check(label, condition, detail=''):
        print(('ok   ' if condition else 'FAIL ') + label + (f'  {detail}' if detail else ''))
        if not condition:
            failures.append(label)

    def get(role, path, **params):
        response = clients[role].get(path, params=params)
        assert response.status_code == 200, (role, path, response.status_code, response.text[:300])
        return response.json()

    admin, teacher = 'admin', 'teacher'
    schedule = get(admin, '/api/check')
    check('timetable has no issues and every lesson is placed', schedule['issueCount'] == 0 and schedule['placed'] == schedule['required'], f'{schedule["placed"]}/{schedule["required"]}')
    state = get(admin, '/api/state')
    check('school name is marked as fictional', 'Пример' in state['settings']['name'])
    check('people use invented surnames', all(person['name'].split()[0] in names.SURNAMES for person in state['students'] + state['teachers']))
    table = get(teacher, '/api/journal/course-table/courses')
    check('clock stands on the demo day', table['today'] == meta['now'][:10], table['today'])
    day = get(teacher, '/api/journal/day', day=meta['now'][:10])
    check('morning lessons of the demo day are written', any(row['hasPublished'] for row in day['lessons']))
    coverage = clients[admin].post('/api/journal/coverage/report', json=dict(start='2026-10-05', end=meta['now'][:10], status='all')).json()
    counts = coverage['counts']
    check('coverage has complete lessons and real gaps', counts['complete'] > 300 and counts['missing'] + counts['draft'] > 0, str(counts))
    periods = get(admin, '/api/journal/periods')['items']
    check('first period is closed, second is open', sorted(p['status'] for p in periods) == ['closed', 'open'])
    options = get(admin, '/api/final-grades/options')
    book = options['books'][0]['id']
    statuses = set()
    offset = 0
    while offset is not None:
        page = get(admin, f'/api/final-grades/books/{book}', offset=offset)
        statuses |= {item['status'] for item in page['items']}
        offset = page['nextOffset']
    check('final table has published and unpublished rows', 'published' in statuses and statuses - {'published'}, str(sorted(statuses)))
    queue = get(teacher, '/api/journal/homework-queue')
    check('homework queue of the showcase teacher is not empty', len(queue['items']) > 0, str(len(queue['items'])))
    forms = get(admin, '/api/forms/items')
    form = forms['items'][0]['id']
    responses = get(admin, f'/api/forms/items/{form}/responses', limit=50)
    pending = get(admin, f'/api/forms/items/{form}/pending', limit=50, offset=0)
    check('form has answers and families that did not answer', len(responses['items']) > 0 and len(pending['items']) > 0, f'{len(responses["items"])} / {len(pending["items"])}')
    meetings = get(teacher, '/api/meetings/day', onDate='2026-11-03')
    check('consultation day shows the booking of the showcase pupil',
          any(slot['status'] == 'occupied' and slot['booking']['studentId'] == meta['showcase']['pupilId'] for slot in meetings['slots']))
    calendar = get(admin, '/api/calendar/day', day=meta['now'][:10])
    check('substitution appears on the demo day', 'substitution' in json.dumps(calendar))
    inbox = get('parent', '/api/notifications/inbox')
    check('parent inbox is not empty', len(inbox['items']) > 0, str(len(inbox['items'])))
    unread = {role: get(role, '/api/notifications/inbox')['unread'] for role in meta['roles']}
    check('only the latest notifications are unread', all(count <= 3 for count in unread.values()), str(unread))
    names_of_subjects = {subject['name'] for subject in state['subjects']}
    check('Kazakh language and physical education are taught', {'Казахский язык', 'Физкультура'} <= names_of_subjects)
    absences = get(admin, '/api/journal/absences', status='all')
    states = {item['status'] for item in absences['items']}
    check('absence notes exist in several states', len(absences['items']) >= 5 and len(states) >= 2, str(sorted(states)))
    families = get(admin, '/api/access/families')['items']
    check('guardian relationships are verified', families and all(item['verified'] for item in families), str(len(families)))
    pupil_threads = get('student', '/api/messages/threads')['items']
    check('the pupil has a class announcement to read', len(pupil_threads) > 0, str(len(pupil_threads)))
    pupil_quizzes = clients['student'].get('/api/journal/quizzes', params=dict(studentId=meta['showcase']['pupilId']))
    check('pupil can read the course tests', pupil_quizzes.status_code == 200, str(pupil_quizzes.status_code))
    api.shutdown_gradebook_jobs()
    if failures:
        print(f'{len(failures)} check(s) failed')
        return 1
    print('seed checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
