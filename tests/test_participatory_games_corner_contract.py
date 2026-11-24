import json
import re
from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "participatory_games_corner"
    / "fixed_participatory_games_example.json"
)

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
URL_RE = re.compile(r"^https://")

# Use broad required fields (all we know)
REQUIRED_TOPIC_FIELDS = {
    "title","topicId","eventDate","geography","actors","thirtySecondSummary",
    "oneLineSummary","whatHappened","partyClaims","institutionalLegalPosition",
    "historicalBackground","geoEconomicSecurityConditions","keyDisputes",
    "opposingViewpoints","japanImpact","indicatorsAndEvents","labelsPolicy",
    "segmentLabels","limitations","uncertainty","figureOrDiagram",
    "youtubeIndependentDraft","primarySources","sourcePriority","citationKeepUrl","url",
    "physicsField","todaysQuestion","intuitivePrediction","observedPhenomenon",
    "physicalLaw","formulaAndModel","experimentAndTechnologyApplication",
    "intuitionFailurePoint","requirementsAndConstraints","formulaEntries",
    "figureAndFormulaHandling","mathField","mathematicalLaw","mathematicalModel",
    "mathematicalPrinciple","mathematicsField","meaning","mechanismAndPrinciples",
    "mechanismTheme","merikenPerspective","merikenView","moat","multipleRegionPerspectives",
    "naiveApproach","newsApplication","nuance","offering","operatingEnvironment",
    "operation","physicalQuantities","population","pricing","principles",
    "problemContext","problemStatement","questionOfDay","quotePolicy",
    "realWorldApplication","redundancy","region","revenueModel","safetyDesign",
    "scaleAndCapacity","scalingDynamics","shortQuote","similarExpressions",
    "siteContext","smallExample","socialEconomicImpact","sourceAccessNote",
    "sourceMaterialBrief","sourceTitle","sourceType","stakeholderImpact",
    "structure","structureAndMaterials","structureAndMethod","subjectSystem",
    "terrainClimate","timeline","title","todayExample","todayQuestion","todaysQuestion",
    "topicId","tradeoffs","transportRoutes","turningPoints","uncertainty",
    "unitEconomics","valueProposition","vulnerabilities","weakPoints","weaknesses",
    "whatHappened","whatItSupports","youtubeIndependentDraft",
    "supportedGames","gameList","gameModes","matchType","matchFormat","players",
    "playerCount","comradeMatch","participatory","puyo","tetris","othello",
    "tsuitateShogi","fps","gameRules","controls","scoring","winCondition",
    "spectatorView","chatCommands","roundStructure","difficulty","aiOpponent",
    "multiplayer","realTime","turnBased","boardSize","pieceSet",
}

def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))

def test_fixture_exists_and_parses():
    assert FIXTURE.exists()
    assert load_fixture()["corner"] == "participatory-games"

def test_top_level_contract():
    data = load_fixture()
    assert data["schemaVersion"] == 1
    assert data["isSyntheticFixture"] is True
    assert "nonProductionAcceptance" in data
    topic = data["topic"]
    assert REQUIRED_TOPIC_FIELDS.issubset(topic.keys())
    assert topic["citationKeepUrl"] is True
    assert isinstance(topic["primarySources"], list) and topic["primarySources"]

def test_source_urls_are_https():
    data = load_fixture()
    topic = data["topic"]
    assert URL_RE.match(topic["url"])
    for url in topic["primarySources"]:
        assert URL_RE.match(url)
    for item in data["verifiedWebMaterial"]["items"]:
        assert URL_RE.match(item["url"])

def test_non_production_acceptance_boundary():
    data = load_fixture()
    acceptance = data["nonProductionAcceptance"]
    assert acceptance["requiresReviewerApproval"] is True
    assert acceptance["mustNotPresentSyntheticAsReal"] is True
    assert acceptance["mustKeepSourceUrls"] is True
    assert acceptance["overlayAndProgressSplitAfterInputContract"] is True
    assert acceptance["partialCompletionAllowed"] is True
    assert acceptance["independentYouTubeDraftRequired"] is True

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
        for key in ("url","body_sha256","text_sha256","excerpt_sha256","excerpt","query_indexes"):
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
