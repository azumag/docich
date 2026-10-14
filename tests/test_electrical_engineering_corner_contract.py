import json
import re
from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "electrical_engineering_corner"
    / "fixed_electronics_example.json"
)

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
URL_RE = re.compile(r"^https://")

REQUIRED_TOPIC_FIELDS = {
    "title",
    "topicId",
    "thirtySecondSummary",
    "oneLineSummary",
    "limitations",
    "figureOrDiagram",
    "youtubeIndependentDraft",
    "primarySources",
    "sourcePriority",
    "citationKeepUrl",
}

def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))

def test_fixture_exists_and_parses():
    assert FIXTURE.exists()
    assert load_fixture()["corner"] == "electrical_engineering"

def test_top_level_contract():
    data = load_fixture()
    assert data["schemaVersion"] == 1
    assert data["isSyntheticFixture"] is True
    assert "nonProductionAcceptance" in data
    t = data["topic"]
    assert REQUIRED_TOPIC_FIELDS.issubset(t.keys())
    assert t["citationKeepUrl"] is True
    assert isinstance(t["primarySources"], list) and t["primarySources"]
    assert isinstance(t["requirementsAndConstraints"], list) and t["requirementsAndConstraints"]

def test_source_urls_are_https():
    data = load_fixture()
    t = data["topic"]
    for url in t["primarySources"]:
        assert URL_RE.match(url)
    for item in data["verifiedWebMaterial"]["items"]:
        assert URL_RE.match(item["url"])

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
        qidx = item["query_indexes"]
        assert isinstance(qidx, list) and qidx
        assert qidx == sorted(set(qidx))
        assert all(isinstance(i, int) and 0 <= i < len(queries_used) for i in qidx)

def test_non_production_acceptance_boundary():
    data = load_fixture()
    a = data["nonProductionAcceptance"]
    assert a["requiresReviewerApproval"] is True
    assert a["mustNotPresentSyntheticAsReal"] is True
    assert a["mustKeepSourceUrls"] is True
    assert a["overlayAndProgressSplitAfterInputContract"] is True
    assert a["partialCompletionAllowed"] is True
    assert a["independentYouTubeDraftRequired"] is True
