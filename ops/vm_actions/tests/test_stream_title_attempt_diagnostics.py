"""Synthetic-only coverage for fixed, private title-updater observations."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "stream_title_attempt_collector", ROOT / "ops/vm_actions/collect_diagnostics.py"
)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class StreamTitleAttemptDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.soren = Path(self.tmp.name) / "soren"
        self.state = Path(self.tmp.name) / "state"
        self.soren.mkdir()
        self.state.mkdir()
        self.now = 1_800_000_000

    def log(self, body, *, source="game", age=5, newline=True):
        roots = {"game": (self.soren, "tmp/logs/stream_game.log", "stream_game"),
                 "dispatch": (self.state, "logs/stream-game.log", "stream_game"),
                 "day": (self.soren, "tmp/logs/stream_title_day.log", "stream_title")}
        root, relative, prefix = roots[source]
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now - age))
        path.write_text(f"[{prefix} {stamp}] {body}" + ("\n" if newline else ""))
        path.chmod(0o600)
        return path

    def collect(self, **kwargs):
        with mock.patch.object(collector, "_systemctl_scope", return_value=None):
            return collector._collect_stream_title_attempts(
                self.soren, self.state, self.now, **kwargs
            )

    def test_exact_producer_messages_are_fixed_observations_not_current_causes(self):
        examples = {
            "ERROR: no token (set TWITCH_GAME_TOKEN, TWITCH_TITLE_TOKEN, TWITCH_BOT_TOKEN or TWITCH_PREDICTIONS_TOKEN)": "credential_missing",
            "ERROR: TWITCH_BROADCASTER_ID not set": "broadcaster_missing",
            "ERROR: no client id (token invalid? resp: PRIVATE https://private.invalid/id)": "credential_validation_failed",
            "ERROR: token(login=PRIVATE) lacks 'channel:manage:broadcast' scope. TWITCH_GAME_TOKEN (broadcaster の broadcast 付きトークン) を設定してください。": "broadcast_scope_missing",
            "ERROR: public title helper not found: /PRIVATE/body": "helper_missing",
            "ERROR: failed to compose public title": "composition_failed",
            "ERROR: failed to fetch current channel (resp: PRIVATE)": "channel_read_failed",
            "ERROR: PATCH failed (HTTP 401): PRIVATE": "channel_update_failed",
        }
        for message, reason in examples.items():
            with self.subTest(reason=reason):
                self.log(message)
                out = self.collect()
                self.assertEqual(out["logs"]["game"]["observed_reason"], reason)
                self.assertEqual(out["logs"]["game"]["record_status"], "fresh")
                self.assertEqual(out["current_failure"], "unknown")
                self.assertNotIn("PRIVATE", json.dumps(out))
                self.assertNotIn("private.invalid", json.dumps(out))

    def test_stale_failure_is_retained_but_not_a_current_cause(self):
        self.log("ERROR: no valid broadcaster credential with channel:manage:broadcast", source="day", age=39000)
        out = self.collect()
        row = out["logs"]["day"]
        self.assertEqual(row["record_status"], "stale")
        self.assertEqual(row["observed_reason"], "credential_candidates_rejected")
        self.assertEqual(row["age_sec"], 39000)
        self.assertEqual(row["time_basis"], "host_local_unverified")
        self.assertEqual(out["current_failure"], "unknown")

    def test_pinned_daily_success_nochange_and_body_failure_literals(self):
        # Public Soren 916b0ced, update_stream_title_day.sh lines 154/189/211.
        for body, outcome, reason in (
            ("OK: title updated -> PRIVATE", "twitch_updated", "none"),
            ("title already current for day 222; no change needed", "unchanged", "none"),
            ("ERROR: failed to choose public title body", "failure", "body_selection_failed"),
        ):
            with self.subTest(outcome=outcome, reason=reason):
                self.log(body, source="day")
                row = self.collect()["logs"]["day"]
                self.assertEqual(row["observed_outcome"], outcome)
                self.assertEqual(row["observed_reason"], reason)
                self.assertNotIn("PRIVATE", json.dumps(row))
        for day in ("-1", "unknown", "0", "1\nPRIVATE", "9" * 100):
            with self.subTest(day=day):
                self.log(f"title already current for day {day}; no change needed", source="day")
                self.assertEqual(self.collect()["logs"]["day"]["observed_reason"], "unknown")

    def test_later_success_and_unknown_lines_supersede_an_old_failure(self):
        path = self.log("ERROR: failed to compose public title", age=10)
        old = path.read_text()
        self.log("OK: updated title=PRIVATE game_id=123", age=5)
        path.write_text(old + path.read_text())
        row = self.collect()["logs"]["game"]
        self.assertEqual(row["observed_outcome"], "twitch_updated")
        self.assertEqual(row["observed_reason"], "none")
        path.write_text(path.read_text() + "UNRECOGNIZED PRIVATE https://private.invalid\n")
        row = self.collect()["logs"]["game"]
        self.assertEqual(row["observed_reason"], "unknown")
        self.assertEqual(row["record_status"], "unrecognized")

    def test_future_malformed_or_partial_lines_fail_closed(self):
        for age, newline in ((-61, True), (5, False)):
            with self.subTest(age=age, newline=newline):
                self.log("ERROR: failed to compose public title", age=age, newline=newline)
                row = self.collect()["logs"]["game"]
                self.assertEqual(row["observed_reason"], "unknown")
        self.log("ERROR: failed to compose public title").write_text(
            "[stream_game 2026-02-30 00:00:00] ERROR: failed to compose public title\n"
        )
        self.assertEqual(self.collect()["logs"]["game"]["record_status"], "unrecognized")

    def test_temporal_overlap_is_never_promoted_to_same_execution(self):
        self.log("ERROR: failed to compose public title")
        stamp = dt.datetime.fromtimestamp(self.now - 10, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = self.collect(sync={"occurred_at": stamp, "event": "invoked"})["logs"]["game"]
        self.assertEqual(row["journal_relation"], "time_window_only")
        row = self.collect(sync={"occurred_at": "2020-01-01T00:00:00Z", "event": "invoked"})["logs"]["game"]
        self.assertEqual(row["journal_relation"], "not_correlated")

    def test_fixed_roots_reject_symlinks_hardlinks_fifo_and_writable_files(self):
        path = self.log("ERROR: failed to compose public title")
        target = Path(self.tmp.name) / "private"
        target.write_text("PRIVATE")
        for kind in ("symlink", "hardlink", "fifo", "writable"):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == "symlink":
                    path.symlink_to(target)
                elif kind == "hardlink":
                    os.link(target, path)
                elif kind == "fifo":
                    os.mkfifo(path, 0o600)
                else:
                    path.write_text("PRIVATE")
                    path.chmod(0o666)
                out = self.collect()
                self.assertIn(out["logs"]["game"]["record_status"], {"unsafe", "unreadable"})
                self.assertNotIn("PRIVATE", json.dumps(out))

    def test_parent_symlink_and_foreign_owner_are_not_followed(self):
        path = self.log("ERROR: failed to compose public title")
        saved = path.parent.with_name("saved")
        path.parent.rename(saved)
        path.parent.symlink_to(saved, target_is_directory=True)
        self.assertIn(self.collect()["logs"]["game"]["record_status"], {"unsafe", "unreadable"})
        path.parent.unlink()
        saved.rename(path.parent)
        with mock.patch.object(collector.os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(self.collect()["logs"]["game"]["record_status"], "unsafe")

    def test_tail_read_is_bounded_and_latest_partial_is_not_reused(self):
        path = self.log("ERROR: failed to compose public title")
        line = path.read_bytes()
        path.write_bytes(b"PRIVATE\n" * 100000 + line)
        row = self.collect()["logs"]["game"]
        self.assertTrue(row["tail_truncated"])
        self.assertEqual(row["observed_reason"], "composition_failed")
        path.write_bytes(line + b"partial PRIVATE")
        self.assertEqual(self.collect()["logs"]["game"]["observed_reason"], "unknown")

    def test_backwards_timestamps_are_ambiguous_not_a_new_failure(self):
        path = self.log("OK: updated title=PRIVATE game_id=123", age=5)
        success = path.read_text()
        self.log("ERROR: failed to compose public title", age=39000)
        path.write_text(success + path.read_text())
        row = self.collect()["logs"]["game"]
        self.assertEqual(row["record_status"], "ambiguous_order")
        self.assertEqual(row["observed_reason"], "unknown")

    def test_ancestor_of_configured_root_cannot_be_a_symlink(self):
        self.log("ERROR: failed to compose public title")
        alias = Path(self.tmp.name) / "alias"
        alias.symlink_to(self.soren.parent, target_is_directory=True)
        with mock.patch.object(collector, "_systemctl_scope", return_value=None):
            out = collector._collect_stream_title_attempts(
                alias / "soren", self.state, self.now
            )
        self.assertEqual(out["logs"]["game"]["observed_reason"], "unknown")

    def test_mutation_and_path_replacement_during_read_are_rejected(self):
        path = self.log("ERROR: failed to compose public title")
        original_read = os.read
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                path = self.log("ERROR: failed to compose public title")
                changed = False

                def read(fd, length):
                    nonlocal changed
                    data = original_read(fd, length)
                    if not changed:
                        changed = True
                        if replacement:
                            path.rename(path.with_name("previous"))
                            self.log("OK: updated title=PRIVATE game_id=123")
                        else:
                            with path.open("a") as stream:
                                stream.write("partial PRIVATE")
                    return data

                with mock.patch.object(collector.os, "read", side_effect=read):
                    row = self.collect()["logs"]["game"]
                self.assertEqual(row["record_status"], "changed")
                self.assertEqual(row["observed_reason"], "unknown")

    def test_oversized_latest_line_or_invalid_utf8_is_not_classified(self):
        path = self.log("ERROR: no client id (token invalid? resp: " + "PRIVATE" * 1000)
        self.assertEqual(self.collect()["logs"]["game"]["observed_reason"], "unknown")
        path.write_bytes(b"\xff\xfe\n")
        self.assertEqual(self.collect()["logs"]["game"]["record_status"], "unrecognized")

    def test_dispatch_observation_is_separate_from_daily_and_game_sources(self):
        self.log("ERROR: failed to compose public title", source="dispatch")
        out = self.collect()
        self.assertEqual(out["logs"]["dispatch"]["observed_reason"], "composition_failed")
        self.assertEqual(out["logs"]["game"]["record_status"], "absent")
        self.assertEqual(out["logs"]["day"]["record_status"], "absent")

    def test_output_stays_fixed_and_within_existing_gateway_budget(self):
        self.log("ERROR: no client id (token invalid? resp: PRIVATE https://private.invalid/body)")
        out = self.collect()
        self.assertLess(len(json.dumps(out).encode()), 4096)
        payload = {"status": "ok", "stream_title_attempts": out}
        budgeted = collector._diagnostics_budget(payload)
        self.assertEqual(json.loads(budgeted)["stream_title_attempts"], out)
        self.assertEqual(budgeted, json.dumps(json.loads(budgeted), sort_keys=True,
                                             ensure_ascii=False, separators=(",", ":")))
        for value in ("PRIVATE", "private.invalid", str(self.soren), str(self.state)):
            self.assertNotIn(value, budgeted)

    def test_day_script_mode_is_observed_without_reading_its_body(self):
        script = self.soren / "update_stream_title_day.sh"
        script.write_text("PRIVATE")
        for mode, executable in ((0o644, False), (0o755, True)):
            script.chmod(mode)
            row = self.collect()["day_script"]
            self.assertEqual(row["mode"], f"{mode:04o}")
            self.assertIs(row["executable"], executable)
        script.unlink()
        script.symlink_to(Path(self.tmp.name) / "private")
        self.assertIsNone(self.collect()["day_script"]["executable"])

    def test_units_use_fixed_read_only_queries_and_never_project_raw_properties(self):
        text = "LoadState=loaded\nActiveState=failed\nUnitFileState=static\nResult=exit-code\nExecMainCode=1\nExecMainStatus=203\n"
        with mock.patch.object(collector, "_systemctl_scope", return_value=(0, text)) as query:
            out = collector._collect_stream_title_attempts(self.soren, self.state, self.now)
        self.assertEqual(query.call_count, 4)
        for call in query.call_args_list:
            scope, argv = call.args
            self.assertIn(scope, {"user", "system"})
            self.assertEqual(argv[0], "show")
            self.assertIn(argv[1], {"soren-title-day.service", "soren-title-day.timer"})
            self.assertNotIn("Environment", " ".join(argv))
        self.assertEqual(out["day_units"]["system"]["service"]["exit_status"], 203)
        with mock.patch.object(collector, "_systemctl_scope", return_value=(0, text + "Result=PRIVATE\n")):
            out = collector._collect_stream_title_attempts(self.soren, self.state, self.now)
        self.assertEqual(out["day_units"]["system"]["service"]["result"], "unknown")
        self.assertNotIn("PRIVATE", json.dumps(out))


if __name__ == "__main__":
    unittest.main()
