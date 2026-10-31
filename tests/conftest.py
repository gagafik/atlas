import pytest
from backend import journal


@pytest.fixture(autouse=True)
def lesson_clock(monkeypatch):
    """A lesson of today has always begun, whatever the real time of day: tests that pin journal.today to a lesson day must not depend on the clock."""
    monkeypatch.setattr(journal,'minute',lambda:'23:59')
