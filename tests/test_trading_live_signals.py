"""LIVE L4: pure signal generation over market frames (Issue #3623)."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest

from docich.trading import strategies, strategy_runtime
from docich.trading.live import signals
from docich.trading.live.policy import build_policy, policy_set_from_policies
from docich.trading.live.signals import (
    DEFAULT_MAX_PAIR_CORRELATION,
    HISTORY_LIMIT,
    TIMEFRAME,
    LiveSignals,
    scan_live,
)
from docich.trading.live.views import CostBasis
from docich.trading.market_data import MarketFrame
from docich.trading.strategy_lab import Condition, Rule, StrategyExperiment

from test_trading_live_support import block_network

D = Decimal
NOW = 1_800_000_000.0
SRC = Path(signals.__file__).parents[3]
RISING = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
# Returns track each other closely; correlation is about 0.8000, between the
# "below" (0.60) and "above" (0.95) policy thresholds used in the tests. Both
# thresholds also sit on opposite sides of DEFAULT_MAX_PAIR_CORRELATION.
CORRELATED_A = [100, 101.0, 101.808, 101.299, 102.5145, 103.5397, 103.1255, 104.6724, 105.5098, 105.9318]
CORRELATED_B = [100, 101.3535, 101.706, 100.7889, 101.5759, 102.3722, 102.0101, 103.0841, 104.2185, 105.5491]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


def frame(symbol: str, prices: list[object]) -> MarketFrame:
    count = len(prices)
    return MarketFrame(
        symbol=symbol,
        timeframe_seconds=300,
        timestamps=tuple(NOW - (count - index - 1) * 300 for index in range(count)),
        closes=tuple(D(str(value)) for value in prices),
        volumes=tuple(D("1") for _ in prices),
    )


def spec(
    experiment_id: str,
    *,
    entry_op: str = ">=",
    entry_threshold: str = "100",
    exit_hold_minutes: str = "100000",
    max_pair_correlation: str = "0.85",
) -> StrategyExperiment:
    return StrategyExperiment(
        experiment_id=experiment_id,
        name=f"{experiment_id} name",
        thesis=f"{experiment_id} thesis",
        entry_rules=(
            Rule(
                "entry-1",
                "all",
                (Condition("return_bps", entry_op, D(entry_threshold), 3),),
                D("0.10"),
            ),
        ),
        exit_rules=(
            Rule(
                "exit-1",
                "all",
                (Condition("hold_minutes", ">=", D(exit_hold_minutes)),),
                D("0.10"),
            ),
        ),
        max_pair_correlation=D(max_pair_correlation),
    )


def policy_set_for(*pairs):
    return policy_set_from_policies(
        {symbol: build_policy(experiment, symbol) for experiment, symbol in pairs}
    )


def position(net_base: str, *, avg_cost: str = "90", opened_at: float | None = NOW - 3600) -> CostBasis:
    return CostBasis(
        net_base=D(net_base), cost=D(avg_cost) * D(net_base), avg_cost=D(avg_cost), opened_at=opened_at
    )


def test_timeframe_and_history_limit_match_paper_worker():
    from docich.trading import worker

    assert TIMEFRAME == worker.TIMEFRAME
    assert HISTORY_LIMIT == worker.HISTORY_LIMIT
    assert DEFAULT_MAX_PAIR_CORRELATION == D("0.85")


def test_entry_produces_expected_opportunity_id():
    ps = policy_set_for((spec("exp-b"), "OAS/JPY"))
    result = scan_live({"OAS/JPY": frame("OAS/JPY", RISING)}, {}, ps, now=NOW)

    assert tuple(item.opportunity_id for item in result.entries) == (
        f"lab:exp-b:entry-1:OAS/JPY:{int(NOW)}",
    )
    assert result.exits == ()
    assert result.skipped == ()
    assert tuple(result.reason_contexts) == (f"lab:exp-b:entry-1:OAS/JPY:{int(NOW)}",)


def test_entry_rule_that_matches_nothing_produces_no_candidates():
    ps = policy_set_for((spec("exp-b"), "OAS/JPY"))
    result = scan_live({"OAS/JPY": frame("OAS/JPY", [100] * 10)}, {}, ps, now=NOW)

    assert result == LiveSignals((), (), (), {})


def test_exit_suppresses_entry_for_the_same_symbol():
    now_spec = spec("exp-c", exit_hold_minutes="10")
    ps = policy_set_for((now_spec, "OAS/JPY"))
    positions = {"OAS/JPY": position("5")}
    result = scan_live({"OAS/JPY": frame("OAS/JPY", RISING)}, positions, ps, now=NOW)

    assert tuple(item.opportunity_id for item in result.exits) == (
        f"lab-exit:exp-c:exit-1:OAS/JPY:{int(NOW)}",
    )
    assert result.entries == ()
    assert result.skipped == ()


def test_each_symbol_is_evaluated_with_its_own_policy_experiment():
    up = spec("exp-up", entry_op=">=", entry_threshold="100")
    down = spec("exp-down", entry_op="<=", entry_threshold="-100")
    ps = policy_set_for((up, "OAS/JPY"), (down, "XYM/JPY"))
    # Anti-correlated so diversification never hides one of the two entries.
    frames = {
        "OAS/JPY": frame("OAS/JPY", [100, 98, 100, 99, 101, 100, 102, 101, 103, 104]),
        "XYM/JPY": frame("XYM/JPY", [100, 102, 100, 102, 100, 102, 100, 102, 100, 98]),
    }
    result = scan_live(frames, {}, ps, now=NOW)

    assert sorted(item.opportunity_id for item in result.entries) == [
        f"lab:exp-down:entry-1:XYM/JPY:{int(NOW)}",
        f"lab:exp-up:entry-1:OAS/JPY:{int(NOW)}",
    ]
    assert result.exits == ()
    assert result.skipped == ()


def test_diversification_uses_policy_threshold_and_ignores_paper_global(monkeypatch):
    frames = {
        "OAS/JPY": frame("OAS/JPY", CORRELATED_A),
        "XYM/JPY": frame("XYM/JPY", CORRELATED_B),
    }

    low = spec("corr-low", max_pair_correlation="0.60")
    low_ps = policy_set_for((low, "OAS/JPY"), (low, "XYM/JPY"))
    low_result = scan_live(frames, {}, low_ps, now=NOW)
    assert len(low_result.entries) == 1
    assert len(low_result.skipped) == 1
    assert low_result.skipped[0].reason_code == "correlated_exposure"
    assert {low_result.entries[0].symbol, low_result.skipped[0].symbol} == {"OAS/JPY", "XYM/JPY"}

    high = spec("corr-high", max_pair_correlation="0.95")
    high_ps = policy_set_for((high, "OAS/JPY"), (high, "XYM/JPY"))
    high_result = scan_live(frames, {}, high_ps, now=NOW)
    assert tuple(item.symbol for item in high_result.entries) == ("OAS/JPY", "XYM/JPY")
    assert high_result.skipped == ()

    paper_global = spec("paper-global", max_pair_correlation="0.01")
    monkeypatch.setattr(strategies, "get_active_experiment", lambda: paper_global)
    assert scan_live(frames, {}, low_ps, now=NOW) == low_result
    assert scan_live(frames, {}, high_ps, now=NOW) == high_result


def test_exit_skips_use_exact_ids_and_non_positive_positions_are_ignored():
    ps = policy_set_for((spec("exp-f"), "OAS/JPY"))
    positions = {
        "OAS/JPY": position("5"),      # policy, but no frame
        "NOP/JPY": position("5"),      # no policy
        "ZERO/JPY": position("0"),     # ignored before the policy check
        "FLAT/JPY": position("-3"),    # ignored as well
    }
    result = scan_live({"XYM/JPY": frame("XYM/JPY", RISING)}, positions, ps, now=NOW)

    assert result.entries == ()
    assert result.exits == ()
    assert tuple(item.opportunity_id for item in result.skipped) == (
        "live-no-policy:NOP/JPY",
        "live-no-frame:OAS/JPY",
    )
    assert tuple(item.reason_code for item in result.skipped) == ("no_policy", "no_frame")
    assert tuple(item.side for item in result.skipped) == ("sell", "sell")
    assert "ZERO/JPY" not in {item.symbol for item in result.skipped}
    assert "FLAT/JPY" not in {item.symbol for item in result.skipped}


def test_scan_live_is_pure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    files_before = sorted(os.listdir(tmp_path))
    registry_before = dict(strategy_runtime._reason_contexts)

    ps = policy_set_for((spec("exp-g", exit_hold_minutes="10"), "OAS/JPY"),
                        (spec("exp-g", exit_hold_minutes="10"), "XYM/JPY"))
    frames = {
        "OAS/JPY": frame("OAS/JPY", RISING),
        "XYM/JPY": frame("XYM/JPY", RISING),
    }
    positions = {"OAS/JPY": position("5")}

    first = scan_live(frames, positions, ps, now=NOW)
    second = scan_live(frames, positions, ps, now=NOW)

    assert first == second
    assert first.reason_contexts
    assert len(first.exits) == 1
    assert len(first.entries) == 1
    assert strategy_runtime._reason_contexts == registry_before
    assert sorted(os.listdir(tmp_path)) == files_before


def test_signal_import_without_network_or_side_effects(tmp_path):
    code = textwrap.dedent(f"""
        import os, sys
        before = sorted(os.listdir({str(tmp_path)!r}))
        import docich.trading.live.signals
        bad = [m for m in ("ssl", "ccxt", "http.client", "urllib.request", "requests", "socket") if m in sys.modules]
        assert not bad, bad
        assert sorted(os.listdir({str(tmp_path)!r})) == before
        print("clean")
    """)
    out = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(SRC)),
    )
    assert out.stdout.strip() == "clean", out.stderr
