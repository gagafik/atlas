"""Per-teacher lesson facts for dates: who taught, replaced, missed or has yet to teach.

The one place that decides who taught a lesson on a date. A dated change beats a journal publication, and a
publication beats the weekly template (and a closure recorded later). A publication shows the lesson was given only if it was made
from the lesson's start on (its published_at, school time), and only once that start has come by the clock: a topic or homework posted
ahead proves nothing, however long ago the bell rang. A republish after the bell counts from then. Pure apart from moment(): no database, no writes.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .access import now
from .calendar_ops import dates, resolve_day
from .journal import key_for
from .scheduling import minutes

FIELDS = ('planned', 'plannedToDate', 'taught', 'minutes', 'own', 'substitutions', 'extra', 'journalOnly', 'merged', 'missed', 'missedAbsent',
          'uncovered', 'cancelled', 'moved', 'upcoming', 'confirmed', 'differs')
KINDS = ('substitution', 'merge', 'self_study', 'move')
ZONE = ZoneInfo('Asia/Qyzylorda')


def blank():
    return {**dict.fromkeys(FIELDS, 0), 'byType': dict.fromkeys(KINDS, 0)}


def moment():
    """School date and time now, for deciding which lessons have finished."""
    t = now().astimezone(ZONE)
    return t.date(), t.strftime('%H:%M')


def stamped(text):
    """(school date, 'HH:MM') of a stored ISO instant; None when it is missing or unreadable."""
    try: t = datetime.fromisoformat(text)
    except (TypeError, ValueError): return None
    t = (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(ZONE)
    return t.date(), t.strftime('%H:%M')


def given(pub, day, clock):
    """Whether a publication shows its lesson was given: made from the lesson's start on, and that start is not still ahead of the clock.
    A publication with no readable time (an old entry) counts: nothing shows it was early."""
    start = (day, pub['start'])
    if clock is not None and start > clock: return False
    made = stamped(pub.get('publishedAt'))
    return made is None or made >= start


def day_facts(view, ctx, day, clock, pubs):
    """pubs: lesson key -> the publications dated this day, each with publishedAt. clock None treats every day as past."""
    iso = day.isoformat()
    rows = [r for r in resolve_day(view, day, ctx)[0] if r['activity'] == 'lesson' and ctx.maps['groups'][r['groupId']].kind != 'staff']
    live = {r['id']: r for r in rows if r['status'] != 'cancelled'}
    # A publication made before the lesson began (a topic or homework posted ahead) does not show that the lesson was given, not even later.
    pubs = {k: p for k, p in pubs.items() if given(p, day, clock)}
    keys = [key_for(r) for r in rows]
    # A dated change of subject makes a new key: the publication of that slot then belongs to the row of the same group at that time.
    left, taken = {k: p for k, p in pubs.items() if k not in keys}, {}
    for r, key in zip(rows, keys):
        if key in pubs: continue
        match = next((k for k, p in left.items() if p['groupId'] == r['groupId'] and p['start'] < r['end'] and r['start'] < p['end']), None)
        if match: taken[key] = left.pop(match)
    gone = [a for a in view.absences if a.start <= day <= a.end]
    facts = []
    def absent(t, start, end): return any(a.teacherId == t and a.startTime < end and start < a.endTime for a in gone)
    def fact(t, group, subject, start, end, outcome, kind, planned, past, pub, differs):
        return dict(teacherId=t, date=iso, start=start, end=end, minutes=minutes(end) - minutes(start), group=group, subject=subject, outcome=outcome,
                    kind=kind, planned=planned, past=past or bool(pub), confirmed=bool(pub), differs=differs, absent=absent(t, start, end))
    for r, key in zip(rows, keys):
        # A publication of another date (after a move) is not among this day's: it does not confirm this row.
        pub = pubs.get(key) or taken.get(key)
        active, moved = r['status'] != 'cancelled', bool(r.get('movedTo'))
        # A calendar closure is neither planned nor missed.
        closed = not active and (not r['changeId'] or bool(r['excludedClassIds']) and not r['classIds'])
        dated = bool(r['changeId']) and not moved
        teachers = list(r['teacherIds']) if active and (dated or not pub) else list(pub['teacherIds']) if pub and (active or closed) else []
        start, end = (pub['start'], pub['end']) if pub and not dated else (r['start'], r['end'])
        past = clock is None or day < clock[0] or day == clock[0] and end <= clock[1]
        differs = bool(pub) and (closed or not active or set(pub['teacherIds']) != set(r['teacherIds']))
        originals = r['originalTeacherIds'] if r['placementId'] and r['sourceDate'] == r['date'] and not closed else []
        host = live.get(r.get('mergeWith'))
        for t in dict.fromkeys([*originals, *teachers]):
            planned = t in originals
            if t not in teachers: outcome, kind = 'moved' if moved else 'cancelled' if not active else 'replaced', None
            elif host and t in host['teacherIds']: outcome, kind = 'merged', 'merge'
            elif not pub and absent(t, start, end): outcome, kind = 'uncovered', None
            elif not pub and not past: outcome, kind = 'upcoming', None
            else:
                outcome = 'taught'
                # A merge whose host no longer has this teacher was not merged: it is a replacement like any other.
                kind = ('extra' if not r['placementId'] else 'own' if t in r['originalTeacherIds'] else
                        (r['changeType'] if r['changeType'] and r['changeType'] != 'merge' else 'substitution') if r['changeId'] else 'journal')
            facts.append(fact(t, r['group'], r['subject'], start, end, outcome, kind, planned, past, pub, differs))
    for pub in left.values():
        group = ctx.maps['groups'].get(pub['groupId'])
        if group and group.kind == 'staff': continue
        for t in pub['teacherIds']:
            # A dated subject change makes a new key: the old publication must not count the same time twice.
            if any(f['teacherId'] == t and f['outcome'] in ('taught', 'merged') and f['start'] < pub['end'] and pub['start'] < f['end'] for f in facts): continue
            facts.append(fact(t, pub['group'], pub['subject'], pub['start'], pub['end'], 'taught', 'journal', False, True, pub, True))
    return facts


def period_facts(history, first, last, clock, published, names):
    """Facts of every day first..last, each against the template valid that day. names gets the teachers of the versions seen."""
    calendars, by_day = {}, {}
    for key, pub in published.items(): by_day.setdefault(pub['date'], {})[key] = pub
    for day in dates(first, last):
        view, ctx = history.context(day)
        index = history.segment(day)
        # Duties and meetings are not lessons: the resolved day skips their costly rows.
        if index not in calendars: calendars[index] = view.model_copy(update=dict(commitments=[]))
        names.update({t.id: t.name for t in view.teachers})
        yield from day_facts(calendars[index], ctx, day, clock, by_day.get(day.isoformat(), {}))


def add(total, f):
    """Add one fact to a blank() total."""
    total['planned'] += f['planned']
    total['plannedToDate'] += f['planned'] and f['past']
    total['differs'] += f['differs']
    total['confirmed'] += f['confirmed'] and f['outcome'] == 'taught'
    outcome = f['outcome']
    if outcome == 'taught':
        total['taught'] += 1
        total['minutes'] += f['minutes']
        if f['kind'] in ('own', 'extra'): total[f['kind']] += 1
        elif f['kind'] == 'journal': total['journalOnly'] += 1
        else:
            total['substitutions'] += 1
            total['byType'][f['kind']] = total['byType'].get(f['kind'], 0) + 1
    elif outcome == 'merged':
        total['merged'] += 1
        total['byType']['merge'] += 1
    elif outcome == 'upcoming': total['upcoming'] += 1
    elif outcome == 'moved': total['moved'] += 1
    else:
        total['missed'] += f['planned']
        total['cancelled'] += outcome == 'cancelled'
        total['uncovered'] += outcome == 'uncovered'
        total['missedAbsent'] += f['absent'] and f['planned']
