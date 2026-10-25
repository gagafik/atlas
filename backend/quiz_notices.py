"""What a family reads about a quiz: its title, subject, deadline and this pupil's own result, prepared per recipient."""
from datetime import datetime

from .notifications import ZONE, student_ids

ENDED = {'quiz.close': 'Тест «{}»: приём ответов завершён', 'quiz.withdraw': 'Тест «{}» отозван'}


def local(iso):
    return datetime.fromisoformat(iso).astimezone(ZONE).strftime('%d.%m %H:%M')


def score_text(score, maximum):
    return f"{score:g} из {maximum:g}".replace('.', ',') if score is not None and maximum is not None else None


def deliver_family(notifications, db, ctx, action, users, ids):
    """One entry per account about the assigned pupils it may see; results come one per child, the newest reviewed attempt."""
    workspace, quiz = ctx['workspace'], ctx['quiz']
    document = quiz['published'] or quiz['document']
    title, subject = document['definition']['title'], document['lesson']['subject']
    names = {s['id']: s['name'] for s in document['lesson']['students']}
    base, version = f"quiz:{workspace}:{quiz['id']}:", quiz['version']

    def target(user, text, kids):
        return dict(module='quizzes', recordId=quiz['id'], students=[dict(id=k, name=names.get(k, '')) for k in kids], title=text)

    if action == 'quiz.published':
        text = f"Новый тест «{title}»: {subject}, до {local(document['definition']['closesAt'])}"
        for user in users:
            notifications.deliver(db, [user], workspace, version, action, topic=base + 'published', target=target(user, text, sorted(student_ids(user, workspace) & ids)))
    elif action in ENDED:
        text = ENDED[action].format(title)
        for user in users:
            if action == 'quiz.withdraw':
                # A withdrawn quiz keeps no readable result either.
                notifications.redact(db, [user['id']], base, base + '￿')
            notifications.deliver(db, [user], workspace, version, action, topic=base + 'published', supersede=True, target=target(user, text, sorted(student_ids(user, workspace) & ids)))
    else:
        scores = {}
        for student, score, maximum in db.execute("SELECT student_id,score,max_score FROM quiz_attempts WHERE workspace=? AND quiz_id=? AND status='reviewed' ORDER BY number", (workspace, quiz['id'])):
            scores[student] = score_text(score, maximum)
        opened, corrected = 'Результат теста', 'Исправлен результат теста'
        for user in users:
            for kid in sorted(student_ids(user, workspace) & ids):
                result = scores.get(kid)
                if result: text = f"{corrected if action == 'quiz.results_updated' else opened} «{title}»: {result}"
                else: text = f"Исправлен результат теста «{title}»" if action == 'quiz.results_updated' else f"Открыт разбор теста «{title}»"
                notifications.deliver(db, [user], workspace, version, action, topic=f'{base}{kid}:result', supersede=True, target=target(user, text, [kid]))
