import sys
from pathlib import Path

import pytest
from tools import benchmark_journal_reads as tool


def run(monkeypatch,*arguments):
    def forbidden(**kwargs):raise AssertionError('The probe must refuse before starting a server.')
    monkeypatch.setattr(tool,'synthetic_server',forbidden)
    monkeypatch.setattr(sys,'argv',['benchmark_journal_reads',*arguments])
    tool.main()


def test_results_are_refused_inside_the_data_directory(monkeypatch,capsys):
    inside=tool.ROOT/'data'/'journal-reads.json'
    with pytest.raises(SystemExit) as stop:run(monkeypatch,'--output',str(inside))
    assert stop.value.code==2 and 'data/' in capsys.readouterr().err
    with pytest.raises(SystemExit):run(monkeypatch,'--output',str(tool.ROOT/'data'/'nested'/'..'/'x.json'))


def test_days_and_readers_are_bounded(monkeypatch,tmp_path):
    for arguments in [['--days','4'],['--days','201'],['--readers','0'],['--readers','21']]:
        with pytest.raises(SystemExit) as stop:run(monkeypatch,'--output',str(tmp_path/'out.json'),*arguments)
        assert stop.value.code==2


def test_seed_only_writes_a_synthetic_temporary_database(tmp_path):
    with pytest.raises(ValueError,match='Synthetic temporary DB'):tool.seed(tmp_path/'school.db',{},5)
