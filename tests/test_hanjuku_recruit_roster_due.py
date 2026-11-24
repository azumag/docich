"""Regression: recruitment must not postpone an already due roster read."""

import pytest

from docich.hanjuku.flows import month


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, 140),    # no earlier request
        (110, 110),     # earlier castle/return/reinforcement request wins
        (140, 140),     # equal request remains unchanged
        (180, 140),     # a later request may be brought forward
    ],
)
def test_recruit_join_preserves_earliest_roster_due(monkeypatch, existing, expected):
    monkeypatch.setattr(month.chart, "home_castle", lambda chapter: "アルマムーン")
    mem = {"chapter": 1, "tick": 100}
    if existing is not None:
        mem["roster_read_due"] = {"アルマムーン": existing, "別の城": 77}

    month._schedule_recruit_roster_read(mem)

    assert mem["roster_read_due"]["アルマムーン"] == expected
    if existing is not None:
        assert mem["roster_read_due"]["別の城"] == 77


def test_recruit_join_does_not_trust_non_integer_due(monkeypatch):
    monkeypatch.setattr(month.chart, "home_castle", lambda chapter: "アルマムーン")
    mem = {"chapter": 1, "tick": 100, "roster_read_due": {"アルマムーン": "0"}}

    month._schedule_recruit_roster_read(mem)

    assert mem["roster_read_due"]["アルマムーン"] == 140
