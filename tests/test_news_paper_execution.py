"""N06 execution-model tests: next-observation IOC, partial fills, cost, fence.

No network, credentials, broker, database or LLM call: everything is a fixture
in memory. All prices/limits below are FIXTURES, not approved operational
values (N00 §5 keeps those unapproved).
"""
import ast
import datetime as dt
import inspect
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from docich.trading.news_paper import execution as ex

D = Decimal
UTC = dt.timezone.utc


def T(seconds=0, micros=0):
    return dt.datetime(2026, 9, 18, 0, 10, 4, micros, tzinfo=UTC) + dt.timedelta(seconds=seconds)


def constraints(**changes):
    base = dict(
        version="fixture-stocks-v1",
        market="stocks",
        quote_currency="JPY",
        lot=D("100"),
        min_quantity=D("100"),
        min_notional=D("10000"),
        price_tick=D("0.1"),
        fee_bps=D("1"),
        fee_min=D("0"),
        slippage_bps=D("2"),
        spread_limit_bps=D("50"),
        participation_rate=D("0.5"),
        max_quote_age_seconds=D("5"),
        volatility_limit_bps=D("200"),
    )
    base.update(changes)
    return ex.InstrumentConstraints(**base)


CLOCK = ex.ClockQuality(synced=True, source="ntp", max_error_ms=1000)
# budget = 2 * 1000 ms = 2 seconds


def decision_quote():
    return ("fixture_primary", "stocks:XTKS:DEMO", T(-1), "a" * 64)


def receipt(**changes):
    base = dict(
        receipt_id="receipt_1",
        status="accepted",
        input_cutoff_sequence=120,
        receipt_committed_at=T(0, 10),
        decision_quote=decision_quote(),
        evidence_valid=True,
    )
    base.update(changes)
    return ex.ReceiptState(**base)


def fence(**changes):
    base = dict(
        fence_id="fence_1",
        marker_id="marker_1",
        receipt_id="receipt_1",
        fence_at=T(0, 20),
        clock_quality=CLOCK,
        worker_id="worker_1",
    )
    base.update(changes)
    return ex.Fence(**base)


def order(**changes):
    base = dict(
        order_id="order_1",
        receipt_id="receipt_1",
        run_id="run_1",
        scenario_id="price_plus_news",
        account_id="paper_news_demo",
        asset="stocks:XTKS:DEMO",
        side="buy",
        requested_quantity=D("200"),
        remaining_quantity=D("200"),
        filled_quantity=D("0"),
        status="pending",
        expires_at=T(300),
        accepted_at=T(0, 10),
        not_before_at=T(0, 30),
        reservation_id="reservation_1",
        event_use_id="event_use_1",
    )
    base.update(changes)
    return ex.PendingOrder(**base)


def observation(**changes):
    base = dict(
        observation_id="quote_1",
        source="fixture_primary",
        asset="stocks:XTKS:DEMO",
        ingest_sequence=121,
        market_event_at=T(10),
        market_available_at=T(10),
        available_at=T(10),
        bid=D("999.9"),
        ask=D("1000"),
        bid_size=D("1000"),
        ask_size=D("1000"),
        size_semantics="executable",
        tradeable=True,
        realtime=True,
        session_id="XTKS-day",
        calendar_version="fixture-calendar-v1",
        quote_digest="b" * 64,
        provenance_class="test_fixture",
        instrument_constraints_version="fixture-stocks-v1",
    )
    base.update(changes)
    return ex.MarketObservation(**base)


def risk(**changes):
    base = dict(cash_available=D("10000000"), inventory_available=D("10000000"),
                exposure_headroom=D("1000000000"))
    base.update(changes)
    return ex.RiskSnapshot(**base)


