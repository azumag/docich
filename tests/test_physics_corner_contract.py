import json
import re
from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "physics_corner"
    / "fixed_physics_example.json"
)

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
URL_RE = re.compile(r"^https://")

REQUIRED_TOPIC_FIELDS = {
    "title",
    "topicId",
    "physicsField",
    "todaysQuestion",
    "intuitivePrediction",
    "observedPhenomenon",
    "physicalLaw",
    "formulaAndModel",
    "experimentAndTechnologyApplication",
    "intuitionFailurePoint",
    "thirtySecondSummary",
    "oneLineSummary",
    "requirementsAndConstraints",
    "formulaEntries",
    "figureAndFormulaHandling",
    "labelsPolicy",
    "segmentLabels",
    "limitations",
    "uncertainty",
    "figureOrDiagram",
    "youtubeIndependentDraft",
    "primarySources",
    "sourcePriority",
    "citationKeepUrl",
    "url",
}


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_exists_and_parses():
    assert FIXTURE.exists()
    assert load_fixture()["corner"] == "physics"


def test_top_level_contract():
    data = load_fixture()
    assert data["schemaVersion"] == 1
    assert data["isSyntheticFixture"] is True
    assert "nonProductionAcceptance" in data
    topic = data["topic"]
    assert REQUIRED_TOPIC_FIELDS.issubset(topic.keys())
    assert topic["citationKeepUrl"] is True
    assert isinstance(topic["primarySources"], list) and topic["primarySources"]
    assert isinstance(topic["requirementsAndConstraints"], list) and topic["requirementsAndConstraints"]
    assert isinstance(topic["segmentLabels"], list) and topic["segmentLabels"]
    assert isinstance(topic["formulaEntries"], list) and topic["formulaEntries"]


def test_source_urls_are_https():
    data = load_fixture()
    topic = data["topic"]
    assert URL_RE.match(topic["url"])
    for url in topic["primarySources"]:
        assert URL_RE.match(url)
    for item in data["verifiedWebMaterial"]["items"]:
        assert URL_RE.match(item["url"])


def test_narrative_sections_are_explicit():
    data = load_fixture()
    topic = data["topic"]
    assert topic["physicsField"]
    assert topic["todaysQuestion"]
    assert topic["intuitivePrediction"]
    assert topic["observedPhenomenon"]
    assert topic["physicalLaw"]
    assert topic["formulaAndModel"]
    assert topic["experimentAndTechnologyApplication"]
    assert topic["intuitionFailurePoint"]
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["mustIncludeTodaysQuestion"] is True
    assert acceptance["mustIncludeIntuitivePrediction"] is True
    assert acceptance["mustIncludeObservedPhenomenon"] is True
    assert acceptance["mustIncludePhysicalLaw"] is True
    assert acceptance["mustIncludeSimpleFormulaOrModel"] is True
    assert acceptance["mustIncludeExperimentAndTechnologyApplication"] is True
    assert acceptance["mustIncludeIntuitionFailurePoint"] is True


def test_source_priority_favors_public_research_and_standards_sources():
    data = load_fixture()
    topic = data["topic"]
    assert "public-institution-and-research-institute" in topic["sourcePriority"]
    assert topic["sourcePriority"][0] == "public-institution-and-research-institute"
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["mustPrioritizePublicResearchAndStandardsSources"] is True


def test_fact_analysis_forecast_labels_are_declared():
    data = load_fixture()
    topic = data["topic"]
    assert topic["labelsPolicy"]
    for label in ("fact", "analysis", "forecast"):
        assert label in topic["segmentLabels"]
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["mustIncludeFactAnalysisForecastLabels"] is True


def test_formula_entries_carry_explanation_not_just_symbols():
    """Check the fixed synthetic input, not the correctness of any real formula."""
    data = load_fixture()
    assert data["isSyntheticFixture"] is True
    topic = data["topic"]
    for entry in topic["formulaEntries"]:
        for key in (
            "formula",
            "whatItExplains",
            "symbolDefinitions",
            "units",
            "validityRange",
            "numericExample",
            "plainRestatement",
            "diagramReference",
        ):
            assert key in entry, key
        assert entry["formula"]
        # A formula must never be shipped alone: meaning, range, and one number.
        assert entry["whatItExplains"]
        assert entry["validityRange"]
        assert entry["numericExample"]
        assert entry["plainRestatement"]
    note = topic["figureAndFormulaHandling"]
    assert "simplification" in note
    assert "not a measurement" in note
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["mustIncludeFigureAndFormulaHandling"] is True
    assert acceptance["mustNotPresentFormulasAsGoal"] is True
    assert acceptance["mustNotPresentSyntheticAsReal"] is True


