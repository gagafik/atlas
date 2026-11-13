import pytest
from tools import benchmark_journal_reads as tool


def test_seed_only_writes_a_synthetic_temporary_database(tmp_path):
    with pytest.raises(ValueError,match='Synthetic temporary DB'):tool.seed(tmp_path/'school.db',{},5)
