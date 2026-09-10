from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from docich import config  # noqa: E402
from docich.trading.paper_improve import (  # noqa: E402
    PaperImproveError,
    build_improve_prompt,
    parse_policy_candidate,
    run_paper_improve,
)
from docich.trading.strategy_store import load_strategy_policy  # noqa: E402

NOW = 1_800_000_000.0


def _global(root: Path):
    cfg = root / "docich.toml"
    cfg.write_text(
        '[paths]\nstate_dir = "run"\n[trading]\npaper_worker_enabled = true\n',
        encoding="utf-8",
    )
    return config.load_global(root, config_path=cfg)


def _trading_dir(g) -> Path:
    trading_dir = g.state_dir / "trading"
    trading_dir.mkdir(parents=True, exist_ok=True)
    (trading_dir / "status.json").write_text(
        json.dumps(
            {
                "worker_state": "paper_worker_idle",
                "capital_reference": "10000",
                "deployed_reference": "0",
                "open_positions": {},
                "recent_fills": [],
                "skipped_reason_codes": [],
                "signal_summary": {"candidate_count": 0},
            }
        ),
        encoding="utf-8",
    )
    return trading_dir


def _payload(**overrides) -> str:
    data = {
        "momentum_lookback": 8,
        "momentum_threshold_bps": 250,
        "mean_reversion_lookback": 12,
        "mean_reversion_z": -2.0,
        "max_notional_fraction": 0.2,
    }
    data.update(overrides)
    return json.dumps(data)


def test_dry_run_does_not_call_ai_or_write(tmp_path):
    g = _global(tmp_path)
    trading_dir = _trading_dir(g)
    calls: list[str] = []
    result = run_paper_improve(
        g, trading_dir=trading_dir, agents="opencode:x", dry_run=True,
        llm=lambda prompt: calls.append(prompt) or _payload(), now=NOW,
    )
    assert result["status"] == "dry-run"
    assert result["prompt_chars"] > 0
    assert calls == []
    assert not (trading_dir / "strategy_policy.json").exists()


def test_no_agents_skips_without_write(tmp_path):
    g = _global(tmp_path)
    trading_dir = _trading_dir(g)
    result = run_paper_improve(
        g, trading_dir=trading_dir, agents="", llm=lambda prompt: _payload(), now=NOW
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "no-agents"
    assert not (trading_dir / "strategy_policy.json").exists()


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        '{"momentum_lookback": 8}',  # missing keys
        _payload(momentum_lookback=0),  # below minimum
        _payload(mean_reversion_z=1.0),  # must be negative
        _payload(max_notional_fraction=2.0),  # outside (0, 1]
        _payload(momentum_threshold_bps=0),  # must be positive
        _payload(unknown="x"),  # unknown key
    ],
)
def test_invalid_output_rejected_without_write(tmp_path, bad):
    g = _global(tmp_path)
    trading_dir = _trading_dir(g)
    result = run_paper_improve(
        g, trading_dir=trading_dir, agents="opencode:x",
        llm=lambda prompt, value=bad: value, now=NOW,
    )
    assert result["status"] == "failed"
    assert not (trading_dir / "strategy_policy.json").exists()


def test_valid_output_persists_policy(tmp_path):
    g = _global(tmp_path)
    trading_dir = _trading_dir(g)
    result = run_paper_improve(
        g, trading_dir=trading_dir, agents="opencode:x",
        llm=lambda prompt: _payload(), now=NOW,
    )
    assert result["status"] == "improved"
    loaded = load_strategy_policy(trading_dir)
    assert loaded.momentum_lookback == 8
    assert loaded.mean_reversion_lookback == 12
    assert loaded.max_notional_fraction == Decimal("0.2")
    assert (trading_dir / "strategy_policy.json").stat().st_mode & 0o777 == 0o600


def test_parse_policy_candidate_accepts_fenced_json():
    candidate = parse_policy_candidate("```json\n" + _payload() + "\n```")
    assert candidate["momentum_lookback"] == 8
    assert candidate["momentum_threshold_bps"] == Decimal("250")


def test_parse_policy_candidate_rejects_bounds():
    with pytest.raises(PaperImproveError):
        parse_policy_candidate(_payload(momentum_lookback=1))
    with pytest.raises(PaperImproveError):
        parse_policy_candidate(_payload(mean_reversion_lookback=2))
    with pytest.raises(PaperImproveError):
        parse_policy_candidate(_payload(mean_reversion_z=0))


def test_build_improve_prompt_embeds_only_allowed_keys():
    prompt = build_improve_prompt({"capital_jpy": "10000", "policy": {}})
    for key in (
        "momentum_lookback",
        "momentum_threshold_bps",
        "mean_reversion_lookback",
        "mean_reversion_z",
        "max_notional_fraction",
    ):
        assert key in prompt
