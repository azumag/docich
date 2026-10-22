"""N05 risk-gate tests: sizing, reservations, limits, loss asymmetry.

All numbers are FIXTURES (``fixture-risk-v1`` / ``fixture-stocks-v1``); N00
v2 §5 leaves every risk value unapproved and nothing here inherits the crypto
defaults. Contract sources: parent #769 D8/B3, N00 v2 §3.4 (risk code group ->
DECIDE hold), issue #782 acceptance items (min/lot, exposure caps, sell
proceeds never funding a buy, risk reduction never stopped).
"""
import datetime as dt
import unittest
from decimal import Decimal as D

from docich.trading.news_paper.execution import InstrumentConstraints
from docich.trading.news_paper.ledger import AccountState
from docich.trading.news_paper.risk import (
    FIXTURE_RISK_POLICY_V1,
    RiskPolicy,
    evaluate,
    gross_exposure,
    headroom_snapshot,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 18, 0, 10, 4, tzinfo=UTC)
EXPIRES = dt.datetime(2026, 9, 18, 0, 25, 0, tzinfo=UTC)
ASSET = "stocks:XTKS:DEMO"


def constraints(**changes) -> InstrumentConstraints:
    base = dict(
        version="fixture-stocks-v1",
        market="stocks",
        quote_currency="JPY",
        lot=D("100"),
        min_quantity=D("100"),
        min_notional=D("50000"),
        price_tick=D("1"),
        fee_bps=D("1"),
        fee_min=D("1"),
        slippage_bps=D("2"),
        spread_limit_bps=D("50"),
        participation_rate=D("0.1"),
        max_quote_age_seconds=D("60"),
    )
    base.update(changes)
    return InstrumentConstraints(**base)


def account(**changes) -> AccountState:
    base = dict(
        account_id="paper_news_demo",
        run_id="run_demo_001",
        scenario_id="price_plus_news",
        quote_currency="JPY",
        initial_cash=D("1000000"),
        cash=D("1000000"),
        revision=1,
        created_at="2026-09-18T00:00:00.000000Z",
    )
    base.update(changes)
    return AccountState(**base)


def buy(**changes):
    kwargs = dict(
        account=account(), side="buy", asset=ASSET, ref_price=D("1000"),
        constraints=constraints(), policy=FIXTURE_RISK_POLICY_V1, now=NOW,
        expires_at=EXPIRES,
    )
    kwargs.update(changes)
    return evaluate(**kwargs)


class SizingTest(unittest.TestCase):
    def test_buy_sizes_to_cap_and_reserves_cost_plus_fee(self):
        decision = buy(quantity_cap=D("300"))
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.requested_quantity, D("300"))
        # execution price 1000 * 1.0002 -> tick ceil 1001; fee ceil(300300*1bp)=31
        self.assertEqual(D(decision.reservation.amount), D("300331"))
        self.assertEqual(decision.reservation.kind, "cash")

    def test_lot_floor_keeps_reason_honest(self):
        decision = buy(quantity_cap=D("250"))
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.requested_quantity, D("200"))

    def test_below_minimum_is_hold_not_reject(self):
        decision = buy(quantity_cap=D("50"))
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_codes, ("MIN_ORDER_NOT_MET",))

    def test_cash_headroom_caps_quantity(self):
        poor = account(cash=D("120000"))
        decision = buy(account=poor)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.requested_quantity, D("100"))

    def test_per_asset_exposure_cap(self):
        small_cap = RiskPolicy(
            version="fixture-risk-v1", max_positions=5,
            max_gross_exposure=D("10000000"),
            per_asset_exposure_cap=D("250000"),
            daily_loss_limit=D("999999"), drawdown_limit=D("999999"),
        )
        decision = buy(policy=small_cap)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.requested_quantity, D("200"))