def test_verified_material_hashes_are_well_formed():
    import hashlib

    data = load_fixture()
    material = data["verifiedWebMaterial"]
    assert material["status"] in {"ok", "partial"}
    queries_used = material["queries_used"]
    assert isinstance(queries_used, list) and queries_used
    assert 1 <= len(queries_used) <= 3
    assert isinstance(material["items"], list)
    assert 1 <= len(material["items"]) <= 4
    for item in material["items"]:
        for key in ("url", "body_sha256", "text_sha256", "excerpt_sha256", "excerpt", "query_indexes"):
            assert key in item
        assert HASH_RE.fullmatch(item["body_sha256"])
        assert HASH_RE.fullmatch(item["text_sha256"])
        assert HASH_RE.fullmatch(item["excerpt_sha256"])
        assert 0 < len(item["excerpt"].encode("utf-8")) <= 8192
        assert hashlib.sha256(item["excerpt"].encode("utf-8")).hexdigest() == item["excerpt_sha256"]
        query_indexes = item["query_indexes"]
        assert isinstance(query_indexes, list) and query_indexes
        assert query_indexes == sorted(set(query_indexes))
        assert all(
            isinstance(index, int) and 0 <= index < len(queries_used)
            for index in query_indexes
        )


def test_non_production_acceptance_boundary():
    data = load_fixture()
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["requiresReviewerApproval"] is True
    assert acceptance["mustNotClaimRealPhysics"] is True
    assert acceptance["mustNotPresentSyntheticAsReal"] is True
    assert acceptance["mustKeepSourceUrls"] is True
    assert acceptance["mustNotClaimRealPublication"] is True
    assert acceptance["mustKeepCitations"] is True
    assert acceptance["overlayAndProgressSplitAfterInputContract"] is True
    assert acceptance["partialCompletionAllowed"] is True
    assert acceptance["independentYouTubeDraftRequired"] is True

def test_synthetic_fixture_contains_historical_improvement_input():
    """Check this fixed fixture's fictional input, not real-history validation."""
    data = load_fixture()
    assert data["isSyntheticFixture"] is True
    narrative = data["topic"]["physicalLaw"]
    assert "決まる)。" in narrative
    for required in (
        "合成fixture内の改良履歴",
        "旧案",
        "改良案",
        "架空の設計履歴",
        "実物理の法則導出や測定改善を示さない",
    ):
        assert required in narrative, required


# --- runtime slice: overlay payload + start/end progression -----------------
#
# These tests pin the runtime behaviour (design doc phase 2) for this corner.
# Each one fails when the runtime stops preserving the fixed section set, the
# citation URLs, the explicit labels, or the start/end/status alignment.

RUNTIME_ROOT = Path(__file__).resolve().parents[1]

# The design doc's required section order: 問い → 直感 → 観測 → 法則 → 式 → 応用 → 直感が外れる点.
RUNTIME_SECTION_ORDER = (
    "todaysQuestion",
    "intuitivePrediction",
    "observedPhenomenon",
    "physicalLaw",
    "formulaAndModel",
    "experimentAndTechnologyApplication",
    "intuitionFailurePoint",
)


def test_runtime_overlay_keeps_sections_labels_and_citations():
    from docich.new_corner_runtime import load_corner_fixture, render_overlay

    data = load_corner_fixture("physics", root=RUNTIME_ROOT)
    overlay = render_overlay(data)
    assert overlay["corner"] == "physics"
    assert overlay["isSynthetic"] is True
    assert overlay["title"] == data["topic"]["title"]
    assert overlay["question"] == data["topic"]["todaysQuestion"]
    assert overlay["summary"] == data["topic"]["thirtySecondSummary"]
    assert tuple(section["key"] for section in overlay["sections"]) == RUNTIME_SECTION_ORDER
    assert overlay["segmentLabels"] == ["fact", "analysis", "forecast"]
    for url in data["topic"]["primarySources"]:
        assert url in overlay["citationUrls"]
    assert overlay["caveats"]["limitations"] == data["topic"]["limitations"]
    assert overlay["caveats"]["uncertainty"] == data["topic"]["uncertainty"]


