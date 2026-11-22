"""Acceptance tests for issue #1717 新コーナー「実際のニュース・技術資料から学ぶ英語」.

Covers the design doc's phase-2 (overlay and progression) acceptance items:

1. overlay displays the source badge, subject, key phrase, meaning,
   grammar/usage, nuance, similar expressions, alternative example, citation
2. コーナーを自動進行できる (automatic progression from start to finish)
3. 出典URLが最後まで保持される (citation URL persistence)
4. YouTube 切り出しを想定した独立原稿がある (independent YouTube-shaped script)

All tests are passive/offline: they load the fixed synthetic fixture and assert
on the structured draft, overlay payload, and progression frames.
"""

from __future__ import annotations

import json
from pathlib import Path

from docich.new_corner_runtime import (
    build_draft,
    load_corner_fixture,
    progress_frames,
    render_overlay,
    validate_status,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    FIXTURE_ROOT
    / "tests"
    / "fixtures"
    / "english_corner"
    / "fixed_english_example.json"
)


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _section_text_nonempty(record: dict) -> bool:
    text = record["text"]
    if isinstance(text, str):
        return bool(text.strip())
    if isinstance(text, (list, tuple)):
        return bool(text) and all(
            isinstance(t, dict) and t.get("phrase") or isinstance(t, str) and t.strip()
            for t in text
        )
    return bool(text)


# ---------------------------------------------------------------------------
# 1. overlay displays the learning elements and the source citation
# ---------------------------------------------------------------------------

def test_overlay_displays_subject_key_phrase_and_learning_sections():
    data = load_corner_fixture("english", root=FIXTURE_ROOT)
    overlay = render_overlay(data)
    # Subject: title + today's real example + summary for the overlay header.
    assert overlay["title"] and overlay["title"].strip()
    assert overlay["question"] == data["topic"]["todayExample"]
    assert overlay["summary"] and overlay["summary"].strip()
    # Source badge: the example's source URL is carried into the citation list.
    assert data["topic"]["url"] in overlay["citationUrls"]
    # Learning sections: key phrase, meaning, grammar/usage, nuance, similar
    # expressions, alternative example are each an overlay section.
    keys = [section["key"] for section in overlay["sections"]]
    for required in (
        "keyPhrases",
        "meaning",
        "grammarUsage",
        "nuance",
        "differenceFromSimilar",
        "alternativeExample",
        "sourceMaterialBrief",
    ):
        assert required in keys, f"missing overlay section: {required}"
    for section in overlay["sections"]:
        assert section["heading"] and section["heading"].strip()
        assert _section_text_nonempty(section), f"empty text in section {section['key']}"


def test_overlay_keeps_the_synthetic_marker_and_caveats():
    data = load_corner_fixture("english", root=FIXTURE_ROOT)
    overlay = render_overlay(data)
    # The synthetic fixture is never presented as a verified real example.
    assert overlay["isSynthetic"] is True
    assert overlay["caveats"]["limitations"] == data["topic"]["limitations"]
    assert overlay["caveats"]["uncertainty"] == data["topic"]["uncertainty"]


def test_source_priority_favors_primary_technical_documents():
    data = load_fixture()
    priority = data["topic"]["sourcePriority"]
    assert isinstance(priority, list) and priority
    assert priority[0] == "primary-technical-document"
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["mustPrioritizePrimaryTechnicalSources"] is True


# ---------------------------------------------------------------------------
# 2. automatic progression from start to finish
# ---------------------------------------------------------------------------

def test_can_auto_advance_through_bounded_progression_frames():
    data = load_corner_fixture("english", root=FIXTURE_ROOT)
    frames = progress_frames(data)
    assert isinstance(frames, tuple) and len(frames) >= 3  # start + sections + end
    assert frames[0]["kind"] == "start"
    assert frames[-1]["kind"] == "end"
    # Contiguous bounded indices covering every learning section.
    assert [f["index"] for f in frames] == list(range(len(frames)))
    assert all(f["total"] == len(frames) for f in frames)
    section_keys = [f["key"] for f in frames if f["kind"] == "section"]
    assert section_keys == [r["key"] for r in build_draft(data)]
    # Status overlay aligns with the frame sequence (auto-advance progress).
    entry = validate_status(data, frames)
    assert entry["startIndex"] == 0
    assert entry["endIndex"] == len(frames) - 1
    assert entry["totalFrames"] == len(frames)


# ---------------------------------------------------------------------------
# 3. citation URL persistence
# ---------------------------------------------------------------------------

def test_citation_urls_persist_from_start_frame_to_end_frame():
    data = load_corner_fixture("english", root=FIXTURE_ROOT)
    overlay = render_overlay(data)
    frames = progress_frames(data)
    assert frames[0]["citationUrls"] == frames[-1]["citationUrls"]
    assert frames[0]["citationUrls"] == overlay["citationUrls"]
    assert all(u.startswith("https://") for u in overlay["citationUrls"])
    # Short-quote policy is retained end to end.
    assert data["topic"]["citationKeepUrl"] is True
    assert data["topic"]["quotePolicy"] == "short-quote-only"


# ---------------------------------------------------------------------------
# 4. independent YouTube-shaped script
# ---------------------------------------------------------------------------

def test_youtube_independent_draft_exists_for_clip_extraction():
    data = load_fixture()
    draft_text = data["topic"].get("youtubeIndependentDraft")
    assert draft_text and draft_text.strip(), "youtubeIndependentDraft must be non-empty"
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["independentYouTubeDraftRequired"] is True
    assert acceptance["overlayAndProgressSplitAfterInputContract"] is True


def test_acceptance_smoke_fixture_and_runtime_are_consistent():
    """Smoke: fixture + runtime agree on sections, citations, and status."""
    data = load_corner_fixture("english", root=FIXTURE_ROOT)
    draft = build_draft(data)
    overlay = render_overlay(data)
    frames = progress_frames(data)
    entry = validate_status(data, frames)
    assert [s["key"] for s in overlay["sections"]] == [r["key"] for r in draft]
    assert entry["citationUrls"] == overlay["citationUrls"]
