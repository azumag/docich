from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.events import build_fill_event  # noqa: E402
from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import AllocationDecision  # noqa: E402
from docich.trading.paper import PaperBroker, paper_execution_price  # noqa: E402
from docich.trading.performance import build_round_trips  # noqa: E402
from docich.trading.presentation import render_notification  # noqa: E402
from docich.trading.status import signal_context_payload  # noqa: E402
from docich.trading.strategies import scan_exit_opportunities  # noqa: E402
from docich.trading.strategy_lab import (  # noqa: E402
    experiment_from_mapping,
    scan_experiment_entries,
    scan_experiment_exits,
)
from docich.trading.strategy_runtime import (  # noqa: E402
    pop_reason_context,
    set_active_experiment,
)

D = Decimal
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def reset_experiment():
    set_active_experiment(None)
    yield
    set_active_experiment(None)


def frame(symbol: str, prices: list[object]) -> MarketFrame:
    count = len(prices)
    return MarketFrame(
        symbol=symbol,
        timeframe_seconds=300,
        timestamps=tuple(NOW - (count - index - 1) * 300 for index in range(count)),
        closes=tuple(D(str(value)) for value in prices),
        volumes=(D("1"),) * count,
    )


def experiment(*, profit_bps: str = "10"):
    return experiment_from_mapping({
        "experiment_id": "cost-check",
        "name": "費用込み決済",
        "thesis": "約定費用を含めて出口条件を評価する。",
        "entry_rules": [{
            "rule_id": "entry",
            "combine": "any",
            "conditions": [
                {"feature": "return_bps", "lookback": 3, "op": ">=", "threshold": 100},
                {"feature": "rsi", "lookback": 3, "op": "<=", "threshold": 30},
            ],
        }],
        "exit_rules": [{
            "rule_id": "profit",
            "combine": "all",
            "conditions": [{"feature": "pnl_bps", "op": ">=", "threshold": profit_bps}],
        }],
    })


def decision(oid: str, side: str, price: object, *, strategy_id: str = "cost-check"):
    amount = D("1")
    price = D(str(price))
    return AllocationDecision(
        opportunity_id=oid, strategy_id=strategy_id,
        symbol="BTC/JPY", side=side, quote="JPY", amount=amount, price=price,
        quote_notional=amount * price, reference_notional=amount * price,
        reason_code="paper_lab_entry" if side == "buy" else "paper_lab_exit",
    )


@pytest.mark.parametrize("fee,slip,buy_price,sell_price", [
    ("0", "0", "100", "100"),
    ("0.003", "7", "100.3702100", "99.6302100"),
])
def test_execution_helper_preserves_broker_cost_overrides(tmp_path, fee, slip, buy_price, sell_price):
    ledger = PaperLedger(tmp_path / "paper.sqlite3")
    try:
        broker = PaperBroker(ledger, taker_fee_rate=fee, slippage_bps=slip)
        for side, expected in (("buy", buy_price), ("sell", sell_price)):
            fill = broker.fill(decision(side, side, "100"), timestamp=NOW)
            assert fill.price == D(expected)
            assert paper_execution_price("100", side, taker_fee_rate=fee, slippage_bps=slip) == fill.price
    finally:
        ledger.close()


def test_lab_does_not_take_apparent_profit_that_becomes_loss_after_sell_costs():
    frames = {"BTC/JPY": frame("BTC/JPY", [100, 100, 100, "100.1"])}
    result = scan_experiment_exits(
        frames, {"BTC/JPY": (D("1"), D("100"), NOW - 300)}, experiment(), now=NOW,
    )
    # Gross return is +10 bps; the executable return is -7.010994 bps.
    assert paper_execution_price("100.1", "sell") == D("99.92989006")
    assert result.opportunities == ()


