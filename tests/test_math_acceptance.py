"""Acceptance tests for issue #1715 新コーナー「問いから入る数学」 (math corner).

Covers the MVP acceptance items:

1. テーマ候補を管理できる（一覧から本日のテーマを選べる)
2. 1テーマについて一次情報を収集した原稿が生成できる
3. 「問い → 予想 → 観測 → 小さい例 → モデル → 一般化 → 実世界応用 → 直感的まとめ」
   の原稿が生成できる
4. 出典を保持する
5. 配信用 overlay へ章・図・要点を表示できる
6. コーナーを自動進行できる
7. YouTube 切り出しを想定した独立原稿を生成できる（MVP)

All tests are passive/offline: they load fixed synthetic fixtures and assert on
the structured draft, overlay payload, progression frames, and theme candidates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docich.new_corner_runtime import (
    CornerRuntimeError,
    build_draft,
    load_corner_fixture,
    load_theme_candidates,
    list_theme_candidates,
    progress_frames,
    render_overlay,
    select_theme,
    validate_status,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    FIXTURE_ROOT
    / "tests"
    / "fixtures"
    / "math_corner"
    / "fixed_math_example.json"
)

# The 8 required sections: the design doc's 7-step narrative
# (問い → 予想 → 小さい例 → モデル → 一般化 → 実世界応用 → 直感的まとめ) plus the
# actually-observed phenomenon recorded between the prediction and the example.
NARRATIVE_SECTIONS = (
    "todaysQuestion",
    "intuitivePrediction",
    "observedPhenomenon",
    "smallExample",
    "mathematicalModel",
    "generalization",
    "realWorldApplication",
    "intuitiveSummary",
)


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. テーマ候補を管理できる（一覧から本日のテーマを選べる)
# ---------------------------------------------------------------------------

def test_theme_candidates_can_be_listed():
    candidates = list_theme_candidates("math", root=FIXTURE_ROOT)
    assert isinstance(candidates, list) and len(candidates) >= 1
    ids = [c["id"] for c in candidates]
    assert len(ids) == len(set(ids))  # unique ids
    for c in candidates:
        assert set(c.keys()) >= {"id", "title", "domain", "description"}
        assert c["id"] and c["title"] and c["domain"] and c["description"]


def test_theme_candidate_can_be_selected_as_todays_theme():
    selected = select_theme("birthday-paradox", "math", root=FIXTURE_ROOT)
    assert selected["id"] == "birthday-paradox"
    assert selected["domain"] == "probability-combinatorics"
    assert "誕生日" in selected["title"]


def test_theme_candidate_selection_rejects_unknown_id():
    with pytest.raises(CornerRuntimeError):
        select_theme("no-such-theme", "math", root=FIXTURE_ROOT)


def test_theme_candidate_list_loads_from_fixture():
    data = load_theme_candidates("math", root=FIXTURE_ROOT)
    assert data.get("isSyntheticFixture") is True
    assert data.get("corner") == "math"
    candidates = data["candidates"]
    assert len(candidates) >= 3  # multiple math fields available
    domains = {c["domain"] for c in candidates}
    assert "probability-combinatorics" in domains


# ---------------------------------------------------------------------------
# 2. 1テーマについて一次情報を収集した原稿が生成できる
# ---------------------------------------------------------------------------

def _section_text_nonempty(record: dict) -> bool:
    text = record["text"]
    if isinstance(text, str):
        return bool(text.strip())
    if isinstance(text, (list, tuple)):
        return bool(text) and all(isinstance(t, str) and t.strip() for t in text)
    return bool(text)


def test_draft_generated_for_one_theme_from_primary_sources():
    data = load_corner_fixture("math", root=FIXTURE_ROOT)
    draft = build_draft(data)
    assert isinstance(draft, tuple) and len(draft) > 0
    # Every section carries non-empty text drawn from the theme's primary material.
    for record in draft:
        assert _section_text_nonempty(record), f"empty text in section {record['key']}"
        assert record["heading"] and record["heading"].strip()
    # Primary sources exist and are cited.
    topic = data["topic"]
    assert isinstance(topic["primarySources"], list) and topic["primarySources"]
    for url in topic["primarySources"]:
        assert url.startswith("https://")


# ---------------------------------------------------------------------------
# 3. 「問い → 予想 → 観測 → 小さい例 → モデル → 一般化 → 実世界応用 → まとめ」
# ---------------------------------------------------------------------------

def test_draft_contains_the_eight_narrative_sections_in_order():
    data = load_corner_fixture("math", root=FIXTURE_ROOT)
    draft = build_draft(data)
    keys = [record["key"] for record in draft]
    assert keys == list(NARRATIVE_SECTIONS)
    # Spot-check Japanese headings.
    headings = {record["key"]: record["heading"] for record in draft}
    assert "問い" in headings["todaysQuestion"]
    assert "予想" in headings["intuitivePrediction"]
    assert "観測" in headings["observedPhenomenon"]
    assert "例" in headings["smallExample"]
    assert "モデル" in headings["mathematicalModel"]
    assert "一般化" in headings["generalization"]
    assert "応用" in headings["realWorldApplication"]
    assert "まとめ" in headings["intuitiveSummary"]


def test_formula_entries_are_explanation_objects_not_goals():
    """Every formula carries what it explains, range, units and one number."""
    data = load_fixture()
    topic = data["topic"]
    assert isinstance(topic["formulaEntries"], list) and topic["formulaEntries"]
    for entry in topic["formulaEntries"]:
        assert entry["formula"]
        assert entry["whatItExplains"]
        assert entry["symbolDefinitions"]
        assert entry["units"]
        assert entry["validityRange"]
        assert entry["numericExample"]
        assert entry["plainRestatement"]
        assert entry["diagramReference"]
    note = topic["figureAndFormulaHandling"]
    assert "simplification" in note
    assert "not a measurement" in note


# ---------------------------------------------------------------------------
# 4. 出典を保持する
# ---------------------------------------------------------------------------

def test_sources_are_retained_in_fixture_and_overlay():
    data = load_corner_fixture("math", root=FIXTURE_ROOT)
    topic = data["topic"]
    assert topic.get("citationKeepUrl") is True
    assert isinstance(topic["primarySources"], list) and topic["primarySources"]
    assert isinstance(topic["sourcePriority"], list) and topic["sourcePriority"]
    assert topic["sourcePriority"][0] == "public-institution-and-research-institute"
    overlay = render_overlay(data)
    for url in topic["primarySources"]:
        assert url in overlay["citationUrls"]
    # Citation URLs persist from start frame to end frame.
    frames = progress_frames(data)
    assert frames[0]["citationUrls"] == frames[-1]["citationUrls"]
    assert all(u.startswith("https://") for u in overlay["citationUrls"])


# ---------------------------------------------------------------------------
# 5. 配信用 overlay へ章・図・要点を表示できる
# ---------------------------------------------------------------------------

def test_overlay_displays_sections_diagram_and_key_points():
    data = load_corner_fixture("math", root=FIXTURE_ROOT)
    overlay = render_overlay(data)
    # 章 (sections): one entry per design-doc section with heading + text.
    assert isinstance(overlay["sections"], list) and len(overlay["sections"]) == len(NARRATIVE_SECTIONS)
    for section in overlay["sections"]:
        assert section["heading"] and section["heading"].strip()
        assert _section_text_nonempty(section), f"empty text in section {section['key']}"
        assert section["order"] >= 1
    # 図 (figureOrDiagram): diagram/schematic description retained for overlay.
    assert overlay["figureOrDiagram"] and overlay["figureOrDiagram"].strip()
    # 要点 (summary/thirtySecondSummary): key point summary for the overlay header.
    assert overlay["summary"] and overlay["summary"].strip()
    assert overlay["title"] and overlay["title"].strip()
    assert overlay["question"] and overlay["question"].strip()
    # fact/analysis/forecast labels stay declared on the overlay payload.
    assert overlay["segmentLabels"] == ["fact", "analysis", "forecast"]


# ---------------------------------------------------------------------------
# 6. コーナーを自動進行できる
# ---------------------------------------------------------------------------

def test_can_auto_advance_through_bounded_progression_frames():
    data = load_corner_fixture("math", root=FIXTURE_ROOT)
    frames = progress_frames(data)
    assert isinstance(frames, tuple) and len(frames) >= 3  # start + sections + end
    assert frames[0]["kind"] == "start"
    assert frames[-1]["kind"] == "end"
    # Contiguous bounded indices.
    assert [f["index"] for f in frames] == list(range(len(frames)))
    assert all(f["total"] == len(frames) for f in frames)
    # Status overlay aligns with the frame sequence (auto-advance progress).
    entry = validate_status(data, frames)
    assert entry["startIndex"] == 0
    assert entry["endIndex"] == len(frames) - 1
    assert entry["totalFrames"] == len(frames)


# ---------------------------------------------------------------------------
# 7. YouTube 切り出しを想定した独立原稿を生成できる（MVP)
# ---------------------------------------------------------------------------

def test_youtube_independent_draft_exists_for_clip_extraction():
    data = load_corner_fixture("math", root=FIXTURE_ROOT)
    topic = data["topic"]
    draft_text = topic.get("youtubeIndependentDraft")
    assert draft_text and draft_text.strip(), "youtubeIndependentDraft must be non-empty"
    # The independent draft rebuilds the narrative standalone.
    for keyword in ("問い", "直感", "小さい例", "モデル", "一般化", "応用"):
        assert keyword in draft_text, f"independent draft missing keyword: {keyword}"
    # Non-production acceptance marks the independent draft as required.
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["independentYouTubeDraftRequired"] is True
    assert acceptance["overlayAndProgressSplitAfterInputContract"] is True


def test_acceptance_tests_all_pass_smoke():
    """Smoke: all acceptance areas are exercised above and the fixture
    + runtime are mutually consistent."""
    data = load_corner_fixture("math", root=FIXTURE_ROOT)
    draft = build_draft(data)
    overlay = render_overlay(data)
    frames = progress_frames(data)
    validate_status(data, frames)
    # Cross-cutting: overlay sections match the draft.
    assert [s["key"] for s in overlay["sections"]] == [r["key"] for r in draft]
    # Cross-cutting: citations present on start and end frames.
    assert frames[0]["citationUrls"] == overlay["citationUrls"]
