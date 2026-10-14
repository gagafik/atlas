"""Calendar rotation and isolated views of a weekly scheduling pattern."""
from datetime import timedelta


def active_in(item, cycle_id):
    return not item.cycleIds or cycle_id in item.cycleIds


def week_state(state, cycle_id):
    workloads = [w.model_copy(update={
        'hours': w.cycleHours.get(cycle_id, w.hours),
        'lessonLengths': w.cycleLessonLengths.get(cycle_id, w.lessonLengths),
    }) for w in state.workloads if active_in(w, cycle_id)]
    ids = {w.id for w in workloads}
    rules = []
    for rule in state.rules:
        if not active_in(rule, cycle_id):
            continue
        if rule.scope == 'workload' and rule.targetIds:
            targets = [id for id in rule.targetIds if id in ids]
            if not targets:
                continue
            rule = rule.model_copy(update={'targetIds': targets})
        rules.append(rule)
    return state.model_copy(update={
        'workloads': workloads, 'rules': rules,
        'placements': [p for p in state.placements if p.cycleId == cycle_id],
    })


def pattern_on(state, day):
    """Return the named pattern for a date, or None outside teaching periods.

    Rotation follows calendar weeks, including holidays. Exceptions on actual
    dates are a separate calendar operation and never shift the A/B sequence.
    """
    if not state.settings.yearStart <= day <= state.settings.yearEnd:
        return None
    if state.cycles.periods:
        term = next((p for p in state.cycles.periods if p.start <= day <= p.end), None)
        if term is None:
            return None
        sequence, anchor = term.patternIds, term.anchor
    else:
        sequence = [p.id for p in state.cycles.patterns]
        anchor = state.settings.yearStart - timedelta(days=state.settings.yearStart.weekday())
    return sequence[((day-anchor).days // 7) % len(sequence)]
