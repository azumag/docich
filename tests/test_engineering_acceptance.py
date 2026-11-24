"""Acceptance tests for issue #1707 新コーナー「構造と原理」 (engineering corner).

Covers the 7 MVP acceptance items:

1. テーマ候補を管理できる（一覧から本日のテーマを選べる)
2. 1テーマについて一次情報を収集した原稿が生成できる
3. 「問題 → 原理 → 構造 → 故障 → 安全設計」の原稿が生成できる
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
    / "fixtures"
    / "engineering_structure_principles_corner"
    / "fixed_engineering_example.json"
)

# The 5 required chapters from the design doc: 問題 → 原理 → 構造 → 故障 → 安全設計.
FIVE_CHAPTERS = ("problemStatement", "principles", "structure", "failureModes", "safetyDesign")


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. テーマ候補を管理できる（一覧から本日のテーマを選べる)
# ---------------------------------------------------------------------------

def test_theme_candidates_can_be_listed():
    candidates = list_theme_candidates(root=FIXTURE_ROOT)
    assert isinstance(candidates, list) and len(candidates) >= 1
    ids = [c["id"] for c in candidates]
    assert len(ids) == len(set(ids))  # unique ids
    for c in candidates:
        assert set(c.keys()) >= {"id", "title", "domain", "description"}
        assert c["id"] and c["title"] and c["domain"] and c["description"]


def test_theme_candidate_can_be_selected_as_todays_theme():
    selected = select_theme("train-nose-air-pressure", root=FIXTURE_ROOT)
    assert selected["id"] == "train-nose-air-pressure"
    assert selected["domain"] == "railway"
    assert "トンネル" in selected["title"]


def test_theme_candidate_selection_rejects_unknown_id():
    with pytest.raises(CornerRuntimeError):
        select_theme("no-such-theme", root=FIXTURE_ROOT)


def test_theme_candidate_list_loads_from_fixture():
    data = load_theme_candidates(root=FIXTURE_ROOT)
    assert data.get("isSyntheticFixture") is True
    assert data.get("corner") == "engineering"
    candidates = data["candidates"]
    assert len(candidates) >= 3  # multiple domains available for future corner groups
    domains = {c["domain"] for c in candidates}
    assert {"railway", "architecture", "electronics", "information_engineering", "mega_infrastructure"} <= domains


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
    data = load_corner_fixture("engineering", root=FIXTURE_ROOT)
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
# 3. 「問題 → 原理 → 構造 → 故障 → 安全設計」の原稿が生成できる
# ---------------------------------------------------------------------------

def test_draft_contains_the_five_required_chapters_in_order():
    data = load_corner_fixture("engineering", root=FIXTURE_ROOT)
    draft = build_draft(data)
    keys = [record["key"] for record in draft]
    for chapter in FIVE_CHAPTERS:
        assert chapter in keys, f"missing chapter: {chapter}"
    # Confirm the relative order: 問題(問題定義) → 原理 → 構造 → 故障 → 安全設計.
    order = [keys.index(k) for k in FIVE_CHAPTERS]
    assert order == sorted(order), "chapters must appear in design-doc order"
    # Spot-check Japanese headings.
    headings = {record["key"]: record["heading"] for record in draft}
    assert "問題" in headings["problemStatement"]
    assert "原理" in headings["principles"]
    assert "構造" in headings["structure"]
    assert "壊れ方" in headings["failureModes"] or "故障" in headings["failureModes"]
    assert "安全" in headings["safetyDesign"]


# ---------------------------------------------------------------------------
# 4. 出典を保持する
# ---------------------------------------------------------------------------

def test_sources_are_retained_in_fixture_and_overlay():
    data = load_corner_fixture("engineering", root=FIXTURE_ROOT)
    topic = data["topic"]
    assert topic.get("citationKeepUrl") is True
    assert isinstance(topic["primarySources"], list) and topic["primarySources"]
    assert isinstance(topic["sourcePriority"], list) and topic["sourcePriority"]
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

def test_overlay_displays_chapters_diagram_and_key_points():
    data = load_corner_fixture("engineering", root=FIXTURE_ROOT)
    overlay = render_overlay(data)
    # 章 (sections): one entry per design-doc section with heading + text.
    assert isinstance(overlay["sections"], list) and len(overlay["sections"]) > 0
    for section in overlay["sections"]:
        assert section["heading"] and section["heading"].strip()
        assert _section_text_nonempty(section), f"empty text in section {section['key']}"
        assert section["order"] >= 1
    # 図 (figureOrDiagram): diagram/schematic description retained for overlay.
    assert overlay["figureOrDiagram"] and overlay["figureOrDiagram"].strip()
    assert "図" in overlay["figureOrDiagram"] or "模式図" in overlay["figureOrDiagram"] or "断面図" in overlay["figureOrDiagram"]
    # 要点 (summary/thirtySecondSummary): key point summary for the overlay header.
    assert overlay["summary"] and overlay["summary"].strip()
    assert overlay["title"] and overlay["title"].strip()
    assert overlay["question"] and overlay["question"].strip()


# ---------------------------------------------------------------------------
# 6. コーナーを自動進行できる
# ---------------------------------------------------------------------------

def test_can_auto_advance_through_bounded_progression_frames():
    data = load_corner_fixture("engineering", root=FIXTURE_ROOT)
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
    data = load_corner_fixture("engineering", root=FIXTURE_ROOT)
    topic = data["topic"]
    draft_text = topic.get("youtubeIndependentDraft")
    assert draft_text and draft_text.strip(), "youtubeIndependentDraft must be non-empty"
    # The independent draft should reference the 5 chapters so a clip can be
    # reconstructed standalone.
    for keyword in ("要約", "問題", "原理", "構造", "故障", "安全設計"):
        assert keyword in draft_text, f"independent draft missing keyword: {keyword}"
    # Non-production acceptance marks the independent draft as required.
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["independentYouTubeDraftRequired"] is True
    assert acceptance["overlayAndProgressSplitAfterInputContract"] is True


def test_acceptance_tests_all_pass_smoke():
    """Smoke: all 7 acceptance areas are exercised above and the fixture
    + runtime are mutually consistent."""
    data = load_corner_fixture("engineering", root=FIXTURE_ROOT)
    draft = build_draft(data)
    overlay = render_overlay(data)
    frames = progress_frames(data)
    validate_status(data, frames)
    # Cross-cutting: overlay sections match the draft.
    assert [s["key"] for s in overlay["sections"]] == [r["key"] for r in draft]
    # Cross-cutting: citations present on start and end frames.
    assert frames[0]["citationUrls"] == overlay["citationUrls"]