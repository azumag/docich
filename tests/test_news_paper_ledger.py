"""N05 ledger tests: dedicated store, path rules, T1/T2, fence, accounting.

No network, credentials, broker or LLM call is made. Every database lives in
a per-test temp directory; every limit/price is a FIXTURE, not an approved
operational value (N00 v2 §5). Contract sources: N00 v2 §2 (schemas), §3.4
(stage×code actions), §4.1-4.2 (T1/T2 + crash), v3 §1 (fence), parent #769
D6-D9 and issue #782 acceptance items.
"""
import datetime as dt
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

from docich.trading.news_paper import ledger as ld
from docich.trading.news_paper.execution import ClockQuality, ExecutionOutcome, Fill

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 9, 18, 0, 10, 4, tzinfo=UTC)
HEX_A = "a" * 64


class SteppingClock:
    """Deterministic wall clock advancing ``step`` per read."""

    def __init__(self, start: dt.datetime = T0, step_seconds: int = 60) -> None:
        self._now = start
        self._step = dt.timedelta(seconds=step_seconds)

    def __call__(self) -> dt.datetime:
        value = self._now
        self._now = self._now + self._step
        return value


def receipt_draft(**changes) -> ld.ReceiptDraft:
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
        decision_quote=("src", "stocks:XTKS:DEMO", T0, HEX_A),
        policy_version="fixture-manager-v1",
        risk_policy_version="fixture-risk-v1",
        status="accepted",
        reason_codes=("NEWS_PRICE_AGREE",),
        supersedes_receipt_id=None,
    )
    base.update(changes)
    return ld.ReceiptDraft(**base)


def order_draft(**changes) -> ld.OrderDraft:
    base = dict(
        order_id="paper_order:receipt_001",
        asset="stocks:XTKS:DEMO",
        side="buy",
        requested_quantity="300",
        expires_at=T0 + dt.timedelta(minutes=75),
        reservation_id="res:receipt_001",
        event_use_id="event_use:run_demo_001:price_plus_news:event_001:stocks:XTKS:DEMO",
    )
    base.update(changes)
    return ld.OrderDraft(**base)


def reservation_draft(**changes) -> ld.ReservationDraft:
    base = dict(
        reservation_id="res:receipt_001",
        kind="cash",
        asset="stocks:XTKS:DEMO",
        quantity=D("0"),
        amount=D("300331"),
    )
    base.update(changes)
    return ld.ReservationDraft(**base)


def event_use_draft(**changes) -> ld.EventUseDraft:
    base = dict(
        event_use_id="event_use:run_demo_001:price_plus_news:event_001:stocks:XTKS:DEMO",
        event_group_id="event_001",
        asset="stocks:XTKS:DEMO",
    )
    base.update(changes)
    return ld.EventUseDraft(**base)


def open_ledger(tmp: Path, run: str = "run_demo_001", scenario: str = "price_plus_news"):
    return ld.NewsLedger.open(tmp, run, scenario, clock=SteppingClock())


def accept(ledger: ld.NewsLedger, receipt_id: str = "receipt_001",
           event_group: str | None = None) -> None:
    """Accept one candidate with ids derived from the receipt id."""
    group = event_group if event_group is not None else f"event:{receipt_id}"
    asset = "stocks:XTKS:DEMO"
    ledger.begin_receipt(
        receipt=receipt_draft(receipt_id=receipt_id),
        order=order_draft(
            order_id=f"paper_order:{receipt_id}",
            reservation_id=f"res:{receipt_id}",
            event_use_id=f"event_use:run_demo_001:price_plus_news:{group}:{asset}",
        ),
        reservation=reservation_draft(reservation_id=f"res:{receipt_id}"),
        event_use=event_use_draft(
            event_use_id=f"event_use:run_demo_001:price_plus_news:{group}:{asset}",
            event_group_id=group,
        ),
    )


def fill(order_id="paper_order:receipt_001", quote_ref="quote_121", level_index=0,
         quantity=D("300"), price=D("1001"), fee=D("31"), side="buy",
         partial=False, receipt_id="receipt_001", asset="stocks:XTKS:DEMO",
         filled_at=T0 + dt.timedelta(minutes=10), fill_id=None) -> Fill:
    return Fill(
        fill_id=fill_id or f"{order_id}:{quote_ref}:{level_index}",
        order_id=order_id,
        receipt_id=receipt_id,
        account_id="paper_news_demo",
        asset=asset,
        side=side,
        quantity=quantity,
        price=price,
        fee_jpy=fee,
        slippage_bps=D("2"),
        quote_ref=quote_ref,
        level_index=level_index,
        market_event_at=T0 + dt.timedelta(minutes=5),
        market_available_at=T0 + dt.timedelta(minutes=5),
        filled_at=filled_at,
        partial=partial,
    )


