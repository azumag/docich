"""Non-production runtime helpers for the new (input-contract stage) corners.

This module deliberately stays outside the broadcast overlay pipeline.  It makes
the already-fixed corner fixtures consumable by a first runtime layer:

1. load a fixture and validate its input contract;
2. build the structured draft in the section order the corner's design doc
   fixes (``build_draft``);
3. render the overlay payload (``render_overlay``);
4. derive the bounded start -> sections -> end progression frames and the
   single status-overlay entry that has to stay aligned with them
   (``progress_frames`` / ``status_overlay_entry`` / ``validate_status``).

It is passive and offline: no network access, no LLM call, no clock, and no
write outside the process.  It does not claim that production runtime,
production overlay wiring, or production startup are done; a production
adapter is expected to call these pure functions behind its own gates.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


# World-history keeps its original module-level names for the existing
# mutation tests and any caller that imported them.
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

# Bounded progression: a corner may not declare an unbounded frame sequence.
MAX_PROGRESS_FRAMES = 48


@dataclass(frozen=True)
class Section:
    """One overlay section: the fixture field, its segment label, its heading."""

    key: str
    label: str
    heading: str


@dataclass(frozen=True)
class CornerRuntimeSpec:
    """Per-corner runtime contract derived from the corner's design doc.

    ``labels is None`` means the fixture itself carries the explicit segment
    labels (world-history does); otherwise the spec fixes them.
    """

    corner_id: str
    fixture_parts: tuple[str, ...]
    container: str
    title_key: str
    question_key: str
    summary_key: str
    sections: tuple[Section, ...]
    required_fields: tuple[str, ...]
    caveat_keys: tuple[str, ...]
    labels: tuple[str, ...] | None = None


WORLD_HISTORY_SPEC = CornerRuntimeSpec(
    corner_id="world-history",
    fixture_parts=(
        "tests",
        "fixtures",
        "world_history_corner",
        "fixed_world_history_example.json",
    ),
    container="topic",
    title_key="title",
    question_key="currentQuestion",
    summary_key="oneLineSummary",
    sections=tuple(
        Section(key=key, label="", heading=key) for key in REQUIRED_SECTIONS
    ),
    required_fields=REQUIRED_SECTIONS,
    caveat_keys=("limitations", "uncertainty"),
    labels=None,
)

RESEARCH_PAPER_SPEC = CornerRuntimeSpec(
    corner_id="research-paper",
    fixture_parts=(
        "tests",
        "fixtures",
        "research_paper_corner",
        "fixed_paper_example.json",
    ),
    container="paper",
    title_key="title",
    question_key="researchQuestion",
    summary_key="thirtySecondSummary",
    sections=(
        Section("thirtySecondSummary", "fact", "30秒要約"),
        Section("researchQuestion", "fact", "研究者が調べたこと"),
        Section("priorProblem", "analysis", "従来は何が問題だったのか"),
        Section("novelty", "analysis", "今回何が新しいのか"),
        Section("experimentStrength", "analysis", "実験・データはどの程度強いのか"),
        Section("limitations", "caveat", "限界 / 注意点"),
        Section("practicalUse", "application", "で、何に使える？"),
        Section("docichApplication", "application", "docich系プロジェクトへの応用"),
    ),
    required_fields=(
        "title",
        "authors",
        "affiliations",
        "field",
        "publishedAt",
        "reviewStatus",
        "url",
        "thirtySecondSummary",
        "researchQuestion",
        "priorProblem",
        "novelty",
        "experimentStrength",
        "limitations",
        "practicalUse",
        "docichApplication",
        "primarySources",
        "sourcePriority",
        "citationKeepUrl",
    ),
    caveat_keys=("reviewStatus", "limitations"),
    labels=("fact", "analysis", "caveat", "application"),
)

ENGINEERING_SPEC = CornerRuntimeSpec(
    corner_id="engineering",
    fixture_parts=(
        "tests",
        "fixtures",
        "engineering_structure_principles_corner",
        "fixed_engineering_example.json",
    ),
    container="topic",
    title_key="title",
    question_key="problemStatement",
    summary_key="thirtySecondSummary",
    sections=(
        Section("subjectSystem", "fact", "まず現物を見る"),
        Section("problemStatement", "fact", "問題を定義"),
        Section("principles", "analysis", "原理（必要最小限）"),
        Section("structure", "analysis", "構造（断面・模式図）"),
        Section("inputToMechanismToOutput", "analysis", "入力 → 機構 → 出力"),
        Section("physicalQuantities", "fact", "重要な物理量"),
        Section("failureModes", "caveat", "壊れ方"),
        Section("weakPoints", "caveat", "弱点・想定外状態"),
        Section("safetyDesign", "analysis", "安全・冗長設計"),
        Section("tradeoffs", "analysis", "技術的トレードオフ"),
        Section("limitations", "caveat", "限界・未検証点"),
    ),
    required_fields=(
        "title",
        "topicId",
        "subjectSystem",
        "operatingEnvironment",
        "thirtySecondSummary",
        "oneLineSummary",
        "problemStatement",
        "principles",
        "structure",
        "inputToMechanismToOutput",
        "physicalQuantities",
        "failureModes",
        "weakPoints",
        "safetyDesign",
        "tradeoffs",
        "limitations",
        "figureOrDiagram",
        "youtubeIndependentDraft",
        "primarySources",
        "sourcePriority",
        "citationKeepUrl",
    ),
    caveat_keys=("limitations",),
    labels=("fact", "analysis", "caveat"),
)

CORNER_SPECS = {
    WORLD_HISTORY_SPEC.corner_id: WORLD_HISTORY_SPEC,
    RESEARCH_PAPER_SPEC.corner_id: RESEARCH_PAPER_SPEC,
    ENGINEERING_SPEC.corner_id: ENGINEERING_SPEC,
}


class CornerRuntimeError(ValueError):
    """Invalid fixture, unknown corner, or an inconsistent overlay frame set."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def spec_for(corner_id: str) -> CornerRuntimeSpec:
    spec = CORNER_SPECS.get(corner_id)
    if spec is None:
        raise CornerRuntimeError(f"unknown corner runtime spec: {corner_id}")
    return spec


