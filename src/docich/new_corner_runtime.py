"""Non-production runtime helpers for the new (input-contract stage) corners.

This module deliberately stays outside the broadcast overlay pipeline.  It makes
the already-fixed corner fixtures consumable by a first runtime layer: load a
fixture, validate the input contract, and render an overlay payload.  It does
not claim that production runtime/overlay progression/automation are done.
"""

from __future__ import annotations

import json
from pathlib import Path


REQUIRED_SECTIONS = (
    "currentQuestion",
    "directPrehistory",
    "deeperCauses",
    "turningPoints",
    "multipleRegionPerspectives",
    "lastingImpact",
    "timeline",
    "mapHandling",
    "limitations",
    "uncertainty",
)

LABELS = ("fact", "analysis", "forecast")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def fixture_path(corner_id: str, root: Path | None = None) -> Path:
    root = root or repo_root()
    suffix = corner_id.replace("-", "_")
    return root / "tests" / "fixtures" / f"{suffix}_corner" / f"fixed_{suffix}_example.json"


def load_corner_fixture(corner_id: str, root: Path | None = None) -> dict:
    path = fixture_path(corner_id, root)
    if not path.is_file():
        raise FileNotFoundError(f"missing corner fixture: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("corner fixture must be a JSON object")
    if data.get("isSyntheticFixture") is not True:
        raise ValueError("corner fixture must be marked as synthetic")
    topic = data.get("topic")
    if not isinstance(topic, dict):
        raise ValueError("corner fixture must contain a topic object")
    missing = [key for key in REQUIRED_SECTIONS if not topic.get(key)]
    if missing:
        raise ValueError(f"corner fixture is missing required runtime sections: {missing}")
    # Keep citation URLs first-class in the rendered payload and the mutation
    # test below: if one is removed, render_overlay fails rather than silently
    # dropping it.
    if not isinstance(topic.get("primarySources"), list) or not topic["primarySources"]:
        raise ValueError("corner fixture must retain primary sources")
    if topic.get("citationKeepUrl") is not True:
        raise ValueError("corner fixture must retain citation URLs")
    return data


def _verified_material(data: dict) -> dict:
    material = data.get("verifiedWebMaterial")
    if not isinstance(material, dict):
        raise ValueError("corner fixture must retain verified web material")
    if "items" not in material:
        raise ValueError("corner fixture must retain verified material items")
    return material


def render_overlay(data: dict) -> dict:
    """Render the minimal overlay payload for mutation-sensitive testing."""

    topic = data["topic"]
    material = _verified_material(data)
    urls = [topic.get("url")] + list(topic.get("primarySources", []))
    urls.extend(item.get("url") for item in material.get("items", []))
    urls = [url for url in urls if url]
    if not urls:
        raise ValueError("corner overlay requires citation URLs")
    labels = list(topic.get("segmentLabels") or [])
    if not labels or not all(isinstance(label, str) for label in labels):
        raise ValueError("corner overlay requires explicit segment labels")

    sections = [(key, topic[key]) for key in REQUIRED_SECTIONS]
    return {
        "corner": data.get("corner"),
        "title": topic["title"],
        "isSynthetic": True,
        "question": topic["currentQuestion"],
        "sections": sections,
        "segmentLabels": labels,
        "citationUrls": urls,
        "limits": topic.get("limitations"),
        "uncertainty": topic.get("uncertainty"),
    }
