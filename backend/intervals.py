"""Maximal cliques of optional, fixed clock intervals.

At most one selected interval in each clique is exactly no-overlap for an
interval graph. Endpoints are half-open: a lesson may start at another's end.
"""
from collections import defaultdict


def overlap_cliques(intervals):
    events=defaultdict(lambda: [[],[]])
    for start,end,value in intervals:
        events[start][1].append(value)
        events[end][0].append(value)
    active={};grew=False
    for time in sorted(events):
        ends,starts=events[time]
        if ends and grew:
            if len(active)>1: yield list(active.values())
            grew=False
        for value in ends: active.pop(value.index,None)
        for value in starts: active[value.index]=value
        grew=grew or bool(starts)