def fixture_path(corner_id: str, root: Path | None = None) -> Path:
    root = root or repo_root()
    spec = spec_for(corner_id)
    return root.joinpath(*spec.fixture_parts)


def _resolve_spec(data: dict, corner_id: str | None) -> CornerRuntimeSpec:
    if corner_id is None:
        corner_id = data.get("corner")
    if not isinstance(corner_id, str) or not corner_id:
        raise CornerRuntimeError("corner fixture must declare a corner id")
    spec = spec_for(corner_id)
    if data.get("corner") != corner_id:
        raise CornerRuntimeError(
            f"corner fixture id {data.get('corner')!r} does not match spec {corner_id!r}"
        )
    return spec


def _container(data: dict, spec: CornerRuntimeSpec) -> dict:
    value = data.get(spec.container)
    if not isinstance(value, dict):
        raise CornerRuntimeError(
            f"corner fixture must contain a {spec.container} object"
        )
    return value


def _is_present(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return value is not None


def _missing_fields(container: dict, spec: CornerRuntimeSpec) -> list:
    return [key for key in spec.required_fields if not _is_present(container.get(key))]


def _verified_material(data: dict) -> dict:
    material = data.get("verifiedWebMaterial")
    if not isinstance(material, dict):
        raise CornerRuntimeError("corner fixture must retain verified web material")
    if "items" not in material:
        raise CornerRuntimeError("corner fixture must retain verified material items")
    return material


def load_corner_fixture(corner_id: str, root: Path | None = None) -> dict:
    path = fixture_path(corner_id, root)
    if not path.is_file():
        raise FileNotFoundError(f"missing corner fixture: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CornerRuntimeError("corner fixture must be a JSON object")
    if data.get("isSyntheticFixture") is not True:
        raise CornerRuntimeError("corner fixture must be marked as synthetic")
    spec = _resolve_spec(data, corner_id)
    container = _container(data, spec)
    missing = _missing_fields(container, spec)
    if missing:
        raise CornerRuntimeError(
            f"corner fixture is missing required runtime sections: {missing}"
        )
    # Keep citation URLs first-class in the rendered payload.  If one is
    # removed the runtime fails instead of silently dropping it.
    if not isinstance(container.get("primarySources"), list) or not container["primarySources"]:
        raise CornerRuntimeError("corner fixture must retain primary sources")
    if container.get("citationKeepUrl") is not True:
        raise CornerRuntimeError("corner fixture must retain citation URLs")
    _verified_material(data)
    return data


def citation_urls(data: dict, corner_id: str | None = None) -> tuple:
    """Ordered, de-duplicated citation URLs retained by the fixture."""

    spec = _resolve_spec(data, corner_id)
    container = _container(data, spec)
    material = _verified_material(data)
    raw = [container.get("url")]
    primary = container.get("primarySources")
    if isinstance(primary, list):
        raw.extend(primary)
    for item in material.get("items", []):
        if isinstance(item, dict):
            raw.append(item.get("url"))
    urls = []
    for url in raw:
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
    if not urls:
        raise CornerRuntimeError("corner overlay requires citation URLs")
    return tuple(urls)


def _labels(data: dict, spec: CornerRuntimeSpec) -> tuple:
    if spec.labels is None:
        container = _container(data, spec)
        labels = container.get("segmentLabels")
    else:
        labels = list(spec.labels)
    if not isinstance(labels, (list, tuple)) or not labels:
        raise CornerRuntimeError("corner overlay requires explicit segment labels")
    if not all(isinstance(label, str) and label for label in labels):
        raise CornerRuntimeError("corner overlay requires explicit segment labels")
    return tuple(labels)


def build_draft(data: dict, corner_id: str | None = None) -> tuple:
    """The structured draft: one ordered record per design-doc section."""

    spec = _resolve_spec(data, corner_id)
    container = _container(data, spec)
    if len(spec.sections) > MAX_PROGRESS_FRAMES:
        raise CornerRuntimeError("corner declares too many overlay sections")
    draft = []
    for order, section in enumerate(spec.sections, start=1):
        text = container.get(section.key)
        if not _is_present(text):
            raise CornerRuntimeError(
                f"corner draft is missing required section: {section.key}"
            )
        draft.append(
            {
                "order": order,
                "key": section.key,
                "label": section.label,
                "heading": section.heading,
                "text": text,
            }
        )
    return tuple(draft)


def _caveats(data: dict, spec: CornerRuntimeSpec) -> dict:
    container = _container(data, spec)
    caveats = {}
    for key in spec.caveat_keys:
        value = container.get(key)
        if _is_present(value):
            caveats[key] = value
    if not caveats:
        raise CornerRuntimeError("corner overlay requires retained caveats")
    return caveats


def render_overlay(data: dict, corner_id: str | None = None) -> dict:
    """Render the overlay payload for one corner fixture."""

    spec = _resolve_spec(data, corner_id)
    container = _container(data, spec)
    draft = build_draft(data, spec.corner_id)
    labels = _labels(data, spec)
    urls = citation_urls(data, spec.corner_id)

    return {
        "corner": spec.corner_id,
        "title": container[spec.title_key],
        "isSynthetic": True,
        "question": container[spec.question_key],
        "summary": container.get(spec.summary_key),
        "sections": [dict(record) for record in draft],
        "segmentLabels": list(labels),
        "citationUrls": list(urls),
        "caveats": _caveats(data, spec),
        "limits": container.get("limitations"),
        "uncertainty": container.get("uncertainty"),
    }


def progress_frames(data: dict, corner_id: str | None = None) -> tuple:
    """The bounded start -> section -> end frame sequence for one corner."""

    spec = _resolve_spec(data, corner_id)
    overlay = render_overlay(data, spec.corner_id)
    draft = overlay["sections"]
    total = len(draft) + 2
    if total > MAX_PROGRESS_FRAMES:
        raise CornerRuntimeError("corner progression exceeds the bounded frame budget")
    caveat_text = " / ".join(str(value) for value in overlay["caveats"].values())

    frames = [
        {
            "index": 0,
            "total": total,
            "kind": "start",
            "key": "start",
            "label": "start",
            "heading": overlay["title"],
            "text": overlay["summary"],
            "citationUrls": list(overlay["citationUrls"]),
            "caveats": dict(overlay["caveats"]),
        }
    ]
    for record in draft:
        frames.append(
            {
                "index": record["order"],
                "total": total,
                "kind": "section",
                "key": record["key"],
                "label": record["label"],
                "heading": record["heading"],
                "text": record["text"],
            }
        )
    frames.append(
        {
            "index": total - 1,
            "total": total,
            "kind": "end",
            "key": "end",
            "label": "end",
            "heading": overlay["title"],
            "text": caveat_text,
            "citationUrls": list(overlay["citationUrls"]),
            "caveats": dict(overlay["caveats"]),
        }
    )
    return tuple(frames)


def status_overlay_entry(data: dict, corner_id: str | None = None) -> dict:
    """The single status-overlay record a scheduler shows while the corner runs.

    The entry and the content frames are derived from the same fixture, so the
    start/end indices and the citation list stay aligned with what the overlay
    is showing.
    """

    spec = _resolve_spec(data, corner_id)
    frames = progress_frames(data, spec.corner_id)
    return {
        "corner": spec.corner_id,
        "title": frames[0]["heading"],
        "isSynthetic": True,
        "totalFrames": len(frames),
        "startIndex": frames[0]["index"],
        "endIndex": frames[-1]["index"],
        "startKind": frames[0]["kind"],
        "endKind": frames[-1]["kind"],
        "citationUrls": list(frames[0]["citationUrls"]),
        "caveatKeys": sorted(frames[-1]["caveats"]),
    }


def validate_status(data: dict, frames=None, corner_id: str | None = None) -> dict:
    """Re-check that the frame sequence and the status entry agree.

    ``frames`` is accepted so an integration point can re-validate the exact
    sequence it is about to show (for example after a renderer or persistence
    step mutated it) instead of trusting a re-derivation.
    """

    spec = _resolve_spec(data, corner_id)
    entry = status_overlay_entry(data, spec.corner_id)
    if frames is None:
        frames = progress_frames(data, spec.corner_id)
    else:
        frames = tuple(frames)
    if not frames:
        raise CornerRuntimeError("corner progression has no frames")
    if len(frames) != entry["totalFrames"]:
        raise CornerRuntimeError("corner frames do not match the status overlay length")
    if [frame.get("index") for frame in frames] != list(range(len(frames))):
        raise CornerRuntimeError("corner frames are not a contiguous bounded sequence")
    if any(len(frame.get("citationUrls") or ()) == 0 for frame in (frames[0], frames[-1])):
        raise CornerRuntimeError("corner overlay requires citation URLs")
    if frames[0].get("citationUrls") != frames[-1].get("citationUrls"):
        raise CornerRuntimeError("corner citation URLs are not persisted to the end frame")
    if list(frames[0]["citationUrls"]) != entry["citationUrls"]:
        raise CornerRuntimeError("corner frames and status overlay cite different URLs")
    if frames[0].get("kind") != "start" or frames[-1].get("kind") != "end":
        raise CornerRuntimeError("corner progression must start and end with its own frames")
    if frames[0]["index"] != entry["startIndex"] or frames[-1]["index"] != entry["endIndex"]:
        raise CornerRuntimeError("corner start/end indices do not match the status overlay")
    if not frames[-1].get("caveats"):
        raise CornerRuntimeError("corner end frame must retain the caveats")
    if sorted(frames[-1]["caveats"]) != entry["caveatKeys"]:
        raise CornerRuntimeError("corner caveats do not match the status overlay")
    return entry
