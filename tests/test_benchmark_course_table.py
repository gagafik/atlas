import sys

import pytest
from tools import benchmark_course_table,benchmark_journal_reads


@pytest.mark.parametrize('tool',[benchmark_course_table,benchmark_journal_reads],ids=lambda tool:tool.__name__.rsplit('.',1)[-1])
@pytest.mark.parametrize('relative',[('course-table.json',),('nested','..','x.json')])
def test_results_are_refused_inside_the_data_directory(monkeypatch,tool,relative):
    started=[]
    monkeypatch.setattr(tool,'synthetic_server',lambda **kwargs:started.append(kwargs))
    monkeypatch.setattr(sys,'argv',[tool.__name__,'--output',str(tool.ROOT.joinpath('data',*relative))])
    with pytest.raises(SystemExit) as stop:tool.main()
    assert stop.value.code==2 and not started
