import json
import re
from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "research_paper_corner"
    / "fixed_paper_example.json"
)

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
URL_RE = re.compile(r"^https://")

REQUIRED_PAPER_FIELDS = {
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
}


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_exists_and_parses():
    assert FIXTURE.exists()
    assert load_fixture()["corner"] == "research-paper"


def test_top_level_contract():
    data = load_fixture()
    assert data["schemaVersion"] == 1
    assert data["isSyntheticFixture"] is True
    assert "nonProductionAcceptance" in data
    paper = data["paper"]
    assert REQUIRED_PAPER_FIELDS.issubset(paper.keys())
    assert paper["citationKeepUrl"] is True
    assert isinstance(paper["authors"], list) and paper["authors"]
    assert isinstance(paper["primarySources"], list) and len(paper["primarySources"]) >= 1


def test_source_urls_are_https():
    data = load_fixture()
    paper = data["paper"]
    assert URL_RE.match(paper["url"])
    for url in paper["primarySources"]:
        assert URL_RE.match(url)
    for item in data["verifiedWebMaterial"]["items"]:
        assert URL_RE.match(item["url"])


def test_review_and_research_caveats_are_explicit():
    data = load_fixture()
    paper = data["paper"]
    assert paper["reviewStatus"] in {"preprint", "peer-reviewed", "unknown"}
    assert paper["limitations"]
    assert data["nonProductionAcceptance"]["mustStatePreprintStatusIfUnreviewed"] is True
    assert data["nonProductionAcceptance"]["mustIncludeResearchCaveats"] is True
    if paper["reviewStatus"] != "peer-reviewed":
        assert "preprint" in paper["limitations"].lower() or "査読" in paper["limitations"]


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
    assert acceptance["mustNotClaimRealPublication"] is True
    assert acceptance["overlayAndProgressSplitAfterInputContract"] is True
