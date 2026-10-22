"""N05 manager tests: decisions, T1/T2, N06 execution boundary, recovery.

No network, credentials, broker or LLM call. Databases live in per-test temp
directories; all numbers are FIXTURES. Contract sources: N00 v2 §2.6/§2.7,
§3.4, §4.1-4.2, v3 §1, parent #769 D6/D7/D9, issue #782 acceptance items
(uncommitted fill 0, recovery backfill 0, double submit, event-use uniqueness,
no-trade tracking, no raw payloads, correction keeps past fills).
"""
import datetime as dt
import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

from docich.trading.news_paper.execution import (
    ClockQuality,
    ExecutionOutcome,
    InstrumentConstraints,
    MarketObservation,
)
from docich.trading.news_paper.ledger import NewsLedger, _parse_ts
from docich.trading.news_paper.manager import (
    DecisionRequest,
    PaperManager,
    FIXTURE_MANAGER_POLICY_V1,
)
from docich.trading.news_paper.manager import ManagerPolicy

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 9, 18, 0, 10, 4, tzinfo=UTC)
EXPIRES = dt.datetime(2026, 9, 18, 1, 25, 0, tzinfo=UTC)
ASSET = "stocks:XTKS:DEMO"


class SteppingClock:
    def __init__(self, start: dt.datetime = T0, step_seconds: int = 60) -> None:
        self._now = start
        self._step = dt.timedelta(seconds=step_seconds)

    def __call__(self) -> dt.datetime:
        value = self._now
        self._now = self._now + self._step
        return value


def constraints(**changes) -> InstrumentConstraints:
    base = dict(
        version="fixture-stocks-v1", market="stocks", quote_currency="JPY",
        lot=D("100"), min_quantity=D("100"), min_notional=D("50000"),
        price_tick=D("1"), fee_bps=D("1"), fee_min=D("1"), slippage_bps=D("2"),
        spread_limit_bps=D("50"), participation_rate=D("0.1"),
        max_quote_age_seconds=D("60"),
    )
    base.update(changes)
    return InstrumentConstraints(**base)


def request(**changes) -> DecisionRequest:
    base = dict(
        receipt_id="receipt_001",
        run_id="run_demo_001",
        scenario_id="price_plus_news",
        account_id="paper_news_demo",
        decision_at=T0,
        manager_seen_at=T0 - dt.timedelta(seconds=2),
        input_cutoff_sequence=120,
        signal_refs=("signal_001",),
        market_refs=("quote_120",),
        decision_quote=("src", ASSET, T0, "a" * 64),
        asset=ASSET,
        side="buy",
        reference_price=D("1000"),
        expires_at=EXPIRES,
        constraints=constraints(),
        event_group_id="event_001",
        quantity_cap=D("300"),
    )
    base.update(changes)
    return DecisionRequest(**base)


class ManagerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="n05-manager-"))
        self.ledger = NewsLedger.open(
            self.tmp, "run_demo_001", "price_plus_news", clock=SteppingClock()
        )
        self.ledger.create_account(
            account_id="paper_news_demo", run_id="run_demo_001",
            scenario_id="price_plus_news", initial_cash="1000000",
        )
        self.manager = PaperManager(
            self.ledger, clock_quality=ClockQuality(True, "injected", 1000)
        )

    def tearDown(self) -> None:
        self.ledger.close()

    def observation(self, *, ingest=121, after_fence: dt.datetime | None = None,
                    before_fence: dt.datetime | None = None,
                    observation_id="quote_121", digest="c" * 64,
                    bid=D("999"), ask=D("1000")) -> MarketObservation:
        if after_fence is not None:
            event_at = after_fence - dt.timedelta(seconds=10)
            return MarketObservation(
                observation_id=observation_id, source="src", asset=ASSET,
                ingest_sequence=ingest, market_event_at=event_at,
                market_available_at=after_fence,
                available_at=after_fence + dt.timedelta(seconds=1),
                bid=bid, ask=ask, bid_size=D("5000"), ask_size=D("5000"),
                size_semantics="executable", tradeable=True, realtime=True,
                session_id="s1", calendar_version="c1", quote_digest=digest,
                provenance_class="test_fixture",
                instrument_constraints_version="fixture-stocks-v1",
            )
        assert before_fence is not None
        return MarketObservation(
            observation_id=observation_id, source="src", asset=ASSET,
            ingest_sequence=ingest, market_event_at=before_fence,
            market_available_at=before_fence,
            available_at=before_fence,
            bid=bid, ask=ask, bid_size=D("5000"), ask_size=D("5000"),
            size_semantics="executable", tradeable=True, realtime=True,
            session_id="s1", calendar_version="c1", quote_digest=digest,
            provenance_class="test_fixture",
            instrument_constraints_version="fixture-stocks-v1",
        )


