from itertools import combinations
import random
from ortools.sat.python import cp_model
from backend.intervals import overlap_cliques


def test_cliques_exactly_cover_all_overlaps_including_equal_and_touching_times():
    rng=random.Random(715)
    for trial in range(30):
        model=cp_model.CpModel()
        windows=[(0,40),(40,80),(20,60),(0,40)]+[(start:=rng.randrange(0,100),start+rng.randrange(1,60)) for _ in range(20)]
        intervals=[(start,end,model.new_bool_var(str(i))) for i,(start,end) in enumerate(windows)]
        actual={tuple(sorted((a.index,b.index))) for clique in overlap_cliques(intervals) for a,b in combinations(clique,2)}
        expected={(a,b) for a,b in combinations(range(len(windows)),2) if windows[a][0]<windows[b][1] and windows[b][0]<windows[a][1]}
        assert actual==expected
        assert (0,1) not in actual # End and start at 40 do not overlap.
