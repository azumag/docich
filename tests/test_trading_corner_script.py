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


def test_build_facts_uses_status_and_policy(tmp_path):
    _write_status(tmp_path)
    policy = StrategyPolicy(momentum_lookback=9, momentum_threshold_bps=Decimal("111"))
    facts = build_facts(tmp_path, now=1010.0, policy=policy)
    assert facts["capital_jpy"] == "10000"
    assert facts["deployed_jpy"] == "3000"
    assert facts["open_positions"] == [{"symbol": "btc_jpy", "amount": "0.001"}]
    assert facts["candidate_count"] == 2
    assert facts["policy"]["momentum_lookback"] == 9
    assert facts["policy"]["momentum_threshold_bps"] == "111"
    assert facts["fresh_markets"] == 1
    assert facts["total_markets"] == 2
    # Reason codes are surfaced as human labels, never raw response text.
    assert "モメンタム上振れ" in facts["candidate_reasons"]


def test_build_facts_survives_missing_files(tmp_path):
    facts = build_facts(tmp_path, now=5.0)
    assert facts["capital_jpy"] == "?"
    assert facts["open_positions"] == []
    assert facts["policy"]["momentum_lookback"] == 6


def test_render_fallback_is_deterministic_and_grounded(tmp_path):
    _write_status(tmp_path)
    facts = build_facts(tmp_path, now=1010.0)
    first = render_fallback(facts)
    assert first == render_fallback(facts)
    assert set(first) == set(SEGMENT_KEYS)
    assert "10000" in first["result"]
    assert "btc_jpy" in first["result"]
    assert "correlated_exposure" in first["result"]
    assert "戦略" in first["strategy"]
    assert all(isinstance(value, str) and value for value in first.values())


def test_parse_script_accepts_fenced_and_rejects_bad():
    good = '```json\n{"corner":"a","strategy":"b","result":"c","improve":"d"}\n```'
    assert parse_script(good) == {
        "corner": "a", "strategy": "b", "result": "c", "improve": "d"
    }
    # Surrounding prose must not lose the narration.
    prose = '前置きです。\n{"corner":"a","strategy":"b","result":"c","improve":"d"}\n以上です。'
    assert parse_script(prose)["corner"] == "a"
    # Extra keys are ignored rather than rejected.
    assert parse_script(
        '{"corner":"a","strategy":"b","result":"c","improve":"d","extra":"x"}'
    ) == {"corner": "a", "strategy": "b", "result": "c", "improve": "d"}
    # Over-length values are truncated to the cap, not rejected.
    truncated = parse_script(
        '{"corner":"' + "あ" * 300 + '","strategy":"b","result":"c","improve":"d"}'
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
