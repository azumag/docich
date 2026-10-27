import hashlib
import json

from docich.llm.contracts import DispatchResult
from docich.new_corner_runtime import progress_frames, render_overlay, validate_status
from docich.research_paper_draft import ENABLE_ENV, generate_record
from docich.web_material import VerifiedWebBundle, VerifiedWebMaterial

URL = "https://example.org/papers/synthetic-observation-study"
SUPPLEMENT = "https://example.org/papers/synthetic-observation-study/supplement"
AGENT = "cloudflare-api:cf/qwen/qwen3-30b-a3b-fp8"


def metadata():
    return {
        "title": "Synthetic Observation Study",
        "authors": ["Example Author A", "Example Author B"],
        "affiliations": ["Example Lab"],
        "field": "complex systems",
        "publishedAt": "2026-10-20",
        "reviewStatus": "preprint",
        "doi": "10.5555/example.synthetic.001",
        "url": URL,
        "primarySources": [URL, SUPPLEMENT],
    }


def bundle(url=URL, excerpt=None):
    text = excerpt or (
        "This synthetic primary-source excerpt describes a bounded observational study. "
        "It reports a small sample, a preregistered comparison, and explicitly states that "
        "the observed association does not establish causation."
    )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    item = VerifiedWebMaterial(
        url=url,
        body_sha256="a" * 64,
        text_sha256="b" * 64,
        excerpt_sha256=digest,
        excerpt=text,
        query_indexes=(0,),
    )
    return VerifiedWebBundle("ok", (item,), ("synthetic observation study",))


def generated_output():
    # Deliberately use a different key order. The contract is the exact key set,
    # not provider serialization order.
    return json.dumps({
        "researchQuestion": "研究者は限られた観測数で、観測順と推定誤差の関係を調べました。",
        "thirtySecondSummary": "小さな標本の観測研究で、観測順によって推定の安定性が変わる可能性を比較した合成例です。",
        "priorProblem": "観測資源が少ない状況では、何を先に測るべきかの比較が不足していました。",
        "novelty": "観測順を固定して比較し、推定誤差との関係を明示した点が新規性です。",
        "experimentStrength": "小標本の観測研究であり、事前に決めた比較を使っていますが、因果関係の証明ではありません。",
        "limitations": "査読前のpreprintという前提で、小標本・観測研究・一般化可能性に留保が必要です。",
        "practicalUse": "限られた計測予算で、観測の優先順位を設計する際の参考にできます。",
        "docichApplication": "docichやMoYoGardenの計測設計で、先に取得する指標を決める比較実験へ応用できます。",
        "figureOrSimpleExplanation": "横軸を観測順、縦軸を推定誤差にした単純な比較図で説明できます。",
        "applicationTargets": ["measurement planning", "docich", "MoYoGarden"],
    }, ensure_ascii=False)


def enabled_env():
    return {
        ENABLE_ENV: "1",
        "DOCICH_ALLOW_REAL_AI": "1",
    }


def test_disabled_path_has_zero_generation_calls():
    called = []
    result = generate_record(
        metadata(), bundle(), agents=AGENT, env={},
        generator=lambda **kwargs: called.append(kwargs),
    )
    assert result.status == "disabled"
    assert called == []


def test_valid_verified_material_generates_runtime_compatible_record():
    seen = {}

    def generator(*args, **kwargs):
        seen.update(kwargs)
        return DispatchResult(0, generated_output(), AGENT, attempted=1)

    result = generate_record(
        metadata(), bundle(), agents=AGENT, env=enabled_env(), generator=generator,
    )
    assert result.ok
    record = dict(result.record)
    assert record["corner"] == "research-paper"
    assert record["isSyntheticFixture"] is False
    assert record["paper"]["reviewStatus"] == "preprint"
    assert record["paper"]["primarySources"] == [URL, SUPPLEMENT]
    assert record["verifiedWebMaterial"]["items"][0]["url"] == URL
    assert seen["label"] == "RADIO:paper-corner-draft"
    assert seen["agents"] == AGENT
    assert seen["timeout_sec"] <= 45
    assert "untrusted DATA" in seen["prompt_text"]
    assert "Do not search" in seen["prompt_text"]

    overlay = render_overlay(record)
    assert overlay["title"] == metadata()["title"]
    assert overlay["caveats"]["reviewStatus"] == "preprint"
    frames = progress_frames(record)
    entry = validate_status(record, frames)
    assert entry["citationUrls"] == overlay["citationUrls"]
    assert frames[-1]["caveats"]["limitations"] == record["paper"]["limitations"]


def test_material_must_be_one_of_trusted_primary_sources():
    result = generate_record(
        metadata(), bundle("https://news.example.org/story"),
        agents=AGENT, env=enabled_env(),
        generator=lambda *a, **k: (_ for _ in ()).throw(AssertionError("generation")),
    )
    assert result.status == "invalid_input"


def test_forged_excerpt_hash_is_rejected_before_generation():
    good = bundle()
    item = good.items[0]
    forged = VerifiedWebMaterial(
        url=item.url,
        body_sha256=item.body_sha256,
        text_sha256=item.text_sha256,
        excerpt_sha256="0" * 64,
        excerpt=item.excerpt,
        query_indexes=item.query_indexes,
    )
    result = generate_record(
        metadata(), VerifiedWebBundle("ok", (forged,), good.queries_used),
        agents=AGENT, env=enabled_env(),
        generator=lambda *a, **k: (_ for _ in ()).throw(AssertionError("generation")),
    )
    assert result.status == "invalid_input"


def test_opencode_chain_is_rejected_before_generation():
    result = generate_record(
        metadata(), bundle(), agents="opencode:legacy", env=enabled_env(),
        generator=lambda *a, **k: (_ for _ in ()).throw(AssertionError("generation")),
    )
    assert result.status == "invalid_config"


def test_generated_output_cannot_add_urls_or_unknown_fields():
    payload = json.loads(generated_output())
    payload["novelty"] += " https://unverified.example.org/claim"
    payload["extra"] = "not allowed"
    result = generate_record(
        metadata(), bundle(), agents=AGENT, env=enabled_env(),
        generator=lambda *a, **k: DispatchResult(0, json.dumps(payload, ensure_ascii=False), AGENT),
    )
    assert result.status == "invalid_output"


def test_duplicate_json_key_is_rejected():
    raw = generated_output()
    # Replace the opening object with a duplicate required key before the valid
    # object fields. Duplicate-key rejection happens before semantic use.
    duplicate = '{"thirtySecondSummary":"重複",' + raw.lstrip()[1:]
    result = generate_record(
        metadata(), bundle(), agents=AGENT, env=enabled_env(),
        generator=lambda *a, **k: DispatchResult(0, duplicate, AGENT),
    )
    assert result.status == "invalid_output"


def test_preprint_status_and_limitations_are_preserved_separately():
    result = generate_record(
        metadata(), bundle(), agents=AGENT, env=enabled_env(),
        generator=lambda *a, **k: DispatchResult(0, generated_output(), AGENT),
    )
    assert result.ok
    paper = result.record["paper"]
    assert paper["reviewStatus"] == "preprint"
    assert "査読" in paper["limitations"] or "preprint" in paper["limitations"]
    assert result.record["nonProductionAcceptance"]["mustStatePreprintStatusIfUnreviewed"] is True
