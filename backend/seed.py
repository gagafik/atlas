import json
from pathlib import Path
from .models import State


CLASS_NAMES = '0 kaz,0 rus,1A,1B,1D,1E,1F,1G,2A,2D,2E,2F,2G,3A,3D,3E,3F,3G,4A,4D,4E,4F,4G,5A,5D,5E,5F,6A,6D,6E,6F,6G,6H,7A,7D,7E,7F,8A,8D,8E,8F,8G,9A,9D,9E,9F,9G,10A,10D,10E,10F,11A,11B,11D,11E,11F,11G'.split(',')


def base_data():
    times = [('08:00', '08:15'), ('09:00', '09:40'), ('09:45', '10:25'), ('10:30', '11:10'), ('11:20', '12:00'), ('12:50', '13:30'), ('13:40', '14:20'), ('14:25', '15:05'), ('15:15', '15:55'), ('16:00', '16:45'), ('17:00', '17:45')]
    profiles = []
    for id, name in [('primary', 'Начальная школа'), ('secondary', 'Средняя и старшая школа')]:
        slots = times.copy()
        if id == 'secondary':
            slots[1] = ('08:20', '09:00')
            slots[5] = ('12:05', '12:45')
        profiles.append(dict(id=id, name=name, periods=[dict(label=str(i), start=a, end=b) for i, (a, b) in enumerate(slots)]))
    classes = []
    for name in CLASS_NAMES:
        grade = int(''.join(c for c in name if c.isdigit()))
        classes.append(dict(id=f'c-{name}', name=name, maxDaily=10 if grade >= 8 else 8, profileId='secondary' if grade >= 6 else 'primary'))
    events = [dict(id='reading-autumn', name='Reading Week', start='2026-10-19', end='2026-10-23', kind='reading'), dict(id='winter', name='Winter Break', start='2026-12-28', end='2027-01-08', kind='winter'), dict(id='extra', name='Дополнительные каникулы', start='2027-02-08', end='2027-02-12', kind='extra', grades='1 классы'), dict(id='reading-spring', name='Reading Week', start='2027-03-18', end='2027-03-26', kind='reading'), dict(id='exams', name='Экзаменационный период', start='2027-05-31', end='2027-06-11', kind='exam', grades='5–11 классы')]
    for i, day in enumerate(['2026-10-26', '2026-12-16', '2027-01-01', '2027-01-04', '2027-01-07', '2027-03-08', '2027-03-22', '2027-03-23', '2027-03-24', '2027-05-03', '2027-05-07', '2027-05-10']):
        events.append(dict(id=f'holiday-{i}', name='Праздничный день', start=day, end=day, kind='holiday'))
    return dict(classes=classes, profiles=profiles, events=events, groups=[dict(id=f'g-{c["id"]}', name=c['name'], classIds=[c['id']], kind='whole') for c in classes])


def initial_state(workspace='school'):
    imported = Path(__file__).resolve().parent.parent / 'data' / 'imported-school.json'
    if workspace == 'school' and imported.exists():
        return State.model_validate_json(imported.read_text(encoding='utf-8'))
    data = base_data()
    if workspace == 'demo':
        data['classes'] = [dict(id='c-demo-a', name='10A', maxDaily=10, profileId='secondary'), dict(id='c-demo-d', name='10D', maxDaily=10, profileId='secondary')]
        data['groups'] = [dict(id='g-a', name='10A', classIds=['c-demo-a']), dict(id='g-d', name='10D', classIds=['c-demo-d']), dict(id='g-math', name='Математика · группа A', kind='math', classIds=['c-demo-a', 'c-demo-d'], studentIds=['s1', 's3']), dict(id='g-core', name='Физика · группа B', kind='core', classIds=['c-demo-a', 'c-demo-d'], studentIds=['s2', 's4'])]
        data['students'] = [dict(id=f's{i}', name=f'Ученик {i} (пример)', classId='c-demo-a' if i < 3 else 'c-demo-d') for i in range(1, 5)]
        data['teachers'] = [dict(id=f't{i}', name=n) for i, n in enumerate(['Учитель математики', 'Учитель физики', 'Учитель английского', 'Учитель истории'])]
        data['subjects'] = [dict(id=f'sub{i}', name=n, color=c) for i, (n, c) in enumerate([('Математика', '#dceae2'), ('Физика', '#e4e0f3'), ('Английский язык', '#f6e5ce'), ('История', '#dce9f4')])]
        data['rooms'] = [dict(id=f'r{i}', name=str(301 + i)) for i in range(3)]
        data['workloads'] = []
        for i, (group, subject, hours, block) in enumerate([('g-a', 2, 4, ''), ('g-d', 2, 4, ''), ('g-a', 3, 3, ''), ('g-d', 3, 3, ''), ('g-math', 0, 5, 'core-10'), ('g-core', 1, 5, 'core-10')]):
            data['workloads'].append(dict(id=f'w{i}', groupId=group, subjectId=f'sub{subject}', teacherId=f't{subject}', hours=hours, profileId='secondary', roomIds=['r0', 'r1', 'r2'], block=block, allowedPeriods=list(range(1, 9))))
    return State.model_validate(data)
