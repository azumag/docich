"""Acceptance tests for issue #1718 新コーナー「地図から見る地理・世界」 (geography-world corner).

Covers the design-doc acceptance items for the runtime slice:

1. 地図中心の8セクション原稿が生成できる（map → terrain/climate → population →
   resources → transport → industry → history/politics → current meaning）
2. 出典を保持する（公式地理・公共ソース優先、URL保持）
3. 配信用 overlay へ地図・セクション・ラベル・出典・地図取り扱い情報を表示できる
4. コーナーを自動進行できる
5. YouTube 切り出しを想定した独立原稿を生成できる（MVP)

All tests are passive/offline: they load the fixed synthetic fixture and assert
on the structured draft, overlay payload, and progression frames.
"""

from __future__ import annotations

from pathlib import Path

from docich.new_corner_runtime import (
    build_draft,
    load_corner_fixture,
    progress_frames,
    render_overlay,
    validate_status,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1]

# The 8 required sections from the design doc, in order.
EIGHT_SECTIONS = (
    "mapFocus",
    "terrainClimate",
    "population",
    "resources",
    "transportRoutes",
    "industry",
    "historyPoliticsImpact",
    "currentMeaning",
)


def _section_text_nonempty(record: dict) -> bool:
    text = record["text"]
    if isinstance(text, str):
        return bool(text.strip())
    if isinstance(text, (list, tuple)):
        return bool(text) and all(isinstance(t, str) and t.strip() for t in text)
    return bool(text)


# ---------------------------------------------------------------------------
# 1. 地図中心の8セクション原稿が生成できる
# ---------------------------------------------------------------------------

def test_draft_contains_the_eight_required_sections_in_order():
    data = load_corner_fixture("geography-world", root=FIXTURE_ROOT)
    draft = build_draft(data)
    keys = [record["key"] for record in draft]
    assert keys == list(EIGHT_SECTIONS), "sections must appear in design-doc order"
    for record in draft:
        assert _section_text_nonempty(record), f"empty text in section {record['key']}"
        assert record["heading"] and record["heading"].strip()
    # Every generated segment carries one of the declared labels.
    for record in draft:
        assert record["label"] in {"fact", "analysis", "forecast"}


# ---------------------------------------------------------------------------
# 2. 出典を保持する
# ---------------------------------------------------------------------------

def test_sources_are_retained_in_fixture_and_overlay():
    data = load_corner_fixture("geography-world", root=FIXTURE_ROOT)
    topic = data["topic"]
    assert topic.get("citationKeepUrl") is True
    assert isinstance(topic["primarySources"], list) and topic["primarySources"]
    # Source priority favors official geographic / public sources.
    assert "official-geographic-or-public-source" in topic["sourcePriority"]
    overlay = render_overlay(data)
    for url in topic["primarySources"]:
        assert url in overlay["citationUrls"]
    assert topic["url"] in overlay["citationUrls"]
    # Citation URLs persist from start frame to end frame.
    frames = progress_frames(data)
    assert frames[0]["citationUrls"] == frames[-1]["citationUrls"]
    assert all(u.startswith("https://") for u in overlay["citationUrls"])


# ---------------------------------------------------------------------------
# 3. 配信用 overlay へ地図・セクション・ラベル・出典を表示できる
# ---------------------------------------------------------------------------

def test_overlay_displays_map_sections_labels_and_citations():
    data = load_corner_fixture("geography-world", root=FIXTURE_ROOT)
    overlay = render_overlay(data)
    # The map is the center of the corner: it is both the question and the
    # first displayed section.
    assert overlay["question"] == data["topic"]["mapFocus"]
    assert overlay["sections"][0]["key"] == "mapFocus"
    assert _section_text_nonempty(overlay["sections"][0])
    # 章 (sections): one entry per design-doc section with heading + text.
    assert [section["key"] for section in overlay["sections"]] == list(EIGHT_SECTIONS)
    for section in overlay["sections"]:
        assert section["heading"] and section["heading"].strip()
        assert _section_text_nonempty(section), f"empty text in section {section['key']}"
        assert section["order"] >= 1
    # ラベル: the declared fact/analysis/forecast vocabulary is kept.
    assert overlay["segmentLabels"] == ["fact", "analysis", "forecast"]
    # 出典と要点.
    assert overlay["citationUrls"]
    assert overlay["summary"] and overlay["summary"].strip()
    assert overlay["title"] and overlay["title"].strip()
    assert overlay["figureOrDiagram"] and overlay["figureOrDiagram"].strip()


def test_map_handling_metadata_is_explicit_in_overlay():
    data = load_corner_fixture("geography-world", root=FIXTURE_ROOT)
    overlay = render_overlay(data)
    # The map-handling contract (scope, scale, projection, vintage, license,
    # usage conditions) is retained as a caveat so the overlay keeps it.
    note = overlay["caveats"]["mapHandling"]
    for field in ("center", "scale", "projection", "vintage", "license", "usage conditions"):
        assert field in note, f"map handling note missing {field}"
    # Limitations and uncertainty are retained alongside it.
    assert overlay["caveats"]["limitations"] == data["topic"]["limitations"]
    assert overlay["caveats"]["uncertainty"] == data["topic"]["uncertainty"]
    assert overlay["limits"] == data["topic"]["limitations"]
    assert overlay["uncertainty"] == data["topic"]["uncertainty"]


# ---------------------------------------------------------------------------
# 4. コーナーを自動進行できる
# ---------------------------------------------------------------------------

def test_can_auto_advance_through_bounded_progression_frames():
    data = load_corner_fixture("geography-world", root=FIXTURE_ROOT)
    frames = progress_frames(data)
    assert isinstance(frames, tuple) and len(frames) == len(EIGHT_SECTIONS) + 2
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
# 5. YouTube 切り出しを想定した独立原稿を生成できる（MVP)
# ---------------------------------------------------------------------------

def test_youtube_independent_draft_exists_for_clip_extraction():
    data = load_corner_fixture("geography-world", root=FIXTURE_ROOT)
    topic = data["topic"]
    draft_text = topic.get("youtubeIndependentDraft")
    assert draft_text and draft_text.strip(), "youtubeIndependentDraft must be non-empty"
    # The independent draft should walk the map-centered section sequence so a
    # clip can be reconstructed standalone.
    for keyword in ("map", "terrain", "climate", "population", "resources", "transport", "industry"):
        assert keyword in draft_text, f"independent draft missing keyword: {keyword}"
    # Non-production acceptance marks the independent draft as required.
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["independentYouTubeDraftRequired"] is True
    assert acceptance["overlayAndProgressSplitAfterInputContract"] is True


def test_acceptance_tests_all_pass_smoke():
    """Smoke: all acceptance areas are exercised above and the fixture
    + runtime are mutually consistent."""
    data = load_corner_fixture("geography-world", root=FIXTURE_ROOT)
    draft = build_draft(data)
    overlay = render_overlay(data)
    frames = progress_frames(data)
    validate_status(data, frames)
    # Cross-cutting: overlay sections match the draft.
    assert [s["key"] for s in overlay["sections"]] == [r["key"] for r in draft]
    # Cross-cutting: citations present on start and end frames.
    assert frames[0]["citationUrls"] == overlay["citationUrls"]