def test_runtime_progression_start_and_end_match_the_status_overlay():
    from docich.new_corner_runtime import (
        load_corner_fixture,
        progress_frames,
        status_overlay_entry,
        validate_status,
    )

    data = load_corner_fixture("physics", root=RUNTIME_ROOT)
    frames = progress_frames(data)
    assert [frame["kind"] for frame in frames] == (
        ["start"] + ["section"] * len(RUNTIME_SECTION_ORDER) + ["end"]
    )
    assert [frame["index"] for frame in frames] == list(range(len(frames)))
    assert all(frame["total"] == len(frames) for frame in frames)

    entry = status_overlay_entry(data)
    assert entry["totalFrames"] == len(frames)
    assert entry["startIndex"] == 0
    assert entry["endIndex"] == len(frames) - 1
    assert validate_status(data, frames) == entry

    assert frames[-1]["citationUrls"] == frames[0]["citationUrls"]
    assert frames[-1]["citationUrls"] == entry["citationUrls"]
    assert frames[-1]["caveats"]["limitations"] == data["topic"]["limitations"]
    assert frames[-1]["caveats"]["uncertainty"] == data["topic"]["uncertainty"]


def test_runtime_frame_budget_is_bounded():
    from docich.new_corner_runtime import (
        MAX_PROGRESS_FRAMES,
        load_corner_fixture,
        progress_frames,
    )

    data = load_corner_fixture("physics", root=RUNTIME_ROOT)
    frames = progress_frames(data)
    assert len(frames) == len(RUNTIME_SECTION_ORDER) + 2
    assert len(frames) <= MAX_PROGRESS_FRAMES


def test_runtime_overlay_fails_when_a_required_section_is_removed():
    import copy

    from docich.new_corner_runtime import load_corner_fixture, render_overlay

    data = load_corner_fixture("physics", root=RUNTIME_ROOT)
    mutated = copy.deepcopy(data)
    del mutated["topic"]["physicalLaw"]
    try:
        render_overlay(mutated)
    except ValueError:
        pass
    else:
        raise AssertionError("runtime overlay did not reject a missing section")


def test_runtime_overlay_fails_when_citation_urls_are_removed():
    import copy

    from docich.new_corner_runtime import load_corner_fixture, render_overlay

    data = load_corner_fixture("physics", root=RUNTIME_ROOT)
    mutated = copy.deepcopy(data)
    mutated["topic"]["url"] = ""
    mutated["topic"]["primarySources"] = []
    mutated["verifiedWebMaterial"]["items"] = []
    try:
        render_overlay(mutated)
    except ValueError as exc:
        assert "citation" in str(exc).lower()
    else:
        raise AssertionError("runtime overlay did not fail on mutated citations")


def test_runtime_progression_fails_when_the_end_frame_drops_its_citations():
    from docich.new_corner_runtime import (
        load_corner_fixture,
        progress_frames,
        validate_status,
    )

    data = load_corner_fixture("physics", root=RUNTIME_ROOT)
    frames = [dict(frame) for frame in progress_frames(data)]
    frames[-1]["citationUrls"] = []
    try:
        validate_status(data, frames)
    except ValueError:
        pass
    else:
        raise AssertionError("status check accepted an end frame without citations")


def test_runtime_progression_fails_when_it_cites_something_else_than_the_status():
    from docich.new_corner_runtime import (
        load_corner_fixture,
        progress_frames,
        validate_status,
    )

    data = load_corner_fixture("physics", root=RUNTIME_ROOT)
    frames = [dict(frame) for frame in progress_frames(data)]
    for frame in (frames[0], frames[-1]):
        frame["citationUrls"] = ["https://example.org/other/synthetic-source"]
    try:
        validate_status(data, frames)
    except ValueError:
        pass
    else:
        raise AssertionError("status check accepted frames that disagree with it")


def test_runtime_progression_fails_when_caveats_are_dropped():
    import copy

    from docich.new_corner_runtime import load_corner_fixture, progress_frames

    data = load_corner_fixture("physics", root=RUNTIME_ROOT)
    mutated = copy.deepcopy(data)
    del mutated["topic"]["limitations"]
    del mutated["topic"]["uncertainty"]
    try:
        progress_frames(mutated)
    except ValueError:
        pass
    else:
        raise AssertionError("runtime progression did not reject dropped caveats")


def test_runtime_section_order_follows_the_design_doc():
    """The design doc's 7-step order plus the display fields it retains."""

    from docich.new_corner_runtime import build_draft, load_corner_fixture

    data = load_corner_fixture("physics", root=RUNTIME_ROOT)
    draft = build_draft(data)
    assert [record["order"] for record in draft] == list(range(1, 8))
    assert [record["key"] for record in draft] == list(RUNTIME_SECTION_ORDER)
    assert [record["label"] for record in draft] == [
        "fact",
        "fact",
        "fact",
        "fact",
        "analysis",
        "analysis",
        "analysis",
    ]