class LimitCodesTest(unittest.TestCase):
    def test_position_limit(self):
        held = account(
            positions={
                "stocks:XTKS:A": (D("100"), D("10")),
                "stocks:XTKS:B": (D("100"), D("10")),
                "stocks:XTKS:C": (D("100"), D("10")),
            }
        )
        decision = buy(account=held, asset="stocks:XTKS:NEW")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_codes, ("POSITION_LIMIT",))

    def test_existing_position_does_not_count_against_slot(self):
        held = account(positions={ASSET: (D("100"), D("1000"))})
        decision = buy(account=held, quantity_cap=D("200"))
        self.assertTrue(decision.accepted)

    def test_pending_buy_occupies_a_position_slot(self):
        # An accepted (pending) new entry holds its slot until it settles, so
        # a concurrent accept on a different asset cannot breach the limit.
        pending = account(cash_reserved=D("300331"),
                          pending_buy_assets=frozenset({"stocks:XTKS:OTHER"}))
        decision = buy(account=pending, asset="stocks:XTKS:NEW",
                       policy=RiskPolicy(
                           version="fixture-risk-v1", max_positions=1,
                           max_gross_exposure=D("10000000"),
                           per_asset_exposure_cap=D("10000000"),
                           daily_loss_limit=D("999999"),
                           drawdown_limit=D("999999"),
                       ))
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_codes, ("POSITION_LIMIT",))

    def test_gross_exposure_caps_sizing(self):
        policy = RiskPolicy(
            version="fixture-risk-v1", max_positions=5,
            max_gross_exposure=D("250000"),
            per_asset_exposure_cap=D("10000000"),
            daily_loss_limit=D("999999"), drawdown_limit=D("999999"),
        )
        decision = buy(policy=policy)
        # gross headroom 250000 / 1000 = 250 -> lot floor 200
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.requested_quantity, D("200"))


class LossAsymmetryTest(unittest.TestCase):
    def test_daily_loss_stops_new_risk(self):
        decision = buy(daily_pnl=D("-20000"))
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_codes, ("DAILY_LOSS_LIMIT",))

    def test_drawdown_stops_new_risk(self):
        decision = buy(peak_equity=D("1100000"), current_equity=D("1050000"))
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_codes, ("DRAWDOWN_LIMIT",))

    def test_sell_is_risk_reducing_and_survives_limits(self):
        held = account(positions={ASSET: (D("500"), D("1000"))})
        decision = evaluate(
            account=held, side="sell", asset=ASSET, ref_price=D("1000"),
            constraints=constraints(), policy=FIXTURE_RISK_POLICY_V1, now=NOW,
            expires_at=EXPIRES, daily_pnl=D("-30000"),
            peak_equity=D("1100000"), current_equity=D("1000000"),
        )
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.requested_quantity, D("500"))
        self.assertEqual(decision.reservation.kind, "inventory")
        self.assertEqual(D(decision.reservation.quantity), D("500"))

    def test_sell_without_inventory_is_held(self):
        decision = evaluate(
            account=account(), side="sell", asset=ASSET, ref_price=D("1000"),
            constraints=constraints(), policy=FIXTURE_RISK_POLICY_V1, now=NOW,
            expires_at=EXPIRES,
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_codes, ("MIN_ORDER_NOT_MET",))


class TimeAndEventTest(unittest.TestCase):
    def test_expired_signal_holds(self):
        decision = buy(now=EXPIRES)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_codes, ("SIGNAL_EXPIRED",))

    def test_used_event_holds(self):
        decision = buy(event_already_used=True)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_codes, ("EVENT_ALREADY_USED",))

    def test_codes_never_mix_after_gate(self):
        # An expired AND loss-limited candidate reports expiry only: the check
        # order is fixed and one candidate yields one decisive code group.
        decision = buy(now=EXPIRES, daily_pnl=D("-99999"))
        self.assertEqual(decision.reason_codes, ("SIGNAL_EXPIRED",))


class HeadroomTest(unittest.TestCase):
    def test_own_reservation_added_back_for_fill(self):
        state = account()
        decision = buy(quantity_cap=D("300"))
        snap = headroom_snapshot(
            account=state, policy=FIXTURE_RISK_POLICY_V1,
            ref_prices={ASSET: D("1000")},
            exclude_reservation=decision.reservation,
        )
        # No other reservation exists, so the fill may use the whole reserved
        # amount: cash_available includes the own reservation again.
        self.assertEqual(snap.cash_available, D("1300331"))
        self.assertGreater(snap.exposure_headroom, D("300000"))

    def test_other_reservation_still_holds(self):
        from docich.trading.news_paper.ledger import ReservationDraft

        reserved = account(
            cash_reserved=D("300331"),
        )
        snap = headroom_snapshot(
            account=reserved, policy=FIXTURE_RISK_POLICY_V1,
            ref_prices={ASSET: D("1000")},
        )
        self.assertEqual(snap.cash_available, D("1000000") - D("300331"))

    def test_gross_exposure_counts_positions_and_pending_cash(self):
        held = account(
            positions={ASSET: (D("100"), D("1000"))},
            cash_reserved=D("50000"),
        )
        self.assertEqual(
            gross_exposure(held, {ASSET: D("1000")}), D("150000")
        )


if __name__ == "__main__":
    unittest.main()
