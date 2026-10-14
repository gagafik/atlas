"""Clock-based subject relations and empty teaching periods.

Rows in a bucket belong to one actual pupil, teacher, room or group.
The caller supplies boolean operations so validation and solving use the same
definition of a violation, including room alternatives and fixed lessons.
"""
from collections import defaultdict


def minutes(clock):
    h, m = map(int, clock.split(':'))
    return h * 60 + m


def gap_metrics(rule, day, rows, profiles, any_of=any, all_of=all, negate=lambda x: not x):
    if rule.gapProfileId is None:
        return [], []
    gaps, blocks = [], []
    previous_end = None
    for i, period in enumerate(profiles[rule.gapProfileId].for_day(day)):
        if i in rule.gapExcludedPeriods:
            continue
        start, end = minutes(period.start), minutes(period.end)
        occupied = any_of([r['var'] for r in rows if r['start'] < end and start < r['end']])
        before = any_of([r['var'] for r in rows if r['end'] <= start])
        after = any_of([r['var'] for r in rows if r['start'] >= end])
        gap = all_of([before, after, negate(occupied)])
        if gaps:
            intervening = any_of([r['var'] for r in rows if r['start'] < start and previous_end < r['end']])
            blocks.append(all_of([gap, any_of([negate(gaps[-1]), intervening])]))
        else:
            blocks.append(gap)
        gaps.append(gap)
        previous_end = end
    return gaps, blocks


def relation_violations(rule, rows, any_of=any, all_of=all, negate=lambda x: not x, cancel=None):
    # A lesson has one presence regardless of how many eligible rooms it has.
    slots = defaultdict(list)
    for row in rows:
        slots[row['w'].id, row['day'], row['period']].append(row)
    merged = [{**values[0], 'var': any_of([r['var'] for r in values])} for values in slots.values()]
    for day in sorted({r['day'] for r in merged}):
        day_rows = [r for r in merged if r['day'] == day]
        first = [r for r in day_rows if r['w'].subjectId in rule.subjectIds]
        second = [r for r in day_rows if r['w'].subjectId in rule.otherSubjectIds]
        if rule.relation in ['same_day', 'different_days']:
            a, b = any_of([r['var'] for r in first]), any_of([r['var'] for r in second])
            if rule.relation == 'different_days':
                yield all_of([a, b]), first + second, day
            else:
                yield all_of([a, negate(b)]), first + second, day
                yield all_of([b, negate(a)]), first + second, day
            continue
        for a in first:
            if cancel is not None and cancel.is_set():
                from .rules import RulesCancelled
                raise RulesCancelled()
            for b in second:
                if rule.relation == 'before' and a['end'] > b['start']:
                    yield all_of([a['var'], b['var']]), [a, b], day
                elif rule.relation == 'not_following' and 0 <= b['start']-a['end'] <= rule.relationGapMinutes:
                    between = [r['var'] for r in day_rows if r['start'] >= a['end'] and r['end'] <= b['start']]
                    yield all_of([a['var'], b['var'], negate(any_of(between))]), [a, b], day
