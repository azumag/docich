from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from docich.trading.corner_script import (  # noqa: E402
    MAX_SEGMENT_CHARS,
    SEGMENT_KEYS,
    CornerScriptError,
    build_facts,
    build_prompt,
    generate_corner_script,
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


def test_render_fallback_is_deterministic_and_grounded(tmp_path):
    _write_status(tmp_path)
    facts = build_facts(tmp_path, now=1010.0)
    first = render_fallback(facts)
    assert first == render_fallback(facts)
    assert set(first) == set(SEGMENT_KEYS)
    assert "10000" in first["result"]
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


def test_parse_script_accepts_fenced_and_rejects_bad():
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
    with pytest.raises(CornerScriptError):
        parse_script('{"corner":"a"}')
    with pytest.raises(CornerScriptError):
        parse_script('{"corner":"a","strategy":"","result":"c","improve":"d"}')
    with pytest.raises(CornerScriptError):
        parse_script("not json")


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