def test_lab_exit_estimate_matches_cost_inclusive_fill_and_survives_public_paths(tmp_path):
    ledger = PaperLedger(tmp_path / "paper.sqlite3")
    try:
        broker = PaperBroker(ledger)
        buy = broker.fill(decision("buy", "buy", "100"), timestamp=NOW - 300)
        frames = {"BTC/JPY": frame("BTC/JPY", [100, 100, 100, "100.6"])}
        result = scan_experiment_exits(
            frames, ledger.position_cost_basis(), experiment(profit_bps="20"), now=NOW,
        )
        assert len(result.opportunities) == 1
        opportunity = result.opportunities[0]
        context = result.reason_contexts[opportunity.opportunity_id]
        sell = broker.fill(
            decision(opportunity.opportunity_id, "sell", "100.6", strategy_id=opportunity.strategy_id),
            timestamp=NOW, signal_context=context,
        )
        expected_bps = (sell.price / buy.price - D("1")) * D("10000")
        assert expected_bps > D("20")
        assert D(context["pnl_bps"]) == expected_bps
        assert D(context["conditions"][0]["observed"]) == expected_bps
        assert context["last_price"] == "100.6"
        event = build_fill_event(sell, reason_context=context)
        status_context = signal_context_payload(sell.signal_context)
        trip = build_round_trips(ledger.path)[0]
        for recorded in (event["reason_context"], status_context, trip["exit_signal"]):
            assert recorded["conditions"][0]["unit"] == "net_pnl_bps"
        assert D(trip["realized_jpy"]) == sell.price - buy.price
        assert "売却費用を含む推定損益率プラス0.26%" in render_notification(event, mode="compact").speech_text
    finally:
        ledger.close()


@pytest.mark.parametrize("price,reason", [
    ("101", None),       # Gross +1%, but executable profit is below the 1% target.
    ("101.2", "take_profit"),
    ("97.1", "stop_loss"),  # Net loss has crossed 3% before the gross quote does.
])
def test_builtin_exit_thresholds_use_the_executable_sell_price(price, reason):
    frames = {"BTC/JPY": frame("BTC/JPY", [100, 100, 100, price])}
    result = scan_exit_opportunities(
        frames, {"BTC/JPY": (D("1"), D("100"), NOW - 300)}, now=NOW,
    )
    if reason is None:
        assert result == ()
        return
    assert len(result) == 1 and result[0].reason_code == reason
    context = pop_reason_context(result[0].opportunity_id)
    expected_bps = (paper_execution_price(price, "sell") / D("100") - D("1")) * D("10000")
    assert D(context["conditions"][0]["observed"]) == expected_bps
    assert context["conditions"][0]["unit"] == "net_pnl_bps"


def test_any_rule_ranks_matched_signal_without_rewarding_unmatched_distance():
    frames = {
        "WEAK/JPY": frame("WEAK/JPY", [100, 100, 100, "101.2"]),
        "STRONG/JPY": frame("STRONG/JPY", [100, 100, 100, "101.4"]),
    }
    result = scan_experiment_entries(frames, experiment(), now=NOW)
    scores = {opportunity.symbol: opportunity.score for opportunity in result.opportunities}
    # Both RSI values are 100, far outside the <= 30 condition. They stay in
    # the evidence but must not saturate both opportunities to a score of 1.
    assert scores == {"WEAK/JPY": D("0.6"), "STRONG/JPY": D("0.7")}
    for context in result.reason_contexts.values():
        assert len(context["conditions"]) == 2
        assert context["conditions"][1]["observed"] == "100"


def test_historical_gross_pnl_signal_keeps_its_original_meaning(tmp_path):
    ledger = PaperLedger(tmp_path / "paper.sqlite3")
    try:
        context = {
            "kind": "lab_exit",
            "conditions": [{
                "feature": "pnl_bps", "observed": "150", "threshold": "100",
                "op": ">=", "unit": "bps",
            }],
        }
        sell = PaperBroker(ledger).fill(
            decision("historical", "sell", "101.5"), timestamp=NOW, signal_context=context,
        )
        event = build_fill_event(sell, reason_context=context)
        text = render_notification(event, mode="compact").speech_text
        assert "平均取得価格から1.5%上昇" in text
        assert "売却費用を含む推定損益率" not in text
    finally:
        ledger.close()
