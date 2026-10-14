import json
import re
from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "geography_world_corner"
    / "fixed_geography_example.json"
)

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
URL_RE = re.compile(r"^https://")

REQUIRED_TOPIC_FIELDS = {
    "title",
    "topicId",
    "region",
    "mapFocus",
    "thirtySecondSummary",
    "oneLineSummary",
    "terrainClimate",
    "population",
    "resources",
    "transportRoutes",
    "industry",
    "historyPoliticsImpact",
    "currentMeaning",
    "mapHandling",
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
    assert load_fixture()["corner"] == "geography-world"


def test_top_level_contract():
    data = load_fixture()
    assert data["schemaVersion"] == 1
    assert data["isSyntheticFixture"] is True
    assert "nonProductionAcceptance" in data
    topic = data["topic"]
    assert REQUIRED_TOPIC_FIELDS.issubset(topic.keys())
    assert topic["citationKeepUrl"] is True
    assert isinstance(topic["primarySources"], list) and topic["primarySources"]
    assert isinstance(topic["transportRoutes"], list) and topic["transportRoutes"]
    assert isinstance(topic["industry"], list) and topic["industry"]
    assert isinstance(topic["segmentLabels"], list) and topic["segmentLabels"]


def test_source_urls_are_https():
    data = load_fixture()
    topic = data["topic"]
    assert URL_RE.match(topic["url"])
    for url in topic["primarySources"]:
        assert URL_RE.match(url)
    for item in data["verifiedWebMaterial"]["items"]:
        assert URL_RE.match(item["url"])


def test_source_priority_and_sections_are_explicit():
    data = load_fixture()
    topic = data["topic"]
    assert "official-geographic-or-public-source" in topic["sourcePriority"]
    assert topic["mapFocus"]
    assert topic["terrainClimate"]
    assert topic["population"]
    assert topic["resources"]
    assert topic["transportRoutes"]
    assert topic["industry"]
    assert topic["historyPoliticsImpact"]
    assert topic["currentMeaning"]
    assert topic["mapHandling"]
    assert topic["labelsPolicy"]
    assert "fact" in topic["segmentLabels"]
    assert "analysis" in topic["segmentLabels"]
    assert "forecast" in topic["segmentLabels"]
    assert topic["limitations"]
    assert topic["uncertainty"]
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["mustPrioritizeOfficialGeographicSources"] is True
    assert acceptance["mustIncludeMapHandling"] is True
    assert acceptance["mustIncludeTerrainClimate"] is True
    assert acceptance["mustIncludePopulation"] is True
    assert acceptance["mustIncludeResources"] is True
    assert acceptance["mustIncludeTransport"] is True
    assert acceptance["mustIncludeIndustry"] is True
    assert acceptance["mustIncludeHistoryPolitics"] is True
    assert acceptance["mustIncludeCurrentMeaning"] is True
    assert acceptance["mustIncludeFactAnalysisForecastLabels"] is True


def test_synthetic_map_metadata_states_unavailable_values():
    """Check the fixed synthetic input, not actual map rights or resolution."""
    data = load_fixture()
    assert data["isSyntheticFixture"] is True
    note = data["topic"]["mapHandling"]
    for field in ("resolution", "license", "usage conditions"):
        assert f"{field} = not provided (synthetic fixture)" in note
    assert "no real map license or usage permission is asserted" in note


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
    assert acceptance["mustNotPresentSyntheticAsReal"] is True
    assert acceptance["mustKeepSourceUrls"] is True
    assert acceptance["overlayAndProgressSplitAfterInputContract"] is True
    assert acceptance["partialCompletionAllowed"] is True
    assert acceptance["independentYouTubeDraftRequired"] is True
