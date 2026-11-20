"""Acceptance tests for issue #1710 新コーナー「建築・建築工学を深掘りする」 (architecture corner).

Covers the MVP acceptance item:

1. テーマ候補を管理できる（一覧から本日のテーマを選べる)

All tests are passive/offline: they load fixed synthetic fixtures and assert on
the theme candidate list and selection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docich.new_corner_runtime import (
    CornerRuntimeError,
    load_theme_candidates,
    list_theme_candidates,
    select_theme,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1]


def test_theme_candidates_can_be_listed():
    candidates = list_theme_candidates("architecture", root=FIXTURE_ROOT)
    assert isinstance(candidates, list) and len(candidates) >= 1
    ids = [c["id"] for c in candidates]
    assert len(ids) == len(set(ids))  # unique ids
    for c in candidates:
        assert set(c.keys()) >= {"id", "title", "domain", "description"}
        assert c["id"] and c["title"] and c["domain"] and c["description"]


def test_theme_candidate_can_be_selected_as_todays_theme():
    selected = select_theme("high-rise-seismic", "architecture", root=FIXTURE_ROOT)
    assert selected["id"] == "high-rise-seismic"
    assert selected["domain"] == "architecture"
    assert "超高層" in selected["title"]


def test_theme_candidate_selection_rejects_unknown_id():
    with pytest.raises(CornerRuntimeError):
        select_theme("no-such-theme", "architecture", root=FIXTURE_ROOT)


def test_theme_candidate_list_loads_from_fixture():
    data = load_theme_candidates("architecture", root=FIXTURE_ROOT)
    assert data.get("isSyntheticFixture") is True
    assert data.get("corner") == "architecture"
    candidates = data["candidates"]
    assert len(candidates) >= 3
    domains = {c["domain"] for c in candidates}
    assert "architecture" in domains
