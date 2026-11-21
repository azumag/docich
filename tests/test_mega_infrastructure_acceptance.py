"""Acceptance tests for issue #1713 新コーナー「世界の巨大インフラを深掘りする」.

Covers the design doc's overlay/progression acceptance phase
(``docs/superpowers/specs/2026-10-14-mega-infrastructure-corner-design.md``):

- the overlay exposes the theme, the system sections (what it supports through
  social/economic impact), the figure, the segment labels, and citation URLs;
- progression is a bounded start -> sections -> end sequence;
- citation URLs persist from the start frame to the end frame;
- the input keeps the independently usable YouTube draft outline and the
  map/schematic handling note the later overlay layer consumes.

All tests are passive/offline: they load the fixed synthetic fixture through
the non-production runtime layer.
"""

from __future__ import annotations

import re
from pathlib import Path

from docich.new_corner_runtime import (
    MAX_PROGRESS_FRAMES,
    load_corner_fixture,
    progress_frames,
    render_overlay,
    validate_status,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1]

URL_RE = re.compile(r"^https://")

DISPLAY_SECTION_KEYS = (
    "whatItSupports",
    "demand",
    "geographyAndSite",
    "scaleAndCapacity",
    "structureAndMethod",
    "operation",
    "bottleneck",
    "redundancy",
    "failureAndDisaster",
    "maintenanceAndRenewal",
    "socialEconomicImpact",
)


def test_overlay_displays_the_design_doc_acceptance_set():
    data = load_corner_fixture("mega-infrastructure", root=FIXTURE_ROOT)
    overlay = render_overlay(data)
    assert overlay["question"] == data["topic"]["infrastructureTheme"]
    keys = [section["key"] for section in overlay["sections"]]
    for key in DISPLAY_SECTION_KEYS:
        assert key in keys
    assert overlay["figureOrDiagram"]
    assert overlay["segmentLabels"] == ["fact", "analysis", "forecast"]
    assert overlay["citationUrls"]
    for url in overlay["citationUrls"]:
        assert URL_RE.match(url)


def test_progression_is_bounded_and_preserves_citation_urls():
    data = load_corner_fixture("mega-infrastructure", root=FIXTURE_ROOT)
    frames = progress_frames(data)
    assert frames[0]["kind"] == "start"
    assert frames[-1]["kind"] == "end"
    assert [frame["key"] for frame in frames[1:-1]] == [
        "infrastructureTheme",
        *DISPLAY_SECTION_KEYS,
    ]
    assert len(frames) <= MAX_PROGRESS_FRAMES
    assert frames[0]["citationUrls"]
    assert frames[0]["citationUrls"] == frames[-1]["citationUrls"]
    assert validate_status(data, frames)["totalFrames"] == len(frames)


def test_fixture_retains_youtube_draft_and_map_handling_note():
    data = load_corner_fixture("mega-infrastructure", root=FIXTURE_ROOT)
    topic = data["topic"]
    assert topic["youtubeIndependentDraft"]
    assert topic["mapAndDiagramHandling"]
    assert topic["limitations"]
    assert topic["uncertainty"]