class AcceptPathTest(ManagerTestCase):
    def test_accept_runs_t1_t2_and_returns_committed_receipt(self):
        view = self.manager.submit_candidate(request())
        self.assertEqual(view.status, "accepted")
        self.assertEqual(view.reason_codes, ("NEWS_PRICE_AGREE",))
        self.assertIsNotNone(view.receipt_committed_at)
        self.assertEqual(view.order_ref, "paper_order:receipt_001")
        row = self.ledger.receipt_row("receipt_001")
        self.assertEqual(row["mode"], "paper")
        self.assertEqual(row["live_eligible"], 0)
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertEqual(snap.cash_reserved, D("300331"))

    def test_resend_returns_same_receipt_without_changes(self):
        first = self.manager.submit_candidate(request())
        again = self.manager.submit_candidate(request())
        self.assertEqual(first.receipt_committed_at, again.receipt_committed_at)
        self.assertEqual(first.order_ref, again.order_ref)
        # exactly one order row exists
        rows = self.ledger._conn.execute(
            "SELECT COUNT(*) FROM paper_orders WHERE receipt_id = 'receipt_001'"
        ).fetchone()[0]
        self.assertEqual(rows, 1)

    def test_event_reentry_blocked_across_receipts(self):
        self.manager.submit_candidate(request())
        second = self.manager.submit_candidate(request(receipt_id="receipt_002"))
        self.assertEqual(second.status, "held")
        self.assertEqual(second.reason_codes, ("EVENT_ALREADY_USED",))
        self.assertIsNone(second.order_ref)

    def test_held_receipt_tracks_no_trade(self):
        view = self.manager.submit_candidate(
            request(), intent="held", reason_codes=("NEWS_PRICE_CONFLICT",)
        )
        self.assertEqual(view.status, "held")
        self.assertIsNone(view.order_ref)
        self.assertEqual(view.reason_codes, ("NEWS_PRICE_CONFLICT",))
        # no order, no reservation, no event_use
        self.assertEqual(
            self.ledger._conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0], 0
        )
        self.assertEqual(
            self.ledger._conn.execute("SELECT COUNT(*) FROM event_use").fetchone()[0], 0
        )

    def test_rejected_intent_recorded(self):
        view = self.manager.submit_candidate(
            request(), intent="rejected", reason_codes=("UNKNOWN_KEY",)
        )
        self.assertEqual(view.status, "rejected")
        self.assertIsNone(view.order_ref)

    def test_clock_untrusted_stops_new(self):
        untrusted = PaperManager(
            self.ledger, clock_quality=ClockQuality(False, "vm_clock", 1000)
        )
        with self.assertRaises(Exception) as ctx:
            untrusted.submit_candidate(request())
        self.assertEqual(ctx.exception.code, "CLOCK_UNTRUSTED")


