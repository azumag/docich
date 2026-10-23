import hashlib
import json

from docich.llm.contracts import DispatchResult
from docich.research_paper_discovery import PaperCandidate
from docich.research_paper_draft import generate_record
from docich.research_paper_primary import ENABLE_ENV, fetch_primary_paper
from docich.reply_research_web import Receipt

URL = "https://arxiv.org/abs/2610.12345"
AGENT = "cloudflare-api:cf/qwen/qwen3-30b-a3b-fp8"
TEXT = (
    "arXiv:2610.12345 [cs.AI] 23 Oct 2026 "
    "Title: Bounded Public Evidence for Synthetic Agents "
    "Authors: Alice Example, Bob Example "
    "View a PDF of the paper titled Bounded Public Evidence for Synthetic Agents "
    "Abstract: We evaluate a synthetic agent architecture using bounded public evidence. "
    "The experiment compares two retrieval policies and reports uncertainty explicitly. "
    "Comments: 12 pages, synthetic fixture text for tests only "
    "Subjects: Artificial Intelligence (cs.AI); Machine Learning (cs.LG) "
    "Cite as: arXiv:2610.12345 [cs.AI] "
    "DOI: 10.5555/synthetic.arxiv.2610.12345 "
    "Submission history From: Alice Example [v1] Fri, 23 Oct 2026 12:34:56 UTC (123 KB)"
)


def candidate(url=URL):
    return PaperCandidate(url=url, source_kind="preprint", query_indexes=(0,), rank=1)


def receipt(text=TEXT, *, url=URL, text_sha=None):
    return Receipt(
        url=url,
        receipt="synthetic-receipt",
        sha256="a" * 64,
        text_sha256=text_sha or hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
    )


class Broker:
    def __init__(self, value):
        self.value = value
        self.authorized = []
        self.fetched = []

    def authorize(self, urls):
        self.authorized.extend(urls)

    def fetch(self, url):
        self.fetched.append(url)
        return self.value


def test_disabled_primary_adapter_has_zero_fetch_calls():
    broker = Broker(receipt())
    result = fetch_primary_paper(
        candidate(), queries_used=["synthetic agent evidence"], env={}, broker=broker,
    )
    assert result.status == "disabled"
    assert broker.authorized == []
    assert broker.fetched == []


def test_non_arxiv_candidate_is_rejected_before_fetch():
    broker = Broker(receipt())
    other = PaperCandidate(
        url="https://openreview.net/forum?id=synthetic123",
        source_kind="review-platform",
        query_indexes=(0,),
        rank=1,
    )
    result = fetch_primary_paper(
        other,
        queries_used=["synthetic agent evidence"],
        env={ENABLE_ENV: "1"},
        broker=broker,
    )
    assert result.status == "unsupported_source"
    assert broker.authorized == []
    assert broker.fetched == []


def test_arxiv_receipt_builds_trusted_metadata_and_verified_bundle():
    broker = Broker(receipt())
    result = fetch_primary_paper(
        candidate(),
        queries_used=["synthetic agent evidence"],
        env={ENABLE_ENV: "1"},
        broker=broker,
    )
    assert result.ok
    assert broker.authorized == [URL]
    assert broker.fetched == [URL]
    assert result.metadata == {
        "title": "Bounded Public Evidence for Synthetic Agents",
        "authors": ["Alice Example", "Bob Example"],
        "affiliations": [],
        "field": "Artificial Intelligence (cs.AI)",
        "publishedAt": "2026-10-23",
        "reviewStatus": "preprint",
        "url": URL,
        "primarySources": [URL],
        "doi": "10.5555/synthetic.arxiv.2610.12345",
    }
    assert result.bundle.status == "ok"
    assert result.bundle.queries_used == ("synthetic agent evidence",)
    item = result.bundle.items[0]
    assert item.url == URL
    assert item.query_indexes == (0,)
    assert item.text_sha256 == hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    assert item.excerpt_sha256 == hashlib.sha256(item.excerpt.encode("utf-8")).hexdigest()
    assert "Abstract:" in item.excerpt


def test_forged_receipt_hash_is_rejected():
    broker = Broker(receipt(text_sha="0" * 64))
    result = fetch_primary_paper(
        candidate(),
        queries_used=["synthetic agent evidence"],
        env={ENABLE_ENV: "1"},
        broker=broker,
    )
    assert result.status == "unavailable"


def test_missing_abstract_is_rejected_as_unavailable():
    text = TEXT.replace("Abstract:", "Summary:")
    broker = Broker(receipt(text))
    result = fetch_primary_paper(
        candidate(),
        queries_used=["synthetic agent evidence"],
        env={ENABLE_ENV: "1"},
        broker=broker,
    )
    assert result.status == "unavailable"


def test_primary_output_feeds_direct_draft_without_search_or_opencode():
    primary = fetch_primary_paper(
        candidate(),
        queries_used=["synthetic agent evidence"],
        env={ENABLE_ENV: "1"},
        broker=Broker(receipt()),
    )
    assert primary.ok

    output = json.dumps({
        "thirtySecondSummary": "合成テスト用の論文原稿です。",
        "researchQuestion": "公開情報を有界に扱う方法を比較します。",
        "priorProblem": "取得範囲と根拠の境界が曖昧でした。",
        "novelty": "取得済み一次情報だけを入力に固定します。",
        "experimentStrength": "合成試験で二つの方策を比較しています。",
        "limitations": "査読前のpreprintであり、外的妥当性は未確認です。",
        "practicalUse": "根拠付きの調査パイプライン設計に使えます。",
        "docichApplication": "RADIOコーナーの論文紹介入力に利用できます。",
        "figureOrSimpleExplanation": "候補発見から一次情報検証までを矢印で示します。",
        "applicationTargets": ["docich", "research workflow"],
    }, ensure_ascii=False)

    drafted = generate_record(
        primary.metadata,
        primary.bundle,
        agents=AGENT,
        env={
            "DOCICH_RESEARCH_PAPER_DIRECT_ENABLED": "1",
            "DOCICH_ALLOW_REAL_AI": "1",
        },
        generator=lambda *args, **kwargs: DispatchResult(0, output, AGENT, attempted=1),
    )
    assert drafted.ok
    assert drafted.record["paper"]["title"] == primary.metadata["title"]
    assert drafted.record["paper"]["reviewStatus"] == "preprint"
    assert drafted.record["verifiedWebMaterial"]["items"][0]["url"] == URL
