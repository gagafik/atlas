import sys

import pytest
from tools import benchmark_course_table as tool


def run(monkeypatch,*arguments):
    def forbidden(**kwargs):raise AssertionError('The probe must refuse before starting a server.')
    monkeypatch.setattr(tool,'synthetic_server',forbidden)
    monkeypatch.setattr(sys,'argv',['benchmark_course_table',*arguments])
    tool.main()


def test_results_are_refused_inside_the_data_directory(monkeypatch,capsys):
    inside=tool.ROOT/'data'/'course-table.json'
    with pytest.raises(SystemExit) as stop:run(monkeypatch,'--output',str(inside))
    assert stop.value.code==2 and 'data/' in capsys.readouterr().err
    with pytest.raises(SystemExit):run(monkeypatch,'--output',str(tool.ROOT/'data'/'nested'/'..'/'x.json'))


def test_a_course_needs_sixty_teaching_days(monkeypatch,tmp_path):
    with pytest.raises(SystemExit):run(monkeypatch,'--output',str(tmp_path/'x.json'),'--days','10')