class ExecutionTests(unittest.TestCase):
    def run_order(self, order_obj=None, observations=(), **kwargs):
        kwargs.setdefault("receipt", receipt())
        kwargs.setdefault("fence", fence())
        kwargs.setdefault("constraints", constraints())
        kwargs.setdefault("risk", risk())
        kwargs.setdefault("clock", CLOCK)
        kwargs.setdefault("now", T(10, 100000))
        return ex.execute_pending_order(
            order_obj or order(), observations=list(observations), **kwargs
        )

    # -- selection ---------------------------------------------------------

    def test_decision_quote_is_never_filled(self):
        quote = observation(observation_id="quote_decision", market_event_at=T(-1),
                            market_available_at=T(-1), available_at=T(-1), quote_digest="a" * 64)
        out = self.run_order(observations=[quote])
        self.assertEqual(out.fills, ())
        self.assertFalse(out.terminal)

    def test_same_source_reread_is_not_a_new_quote(self):
        # Same source/asset/event time/digest as the decision quote, newer ingest.
        quote = observation(observation_id="quote_decision_r1", market_event_at=T(-1),
                            market_available_at=T(-1), available_at=T(-1), quote_digest="a" * 64)
        out = self.run_order(observations=[quote])
        self.assertEqual(out.fills, ())

    def test_quote_before_not_before_is_not_eligible(self):
        early = observation(market_event_at=T(1), market_available_at=T(1), available_at=T(1))
        out = self.run_order(observations=[early])
        self.assertEqual(out.fills, ())
        self.assertFalse(out.terminal)

    def test_before_cutoff_sequence_is_not_eligible(self):
        old = observation(ingest_sequence=120)
        out = self.run_order(observations=[old])
        self.assertEqual(out.fills, ())

    def test_first_eligible_observation_only_no_price_cherry_picking(self):
        worse = observation(observation_id="quote_worse", ask=D("1000"), bid=D("999.9"))
        better = observation(observation_id="quote_better", ingest_sequence=122,
                             market_event_at=T(11), market_available_at=T(11), available_at=T(11),
                             ask=D("900"), bid=D("899.9"))
        out = self.run_order(observations=[worse, better])
        self.assertEqual(out.selected_observation_id, "quote_worse")
        self.assertEqual({f.quote_ref for f in out.fills}, {"quote_worse"})

    def test_duplicate_ingest_sequence_is_rejected(self):
        a = observation(observation_id="a", ingest_sequence=121, market_event_at=T(1),
                        market_available_at=T(1), available_at=T(10))
        b = observation(observation_id="b", ingest_sequence=121, market_event_at=T(1),
                        market_available_at=T(1), available_at=T(10), quote_digest="c" * 64)
        with self.assertRaises(ex.ExecutionError):
            self.run_order(observations=[a, b])

    # -- fence / durability ------------------------------------------------

    def test_fence_boundary_is_strict(self):
        clock0 = ex.ClockQuality(synced=True, source="ntp", max_error_ms=0)
        fence0 = fence(clock_quality=clock0, fence_at=T(0, 20))
        cons = constraints(max_quote_age_seconds=D("30"))
        border = T(0, 20)
        at_border = observation(available_at=border, market_event_at=T(0, 15),
                                market_available_at=T(0, 15))
        out = self.run_order(order(accepted_at=T(-10), not_before_at=T(-10)),
                             observations=[at_border],
                             now=T(0, 21), clock=clock0, fence=fence0, constraints=cons)
        self.assertEqual(out.fills, ())
        after = replace(at_border, observation_id="quote_after",
                        available_at=border + dt.timedelta(microseconds=1))
        out = self.run_order(order(accepted_at=T(-10), not_before_at=T(-10)),
                             observations=[after],
                             now=T(0, 21), clock=clock0, fence=fence0, constraints=cons)
        self.assertTrue(out.fills)

    def test_absent_fence_never_fills(self):
        out = self.run_order(observations=[observation()], fence=None)
        self.assertEqual(out.fills, ())
        self.assertFalse(out.terminal)

    def test_uncommitted_receipt_never_fills(self):
        out = self.run_order(observations=[observation()],
                             receipt=receipt(receipt_committed_at=None))
        self.assertEqual(out.fills, ())
        self.assertIn("RECEIPT_NOT_COMMITTED", out.reason_codes)

    def test_duplicate_delivery_does_not_double_fill(self):
        journal = ex.InMemoryFillJournal()
        first = self.run_order(observations=[observation()], journal=journal)
        self.assertTrue(first.fills)
        second = self.run_order(observations=[observation()], journal=journal)
        self.assertEqual(second.fills, ())
        self.assertIn("ORDER_ALREADY_FINAL", second.reason_codes)

    def test_untrusted_clock_cancels_pending(self):
        out = self.run_order(observations=[observation()],
                             clock=ex.ClockQuality(synced=False, source="unknown", max_error_ms=1000))
        self.assertEqual(out.status, "canceled")
        self.assertIn("CLOCK_UNTRUSTED", out.reason_codes)
        self.assertTrue(out.terminal)

    def test_revoked_evidence_cancels_pending(self):
        out = self.run_order(observations=[observation()],
                             receipt=receipt(evidence_valid=False))
        self.assertEqual(out.status, "canceled")
        self.assertIn("EVIDENCE_REVOKED", out.reason_codes)
        self.assertEqual(out.fills, ())

    # -- market quality ----------------------------------------------------

    def test_stale_quote_skips(self):
        stale = observation(market_event_at=T(3), market_available_at=T(3), available_at=T(3))
        out = self.run_order(observations=[stale], now=T(10))
        self.assertIn("QUOTE_STALE", out.reason_codes)
        self.assertEqual(out.fills, ())
        self.assertFalse(out.terminal)

    def test_halted_quote_skips(self):
        out = self.run_order(observations=[observation(tradeable=False)])
        self.assertIn("QUOTE_UNTRADEABLE", out.reason_codes)
        self.assertEqual(out.fills, ())

    def test_market_closed_skips(self):
        out = self.run_order(observations=[observation(session_open=False)])
        self.assertIn("MARKET_CLOSED", out.reason_codes)

    def test_unknown_size_semantics_skips(self):
        out = self.run_order(observations=[observation(size_semantics="unknown")])
        self.assertIn("LIQUIDITY_UNKNOWN", out.reason_codes)
        self.assertEqual(out.fills, ())

    def test_indicative_size_semantics_skips(self):
        out = self.run_order(observations=[observation(size_semantics="indicative")])
        self.assertIn("LIQUIDITY_UNKNOWN", out.reason_codes)

    def test_spread_too_wide_skips(self):
        wide = observation(bid=D("900"), ask=D("1000"))
        out = self.run_order(observations=[wide], constraints=constraints(spread_limit_bps=D("10")))
        self.assertIn("SPREAD_TOO_WIDE", out.reason_codes)

    def test_volatility_limit_skips(self):
        volatile = observation(volatility_bps=D("500"))
        out = self.run_order(observations=[volatile])
        self.assertIn("VOLATILITY_LIMIT", out.reason_codes)

    # -- pricing and cost separation ---------------------------------------

    def test_buy_price_is_adverse_slippage_on_ask_not_mid(self):
        out = self.run_order(observations=[observation(ask=D("1000"), bid=D("999"))])
        self.assertTrue(out.fills)
        # ask 1000 * 1.0002 = 1000.2, tick 0.1 -> 1000.2 (mid would be 999.5).
        self.assertEqual(out.fills[0].price, D("1000.2"))
        self.assertGreater(out.fills[0].price, D("1000"))

    def test_sell_price_is_adverse_slippage_on_bid(self):
        out = self.run_order(order(side="sell"),
                             observations=[observation(bid=D("1000"), ask=D("1000.1"))])
        self.assertTrue(out.fills)
        # bid 1000 * 0.9998 = 999.8, tick 0.1 -> 999.8 (not the ask).
        self.assertEqual(out.fills[0].price, D("999.8"))
        self.assertLess(out.fills[0].price, D("1000"))

    def test_buy_tick_rounds_up_and_sell_rounds_down(self):
        self.assertEqual(ex.ceil_to_tick(D("1000.21"), D("0.1")), D("1000.3"))
        self.assertEqual(ex.floor_to_tick(D("999.89"), D("0.1")), D("999.8"))

    def test_fee_is_separate_from_slippage(self):
        # Cap depth at 100 so the numbers match the D7 worked example.
        out = self.run_order(observations=[observation(ask_size=D("200"))])
        fill = out.fills[0]
        # 100 * 1000.2 = 100020 notional; 1 bps = 10.002 -> ceil 11.
        self.assertEqual(fill.fee_jpy, D("11"))
        self.assertEqual(fill.quantity, D("100"))
        self.assertAlmostEqual(float(fill.notional), 100020.0, places=6)
        self.assertEqual(out.fee_total, D("11"))

    def test_fee_minimum_applies(self):
        cons = constraints(fee_min=D("5"), fee_bps=D("0"))
        out = self.run_order(observations=[observation()], constraints=cons)
        self.assertEqual(out.fills[0].fee_jpy, D("5"))

    # -- quantity, lot, partial, conservation ------------------------------

    def test_partial_fill_cancels_ioc_remainder(self):
        # ask_size 200 * participation 0.5 = 100 available; request 200.
        out = self.run_order(observations=[observation(ask_size=D("200"))])
        self.assertEqual(out.filled_quantity, D("100"))
        self.assertEqual(out.remaining_quantity, D("0"))
        self.assertEqual(out.canceled_quantity, D("100"))
        self.assertEqual(out.status, "canceled")
        self.assertIn("IOC_REMAINDER_CANCELED", out.reason_codes)
        self.assertIn("LIQUIDITY_INSUFFICIENT", out.reason_codes)
        self.assertTrue(out.terminal)

    def test_multi_level_fills_within_one_observation(self):
        levels = (ex.DepthLevel(D("1000"), D("200")), ex.DepthLevel(D("1001"), D("200")))
        quote = observation(asks=levels)
        out = self.run_order(observations=[quote])
        self.assertEqual(out.filled_quantity, D("200"))
        self.assertEqual(out.status, "filled")
        self.assertEqual(len(out.fills), 2)
        self.assertEqual([f.quantity for f in out.fills], [D("100"), D("100")])
        self.assertEqual(out.fills[0].price, D("1000.2"))
        self.assertEqual(out.fills[1].price, D("1001.3"))
        # 100*1000.2 and 100*1001.3 -> 1 bps each = 10.002 / 10.013 -> 11 / 11.
        self.assertEqual(out.fee_total, D("11") + D("11"))

    def test_lot_floor_shrinks_and_cancels(self):
        out = self.run_order(order(requested_quantity=D("250"), remaining_quantity=D("250")),
                             observations=[observation()])
        self.assertEqual(out.filled_quantity, D("200"))
        self.assertEqual(out.canceled_quantity, D("50"))

    def test_below_minimum_quantity_is_zero_fill(self):
        out = self.run_order(observations=[observation(ask_size=D("200"))],
                             constraints=constraints(min_quantity=D("200")))
        self.assertEqual(out.fills, ())
        self.assertEqual(out.status, "canceled")
        self.assertIn("MIN_ORDER_NOT_MET", out.reason_codes)

    def test_below_min_notional_is_zero_fill(self):
        out = self.run_order(observations=[observation()],
                             constraints=constraints(min_notional=D("1000000000")))
        self.assertEqual(out.fills, ())
        self.assertIn("MIN_ORDER_NOT_MET", out.reason_codes)

    def test_risk_gap_shrinks_and_never_increases(self):
        out = self.run_order(observations=[observation()],
                             risk=risk(cash_available=D("200000")))
        self.assertEqual(out.filled_quantity, D("100"))
        self.assertIn("RISK_CHANGED_BEFORE_FILL", out.reason_codes)
        self.assertLess(out.filled_quantity, D("200"))

    def test_insufficient_funds_cancels(self):
        out = self.run_order(observations=[observation()], risk=risk(cash_available=D("1000")))
        self.assertEqual(out.fills, ())
        self.assertIn("INSUFFICIENT_FUNDS", out.reason_codes)
        self.assertEqual(out.canceled_quantity, D("200"))

    def test_insufficient_inventory_cancels(self):
        out = self.run_order(order(side="sell"), observations=[observation()],
                             risk=risk(inventory_available=D("0")))
        self.assertEqual(out.fills, ())
        self.assertIn("INSUFFICIENT_INVENTORY", out.reason_codes)

    def test_exposure_headroom_caps_buy(self):
        out = self.run_order(observations=[observation()],
                             risk=risk(exposure_headroom=D("110000")))
        self.assertEqual(out.filled_quantity, D("100"))

    def test_conservation_invariant_holds_on_all_paths(self):
        cases = [
            (order(), [observation()]),
            (order(), [observation(ask_size=D("200"))]),
            (order(), [observation(tradeable=False)]),
            (order(), [observation(), observation(observation_id="late", ingest_sequence=122,
                                                   market_event_at=T(11), market_available_at=T(11),
                                                   available_at=T(11), quote_digest="d" * 64)]),
            (order(requested_quantity=D("250"), remaining_quantity=D("250")), [observation()]),
        ]
        for order_obj, quotes in cases:
            out = self.run_order(order_obj, observations=quotes)
            self.assertEqual(
                order_obj.requested_quantity,
                out.filled_quantity + out.remaining_quantity + out.canceled_quantity,
            )
            self.assertEqual(out.filled_delta, out.filled_quantity - order_obj.filled_quantity)

    # -- expiry / terminal -------------------------------------------------

    def test_expired_order_is_terminal(self):
        out = self.run_order(observations=[observation()], now=T(400))
        self.assertEqual(out.status, "expired")
        self.assertIn("ORDER_EXPIRED", out.reason_codes)
        self.assertEqual(out.canceled_quantity, D("200"))
        self.assertTrue(out.terminal)

    def test_terminal_order_is_not_filled_again(self):
        out = self.run_order(order(status="canceled", remaining_quantity=D("0"),
                                   filled_quantity=D("0"), requested_quantity=D("200")),
                             observations=[observation()])
        self.assertEqual(out.fills, ())
        self.assertIn("ORDER_ALREADY_FINAL", out.reason_codes)

    def test_advance_partial_keeps_conservation(self):
        current = order(requested_quantity=D("200"), remaining_quantity=D("200"),
                        filled_quantity=D("0"), status="pending")
        out = self.run_order(current, observations=[observation(ask_size=D("200"))])
        advanced = ex.advance_partial(current, out)
        self.assertEqual(advanced.filled_quantity, D("100"))
        self.assertEqual(advanced.remaining_quantity, D("0"))
        self.assertEqual(advanced.status, "canceled")

    # -- account isolation / liquidity sharing -----------------------------

    def test_same_account_does_not_double_use_a_level(self):
        tracker = ex.LiquidityTracker()
        quote = observation(ask_size=D("200"))
        first = self.run_order(order(order_id="o1"), observations=[quote], liquidity=tracker)
        self.assertEqual(first.filled_quantity, D("100"))
        second = self.run_order(order(order_id="o2"), observations=[quote], liquidity=tracker)
        self.assertEqual(second.fills, ())
        self.assertIn("LIQUIDITY_INSUFFICIENT", second.reason_codes)

    def test_independent_accounts_do_not_compete(self):
        tracker = ex.LiquidityTracker()
        quote = observation(ask_size=D("200"))
        first = self.run_order(order(order_id="o1", account_id="account_a"),
                               observations=[quote], liquidity=tracker)
        second = self.run_order(order(order_id="o2", account_id="account_b"),
                                observations=[quote], liquidity=tracker)
        self.assertEqual(first.filled_quantity, D("100"))
        self.assertEqual(second.filled_quantity, D("100"))

    # -- contract guards ---------------------------------------------------

    def test_constraints_require_explicit_values(self):
        with self.assertRaises(TypeError):
            ex.InstrumentConstraints(version="fixture-v1", market="stocks")

    def test_invariants_are_fixed_to_paper(self):
        self.assertEqual(ex.MODE, "paper")
        self.assertFalse(ex.LIVE_ELIGIBLE)
        self.assertEqual(ex.EXECUTION_MODEL_VERSION, "next_observation_ioc_v1")
        self.assertEqual(ex.TIME_IN_FORCE, "next_observation_ioc")

    def test_module_does_not_reuse_incompatible_packages(self):
        source = Path(inspect.getsourcefile(ex)).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in {"free_strategy", "markets", "ledger", "paper", "worker", "risk"}:
            self.assertNotIn(forbidden, imported)
        for token in ("sqlite3", "socket", "subprocess", "requests", "urllib", "http"):
            self.assertNotIn(token, source)
        del imported

    def test_slippage_is_not_double_counted_against_spread(self):
        # Both a tight and a wide spread must yield the same slippage bps on the
        # chosen side; only the touch price differs.
        wide_ok = constraints(spread_limit_bps=D("500"))
        tight = self.run_order(observations=[observation(ask=D("1000"), bid=D("999.9"))],
                               constraints=wide_ok)
        wide = self.run_order(observations=[observation(ask=D("1000"), bid=D("980"))],
                              constraints=wide_ok)
        self.assertEqual(tight.fills[0].slippage_bps, wide.fills[0].slippage_bps)
        self.assertEqual(tight.fills[0].price, wide.fills[0].price)

    def test_spread_bps_helper(self):
        self.assertEqual(ex.spread_bps(observation(bid=D("999"), ask=D("1001"))), D("20"))


if __name__ == "__main__":
    unittest.main()