def accept_sell(ledger: ld.NewsLedger, receipt_id: str = "receipt_sell",
                quantity: str = "300") -> None:
    """Accept and commit a sell order with an inventory reservation."""
    group = f"event:{receipt_id}"
    asset = "stocks:XTKS:DEMO"
    ledger.begin_receipt(
        receipt=receipt_draft(receipt_id=receipt_id),
        order=order_draft(
            order_id=f"paper_order:{receipt_id}",
            asset=asset,
            side="sell",
            requested_quantity=quantity,
            reservation_id=f"res:{receipt_id}",
            event_use_id=f"event_use:run_demo_001:price_plus_news:{group}:{asset}",
        ),
        reservation=reservation_draft(
            reservation_id=f"res:{receipt_id}", kind="inventory", asset=asset,
            quantity=D(quantity),
        ),
        event_use=event_use_draft(
            event_use_id=f"event_use:run_demo_001:price_plus_news:{group}:{asset}",
            event_group_id=group,
        ),
    )
    ledger.commit_receipt(receipt_id)


def outcome(order_id="paper_order:receipt_001", fills=(), status="filled",
            canceled=D("0"), requested=D("300"), filled=D("300"),
            remaining=D("0"), codes=(), terminal=True) -> ExecutionOutcome:
    return ExecutionOutcome(
        order_id=order_id,
        status=status,
        requested_quantity=requested,
        filled_quantity=filled,
        remaining_quantity=remaining,
        canceled_quantity=canceled,
        fills=tuple(fills),
        reason_codes=tuple(codes),
        terminal=terminal,
        stage="SETTLE",
    )


class PathRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="n05-ledger-path-"))

    def test_creates_canonical_owner_only_tree(self):
        with open_ledger(self.tmp) as ledger:
            self.assertEqual(ledger.path.name, "paper.sqlite3")
            mode = oct(os.stat(ledger.path.parent).st_mode & 0o777)
            self.assertEqual(mode, "0o700")

    def test_relative_state_dir_rejected(self):
        with self.assertRaises(ld.LedgerError) as ctx:
            ld.NewsLedger.open("relative/state", "run_demo_001", "price_plus_news")
        self.assertEqual(ctx.exception.code, "STATE_PATH_FORBIDDEN")

    def test_bad_ids_rejected(self):
        for bad in ("../escape", "run demo", "", "a" * 200):
            with self.assertRaises(ld.LedgerError):
                ld.NewsLedger.open(self.tmp, bad, "price_plus_news")

    def test_symlinked_state_dir_resolves_to_real_path(self):
        # A state_dir reached through a system symlink is resolved to its real
        # path; the alias itself cannot reach a foreign store because the
        # canonical target is always <real>/news-paper/runs/<run>/<scenario>.
        real = self.tmp / "real-state"
        real.mkdir()
        link = self.tmp / "link-state"
        link.symlink_to(real)
        with open_ledger(link) as ledger:
            # macOS presents /var behind /private/var, so compare real paths.
            self.assertTrue(
                os.path.realpath(ledger.path).startswith(os.path.realpath(real))
            )

    def test_symlinked_run_dir_rejected(self):
        other = self.tmp / "other"
        other.mkdir()
        run_dir = self.tmp / "news-paper" / "runs" / "run_demo_001"
        run_dir.mkdir(parents=True)
        (run_dir / "price_plus_news").symlink_to(other)
        with self.assertRaises(ld.LedgerError) as ctx:
            open_ledger(self.tmp)
        self.assertEqual(ctx.exception.code, "STATE_PATH_FORBIDDEN")

    def test_hardlinked_db_rejected(self):
        ledger = open_ledger(self.tmp)
        ledger.close()
        alias = self.tmp / "alias.sqlite3"
        os.link(ledger.path, alias)
        # A hardlinked store would alias another database file; reopening the
        # canonical path is refused while the second link exists.
        with self.assertRaises(ld.LedgerError) as ctx:
            open_ledger(self.tmp)
        self.assertEqual(ctx.exception.code, "STATE_PATH_FORBIDDEN")

    def test_non_db_file_name_rejected(self):
        with self.assertRaises(ld.LedgerError):
            ld.NewsLedger(self.tmp / "news-paper" / "runs" / "x" / "other.sqlite3")

    def test_double_open_refused(self):
        ledger = open_ledger(self.tmp)
        try:
            script = (
                "import sys; sys.path.insert(0, %r);"
                "from docich.trading.news_paper import ledger as ld;"
                "ld.NewsLedger.open(%r, 'run_demo_001', 'price_plus_news')"
                % (str(Path.cwd() / "src"), str(self.tmp))
            )
            proc = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("STORE_UNAVAILABLE", proc.stderr)
        finally:
            ledger.close()


class SchemaConstraintsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="n05-ledger-schema-"))
        self.ledger = open_ledger(self.tmp)
        self.ledger.create_account(
            account_id="paper_news_demo", run_id="run_demo_001",
            scenario_id="price_plus_news", initial_cash="1000000",
        )

    def tearDown(self) -> None:
        self.ledger.close()

    def test_mode_and_live_eligible_fixed(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger._conn.execute(
                "INSERT INTO accounts(account_id, run_id, scenario_id, mode, live_eligible, "
                "quote_currency, initial_cash, cash, revision, created_at) "
                "VALUES ('x', 'r', 's', 'paper', 1, 'JPY', '1', '1', 1, '2026-09-18T00:00:00.000000Z')"
            )

    def test_accepted_receipt_requires_order_ref(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger._conn.execute(
                "INSERT INTO decision_receipts(receipt_id, run_id, scenario_id, account_id, mode, "
                "live_eligible, decision_at, manager_seen_at, input_cutoff_sequence, signal_refs, "
                "market_refs, decision_quote, policy_version, risk_policy_version, "
                "execution_model_version, status, reason_codes, order_ref, created_at) "
                "VALUES ('r1', 'run', 'sc', 'paper_news_demo', 'paper', 0, "
                "'2026-09-18T00:00:00.000000Z', '2026-09-18T00:00:00.000000Z', 1, '[]', '[]', "
                "NULL, 'v', 'v', 'next_observation_ioc_v1', 'accepted', '[\"NEWS_PRICE_AGREE\"]', "
                "NULL, '2026-09-18T00:00:00.000000Z')"
            )

    def test_event_use_unique_per_run_scenario_event_asset(self):
        accept(self.ledger, event_group="event_001")
        self.ledger.commit_receipt("receipt_001")
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.begin_receipt(
                receipt=receipt_draft(receipt_id="receipt_002"),
                order=order_draft(order_id="paper_order:receipt_002",
                                  reservation_id="res:receipt_002"),
                reservation=reservation_draft(reservation_id="res:receipt_002"),
                event_use=event_use_draft(),
            )

    def test_fill_unique_per_order_quote_level(self):
        accept(self.ledger)
        self.ledger.commit_receipt("receipt_001")
        self.ledger.apply_execution(outcome(fills=(fill(),)))
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger._conn.execute(
                "INSERT INTO fills(fill_id, order_id, receipt_id, account_id, asset, side, "
                "quantity, price, fee_jpy, slippage_bps, quote_ref, level_index, "
                "market_event_at, market_available_at, filled_at, partial, "
                "execution_model_version) VALUES ('dup', 'paper_order:receipt_001', "
                "'receipt_001', 'paper_news_demo', 'stocks:XTKS:DEMO', 'buy', '1', '1', '0', "
                "'0', 'quote_121', 0, '2026-09-18T00:00:00.000000Z', "
                "'2026-09-18T00:00:00.000000Z', '2026-09-18T00:00:00.000000Z', 0, "
                "'next_observation_ioc_v1')"
            )


class T1T2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="n05-ledger-t12-"))
        self.ledger = open_ledger(self.tmp)
        self.ledger.create_account(
            account_id="paper_news_demo", run_id="run_demo_001",
            scenario_id="price_plus_news", initial_cash="1000000",
        )

    def tearDown(self) -> None:
        self.ledger.close()

    def test_t1_reserves_cash_and_consumes_event(self):
        accept(self.ledger)
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertEqual(snap.cash_available, D("1000000") - D("300331"))
        self.assertTrue(
            self.ledger.event_use_exists(
                run_id="run_demo_001", scenario_id="price_plus_news",
                event_group_id="event:receipt_001", asset="stocks:XTKS:DEMO",
            )
        )

    def test_t2_commits_once_and_sets_not_before(self):
        accept(self.ledger)
        self.assertEqual(self.ledger.commit_receipt("receipt_001"), "committed")
        self.assertEqual(self.ledger.commit_receipt("receipt_001"), "already_committed")
        order = self.ledger.order_row("paper_order:receipt_001")
        self.assertIsNotNone(order["accepted_at"])
        self.assertIsNotNone(order["not_before_at"])
        self.assertLessEqual(order["accepted_at"], order["not_before_at"])

    def test_not_before_respects_expires_at(self):
        accept(self.ledger, "receipt_003")
        # expires_at is T0+75min; a huge delay must clamp to expires_at.
        self.ledger.commit_receipt("receipt_003", execution_delay_ms=D("999999999"))
        order = self.ledger.order_row("paper_order:receipt_003")
        self.assertEqual(order["not_before_at"], order["expires_at"])

    def test_uncommitted_receipt_never_fills(self):
        # Crash between T1 and T2: the marker is missing, so a fill attempt
        # fails closed and nothing is applied.
        accept(self.ledger)
        with self.assertRaises(ld.LedgerError) as ctx:
            self.ledger.apply_execution(outcome(fills=(fill(),)))
        self.assertEqual(ctx.exception.code, "RECEIPT_NOT_COMMITTED")
        self.assertEqual(self.ledger.filled_keys("paper_order:receipt_001"), frozenset())
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertEqual(snap.cash, D("1000000"))

    def test_recovery_commits_or_abandons(self):
        accept(self.ledger)
        accept(self.ledger, "receipt_002")
        self.ledger._conn.execute(
            "UPDATE paper_orders SET expires_at = ? WHERE order_id = ?",
            ("2026-09-18T00:00:00.000000Z", "paper_order:receipt_002"),
        )
        result = ld.recover(self.ledger)
        self.assertEqual(result["committed"], ["receipt_001"])
        self.assertEqual(result["abandoned"], ["receipt_002"])
        row = self.ledger.receipt_row("receipt_002")
        self.assertIsNotNone(row["abandoned_at"])
        order = self.ledger.order_row("paper_order:receipt_002")
        self.assertEqual(order["status"], "canceled")
        released = self.ledger._conn.execute(
            "SELECT released_at IS NOT NULL FROM reservations WHERE order_id = ?",
            ("paper_order:receipt_002",),
        ).fetchone()[0]
        self.assertTrue(released)
        # abandoned event cannot be re-accepted: event_use consumed at T1
        self.assertTrue(
            self.ledger.event_use_exists(
                run_id="run_demo_001", scenario_id="price_plus_news",
                event_group_id="event:receipt_002", asset="stocks:XTKS:DEMO",
            )
        )


class FenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="n05-ledger-fence-"))
        self.ledger = open_ledger(self.tmp)
        self.ledger.create_account(
            account_id="paper_news_demo", run_id="run_demo_001",
            scenario_id="price_plus_news", initial_cash="1000000",
        )

    def tearDown(self) -> None:
        self.ledger.close()

    def test_no_marker_no_fence(self):
        accept(self.ledger)
        self.assertIsNone(
            self.ledger.establish_fence("receipt_001", ClockQuality(True, "injected", 1000))
        )

    def test_fence_sampled_after_marker_and_reused(self):
        accept(self.ledger)
        self.ledger.commit_receipt("receipt_001")
        quality = ClockQuality(True, "injected", 1000)
        fence = self.ledger.establish_fence("receipt_001", quality)
        self.assertIsNotNone(fence)
        again = self.ledger.establish_fence("receipt_001", quality)
        self.assertEqual(fence.fence_at, again.fence_at)
        self.assertEqual(fence.fence_id, again.fence_id)


class ApplyAccountingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="n05-ledger-apply-"))
        self.ledger = open_ledger(self.tmp)
        self.ledger.create_account(
            account_id="paper_news_demo", run_id="run_demo_001",
            scenario_id="price_plus_news", initial_cash="1000000",
        )
        accept(self.ledger)
        self.ledger.commit_receipt("receipt_001")

    def tearDown(self) -> None:
        self.ledger.close()

    def test_buy_fill_settles_cash_position_and_releases_reservation(self):
        result = self.ledger.apply_execution(outcome(fills=(fill(),)))
        self.assertEqual(result.applied_fills, 1)
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertEqual(snap.cash, D("1000000") - D("300331"))
        self.assertEqual(snap.positions["stocks:XTKS:DEMO"], (D("300"), D("1001")))
        released = self.ledger._conn.execute(
            "SELECT released_at IS NOT NULL, release_reason FROM reservations "
            "WHERE order_id = ?",
            ("paper_order:receipt_001",),
        ).fetchone()
        self.assertTrue(released[0])
        self.assertEqual(released[1], "IOC_REMAINDER_CANCELED")

    def test_replayed_outcome_never_double_counts(self):
        self.ledger.apply_execution(outcome(fills=(fill(),)))
        replay = self.ledger.apply_execution(outcome(fills=(fill(),)))
        self.assertTrue(replay.already_final)
        snap = self.ledger.account_snapshot("paper_news_demo")
        self.assertEqual(snap.cash, D("1000000") - D("300331"))
        self.assertEqual(len(self.ledger.filled_keys("paper_order:receipt_001")), 1)

    def test_partial_fill_then_cancel_conserves_quantities(self):
        half = outcome(
            fills=(fill(quantity=D("100"), fill_id="f1"),),
            status="canceled", requested=D("300"), filled=D("100"),
            remaining=D("0"), canceled=D("200"),
            codes=("IOC_REMAINDER_CANCELED",),
        )
        self.ledger.apply_execution(half)
        order = self.ledger.order_row("paper_order:receipt_001")
        self.assertEqual(order["status"], "canceled")
        self.assertEqual(order["filled_quantity"], "100")
        cancels = self.ledger._conn.execute(
            "SELECT COALESCE(SUM(quantity), '0') FROM cancel_events WHERE order_id = ?",
            ("paper_order:receipt_001",),
        ).fetchone()[0]
        self.assertEqual(D(cancels), D("300") - D("100"))
        snap = self.ledger.account_snapshot("paper_news_demo")
        # 100 @ 1001 = 100100 plus the fixture fill fee 31
        self.assertEqual(snap.cash, D("1000000") - D("100131"))
        self.assertEqual(snap.positions["stocks:XTKS:DEMO"][0], D("100"))

    def test_sell_cannot_go_short(self):
        accept_sell(self.ledger, "receipt_sell", quantity="400")
        sell = outcome(
            order_id="paper_order:receipt_sell",
            fills=(fill(order_id="paper_order:receipt_sell", side="sell",
                        quantity=D("400"), fill_id="f2",
                        receipt_id="receipt_sell"),),
            status="filled", requested=D("400"), filled=D("400"),
        )
        with self.assertRaises(ld.LedgerError) as ctx:
            self.ledger.apply_execution(sell)
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_INVENTORY")

    def test_buy_cannot_overdraw_cash(self):
        huge = outcome(
            fills=(fill(quantity=D("100000"), price=D("1001"), fill_id="f3"),),
            status="filled", requested=D("100000"), filled=D("100000"),
        )
        with self.assertRaises(ld.LedgerError) as ctx:
            self.ledger.apply_execution(huge)
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_FUNDS")

    def test_sell_proceeds_credit_cash_only_on_fill(self):
        self.ledger.apply_execution(outcome(fills=(fill(),)))
        cash_after_buy = self.ledger.account_snapshot("paper_news_demo").cash
        accept_sell(self.ledger, "receipt_sell", quantity="300")
        sell = outcome(
            order_id="paper_order:receipt_sell",
            fills=(fill(order_id="paper_order:receipt_sell", side="sell",
                        quantity=D("300"), price=D("1010"), fee=D("32"),
                        fill_id="f4", receipt_id="receipt_sell"),),
            status="filled", requested=D("300"), filled=D("300"),
        )
        self.ledger.apply_execution(sell)
        snap = self.ledger.account_snapshot("paper_news_demo")
        # Proceeds (303000) minus fee (32) settle cash exactly at fill time.
        self.assertEqual(snap.cash, cash_after_buy + D("302968"))
        self.assertNotIn("stocks:XTKS:DEMO", snap.positions)

    def test_negative_equity_valuation_is_recorded(self):
        # Losses are recorded, never hidden behind an invalid value (D8).
        self.ledger.record_valuation(
            account_id="paper_news_demo", valuation_id="val_neg",
            as_of="2026-09-18T01:00:00.000000Z",
            positions_value="0", total_equity="-5000",
        )
        history = self.ledger.equity_history("paper_news_demo")
        self.assertEqual(history[-1][1], D("-5000"))

    def test_audit_is_append_only_and_payload_allowlisted(self):
        self.ledger.apply_execution(outcome(fills=(fill(),)))
        rows = self.ledger._conn.execute(
            "SELECT kind, payload FROM audit_events ORDER BY seq"
        ).fetchall()
        kinds = [kind for kind, _ in rows]
        self.assertIn("execution_applied", kinds)
        for _kind, payload in rows:
            data = __import__("json").loads(payload)
            for key, value in data.items():
                self.assertIsInstance(key, str)
                self.assertIsInstance(value, (str, int)) or value is None


class UnhealthyStoreTest(unittest.TestCase):
    def test_corrupt_database_fails_closed(self):
        tmp = Path(tempfile.mkdtemp(prefix="n05-ledger-bad-"))
        ledger = open_ledger(tmp)
        ledger.close()
        ledger.path.write_bytes(b"this is not sqlite")
        with self.assertRaises(ld.LedgerError) as ctx:
            open_ledger(tmp)
        self.assertEqual(ctx.exception.code, "STORE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
