import json
import re
from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "math_corner"
    / "fixed_math_example.json"
)

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
URL_RE = re.compile(r"^https://")

REQUIRED_TOPIC_FIELDS = {
    "title",
    "topicId",
    "mathField",
    "mathematicsField",
    "todaysQuestion",
    "intuitivePrediction",
    "observedPhenomenon",
    "smallExample",
    "physicalLaw",
    "mathematicalPrinciple",
    "mathematicalModel",
    "mathematicalLaw",
    "formulaAndModel",
    "generalization",
    "experimentAndTechnologyApplication",
    "application",
    "realWorldApplication",
    "intuitionFailurePoint",
    "intuitiveSummary",
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
    assert load_fixture()["corner"] == "math"


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
    assert topic["mathField"]
    assert topic["mathematicsField"]
    assert topic["todaysQuestion"]
    assert topic["intuitivePrediction"]
    assert topic["observedPhenomenon"]
    assert topic["smallExample"]
    assert topic["mathematicalModel"]
    assert topic["formulaAndModel"]
    assert topic["generalization"]
    assert topic["realWorldApplication"]
    assert topic["intuitiveSummary"]
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["mustIncludeTodaysQuestion"] is True
    assert acceptance["mustIncludeIntuitivePrediction"] is True
    assert acceptance["mustIncludeObservedPhenomenon"] is True
    assert acceptance["mustIncludeSmallExample"] is True
    assert acceptance["mustIncludeMathematicalModel"] is True
    assert acceptance["mustIncludeSimpleFormulaOrModel"] is True
    assert acceptance["mustIncludeGeneralization"] is True
    assert acceptance["mustIncludeRealWorldApplication"] is True
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
    assert acceptance["mustNotClaimRealMathematics"] is True
    assert acceptance["mustNotClaimRealPhysics"] is True
    assert acceptance["mustNotPresentSyntheticAsReal"] is True
    assert acceptance["mustKeepSourceUrls"] is True
    assert acceptance["mustNotClaimRealPublication"] is True
    assert acceptance["mustKeepCitations"] is True
    assert acceptance["overlayAndProgressSplitAfterInputContract"] is True
    assert acceptance["partialCompletionAllowed"] is True
    assert acceptance["independentYouTubeDraftRequired"] is True