class FillPathTest(ManagerTestCase):
    def accept(self):
        view = self.manager.submit_candidate(request())
        assert view.status == "accepted"
        return view

    def test_first_eligible_observation_fills(self):
        view = self.accept()
        order_id = view.order_ref
        not_before = _parse_ts(self.ledger.order_row(order_id)["not_before_at"])
        fence = self.ledger.establish_fence(view.receipt_id, self.manager._clock_quality)
        obs = self.observation(after_fence=fence.fence_at + dt.timedelta(seconds=10))
        assert obs.market_event_at > not_before
        outcome = self.manager.process_observations(order_id, [obs], constraints())
        self.assertEqual(outcome.status, "filled")
        self.assertEqual(len(outcome.fills), 1)
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertEqual(snap.cash, D("1000000") - D("300331"))
        self.assertEqual(snap.positions[ASSET], (D("300"), D("1001")))

    def test_old_observation_after_recovery_never_fills(self):
        """Recovery must not unlock quotes that pre-date the fence."""
        view = self.accept()
        order_id = view.order_ref
        fence = self.ledger.establish_fence(view.receipt_id, self.manager._clock_quality)
        old = self.observation(
            before_fence=fence.fence_at - dt.timedelta(seconds=5),
            observation_id="quote_old",
        )
        outcome = self.manager.process_observations(order_id, [old], constraints())
        self.assertEqual(outcome.status, "pending")
        self.assertEqual(outcome.fills, ())
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertEqual(snap.cash, D("1000000"))
        self.assertEqual(len(self.ledger.filled_keys(order_id)), 0)

    def test_uncommitted_receipt_refuses_fill(self):
        """T1/T2 crash: the fill evaluation fails closed, state unchanged."""
        # Simulate a crash right after T1 (no T2) through the raw ledger.
        from docich.trading.news_paper.ledger import (
            EventUseDraft,
            OrderDraft,
            ReceiptDraft,
            ReservationDraft,
        )

        self.ledger.begin_receipt(
            receipt=ReceiptDraft(
                receipt_id="receipt_crash", run_id="run_demo_001",
                scenario_id="price_plus_news", account_id="paper_news_demo",
                decision_at=T0, manager_seen_at=T0, input_cutoff_sequence=120,
                signal_refs=("signal_crash",), market_refs=("quote_120",),
                decision_quote=("src", ASSET, T0, "b" * 64),
                policy_version="fixture-manager-v1",
                risk_policy_version="fixture-risk-v1", status="accepted",
                reason_codes=("NEWS_PRICE_AGREE",),
            ),
            order=OrderDraft(
                order_id="paper_order:receipt_crash", asset=ASSET, side="buy",
                requested_quantity="100", expires_at=EXPIRES,
                reservation_id="res:receipt_crash",
                event_use_id="event_use:run_demo_001:price_plus_news:event_crash:stocks:XTKS:DEMO",
            ),
            reservation=ReservationDraft(
                reservation_id="res:receipt_crash", kind="cash", asset=ASSET,
                amount=D("100200"),
            ),
            event_use=EventUseDraft(
                event_use_id="event_use:run_demo_001:price_plus_news:event_crash:stocks:XTKS:DEMO",
                event_group_id="event_crash", asset=ASSET,
            ),
        )
        outcome = self.manager.process_observations(
            "paper_order:receipt_crash", [], constraints()
        )
        self.assertEqual(outcome.reason_codes, ("RECEIPT_NOT_COMMITTED",))
        self.assertFalse(outcome.terminal)
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertEqual(snap.cash, D("1000000"))
        # recovery commits the interrupted receipt (N00 v2 §4.2a)
        result = self.manager.recover()
        self.assertEqual(result["committed"], ["receipt_crash"])

    def test_evidence_revocation_cancels_pending(self):
        view = self.accept()
        order_id = view.order_ref
        outcome = self.manager.process_observations(
            order_id, [], constraints(), evidence_valid=False
        )
        self.assertEqual(outcome.status, "canceled")
        self.assertIn("EVIDENCE_REVOKED", outcome.reason_codes)
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertEqual(snap.cash, D("1000000"))

    def test_correction_cancels_pending_only(self):
        view = self.accept()
        order_id = view.order_ref
        result = self.manager.cancel_pending(view.receipt_id, reason_code="NEWS_CONFLICT")
        self.assertEqual(result.status, "canceled")
        order = self.ledger.order_row(order_id)
        self.assertEqual(order["status"], "canceled")
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertEqual(snap.cash, D("1000000"))  # reservation released
        # past fills are immutable: nothing was ever written to fills
        self.assertEqual(self.ledger._conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0], 0)

    def test_participation_and_gap_shrink_fill(self):
        view = self.accept()
        order_id = view.order_ref
        fence = self.ledger.establish_fence(view.receipt_id, self.manager._clock_quality)
        # Ask size 150 * participation 0.1 = 15 -> below min quantity 100.
        obs = MarketObservation(
            observation_id="quote_thin", source="src", asset=ASSET,
            ingest_sequence=121,
            market_event_at=fence.fence_at + dt.timedelta(seconds=5),
            market_available_at=fence.fence_at + dt.timedelta(seconds=5),
            available_at=fence.fence_at + dt.timedelta(seconds=6),
            bid=D("999"), ask=D("1000"), bid_size=D("150"), ask_size=D("150"),
            size_semantics="executable", tradeable=True, realtime=True,
            session_id="s1", calendar_version="c1", quote_digest="d" * 64,
            provenance_class="test_fixture",
            instrument_constraints_version="fixture-stocks-v1",
        )
        outcome = self.manager.process_observations(order_id, [obs], constraints())
        # min order not met after accept: shrink-to-zero + cancel (risk group)
        self.assertEqual(outcome.status, "canceled")
        self.assertIn("MIN_ORDER_NOT_MET", outcome.reason_codes)


