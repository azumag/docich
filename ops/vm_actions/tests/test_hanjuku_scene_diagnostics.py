import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


class HanjukuSceneDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hanjuku-scene-diagnostics-")
        self.addCleanup(self.temp.cleanup)
        # pytest exposes TMPDIR through a socket-friendly symlink. The normal
        # fixture must use its real path; dedicated cases test symlink refusal.
        self.root = Path(self.temp.name).resolve()
        spec = importlib.util.spec_from_file_location("scene_diagnostics", COLLECTOR)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.identity = dict(game="hanjuku-hero", runtime_id="g574-0123abcd",
                             generation=574, lease_id="PRIVATE_LEASE")
        self.runtime = self.root / "runtimes" / self.identity["runtime_id"]
        self.runtime.mkdir(parents=True)
        self.canonical = self.root / "game_switch.json"
        self.canonical.write_text(json.dumps(dict(phase="ready", active=self.identity)))
        self.run = self.runtime / "hanjuku_run.json"
        self.run.write_text(json.dumps({**self.identity, "playing": True}))

    def write(self, name, **fields):
        path = self.runtime / name
        path.write_text(json.dumps(dict(schema=1, **self.identity, **fields)), encoding="utf-8")
        return path

    def producer(self, **overrides):
        fields = dict(enabled=True, observed_at=99.0, scene_id="PRIVATE_SCENE_HASH",
                      request=dict(seq=8, at=98.0, expires_at=108.0,
                                   scene_id="PRIVATE_SCENE_HASH", event_key="PRIVATE_EVENT",
                                   facts={"secret": "PRIVATE_FACTS"}))
        fields.update(overrides)
        return self.write("hanjuku_scene.json", **fields)

    def worker(self, **overrides):
        fields = dict(at=99.5, last_request_seq=8, status="deliver_enqueued", reason="none",
                      role="RADIO_AGENTS", counters={"requested": 8, "deliver_enqueued": 2,
                                                     "PRIVATE_COUNTER": 999})
        fields.update(overrides)
        return self.write("hanjuku_scene_worker.json", **fields)

    def row(self, event="generate_succeeded", **overrides):
        fields = dict(schema=1, **self.identity, at=99.0, event=event, reason="none",
                      seq=8, latency_ms=34.125, char_count=45)
        fields.update(overrides)
        return fields

    def log(self, rows):
        path = self.runtime / "hanjuku_scene_commentary.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path

    def collect(self, now=100):
        return self.module._collect_hanjuku_scene_narration(self.root, now)

    def test_current_runtime_projection_has_no_facts_text_or_identity_tokens(self):
        self.producer()
        self.worker()
        self.log([self.row("requested", at=98), self.row(text="PRIVATE_TEXT", path="PRIVATE_PATH")])
        before = {path: path.read_bytes() for path in self.runtime.iterdir()}

        output = self.collect()

        self.assertEqual(output["status"], "available")
        self.assertEqual(output["generation"], 574)
        self.assertEqual(output["producer"]["request_seq"], 8)
        self.assertEqual(output["producer"]["age_sec"], 1)
        self.assertIs(output["producer"]["request_matches_scene"], True)
        self.assertIs(output["producer"]["request_unexpired"], True)
        self.assertEqual(output["worker"]["status"], "deliver_enqueued")
        self.assertEqual(output["worker"]["role"], "RADIO_AGENTS")
        self.assertEqual(output["worker"]["counters"]["deliver_enqueued"], 2)
        self.assertEqual(output["worker"]["counters"]["generate_failed"], 0)
        self.assertEqual(output["log"]["event_counts"]["generate_succeeded"], 1)
        self.assertEqual(output["log"]["last_latency_ms"], 34.125)
        self.assertEqual(output["log"]["last_char_count"], 45)
        self.assertNotIn("PRIVATE", json.dumps(output))
        self.assertNotIn(self.identity["runtime_id"], json.dumps(output))
        self.assertEqual(before, {path: path.read_bytes() for path in self.runtime.iterdir()})

    def test_missing_sources_are_unknown_but_valid_empty_counters_are_zero(self):
        output = self.collect()
        self.assertEqual(output["producer"]["read_status"], "unavailable")
        self.assertIsNone(output["producer"]["enabled"])
        self.assertIsNone(output["worker"]["counters"])
        self.assertIsNone(output["log"]["event_counts"])
        self.worker(counters={})
        self.log([])
        output = self.collect()
        self.assertTrue(all(value == 0 for value in output["worker"]["counters"].values()))
        self.assertEqual(output["log"]["status"], "available")
        self.assertTrue(all(value == 0 for value in output["log"]["event_counts"].values()))

    def test_request_absence_expiry_and_new_scene_are_distinct(self):
        self.producer(enabled=False, request=None)
        producer = self.collect()["producer"]
        self.assertIs(producer["enabled"], False)
        self.assertIs(producer["request_present"], False)
        self.assertIsNone(producer["request_seq"])
        self.producer(scene_id="PRIVATE_NEW_SCENE", request=dict(
            seq=4, at=90, expires_at=95, scene_id="PRIVATE_OLD_SCENE"))
        producer = self.collect()["producer"]
        self.assertIs(producer["request_matches_scene"], False)
        self.assertIs(producer["request_unexpired"], False)
        self.producer(request=dict(seq=5, at=90, expires_at=100, scene_id="PRIVATE_SCENE_HASH"))
        self.assertIs(self.collect()["producer"]["request_unexpired"], False)

    def test_history_request_scene_mismatch_is_observed_without_rejecting_the_source(self):
        self.producer(scene_id="PRIVATE_NEW_SCENE", request=dict(
            seq=4, at=98, expires_at=108, scope="history", scene_id="PRIVATE_OLD_SCENE",
            epoch_id="PRIVATE_EPOCH", source_scene={"secret": "PRIVATE_SOURCE_SCENE"}))
        producer = self.collect()["producer"]
        self.assertEqual(producer["read_status"], "available")
        self.assertEqual(producer["request_scope"], "history")
        self.assertIs(producer["request_matches_scene"], False)
        self.assertIs(producer["request_unexpired"], True)
        self.assertNotIn("PRIVATE", json.dumps(producer))
        self.producer(request=dict(scope="PRIVATE_SCOPE"))
        self.assertIsNone(self.collect()["producer"]["request_scope"])

    def test_programs_use_fresh_fractional_source_clocks_for_live_scene_records(self):
        self.producer(observed_at=100.7)
        self.worker(at=100.8)
        self.log([self.row(at=100.9)])

        def collect_corner(_state_dir, payload, _now):
            payload["game_switch"] = {"active_game": "hanjuku-hero"}
            payload["retro_corner"] = {"readable": True, "status": "active", "game": "hanjuku-hero"}

        with ExitStack() as stack:
            for name, result in {
                "_rotation_timer_selection": ("docich-corner-rotation.timer", False),
                "_unit_is_active": True, "_unit_is_enabled": True,
                "_collect_boundary": {}, "_collect_ab": {}, "_collect_soren_game": {},
                "_collect_hanjuku_predictions": {}, "_collect_hanjuku_narration_playback": {},
            }.items():
                stack.enter_context(mock.patch.object(self.module, name, return_value=result))
            stack.enter_context(mock.patch.object(self.module, "_collect_corner_files", side_effect=collect_corner))
            clock = stack.enter_context(mock.patch.object(
                self.module.time, "time", side_effect=(100.75, 100.85, 100.95)))
            output = self.module._collect_programs(self.root, self.root, 100)

        scene = output["retro_corner"]["scene_narration"]
        self.assertEqual(scene["producer"]["read_status"], "available")
        self.assertEqual(scene["worker"]["read_status"], "available")
        self.assertEqual(scene["log"]["status"], "available")
        self.assertEqual(scene["producer"]["age_sec"], 0.05)
        self.assertEqual(scene["worker"]["age_sec"], 0.05)
        self.assertEqual(scene["log"]["last_age_sec"], 0.05)
        self.assertEqual(clock.call_count, 3)

    def test_unrecognized_worker_values_and_bad_counts_stay_null(self):
        self.worker(status="PRIVATE_STATUS", reason="PRIVATE_REASON", role="PRIVATE_ROLE",
                    last_request_seq=True,
                    counters={"requested": True, "generate_failed": -1,
                              "deliver_failed": 10**30})
        worker = self.collect()["worker"]
        for key in ("status", "reason", "role", "last_request_seq"):
            self.assertIsNone(worker[key])
        for key in ("requested", "generate_failed", "deliver_failed"):
            self.assertIsNone(worker["counters"][key])
        self.assertNotIn("PRIVATE", json.dumps(worker))

    def test_bad_or_foreign_log_rows_never_count_as_current_events(self):
        self.log([
            self.row("generate_failed", lease_id="PRIVATE_OTHER_LEASE"),
            self.row("generate_failed", generation=True),
            self.row("PRIVATE_EVENT"),
            self.row("generate_failed", at=101),
            self.row("generate_failed", at=float("nan")),
            self.row("generate_failed", seq=True),
            self.row(reason="PRIVATE_REASON", latency_ms=float("inf"), char_count=-1),
        ])
        output = self.collect()["log"]
        self.assertEqual(output["status"], "partial")
        self.assertEqual(output["matched_records"], 1)
        self.assertEqual(output["rejected_records"], 6)
        self.assertEqual(output["unknown_reasons"], 1)
        self.assertEqual(output["event_counts"]["generate_failed"], 0)
        self.assertEqual(output["event_counts"]["generate_succeeded"], 1)
        self.assertIsNone(output["last_reason"])
        self.assertIsNone(output["last_latency_ms"])
        self.assertIsNone(output["last_char_count"])
        self.assertNotIn("PRIVATE", json.dumps(output))

    def test_only_malformed_or_foreign_records_do_not_report_zero_successes(self):
        log = self.log([self.row(lease_id="PRIVATE_OTHER_LEASE")])
        with log.open("a") as stream:
            stream.write("not json\n")
        output = self.collect()["log"]
        self.assertEqual(output["status"], "unavailable")
        self.assertEqual(output["rejected_records"], 2)
        self.assertIsNone(output["event_counts"])

    def test_oversized_numeric_metric_stays_null_without_losing_valid_events(self):
        self.log([self.row(latency_ms=10**500, char_count=10**500)])
        output = self.collect()["log"]
        self.assertEqual(output["status"], "available")
        self.assertEqual(output["event_counts"]["generate_succeeded"], 1)
        self.assertIsNone(output["last_latency_ms"])
        self.assertIsNone(output["last_char_count"])

    def test_pre_request_skip_with_no_sequence_is_still_visible(self):
        self.log([self.row("skipped", reason="disabled", seq=None)])
        output = self.collect()["log"]
        self.assertEqual(output["status"], "available")
        self.assertEqual(output["event_counts"]["skipped"], 1)
        self.assertEqual(output["reason_counts"]["disabled"], 1)
        self.assertIsNone(output["last_seq"])

    def test_component_identity_schema_and_future_time_are_independently_rejected(self):
        producer = self.producer()
        self.worker()
        for change in ({"generation": 573}, {"schema": True}, {"observed_at": 101},
                       {"enabled": "yes"}, {"lease_id": "PRIVATE_WRONG"}):
            with self.subTest(change=change):
                self.producer()
                data = json.loads(producer.read_text())
                data.update(change)
                producer.write_text(json.dumps(data))
                output = self.collect()
                self.assertEqual(output["producer"]["read_status"], "unavailable")
                self.assertIsNone(output["producer"]["request_seq"])
                self.assertEqual(output["worker"]["read_status"], "available")

    def test_nonactive_terminal_and_foreign_run_hide_the_projection(self):
        self.producer()
        for change in ({"playing": False}, {"terminal_reason": "game_over"},
                       {"terminal_candidate": "game_over"}, {"generation": 573}):
            with self.subTest(change=change):
                self.run.write_text(json.dumps({**self.identity, "playing": True, **change}))
                output = self.collect()
                self.assertEqual(output["status"], "unavailable")
                self.assertIsNone(output["producer"])
        self.run.write_text(json.dumps({**self.identity, "playing": True}))
        self.canonical.write_text(json.dumps(dict(phase="draining", active=self.identity)))
        self.assertEqual(self.collect()["status"], "unavailable")

    def test_generation_change_during_read_discards_every_component(self):
        self.producer()
        self.worker()
        original = self.module._read_hanjuku_scene_record
        calls = 0

        def read(path):
            nonlocal calls
            data = original(path)
            if path.name == "game_switch.json":
                calls += 1
                if calls == 2:
                    data["active"]["generation"] = 575
            return data

        with mock.patch.object(self.module, "_read_hanjuku_scene_record", side_effect=read):
            output = self.collect()
        self.assertEqual(output["status"], "identity_changed")
        self.assertIsNone(output["generation"])
        self.assertIsNone(output["producer"])
        self.assertIsNone(output["worker"])
        self.assertIsNone(output["log"])

    def test_symlink_directory_symlink_fifo_and_oversized_sources_are_rejected(self):
        producer = self.producer()
        data = producer.read_bytes()
        producer.unlink()
        producer.symlink_to(self.run)
        self.assertEqual(self.collect()["producer"]["read_status"], "unavailable")
        producer.unlink()
        producer.write_bytes(data + b" " * self.module.HANJUKU_SCENE_RECORD_MAX_BYTES)
        self.assertEqual(self.collect()["producer"]["read_status"], "unavailable")
        log = self.runtime / "hanjuku_scene_commentary.jsonl"
        os.mkfifo(log)
        self.assertEqual(self.collect()["log"]["status"], "unavailable")
        log.unlink()
        log.symlink_to(producer)
        self.assertEqual(self.collect()["log"]["status"], "unavailable")
        moved = self.runtime.with_name("private-moved-runtime")
        self.runtime.rename(moved)
        self.runtime.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.collect()["status"], "unavailable")

    def test_bounded_log_tail_and_partial_long_record_are_not_full_history(self):
        log = self.log([])
        log.write_text("padding\n" * 20000 + json.dumps(self.row()) + "\n")
        output = self.collect()["log"]
        self.assertIs(output["tail_truncated"], True)
        self.assertLessEqual(output["sampled_bytes"], self.module.HANJUKU_SCENE_LOG_MAX_BYTES)
        self.assertLessEqual(output["sampled_lines"], self.module.HANJUKU_SCENE_LOG_MAX_LINES)
        self.assertEqual(output["event_counts"]["generate_succeeded"], 1)
        log.write_text("x" * (self.module.HANJUKU_SCENE_LOG_MAX_BYTES + 10))
        output = self.collect()["log"]
        self.assertIs(output["tail_truncated"], True)
        self.assertIsNone(output["event_counts"])

    def test_line_limit_omits_older_events_even_when_byte_limit_allows_them(self):
        self.log([self.row("generate_failed")] + [self.row()] * 2100)
        with mock.patch.object(self.module, "HANJUKU_SCENE_LOG_MAX_BYTES", 2 * 1024 * 1024):
            output = self.collect()["log"]
        self.assertIs(output["tail_truncated"], True)
        self.assertEqual(output["sampled_lines"], 2048)
        self.assertEqual(output["event_counts"]["generate_succeeded"], 2048)
        self.assertEqual(output["event_counts"]["generate_failed"], 0)

    def test_bad_clock_and_unsafe_runtime_name_are_rejected(self):
        for now in (float("nan"), float("inf"), -1, True):
            with self.subTest(now=now):
                self.assertEqual(self.collect(now)["status"], "unavailable")
        active = {**self.identity, "runtime_id": "../PRIVATE_PATH"}
        self.canonical.write_text(json.dumps(dict(phase="ready", active=active)))
        self.assertEqual(self.collect()["status"], "unavailable")

    def test_scene_detail_is_omitted_before_tactical_evidence_at_output_limit(self):
        payload = {
            "corners": {"retro_corner": {"scene_narration": {"large": "x" * 2000}}},
            "hanjuku_tactical": {"status": "ok", "chapter": 1},
            "nethack_history": {"daily": {"records": []}, "completed_runs": {"records": []}},
            "ai": {"recent_events": [], "anomalous_components": {}},
            "workers": {"details": {}}, "soren91_drop_profile": {"profileStatus": "missing"},
        }
        with mock.patch.object(self.module, "MAX_JSON_BYTES", 600):
            text = self.module._diagnostics_budget(payload)
        self.assertLessEqual(len(text.encode()), 600)
        self.assertEqual(payload["hanjuku_tactical"]["status"], "ok")
        self.assertEqual(payload["corners"]["retro_corner"]["scene_narration"],
                         {"status": "output_omitted"})


if __name__ == "__main__":
    unittest.main()
