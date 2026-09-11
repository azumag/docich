from __future__ import annotations

import json
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.corner_research import (  # noqa: E402
    HISTORY_FILENAME,
    RESEARCH_FILENAME,
    finalize_research_result,
    load_research_result,
    prepare_research_context,
)
from docich.trading.corner_script import build_facts, build_prompt  # noqa: E402
from docich.trading.paper_improve import build_improve_prompt  # noqa: E402

NOW = 1_800_000_000.0

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Bitcoin ETF flow shifts again - Source A</title><link>https://example/a</link><source>Source A</source><pubDate>Fri, 15 Jan 2027 00:00:00 GMT</pubDate><description>Institutional flow changed while spot volumes stayed mixed.</description></item>
<item><title>Ethereum developers discuss scaling roadmap - Source B</title><link>https://example/b</link><source>Source B</source><pubDate>Fri, 15 Jan 2027 00:01:00 GMT</pubDate><description>Developers discussed a future scaling roadmap and tradeoffs.</description></item>
<item><title>Stablecoin rules move forward - Source C</title><link>https://example/c</link><source>Source C</source><pubDate>Fri, 15 Jan 2027 00:02:00 GMT</pubDate><description>Lawmakers advanced a stablecoin framework.</description></item>
<item><title>Exchange liquidity changes - Source D</title><link>https://example/d</link><source>Source D</source><pubDate>Fri, 15 Jan 2027 00:03:00 GMT</pubDate><description>Several venues reported changes in visible liquidity.</description></item>
</channel></rss>"""


def _write_status(target: Path, positions=None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "worker_state": "paper_worker_idle",
        "snapshot_generated_at": NOW,
        "capital_reference": "10000",
        "deployed_reference": "3000",
        "open_positions": positions or {"BTC/JPY": "0.01", "ETH/JPY": "0.5"},
        "eligible_symbols": ["BTC/JPY", "ETH/JPY"],
        "recent_fills": [],
        "skipped_reason_codes": [],
        "signal_summary": {"candidate_count": 0, "candidate_reason_codes": []},
        "market_freshness": {"BTC/JPY": {"quality": "fresh"}, "ETH/JPY": {"quality": "fresh"}},
    }
    (target / "status.json").write_text(json.dumps(payload), encoding="utf-8")
    (target / "market_cache.json").write_text(
        json.dumps({"symbols": {"BTC/JPY": {"closes": [100, 101]}, "ETH/JPY": {"closes": [50, 51]}}}),
        encoding="utf-8",
    )


def _fetcher(url: str) -> str:
    if "wikipedia.org" in url:
        return json.dumps({"query": {"pages": [{"title": "Bitcoin", "extract": "Bitcoinは分散型の暗号資産で、2009年に運用が始まった。"}]}})
    return RSS


def _first(items):
    return items[0]


def _finalize(target: Path, context: dict, *, now: float) -> dict:
    return finalize_research_result(
        target,
        context,
        {
            "news_analysis": "ETF資金フローと流動性のニュースは同方向とは限らない。価格だけでなく板厚も観測したい。",
            "asset_spotlight": "保有中の銘柄を歴史と技術の両面から見ると、値動き以外の特徴が見えてくる。",
            "improvement_hints": [
                {
                    "kind": "data",
                    "title": "流動性変化を観測する",
                    "rationale": "ニュース単発で売買せず、板厚の継続変化を検証用特徴量として比較する。",
                    "evidence": "Exchange liquidity changes",
                    "confidence": "medium",
                }
            ],
        },
        now=now,
    )


def test_prepare_research_uses_public_news_and_only_held_asset(tmp_path):
    _write_status(tmp_path)
    context = prepare_research_context(tmp_path, now=NOW, fetcher=_fetcher, chooser=_first)
    assert context["status"] == "prepared"
    assert 1 <= len(context["news_items"]) <= 6
    assert context["news_items"][0]["source"] == "Source A"
    assert context["asset"]["symbol"] in {"BTC/JPY", "ETH/JPY"}
    assert context["asset"]["symbol"] == "BTC/JPY"
    assert context["asset"]["angle"] == "origin_history"
    assert "Bitcoin" in context["asset"]["name"]
    assert "2009" in context["asset"]["background"]
    path = tmp_path / RESEARCH_FILENAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_finalize_persists_analysis_hints_and_private_history(tmp_path):
    _write_status(tmp_path)
    context = prepare_research_context(tmp_path, now=NOW, fetcher=_fetcher, chooser=_first)
    result = _finalize(tmp_path, context, now=NOW)
    assert result["status"] == "finalized"
    assert result["improvement_hints"][0]["kind"] == "data"
    assert result["improvement_hints"][0]["confidence"] == "medium"
    assert "板厚" in result["news_analysis"]
    assert stat.S_IMODE((tmp_path / RESEARCH_FILENAME).stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / HISTORY_FILENAME).stat().st_mode) == 0o600
    loaded = load_research_result(tmp_path)
    assert loaded["status"] == "finalized"


def test_asset_and_angle_dedupe_rotate_between_corners(tmp_path):
    _write_status(tmp_path)
    first = prepare_research_context(tmp_path, now=NOW, fetcher=_fetcher, chooser=_first)
    assert first["asset"]["symbol"] == "BTC/JPY"
    _finalize(tmp_path, first, now=NOW)

    second_now = NOW + 86400
    second = prepare_research_context(tmp_path, now=second_now, fetcher=_fetcher, chooser=_first)
    assert second["asset"]["symbol"] == "ETH/JPY", "recently discussed held asset should be avoided"
    _finalize(tmp_path, second, now=second_now)

    # With one holding only, the symbol must repeat but the discussion angle should rotate.
    solo = tmp_path / "solo"
    _write_status(solo, {"BTC/JPY": "0.01"})
    day1 = prepare_research_context(solo, now=NOW, fetcher=_fetcher, chooser=_first)
    _finalize(solo, day1, now=NOW)
    day2 = prepare_research_context(solo, now=NOW + 86400, fetcher=_fetcher, chooser=_first)
    assert day1["asset"]["symbol"] == day2["asset"]["symbol"] == "BTC/JPY"
    assert day1["asset"]["angle"] != day2["asset"]["angle"]


def test_news_dedupe_prefers_unseen_headlines(tmp_path):
    _write_status(tmp_path)
    first = prepare_research_context(tmp_path, now=NOW, fetcher=_fetcher, chooser=_first)
    first_titles = [x["title"] for x in first["news_items"]]
    _finalize(tmp_path, first, now=NOW)

    # The same feed is allowed as a fallback when every item was already seen,
    # but duplicate keys never appear twice in one corner.
    second = prepare_research_context(tmp_path, now=NOW + 86400, fetcher=_fetcher, chooser=_first)
    second_titles = [x["title"] for x in second["news_items"]]
    assert len(second_titles) == len(set(second_titles))
    assert first_titles


def test_research_is_narration_only_and_excluded_from_automatic_improvement(tmp_path):
    _write_status(tmp_path)
    context = prepare_research_context(tmp_path, now=NOW, fetcher=_fetcher, chooser=_first)
    _finalize(tmp_path, context, now=NOW)

    facts = build_facts(tmp_path, now=NOW + 1)
    assert facts["research"]["status"] == "finalized"
    assert facts["research"]["improvement_hints"][0]["title"] == "流動性変化を観測する"
    narration_prompt = build_prompt(facts)
    assert "Google News RSS" in narration_prompt
    assert "improvement_hints" in narration_prompt
    improve_prompt = build_improve_prompt(facts)
    assert "流動性変化を観測する" not in improve_prompt
    assert "Exchange liquidity changes" not in improve_prompt
    assert '"research"' not in improve_prompt


def test_missing_or_corrupt_research_fails_closed(tmp_path):
    assert load_research_result(tmp_path) == {}
    (tmp_path / RESEARCH_FILENAME).write_text("not-json", encoding="utf-8")
    assert load_research_result(tmp_path) == {}
