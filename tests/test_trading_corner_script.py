from __future__ import annotations

import json
import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from docich.trading.corner_script import (  # noqa: E402
    MAX_SEGMENT_CHARS,
    SEGMENT_KEYS,
    CornerScriptError,
    _condition_text,
    build_facts,
    build_next_prompt,
    build_prompt,
    generate_corner_script,
    generate_next_narration,
    merge_script_segments,
    parse_next_narration,
    parse_script,
    render_fallback,
)
from docich.trading.strategies import StrategyPolicy  # noqa: E402


def _write_status(trading_dir: Path, **overrides) -> None:
    trading_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "worker_state": "paper_worker_idle",
        "snapshot_generated_at": 1000.0,
        "capital_reference": "10000",
        "deployed_reference": "3000",
        "open_positions": {"btc_jpy": "0.001", "eth_jpy": "0"},
        "eligible_symbols": ["btc_jpy", "eth_jpy"],
        "recent_fills": [
            {
                "fill_id": "paper:x",
                "symbol": "btc_jpy",
                "side": "buy",
                "amount": "0.001",
                "price": "10000000",
                "quote": "btc_jpy",
                "filled_at": 990.0,
                "reason_code": "momentum_breakout",
            }
        ],
        "skipped_reason_codes": ["correlated_exposure"],
        "skipped_decisions": [
            {"symbol": "xrp_jpy", "side": "buy", "reason_code": "below_min_amount"}
        ],
        "signal_summary": {
            "candidate_count": 2,
            "candidate_reason_codes": ["momentum_breakout"],
        },
        "market_freshness": {
            "btc_jpy": {"quality": "fresh"},
            "eth_jpy": {"quality": "stale"},
        },
    }
    status.update(overrides)
    (trading_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (trading_dir / "market_cache.json").write_text(
        json.dumps({"symbols": {"btc_jpy": {"closes": [1, 2, 3, 4]}}}), encoding="utf-8"
    )


def test_build_facts_uses_status_policy_and_skip_context(tmp_path):
    _write_status(tmp_path)
    policy = StrategyPolicy(momentum_lookback=9, momentum_threshold_bps=Decimal("111"))
    facts = build_facts(tmp_path, now=1010.0, policy=policy)
    assert facts["capital_jpy"] == "10000"
    assert facts["deployed_jpy"] == "3000"
    assert facts["position_count"] == 1
    assert facts["open_positions_top"][0]["symbol"] == "btc_jpy"
    assert facts["candidate_count"] == 2
    assert facts["policy"]["momentum_lookback"] == 9
    assert facts["policy"]["momentum_threshold_bps"] == "111"
    assert facts["fresh_markets"] == 1
    assert facts["total_markets"] == 2
    assert "モメンタム上振れ" in facts["candidate_reasons"]
    assert facts["skipped_decisions"][0]["symbol"] == "xrp_jpy"
    assert facts["skipped_decisions"][0]["side_label"] == "買い"
    assert "performance" in facts


def test_build_facts_survives_missing_files(tmp_path):
    facts = build_facts(tmp_path, now=5.0)
    assert facts["capital_jpy"] == "?"
    assert facts["open_positions_top"] == []
    assert facts["policy"]["momentum_lookback"] == 6


def test_prompt_requires_interpretation_and_pnl_commentary(tmp_path):
    _write_status(tmp_path)
    prompt = build_prompt(build_facts(tmp_path, now=1010.0))
    assert "数字の読み上げ係ではありません" in prompt
    assert "累積損益" in prompt
    assert "本日の確定損益" in prompt
    assert "軽いツッコミ" in prompt
    assert "損失なら言い訳せず" in prompt


def test_corner_facts_and_fallback_include_theoretical_comparison(tmp_path):
    now = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9))).timestamp()
    _write_status(tmp_path)
    day_start = dt.datetime.fromtimestamp(
        now, tz=dt.timezone(dt.timedelta(hours=9))
    ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    history = [
        {
            "timestamp": day_start + index * 300,
            "close": {0: "100", 1: "90", 2: "120"}.get(index, "110"),
        }
        for index in range(int((now - day_start) // 300))
    ]
    (tmp_path / "market_cache.json").write_text(
        json.dumps({
            "schema_version": 1,
            "symbols": {
                "btc_jpy": {
                    "fetched_at": now,
                    "data_as_of": now - 300,
                    "timeframe_s": 300,
                    "closes": ["100", "90", "120", "110"],
                    "history": history,
                }
            },
        }),
        encoding="utf-8",
    )

    facts = build_facts(tmp_path, now=now)
    benchmark = facts["performance"]["theoretical_benchmark"]
    assert benchmark["status"] == "ready"
    assert benchmark["best_symbol"] == "btc_jpy"
    fallback = render_fallback(facts)
    assert "理論値" in fallback["result"]


def test_render_fallback_is_deterministic_and_grounded(tmp_path):
    _write_status(tmp_path)
    facts = build_facts(tmp_path, now=1010.0)
    first = render_fallback(facts)
    assert first == render_fallback(facts)
    assert set(first) == set(SEGMENT_KEYS)
    assert "10,000" in first["result"]
    assert "btc_jpy" in first["result"]
    assert "xrp_jpy" in first["result"]
    assert "本日の確定損益" in first["result"]
    assert "戦略" in first["strategy"]
    assert all(isinstance(value, str) and value for value in first.values())
    assert sum(len(value) for value in first.values()) >= 500


def test_build_facts_includes_latest_improvement(tmp_path):
    _write_status(tmp_path)
    logs = tmp_path.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "paper-corner-improve-2026-09-11.log").write_text(
        json.dumps({
            "changed": True,
            "status": "improved",
            "policy": {
                "momentum_lookback": 5,
                "momentum_threshold_bps": "150",
                "mean_reversion_lookback": 10,
                "mean_reversion_z": "-1.8",
                "max_notional_fraction": "0.12",
            },
        }) + "\n",
        encoding="utf-8",
    )
    facts = build_facts(tmp_path, now=1010.0)
    assert facts["improvement"]["status"] == "improved"
    assert facts["improvement"]["changed"] is True
    assert facts["improvement"]["policy"]["momentum_lookback"] == 5
    assert "focus" in facts
    assert "improvement" in facts


def test_parse_script_accepts_fenced_and_partial_output():
    good = '```json\n{"corner":"a","strategy":"b","result":"c","improve":"d"}\n```'
    assert parse_script(good) == {
        "corner": "a", "strategy": "b", "result": "c", "improve": "d"
    }
    prose = '前置きです。\n{"corner":"a","strategy":"b","result":"c","improve":"d"}\n以上です。'
    assert parse_script(prose)["corner"] == "a"
    assert parse_script(
        '{"corner":"a","strategy":"b","result":"c","improve":"d","extra":"x"}'
    ) == {"corner": "a", "strategy": "b", "result": "c", "improve": "d"}
    truncated = parse_script(
        '{"corner":"' + "あ" * (MAX_SEGMENT_CHARS + 50) + '","strategy":"b","result":"c","improve":"d"}'
    )
    assert len(truncated["corner"]) == MAX_SEGMENT_CHARS
    # Partial output keeps the usable segments instead of failing the script.
    assert parse_script('{"corner":"a"}') == {"corner": "a"}
    assert parse_script('{"corner":"a","strategy":"","result":"c"}') == {
        "corner": "a", "result": "c"
    }
    with pytest.raises(CornerScriptError):
        parse_script("not json")
    with pytest.raises(CornerScriptError):
        parse_script('{"news_analysis":"x","improvement_hints":[]}')


def test_merge_script_segments_fills_missing_from_fallback():
    fallback = {key: f"F:{key}" for key in SEGMENT_KEYS}
    merged = merge_script_segments(fallback, {"chart": "A:chart", "strategy": "A:strategy"})
    assert set(merged) == set(SEGMENT_KEYS)
    assert merged["chart"] == "A:chart"
    assert merged["strategy"] == "A:strategy"
    assert merged["corner"] == "F:corner"
    assert merged["improve"] == "F:improve"


def test_generate_falls_back_when_ai_disabled(tmp_path, monkeypatch):
    _write_status(tmp_path)
    monkeypatch.delenv("DOCICH_ALLOW_REAL_AI", raising=False)
    result = generate_corner_script(None, trading_dir=tmp_path, agents="opencode:x", now=1010.0)
    assert result["source"] == "fallback"
    assert result["reason"] == "real-ai-disabled"
    assert set(result["segments"]) == set(SEGMENT_KEYS)


def test_generate_dry_run_never_calls_ai(tmp_path):
    _write_status(tmp_path)
    result = generate_corner_script(
        None, trading_dir=tmp_path, agents="opencode:x", dry_run=True, now=1010.0
    )
    assert result["source"] == "fallback"
    assert result["reason"] == "dry-run"
    assert set(result["segments"]) == set(SEGMENT_KEYS)


_TIMEFRAME_FACTS = {
    "symbol": "btc_jpy",
    "generated_at": 1010.0,
    "timeframes": [
        {
            "timeframe": "1d",
            "label": "日足",
            "bars": 22,
            "trend": "上昇",
            "range_change_pct": 4.2,
            "momentum_pct": 1.1,
            "bb_upper": "110",
            "bb_lower": "90",
            "bb_position": 0.85,
            "bb_phrase": "バンド上限寄り",
            "high": "112",
            "low": "88",
            "last_close": "109",
            "stale": False,
        },
        {
            "timeframe": "1m",
            "label": "1分足",
            "bars": 60,
            "trend": "下降",
            "range_change_pct": -0.4,
            "momentum_pct": -0.2,
            "bb_upper": "110",
            "bb_lower": "108",
            "bb_position": 0.1,
            "bb_phrase": "バンド下限寄り",
            "high": "111",
            "low": "107",
            "last_close": "108.2",
            "stale": False,
        },
    ],
    "fill_timeframes": [
        {
            "symbol": "eth_jpy",
            "side": "sell",
            "price": "200",
            "filled_at": 990.0,
            "timeframes": [
                {
                    "timeframe": "1h",
                    "label": "1時間足",
                    "bars": 48,
                    "trend": "上昇",
                    "range_change_pct": 1.5,
                    "momentum_pct": 0.6,
                    "bb_upper": "210",
                    "bb_lower": "190",
                    "bb_position": 0.7,
                    "bb_phrase": "バンド上限寄り",
                    "stale": False,
                }
            ],
        }
    ],
}


def test_build_facts_and_prompt_include_multi_timeframe_walk(tmp_path):
    _write_status(tmp_path)
    facts = build_facts(tmp_path, now=1010.0, timeframes=_TIMEFRAME_FACTS)
    assert facts["timeframes"][0]["label"] == "日足"
    assert facts["fill_timeframes"][0]["symbol"] == "eth_jpy"
    prompt = build_prompt(facts)
    assert "時間足チャートの解説" in prompt
    assert "次の11キー" in prompt
    assert "- chart:" in prompt
    assert "- fills:" in prompt
    assert "- review:" in prompt
    assert "ボリンジャーバンド" in prompt


def test_build_facts_without_timeframes_stays_empty(tmp_path):
    _write_status(tmp_path)
    facts = build_facts(tmp_path, now=1010.0)
    assert facts["timeframes"] == []
    assert facts["fill_timeframes"] == []


def test_render_fallback_chart_is_grounded_or_honest(tmp_path):
    _write_status(tmp_path)
    grounded = render_fallback(build_facts(tmp_path, now=1010.0, timeframes=_TIMEFRAME_FACTS))
    assert "日足" in grounded["chart"]
    assert "1分足" in grounded["chart"]
    assert "eth_jpy" in grounded["chart"]
    assert "高値づかみ" in grounded["chart"]
    honest = render_fallback(build_facts(tmp_path, now=1010.0))
    assert "取得" in honest["chart"]
    assert set(grounded) == set(SEGMENT_KEYS)


def test_parse_script_treats_chart_segment_as_optional():
    four = '{"corner":"a","strategy":"b","result":"c","improve":"d"}'
    parsed = parse_script(four)
    assert set(parsed) == {"corner", "strategy", "result", "improve"}
    five = ('{"corner":"a","strategy":"b","result":"c","improve":"d",'
            '"chart":"1分足は上昇です"}')
    assert parse_script(five)["chart"] == "1分足は上昇です"
    blank = ('{"corner":"a","strategy":"b","result":"c","improve":"d","chart":"  "}')
    assert "chart" not in parse_script(blank)


def test_render_fallback_new_segments_are_grounded():
    facts = {
        "policy": {},
        "capital_jpy": "10000",
        "deployed_jpy": "0",
        "position_count": 1,
        "recent_fills": [
            {
                "symbol": "btc_jpy",
                "side": "buy",
                "amount": "0.001",
                "price": "100",
                "reason_code": "momentum_breakout",
                "signal": {
                    "kind": "builtin_entry",
                    "conditions": [
                        {"feature": "return_bps", "observed": "320", "threshold": "150",
                         "op": ">=", "lookback": 6}
                    ],
                },
            }
        ],
        "round_trips": [
            {
                "symbol": "btc_jpy",
                "entry_reason": "momentum_breakout",
                "exit_reason": "take_profit",
                "realized_jpy": "12",
                "hold_sec": 600,
                "entry_signal": {"conditions": [
                    {"feature": "return_bps", "observed": "320", "threshold": "150",
                     "op": ">=", "lookback": 6}
                ]},
                "exit_signal": {"conditions": [
                    {"feature": "pnl_bps", "observed": "120", "threshold": "100", "op": ">="}
                ]},
            }
        ],
        "research": {"news_items": [{"title": "A", "source": "X"}, {"title": "B", "source": "Y"}]},
    }
    fallback = render_fallback(facts)
    assert set(fallback) == set(SEGMENT_KEYS)
    # The fill explanation names the indicator in plain language and its
    # meaning, not the raw feature code, while keeping the real numbers.
    assert "return_bps" not in fallback["fills"]
    assert "直近の値上がり率" in fallback["fills"] and "150" in fallback["fills"]
    assert "値上がりの勢いが十分だった" in fallback["fills"]
    assert "反発の有無を次に見ます" in fallback["fills"]
    # News covers every available headline, not just one (by position; see
    # test_news_segment_explains_content_instead_of_reciting_title_and_source
    # for the "no verbatim recitation" contract).
    assert "1件目は" in fallback["news"] and "2件目は" in fallback["news"]
    # The review ties the round trip to its entry/exit grounds.
    assert "btc_jpy" in fallback["review"] and "12" in fallback["review"]
    assert "正しかった" in fallback["review"] or "利益" in fallback["review"]
    assert "根拠が再現した結果か" in fallback["review"]


@pytest.mark.parametrize(
    ("realized", "expected", "forbidden"),
    [
        ("12", "12円の利益", "損失"),
        ("-8", "-8円の損失", "損益なし"),
        ("0", "0円の損益なし", "損失"),
        (None, "損益は記録不明", "None円"),
        ("not-a-number", "損益は記録不明", "損失"),
        (True, "損益は記録不明", "損失"),
        (float("nan"), "損益は記録不明", "損失"),
        (float("inf"), "損益は記録不明", "損失"),
    ],
)
def test_review_fallback_distinguishes_zero_and_unknown_pnl(realized, expected, forbidden):
    fallback = render_fallback(
        {
            "policy": {},
            "research": {},
            "round_trips": [
                {
                    "symbol": "btc_jpy",
                    "entry_reason": "momentum_breakout",
                    "exit_reason": "take_profit",
                    "realized_jpy": realized,
                    "hold_sec": 60,
                }
            ],
        }
    )
    assert expected in fallback["review"]
    assert forbidden not in fallback["review"]


def test_render_fallback_without_news_is_honest():
    fallback = render_fallback({"policy": {}, "research": {}})
    assert "取得" in fallback["news"]
    assert "取得" in fallback["chart"]
    assert set(fallback) == set(SEGMENT_KEYS)


def test_generate_uses_injected_timeframe_facts_without_network(tmp_path):
    _write_status(tmp_path)
    result = generate_corner_script(
        None, trading_dir=tmp_path, agents="", now=1010.0, timeframe_facts=_TIMEFRAME_FACTS
    )
    assert result["source"] == "fallback"
    assert "日足" in result["segments"]["chart"]


def test_generate_merges_partial_ai_with_fallback(tmp_path, monkeypatch):
    from docich.trading import corner_script

    _write_status(tmp_path)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setattr(corner_script, "prepare_research_context", lambda *a, **k: {})
    monkeypatch.setattr(
        corner_script,
        "generate_text",
        lambda *a, **k: '{"corner":"AI corner","chart":"AI chart"}',
    )
    result = generate_corner_script(
        object(),
        trading_dir=tmp_path,
        agents="opencode:x",
        now=1010.0,
        timeframe_facts=_TIMEFRAME_FACTS,
    )
    assert result["source"] == "ai-partial"
    assert sorted(result["fallback_segments"]) == [
        "fills", "improve", "news", "result", "review", "strategy"
    ]
    assert result["segments"]["corner"] == "AI corner"
    assert result["segments"]["chart"] == "AI chart"
    # The missing segments keep the grounded deterministic text.
    assert result["segments"]["strategy"]
    assert result["segments"]["review"]
    assert set(result["segments"]) == set(SEGMENT_KEYS)


def test_condition_text_explains_meaning_not_just_the_raw_feature_code():
    # RSI at/above threshold ("overbought") must read as such, not just the
    # bare code name and comparison symbol.
    overbought = _condition_text({"conditions": [
        {"feature": "rsi", "observed": "78.4", "threshold": "70", "op": ">=", "lookback": 12},
    ]})
    assert "rsiが" not in overbought  # bare code name, not the plain-language label
    assert "買われすぎ" in overbought
    assert "78.4" in overbought and "70" in overbought
    assert "以上" in overbought  # natural-language op, not a bare ">=" symbol

    # The same feature at/below threshold ("oversold") must read the other way.
    oversold = _condition_text({"conditions": [
        {"feature": "rsi", "observed": "22.1", "threshold": "30", "op": "<=", "lookback": 12},
    ]})
    assert "売られすぎ" in oversold
    assert "以下" in oversold

    unknown = _condition_text({"conditions": [
        {"feature": "some_future_feature", "observed": "1", "threshold": "2", "op": ">="},
    ]})
    assert "some_future_feature" in unknown  # unknown features degrade gracefully


def test_news_segment_has_no_repeated_disclaimer():
    fallback = render_fallback({
        "policy": {},
        "research": {"news_items": [
            {"title": "A", "source": "X"},
            {"title": "B", "source": "Y"},
        ]},
    })
    # Every item is addressed (by position), without reciting the raw title
    # text or the outlet name aloud (see test_news_segment_explains_content).
    assert "1件目は" in fallback["news"] and "2件目は" in fallback["news"]
    assert "A" not in fallback["news"] and "B" not in fallback["news"]
    assert "X" not in fallback["news"] and "Y" not in fallback["news"]
    assert "見出しの段階" not in fallback["news"]
    assert fallback["news"].count("事実と推測") == 0


def test_news_segment_explains_content_instead_of_reciting_title_and_source():
    """Do not read the headline verbatim or name the outlet aloud; explain

    what the headline is about instead (regulation/flows/price direction).
    """
    fallback = render_fallback({
        "policy": {},
        "research": {"news_items": [
            {"title": "米議会が暗号資産規制法案を否決、先送りへ", "source": "Example News"},
            {"title": "ビットコインETFに資金流入が拡大", "source": "Another Outlet"},
            {"title": "ビットコイン価格が急落、下値模索", "source": "Third Outlet"},
        ]},
    })
    news = fallback["news"]
    for raw in ("米議会が暗号資産規制法案を否決", "ビットコインETFに資金流入が拡大",
                "ビットコイン価格が急落", "Example News", "Another Outlet", "Third Outlet"):
        assert raw not in news, raw
    assert "規制" in news and ("否決" in news or "足踏み" in news)
    assert "資金" in news and "入ってきている" in news
    assert "下向き" in news


def test_spoken_numbers_are_rounded_to_two_decimals():
    from docich.trading.corner_script import _fmt_num
    assert _fmt_num("-198.4754669238077029463999998") == "-198.48"
    assert _fmt_num("7.843078654615100") == "7.84"
    assert _fmt_num("69.31023953378063559684045000") == "69.31"
    assert _fmt_num("10000") == "10,000"
    assert _fmt_num("0.001") is None
    assert _fmt_num(None) is None
    assert _fmt_num("not-a-number") is None
    fallback = render_fallback({
        "policy": {},
        "capital_jpy": "10000",
        "deployed_jpy": "0",
        "position_count": 0,
        "recent_fills": [{
            "symbol": "ARB/JPY",
            "side": "sell",
            "amount": "58.1395",
            "price": "25.97877651380",
            "reason_code": "paper_lab_exit",
            "realized_pnl_jpy": "7.843078654615100",
            "signal": {"conditions": [
                {"feature": "pnl_bps", "observed": "69.31023953378063559684045000",
                 "threshold": "65", "op": ">="},
            ]},
        }],
        "performance": {
            "cumulative_pnl_jpy": "-198.4754669238077029463999998",
            "today_realized_pnl_jpy": "103.4405891598143589596000000",
            "unrealized_pnl_jpy": "0",
        },
        "research": {},
    })
    for key in ("result", "fills", "review"):
        assert "198.47546692380" not in fallback[key]
        assert "7.84307865461" not in fallback[key]
        assert "69.31023953" not in fallback[key]
    assert "-198.48" in fallback["result"]
    assert "69.31" in fallback["fills"]


def test_prompt_instructs_two_decimal_speech():
    prompt = build_prompt({"policy": {}})
    assert "小数第2位" in prompt
    assert "見出しの段階なので" in prompt
    assert "数字ではなく相場や判断の意味を先に言う" in prompt
    assert "だから何を見るか" in prompt


def test_prompt_forbids_reciting_headline_and_outlet_name():
    prompt = build_prompt({"policy": {}})
    assert "見出しの文言をそのまま読み上げず" in prompt
    assert "媒体名も口に出さない" in prompt


def test_generate_accepts_multiline_model_json(tmp_path, monkeypatch):
    from docich.trading import corner_script

    _write_status(tmp_path)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setattr(corner_script, "prepare_research_context", lambda *a, **k: {})
    body = '{\n  "corner": "生成文1\nつづき",\n  "news": "生成文2",\n  "chart": "生成文3",\n  "strategy": "生成文4",\n  "result": "生成文5",\n  "fills": "生成文6",\n  "review": "生成文7",\n  "improve": "生成文8"\n}'
    monkeypatch.setattr(corner_script, "generate_text", lambda *a, **k: body)
    result = generate_corner_script(
        object(), trading_dir=tmp_path, agents="opencode:x", now=1010.0,
        timeframe_facts=_TIMEFRAME_FACTS,
    )
    assert result["source"] == "ai"
    assert "生成文1" in result["segments"]["corner"]


def test_generate_records_ai_text_error_kind_not_just_class_name(tmp_path, monkeypatch):
    """A generate_text failure must leave a diagnosable reason (issue: 9/18 corner

    outage where every AI attempt failed and state only recorded the bare
    exception class name ``AiTextError``, with no way to tell rate-limit vs
    provider failure vs empty output without re-running with instrumentation).
    """
    from docich.trading import corner_script
    from docich.trading.ai_text import AiTextError

    _write_status(tmp_path)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setattr(corner_script, "prepare_research_context", lambda *a, **k: {})

    def _raise(*a, **k):
        raise AiTextError("AI生成が失敗しました (rc=1)", kind="rc-1:rate_limit")

    monkeypatch.setattr(corner_script, "generate_text", _raise)
    result = generate_corner_script(
        object(), trading_dir=tmp_path, agents="opencode:x", now=1010.0,
        timeframe_facts=_TIMEFRAME_FACTS,
    )
    assert result["source"] == "fallback"
    assert result["reason"] == "AiTextError:rc-1:rate_limit"


def test_generate_records_parse_failure_kind(tmp_path, monkeypatch):
    from docich.trading import corner_script

    _write_status(tmp_path)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setattr(corner_script, "prepare_research_context", lambda *a, **k: {})
    monkeypatch.setattr(corner_script, "generate_text", lambda *a, **k: "ただの文章です。")
    result = generate_corner_script(
        object(), trading_dir=tmp_path, agents="opencode:x", now=1010.0,
        timeframe_facts=_TIMEFRAME_FACTS,
    )
    assert result["source"] == "fallback"
    assert result["reason"] == "CornerScriptError:no-json-object"
    monkeypatch.setattr(corner_script, "generate_text", lambda *a, **k: '{"other": 1}')
    result = generate_corner_script(
        object(), trading_dir=tmp_path, agents="opencode:x", now=1010.0,
        timeframe_facts=_TIMEFRAME_FACTS,
    )
    assert result["reason"] == "CornerScriptError:no-usable-segments"


def test_prompt_requires_single_line_json(tmp_path):
    _write_status(tmp_path)
    prompt = build_prompt(build_facts(tmp_path, now=1010.0))
    assert "1行で出力" in prompt


def test_build_next_prompt_lists_covered_topics_and_done_option(tmp_path):
    _write_status(tmp_path)
    prompt = build_next_prompt(build_facts(tmp_path, now=1010.0), ["相場", "ニュース"])
    assert "相場" in prompt and "ニュース" in prompt
    assert '"done"' in prompt
    assert "JSON以外は出力しない" in prompt


def test_parse_next_narration_accepts_item_and_done():
    assert parse_next_narration('{"done": true}') == {"status": "done"}
    assert parse_next_narration('{"topic": "相場", "text": " 本文です "}') == {
        "status": "item", "topic": "相場", "text": "本文です",
    }
    with pytest.raises(CornerScriptError):
        parse_next_narration('{"topic": "見出しだけ"}')
    with pytest.raises(CornerScriptError):
        parse_next_narration('not json')


def test_generate_next_narration_fails_closed_without_agents_or_gate(tmp_path, monkeypatch):
    _write_status(tmp_path)
    assert generate_next_narration(None, trading_dir=tmp_path, agents="") == {
        "status": "failed", "reason": "no-agents",
    }
    monkeypatch.delenv("DOCICH_ALLOW_REAL_AI", raising=False)
    assert generate_next_narration(
        object(), trading_dir=tmp_path, agents="opencode:x", now=1010.0
    ) == {"status": "failed", "reason": "real-ai-disabled"}


def test_generate_next_narration_parses_item_then_done(tmp_path, monkeypatch):
    from docich.trading import corner_script

    _write_status(tmp_path)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setattr(corner_script, "prepare_research_context", lambda *a, **k: {})
    outputs = iter(['{"topic":"相場","text":"AIの本文です。"}', '{"done":true}'])
    monkeypatch.setattr(corner_script, "generate_text", lambda *a, **k: next(outputs))

    item = generate_next_narration(
        object(), trading_dir=tmp_path, agents="opencode:x", covered=["旧"], now=1010.0,
        timeframe_facts=_TIMEFRAME_FACTS,
    )
    assert item == {"status": "item", "topic": "相場", "text": "AIの本文です。"}
    assert generate_next_narration(
        object(), trading_dir=tmp_path, agents="opencode:x", now=1010.0,
        timeframe_facts=_TIMEFRAME_FACTS,
    ) == {"status": "done"}


def test_generate_next_narration_reports_unusable_output_as_failure(tmp_path, monkeypatch):
    from docich.trading import corner_script

    _write_status(tmp_path)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setattr(corner_script, "prepare_research_context", lambda *a, **k: {})
    monkeypatch.setattr(corner_script, "generate_text", lambda *a, **k: 'not json')

    result = generate_next_narration(
        object(), trading_dir=tmp_path, agents="opencode:x", now=1010.0,
        timeframe_facts=_TIMEFRAME_FACTS,
    )
    assert result == {"status": "failed", "reason": "CornerScriptError:no-json-object"}
