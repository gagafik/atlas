"""Named, bounded infeasibility analysis on a disposable CP-SAT model clone.

The normal search keeps unconditional constraints. Only a proven infeasible
search adds diagnostic assumptions to its clone. Interval no-overlap constraints
are also supported through optional copies. Diagnostic assignments are never applied.
"""
import threading
import time
from ortools.sat.python import cp_model


class Constraints:
    def __init__(self):
        self.groups = {}

    def add(self, constraint, kind, key, name, **references):
        group = self.groups.setdefault((kind,key), dict(info=dict(kind=kind,id=key,name=name,**references), indices=[]))
        group['indices'].append(constraint.index)
        return constraint

    def explain(self, model, rules, seconds=5, cancel=None):
        copies = sum(len(model.proto.constraints[i].no_overlap.intervals) for g in self.groups.values()
                     for i in g['indices'] if model.proto.constraints[i].has_no_overlap())
        if len(model.proto.constraints) > 250000 or copies > 200000:
            return dict(status='size_limit', reasons=[])
        if cancel is not None and cancel.is_set():
            return dict(status='cancelled', reasons=[])
        diagnostic = model.clone()
        diagnostic.clear_objective()
        deadline = time.monotonic() + min(10,seconds)
        named = {index:dict(kind='rule',id=r.id,name=r.name,ruleIds=[r.id]) for index,r in rules.items()}
        for group in self.groups.values():
            if cancel is not None and cancel.is_set():
                return dict(status='cancelled', reasons=[])
            if time.monotonic() > deadline: return dict(status='timeout', reasons=[])
            gate = diagnostic.new_bool_var('diagnostic:' + group['info']['kind'] + ':' + group['info']['id'])
            diagnostic.add_assumption(gate)
            named[gate.index] = group['info']
            for index in group['indices']:
                constraint = diagnostic.proto.constraints[index]
                if constraint.has_no_overlap():
                    replacements = []
                    for offset, interval_index in enumerate(list(constraint.no_overlap.intervals)):
                        if offset % 256 == 0:
                            if cancel is not None and cancel.is_set(): return dict(status='cancelled',reasons=[])
                            if time.monotonic() > deadline: return dict(status='timeout',reasons=[])
                        original = diagnostic.proto.constraints[interval_index]
                        interval = diagnostic.get_interval_var_from_proto_index(interval_index)
                        literals = [gate]
                        for literal in original.enforcement_literal:
                            value = diagnostic.get_bool_var_from_proto_index(literal if literal >= 0 else -literal-1)
                            literals.append(value if literal >= 0 else value.Not())
                        present = diagnostic.new_bool_var('diagnostic:interval')
                        diagnostic.add_bool_and(literals).only_enforce_if(present)
                        diagnostic.add_bool_or([v.Not() for v in literals] + [present])
                        optional = diagnostic.new_optional_interval_var(interval.start_expr(),interval.size_expr(),interval.end_expr(),present,'diagnostic:interval')
                        replacements.append(optional.index)
                    constraint.no_overlap.intervals.clear()
                    constraint.no_overlap.intervals.extend(replacements)
                else:
                    constraint.enforcement_literal.append(gate.index)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(0.01, min(10,seconds))
        # Core extraction works in a single worker; normal generation uses four.
        solver.parameters.num_search_workers = 1
        done = threading.Event()
        def watch():
            while not done.wait(0.1):
                if cancel is not None and cancel.is_set(): solver.stop_search()
        watcher = threading.Thread(target=watch,daemon=True) if cancel is not None else None
        if watcher: watcher.start()
        try:
            status = solver.solve(diagnostic)
        finally:
            done.set()
            if watcher: watcher.join(timeout=1)
        if cancel is not None and cancel.is_set(): return dict(status='cancelled',reasons=[])
        if status == cp_model.INFEASIBLE:
            core = solver.sufficient_assumptions_for_infeasibility()
            return dict(status='explained',reasons=[named[i] for i in core if i in named])
        return dict(status='timeout' if status == cp_model.UNKNOWN else 'unavailable',reasons=[])
