"""Synthetic, offline PAPER fixtures; none of these profits are performance evidence."""
import datetime as dt
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from docich.trading.markets.core import D, Limits, PaperBook, Policy, Quote, digest
from docich.trading.markets.lab import active_policy, advance_challenger, propose, write_json


class TrailingPaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()
        self.limits = Limits(lot=1, fee_bps="0", slippage_bps="0", financing_bps_per_day="0",
                             max_spread_bps="100")
        self.policy = Policy(lookback=3, entry_bps=200, exit_mode="trailing")
        self.book = PaperBook(self.root / "paper.sqlite3", "stocks", self.limits)

    def tearDown(self):
        self.book.close()
        self.tmp.cleanup()

    def quote(self, offset, price, **changes):
        return replace(Quote("TEST", self.now + offset, str(price), str(price),
                             "100000", "100000", True, "synthetic-fixture"), **changes)

    def tick(self, offset, price, *, policy=None, force_flat=False, **changes):
        return self.book.process([self.quote(offset, price, **changes)], policy or self.policy,
                                 now=self.now + offset, allow_entries=True, force_flat=force_flat)

    def warm(self, side=1, policy=None):
        for i, price in enumerate((100 - 4 * side, 100 - 2 * side, 100)):
            self.tick(i, price, policy=policy)
        self.assertEqual(self.position()["entry"], "100")
        self.assertEqual(self.position()["side"], side)

    def position(self):
        return self.book.state()["positions"]["TEST"]

    def last_fill(self):
        return json.loads(self.book.db.execute("SELECT body FROM fills ORDER BY ts DESC LIMIT 1").fetchone()[0])

    def restart(self):
        market = self.book.market
        path = Path(self.book.db.execute("PRAGMA database_list").fetchone()[2])
        self.book.close()
        self.book = PaperBook(path, market, self.limits)

    def use_fx(self):
        self.book.close()
        self.book = PaperBook(self.root / "fx.sqlite3", "fx", self.limits)

    def test_legacy_policy_id_and_loading_are_unchanged(self):
        old = {"kind": "momentum", "lookback": 12, "entry_bps": 12,
               "stop_bps": 60, "take_bps": 100, "max_hold_s": 900}
        self.assertEqual(Policy(**old).version, digest(old)[:20])
        write_json(self.root / "active-policy.json", {"policy": old})
        self.assertEqual(active_policy(self.root), Policy())
        self.assertNotEqual(Policy().version, replace(Policy(), exit_mode="trailing").version)

    def test_policy_bounds_unknown_fields_and_live_mode(self):
        for fields in ({"exit_mode": "live"}, {"trail_activation_bps": True},
                       {"trail_distance_bps": "50"}, {"trail_distance_bps": 0},
                       {"trail_distance_bps": 301}, {"trail_activation_bps": 601},
                       {"trail_activation_bps": 50, "trail_distance_bps": 50}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                Policy(**fields)
        with self.assertRaises(TypeError):
            Policy(capital_jpy="1")
        with self.assertRaises(ValueError):
            PaperBook(self.root / "not-live.db", "stocks", self.limits, mode="live")

    def test_activation_boundary_replaces_fixed_take_profit(self):
        self.warm()
        self.tick(3, "100.9999")
        self.assertFalse(self.position()["exit_state"]["armed"])
        self.tick(4, "101")
        state = self.position()["exit_state"]
        self.assertTrue(state["armed"])
        self.assertEqual(D(state["stop_price"]), D("100.495"))
        self.assertEqual(self.last_fill()["kind"], "open")

    def test_long_ratchet_and_inclusive_trigger(self):
        self.warm()
        self.tick(3, 101)
        self.tick(4, 102)
        self.tick(5, "101.9")
        self.assertEqual(D(self.position()["exit_state"]["stop_price"]), D("101.49"))
        self.tick(6, "101.49")
        fill = self.last_fill()
        self.assertEqual(fill["reason"], "trailing_stop")
        self.assertEqual(D(fill["price"]), D("101.49"))
        self.assertFalse(self.book.state()["positions"])

    def test_short_ratchet_and_ask_trigger(self):
        self.use_fx()
        self.warm(side=-1)
        self.tick(3, 99)
        self.tick(4, 98)
        self.tick(5, "98.1")
        self.assertEqual(D(self.position()["exit_state"]["stop_price"]), D("98.49"))
        self.tick(6, "98.1", ask="98.49")
        fill = self.last_fill()
        self.assertEqual(fill["reason"], "trailing_stop")
        self.assertEqual(D(fill["price"]), D("98.49"))

    def test_long_uses_bid_not_ask_to_arm(self):
        self.warm()
        self.tick(3, "100.9", ask="101.1")
        self.assertFalse(self.position()["exit_state"]["armed"])

    def test_gap_is_filled_at_observed_price_not_stop(self):
        self.warm()
        self.tick(3, 102)
        self.tick(4, "100.2")
        fill = self.last_fill()
        self.assertEqual(fill["reason"], "trailing_stop")
        self.assertEqual(D(fill["trigger_stop_price"]), D("101.49"))
        self.assertEqual(D(fill["price"]), D("100.2"))

    def test_initial_stop_before_activation(self):
        self.warm()
        self.tick(3, "99.4")
        self.assertEqual(self.last_fill()["reason"], "stop_loss")

    def test_time_stop_survives_trailing_mode(self):
        self.policy = replace(self.policy, max_hold_s=30)
        self.warm()
        self.tick(3, 101)
        self.tick(32, "100.8")
        self.assertEqual(self.last_fill()["reason"], "max_hold")

    def test_session_end_has_priority(self):
        self.warm()
        self.tick(3, 102)
        self.tick(4, 101, force_flat=True)
        self.assertEqual(self.last_fill()["reason"], "session_end")

    def test_daily_risk_stop_has_priority(self):
        self.warm()
        state = self.book.state()
        state["risk_stopped"] = True
        with self.book.db:
            self.book.db.execute("UPDATE account SET body=?", (json.dumps(state),))
        self.tick(3, 101)
        self.assertEqual(self.last_fill()["reason"], "risk_stop")

    def test_signal_reversal_is_still_an_exit(self):
        self.policy = replace(self.policy, stop_bps=300)
        self.warm()
        for offset in (3, 4):
            self.tick(offset, 100)
        self.tick(5, 98)
        self.assertEqual(self.last_fill()["reason"], "signal_reverse")

    def test_liquidity_failure_latches_and_survives_restart_and_rebound(self):
        self.warm()
        self.tick(3, 102)
        result = self.tick(4, 101, bid_size="1")
        self.assertTrue(result["pending_liquidation"])
        self.assertEqual(self.last_fill()["kind"], "open")
        requested = self.position()["exit_state"]
        self.restart()
        self.assertEqual(self.position()["exit_state"], requested)
        self.tick(5, 103)
        fill = self.last_fill()
        self.assertEqual(fill["reason"], "trailing_stop")
        self.assertEqual(fill["trigger_quote_ts"], self.now + 4)
        self.assertEqual(D(fill["price"]), D(103))

    def test_restart_keeps_armed_extrema_and_stop(self):
        self.warm()
        self.tick(3, 102)
        before = self.position()
        self.restart()
        self.assertEqual(self.position(), before)
        self.tick(4, "101.9")
        self.assertEqual(self.position()["exit_state"]["stop_price"], before["exit_state"]["stop_price"])

    def test_duplicate_or_older_quotes_cannot_move_stop_or_fill(self):
        self.warm()
        self.tick(3, 102)
        before = self.position()
        for now_offset, quote_offset, price in ((4, 3, 105), (5, 2.5, 90)):
            result = self.book.process([self.quote(quote_offset, price)], self.policy,
                                      now=self.now + now_offset, allow_entries=True)
            self.assertEqual(result["accepted_quotes"], 0)
            self.assertEqual(self.position(), before)

    def test_invalid_quotes_do_not_mutate_exit_state(self):
        self.warm()
        self.tick(3, 102)
        before = self.position()["exit_state"]
        quotes = [self.quote(-100, 105), self.quote(100, 105),
                  self.quote(4, 105, tradeable=False), self.quote(4, 105, bid_size="0"),
                  self.quote(4, 105, ask="200"), self.quote(4, "NaN")]
        for i, q in enumerate(quotes):
            with self.subTest(q=q):
                result = self.book.process([q], self.policy, now=self.now + 5 + i,
                                          allow_entries=True, force_flat=True)
                self.assertEqual(result["accepted_quotes"], 0)
                self.assertEqual(self.position()["exit_state"], before)
                self.assertTrue(result["pending_liquidation"])

    def test_duplicate_close_tick_is_idempotent(self):
        self.warm()
        self.tick(3, 102)
        self.tick(4, 101)
        before = self.book.state()
        self.restart()
        self.tick(4, 101)
        self.assertEqual(self.book.state(), before)
        self.assertEqual(self.book.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0], 2)

    def test_open_position_does_not_take_new_policy_exit_settings(self):
        self.warm()
        self.tick(3, 101, policy=replace(self.policy, exit_mode="fixed", trail_distance_bps=5))
        self.assertEqual(self.position()["exit_mode"], "trailing")
        self.assertEqual(self.position()["trail_distance_bps"], 50)
        self.assertEqual(self.last_fill()["kind"], "open")

    def test_legacy_position_stays_fixed_with_partial_mfe_label(self):
        self.warm(policy=replace(self.policy, exit_mode="fixed"))
        state = self.book.state()
        for key in ("exit_mode", "trail_activation_bps", "trail_distance_bps", "exit_state"):
            state["positions"]["TEST"].pop(key)
        with self.book.db:
            self.book.db.execute("UPDATE account SET body=?", (json.dumps(state),))
        self.tick(3, 101)
        fill = self.last_fill()
        self.assertEqual(fill["reason"], "take_profit")
        self.assertEqual(fill["exit_mode"], "fixed")
        self.assertFalse(fill["mfe_tracking_from_entry"])

    def test_reentry_has_fresh_state(self):
        self.use_fx()
        self.warm()
        self.tick(3, 101)
        self.tick(4, 102, force_flat=True)
        self.tick(5, 104)
        pos = self.position()
        self.assertEqual(D(pos["entry"]), D(104))
        self.assertEqual(D(pos["exit_state"]["peak_price"]), D(104))
        self.assertFalse(pos["exit_state"]["armed"])
        self.assertIsNone(pos["exit_state"]["requested_reason"])

    def test_transaction_failure_rolls_back_trigger_fill_and_stats(self):
        self.warm()
        self.tick(3, 102)
        before = self.book.state()
        with patch("docich.trading.markets.core.record_exit_stats", side_effect=RuntimeError("fixture")):
            with self.assertRaises(RuntimeError):
                self.tick(4, 101)
        self.assertEqual(self.book.state(), before)
        self.assertEqual(self.last_fill()["kind"], "open")
        self.tick(4, 101)
        self.assertEqual(self.last_fill()["reason"], "trailing_stop")

    def test_missing_trailing_state_fails_closed_not_reset_to_fixed(self):
        self.warm()
        state = self.book.state()
        state["positions"]["TEST"].pop("exit_state")
        with self.book.db:
            self.book.db.execute("UPDATE account SET body=?", (json.dumps(state),))
        with self.assertRaises(ValueError):
            self.tick(3, 101)
        self.assertEqual(self.book.state(), state)

    def test_mfe_and_giveback_are_decimal_net_metrics(self):
        self.warm()
        self.tick(3, 102)
        result = self.tick(4, "101.49")
        fill = self.last_fill()
        self.assertEqual(D(fill["max_net_pnl_jpy"]), D(2000))
        self.assertEqual(D(fill["net_pnl_jpy"]), D(1490))
        self.assertEqual(D(fill["peak_to_exit_giveback_jpy"]), D(510))
        self.assertTrue(fill["mfe_tracking_from_entry"])
        self.assertEqual(result["exit_stats"]["closed_trades"], 1)
        self.assertEqual(result["exit_stats"]["wins"], 1)
        self.assertEqual(D(result["exit_stats"]["net_pnl_jpy"]), D(1490))

    def test_fee_slippage_and_financing_match_the_close_model(self):
        self.book.close()
        self.limits = replace(self.limits, fee_bps="1", slippage_bps="2", financing_bps_per_day="2")
        self.book = PaperBook(self.root / "costs.sqlite3", "fx", self.limits)
        for i, price in enumerate((96, 98, 100)):
            self.tick(i, price)
        self.tick(3, 102)
        peak_pos = self.position()
        expected_peak = ((D(102) * D("0.9998") - D(peak_pos["entry"])) * D(peak_pos["qty"])
                         - D(peak_pos["qty"]) * D(102) * D("0.9998") / 10000
                         - D(peak_pos["entry_fee"]) - D(peak_pos["financing"]))
        self.tick(4, 101)
        fill = self.last_fill()
        self.assertEqual(D(fill["max_net_pnl_jpy"]), expected_peak)
        self.assertEqual(D(fill["price"]), D("100.9798"))
        self.assertEqual(D(fill["peak_to_exit_giveback_jpy"]), expected_peak - D(fill["net_pnl_jpy"]))
        self.assertEqual(D(self.book.state()["cash"]) - D(self.limits.capital_jpy), D(fill["net_pnl_jpy"]))

    def prepare_comparison(self, **changes):
        baseline = replace(self.policy, exit_mode="fixed")
        write_json(self.root / "active-policy.json", {"policy": asdict(baseline)})
        self.book.report(self.now - 10)
        candidate = replace(self.policy, **changes)
        before = self.book.state()
        result = propose(self.book, self.root, None, agents="offline-test", now=self.now - 10,
                         news=[{"published_at": self.now - 20, "observed_at": self.now - 10}],
                         generate=lambda _: json.dumps({"policy": asdict(candidate), "reason": "synthetic test"}))
        self.assertEqual(self.book.state(), before)
        self.assertEqual(active_policy(self.root), baseline)
        return result

    def test_proposal_is_shadow_only_with_identical_entry_conditions(self):
        self.assertEqual(self.prepare_comparison()["status"], "forward_test")
        marker = json.loads((self.root / "challenger.json").read_text())
        self.assertTrue(marker["exit_comparison"])
        self.assertFalse(marker["live_enabled"])
        self.assertEqual(marker["baseline"]["entry_bps"], marker["policy"]["entry_bps"])

    def test_exit_comparison_cannot_also_tune_entry_or_safety(self):
        self.assertEqual(self.prepare_comparison(stop_bps=100)["status"], "retry")
        self.assertFalse((self.root / "challenger.json").exists())

    def test_preproposal_quotes_are_not_forward_evidence(self):
        self.prepare_comparison()
        marker = json.loads((self.root / "challenger.json").read_text())
        result = advance_challenger(self.book, self.root, [self.quote(-11, 100)],
                                   now=self.now - 9, allow_entries=True, force_flat=False)
        self.assertEqual(result["status"], "collecting")
        for name in ("baseline", "policy"):
            test = PaperBook(self.root / "experiments" / marker["id"] / f"{name}.sqlite3", "stocks", self.limits)
            try:
                self.assertFalse(test.state()["history"])
                self.assertFalse(test.state()["positions"])
            finally:
                test.close()

    def test_two_arms_receive_same_quotes_but_only_candidate_trails(self):
        self.prepare_comparison()
        before = self.book.state()
        for i, price in enumerate((96, 98, 100, 101)):
            result = advance_challenger(self.book, self.root, [self.quote(i, price)],
                                       now=self.now + i, allow_entries=True, force_flat=False)
        self.assertEqual(result["closed_trades"], {"baseline": 1, "policy": 0})
        self.assertEqual(self.book.state(), before)
        self.assertIn("max_drawdown", result["comparison_metrics"]["policy"])

    def test_completed_comparison_never_auto_adopts_even_if_marker_flag_lies(self):
        self.prepare_comparison()
        path = self.root / "challenger.json"
        marker = json.loads(path.read_text())
        marker["exit_comparison"] = False
        write_json(path, marker)
        before = self.book.state()
        active_bytes = (self.root / "active-policy.json").read_bytes()
        for day in range(10):
            for i, price in enumerate((96, 98, 100, 101, 102, "101.49")):
                offset = day * 86400 + i
                result = advance_challenger(self.book, self.root, [self.quote(offset, price)],
                                           now=self.now + offset, allow_entries=True, force_flat=False)
        self.assertEqual(result["status"], "paper_review_required")
        self.assertTrue(result["candidate_passes_screen"])
        self.assertFalse(result["live_enabled"])
        self.assertEqual(result["closed_trades"], {"baseline": 10, "policy": 10})
        self.assertEqual((self.root / "active-policy.json").read_bytes(), active_bytes)
        self.assertEqual(self.book.state(), before)
        self.assertFalse(path.exists())
        self.assertTrue((self.root / "experiments" / marker["id"] / "verdict.json").exists())

    def test_challenger_live_flag_is_rejected(self):
        self.prepare_comparison()
        path = self.root / "challenger.json"
        marker = json.loads(path.read_text())
        marker["live_enabled"] = True
        write_json(path, marker)
        with self.assertRaises(ValueError):
            advance_challenger(self.book, self.root, [self.quote(0, 100)],
                               now=self.now, allow_entries=True, force_flat=False)
        self.assertFalse((self.root / "experiments").exists())


if __name__ == "__main__":
    unittest.main()