class ConcurrentAcceptTest(ManagerTestCase):
    def test_concurrent_accepts_never_overreserve_cash(self):
        """Two simultaneous candidates compete for one cash pool: the second
        sizes down to the remaining headroom and total reservations never
        exceed the account cash (issue #782: no cap breach on same-time
        accepts)."""
        first = self.manager.submit_candidate(request())
        assert first.status == "accepted"
        second = self.manager.submit_candidate(
            request(receipt_id="receipt_002", event_group_id="event_002",
                    quantity_cap=D("5000"))
        )
        assert second.status == "accepted", second.reason_codes
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertLessEqual(snap.cash_reserved, snap.cash)
        self.assertGreater(snap.cash_available, 0)
        first_qty = D(str(self.ledger.order_row(first.order_ref)["requested_quantity"]))
        second_qty = D(str(self.ledger.order_row(second.order_ref)["requested_quantity"]))
        self.assertEqual(first_qty, D("300"))
        # Sizing shrank the second candidate to the remaining cash headroom
        # (600 = floor((699669 - fee headroom) / unit cost, lot 100)) instead
        # of letting it touch the first order's reserved cash.
        self.assertEqual(second_qty, D("600"))
        self.assertLessEqual(snap.cash_reserved, snap.cash)

    def test_pending_slot_limit_holds_concurrent_new_asset(self):
        from docich.trading.news_paper.risk import RiskPolicy

        tight = PaperManager(
            self.ledger,
            clock_quality=ClockQuality(True, "injected", 1000),
            risk_policy=RiskPolicy(
                version="fixture-risk-v1", max_positions=1,
                max_gross_exposure=D("10000000"),
                per_asset_exposure_cap=D("10000000"),
                daily_loss_limit=D("999999"), drawdown_limit=D("999999"),
            ),
        )
        first = tight.submit_candidate(request())
        assert first.status == "accepted"
        second = tight.submit_candidate(
            request(receipt_id="receipt_002", event_group_id="event_002",
                    asset="stocks:XTKS:NEW")
        )
        self.assertEqual(second.status, "held")
        self.assertEqual(second.reason_codes, ("POSITION_LIMIT",))
        # the first pending order is untouched
        self.assertEqual(
            self.ledger.order_row(first.order_ref)["status"], "pending"
        )


class RecoveryAndRestartTest(ManagerTestCase):
    def test_restart_reuses_fence_and_journal(self):
        view = self.manager.submit_candidate(request())
        order_id = view.order_ref
        fence = self.ledger.establish_fence(view.receipt_id, self.manager._clock_quality)
        obs = self.observation(after_fence=fence.fence_at + dt.timedelta(seconds=10))
        self.manager.process_observations(order_id, [obs], constraints())
        cash_before = self.ledger.account_snapshot("paper_news_demo").cash
        # Reopen the same store (restart) and re-run the same observation.
        self.ledger.close()
        self.ledger = NewsLedger.open(
            self.tmp, "run_demo_001", "price_plus_news", clock=SteppingClock()
        )
        self.manager = PaperManager(
            self.ledger, clock_quality=ClockQuality(True, "injected", 1000)
        )
        outcome = self.manager.process_observations(order_id, [obs], constraints())
        self.assertEqual(outcome.reason_codes, ("ORDER_ALREADY_FINAL",))
        self.assertEqual(
            self.ledger.account_snapshot("paper_news_demo").cash, cash_before
        )


class NoRawPayloadTest(ManagerTestCase):
    def test_receipt_stores_ids_and_codes_only(self):
        self.manager.submit_candidate(
            request(), intent="held", reason_codes=("MATERIALITY_LOW",)
        )
        row = self.ledger.receipt_row("receipt_001")
        blob = json.dumps(row, default=str)
        for forbidden in ("raw", "summary", "title", "secret", "token"):
            self.assertNotIn(forbidden, blob.lower())
        # signal refs are validated IDs; a free-form title cannot be stored
        from docich.trading.news_paper.ledger import LedgerError

        bad = request(receipt_id="receipt_bad", signal_refs=("この記事の要約本文がそのまま入る",))
        with self.assertRaises(LedgerError):
            self.manager.submit_candidate(bad, intent="held",
                                          reason_codes=("NO_DIRECTION",))


class SupersedeTest(ManagerTestCase):
    def test_reevaluate_creates_new_receipt(self):
        held = self.manager.submit_candidate(
            request(), intent="held", reason_codes=("COOLDOWN_ACTIVE",)
        )
        follow = request(receipt_id="receipt_002")
        view = self.manager.reevaluate_held(held.receipt_id, follow)
        self.assertEqual(view.status, "accepted")
        self.assertEqual(view.supersedes_receipt_id, "receipt_001")
        # the original held receipt is unchanged
        original = self.ledger.receipt_row("receipt_001")
        self.assertEqual(original["status"], "held")

    def test_reevaluate_non_held_refused(self):
        self.manager.submit_candidate(request())
        with self.assertRaises(Exception):
            self.manager.reevaluate_held("receipt_001", request(receipt_id="receipt_003"))


if __name__ == "__main__":
    unittest.main()
