import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations.yml"
HELPER = ROOT / "ops" / "vm_actions" / "run_cpu_profile_once.sh"
EPOCH = ROOT / "ops" / "runtime_context" / "cpu_profile_epoch"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


collector = load_module("cpu_profile_diagnostics_tested", COLLECTOR)


class CpuProfileDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "profile.json"
        self.now = 1_800_000_000

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def fixture(self, generated_at=None):
        return {
            "schema": "docich.cpu_profile.v1",
            "meta": {
                "scenario": "latest-main",
                "generated_at": self.now if generated_at is None else generated_at,
                "elapsed_sec": 60.2,
                "interval_sec": 1.0,
                "samples": 61,
                "ncpu": 4,
                "worker_pidfiles_resolved": 12,
                "profiler_cpu_sec": 0.11,
                "should_not_leak": "/home/ubuntu/secret",
            },
            "host": {
                "cpu_busy_pct": 72.5,
                "cpu_busy_pct_p50": 73.1,
                "cpu_busy_pct_p95": 89.7,
                "load1_p50": 7.2,
                "load1_p95": 9.1,
                "forks": 1200,
                "forks_per_sec": 20.0,
                "ctxt_per_sec": 5000.0,
                "unknown": "secret",
            },
            "components": [
                {
                    "component": "browser",
                    "cpu_pct": 110.0,
                    "cpu_share_pct": 38.0,
                    "processes": 2,
                    "spawned": 1,
                    "vcs_per_sec": 4.0,
                    "nvcs_per_sec": 2.0,
                    "rss_kb": 123456,
                    "threads": 20,
                    "cmdline": "--token=secret",
                },
                {
                    "component": "token:do-not-project",
                    "cpu_pct": 999,
                },
                {
                    "component": "ffmpeg:stream",
                    "cpu_pct": 42.0,
                },
                {
                    "component": "tail",
                    "cpu_pct": 1.5,
                    "cmdline": "tail --token=secret",
                },
            ],
            "spawns": [
                {"component": "ffmpeg:capture", "spawner": "worker:chat_worker", "count": 7},
                {"component": "token:bad", "spawner": "browser", "count": 100},
            ],
            "top_processes": [{"pid": 123, "component": "browser", "cmdline": "secret"}],
        }

    def test_projects_only_fixed_bounded_fields(self):
        self.write(self.fixture())
        out = collector._collect_cpu_profile(self.now + 5, path=self.path)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["age_sec"], 5)
        self.assertEqual(out["meta"]["scenario"], "latest-main")
        self.assertEqual(out["host"]["forks_per_sec"], 20)
        self.assertEqual(
            [x["component"] for x in out["components"]],
            ["browser", "ffmpeg:stream", "tail"],
        )
        self.assertEqual(out["spawns"], [
            {"component": "ffmpeg:capture", "spawner": "worker:chat_worker", "count": 7}
        ])
        encoded = json.dumps(out, sort_keys=True)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("cmdline", encoded)
        self.assertNotIn("top_processes", encoded)
        self.assertNotIn("/home/ubuntu", encoded)

    def test_stale_profile_is_explicit(self):
        self.write(self.fixture(generated_at=self.now - collector.CPU_PROFILE_FRESH_SEC - 1))
        out = collector._collect_cpu_profile(self.now, path=self.path)
        self.assertEqual(out["status"], "stale")
        self.assertTrue(out["readable"])

    def test_symlink_is_not_followed(self):
        real = Path(self.tmp.name) / "real.json"
        real.write_text(json.dumps(self.fixture()), encoding="utf-8")
        os.symlink(real, self.path)
        out = collector._collect_cpu_profile(self.now, path=self.path)
        self.assertNotEqual(out["status"], "ok")
        self.assertFalse(out["readable"])

    def test_wrong_schema_is_rejected(self):
        data = self.fixture()
        data["schema"] = "other"
        self.write(data)
        out = collector._collect_cpu_profile(self.now, path=self.path)
        self.assertEqual(out["status"], "invalid")


class CpuProfileOperationContractTests(unittest.TestCase):
    def test_helper_is_fixed_readonly_profile(self):
        subprocess.run(["bash", "-n", str(HELPER)], check=True)
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn("[[ \"$#\" -eq 0 ]]", text)
        self.assertIn("profile_cpu.py sample", text)
        self.assertIn("--scenario latest-main", text)
        self.assertIn("--duration 60", text)
        self.assertIn("--interval 0.5", text)
        self.assertIn("/tmp/docich-cpu-profile-latest.json", text)
        for forbidden in ("sudo ", " kill ", "renice", "systemctl"):
            self.assertNotIn(forbidden, text)

    def test_workflow_profiles_only_for_explicit_epoch_change_or_owner_request(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertTrue(EPOCH.is_file())
        self.assertIn("ops/runtime_context/cpu_profile_epoch", text)
        self.assertIn("Run one-shot production CPU profile", text)
        self.assertIn("run_cpu_profile_once.sh", text)
        self.assertIn("github.event_name == 'push'", text)
        self.assertIn("steps.auth.outputs.operation == 'deploy'", text)
        self.assertIn("steps.auth.outputs.target == 'production'", text)
        self.assertIn("steps.auth.outputs.operation == 'cpu_profile'", text)
        self.assertIn("elif [[ \"$OPERATION\" != cpu_profile ]]", text)
        self.assertIn('profile.get("status") != "ok"', text)
        self.assertIn('(data.get("diagnostics") or {}).get("cpu_profile")', text)
        self.assertNotIn("profile_cpu.py sample --scenario", text)


class CpuProfileBudgetTests(unittest.TestCase):
    """Output-pressure reduction keeps the busiest rows and counts the rest."""

    def payload(self, components, spawns):
        return {
            "cpu_profile": {"status": "stale", "components_truncated": 0,
                            "components": components, "spawns": spawns},
            "corners": {"retro_corner": {"decision_plans": {"status": "available"},
                                         "scene_narration": {"status": "available"}}},
            "hanjuku_tactical": {"status": "ok"},
            "ai": {"recent_events": [], "anomalous_components": {}},
            "workers": {"details": {}},
            "nethack_history": {"daily": {"records": []},
                                "completed_runs": {"records": []}},
            "soren91_drop_profile": {"profileStatus": "missing"},
            "pulse_sink_inputs": {"streams": []},
        }

    def rows(self, count, offset=0):
        return [{"component": f"worker-{index}", "cpu_pct": 10.0 - index,
                 "processes": index + offset} for index in range(count)]

    def spawn_rows(self, count):
        return [{"component": "worker", "spawner": "poll_worker", "count": count - index}
                for index in range(count)]

    def test_a_small_envelope_never_touches_the_profile(self):
        payload = self.payload(self.rows(12), self.spawn_rows(12))
        before = json.dumps(payload, sort_keys=True)
        with mock.patch.object(collector, "MAX_JSON_BYTES", len(before) + 64):
            text = collector._diagnostics_budget(payload)
        self.assertEqual(len(payload["cpu_profile"]["components"]), 12)
        self.assertNotIn("components_omitted", text)
        self.assertNotIn("spawns_omitted", text)

    def test_a_full_envelope_keeps_the_busiest_rows_and_counts_the_rest(self):
        payload = self.payload(self.rows(30), self.spawn_rows(30))
        with mock.patch.object(collector, "MAX_JSON_BYTES", 10):
            text = collector._diagnostics_budget(payload)
        profile = payload["cpu_profile"]
        self.assertEqual(len(profile["components"]), collector.CPU_PROFILE_BUDGET_COMPONENTS)
        self.assertEqual(len(profile["spawns"]), collector.CPU_PROFILE_BUDGET_SPAWNS)
        self.assertEqual(profile["components_omitted"], 20)
        self.assertEqual(profile["spawns_omitted"], 20)
        # The producer orders busiest-first: the head rows must be the ones kept.
        self.assertEqual(profile["components"][0]["component"], "worker-0")
        self.assertEqual(profile["spawns"][0]["count"], 30)
        # The existing contract's truncation counter is not overwritten.
        self.assertEqual(profile["components_truncated"], 0)
        self.assertIn("decision_plans", text)

    def test_a_missing_or_short_profile_is_left_alone(self):
        payload = self.payload(self.rows(3), self.spawn_rows(3))
        with mock.patch.object(collector, "MAX_JSON_BYTES", 10):
            collector._diagnostics_budget(payload)
        self.assertNotIn("components_omitted", payload["cpu_profile"])
        self.assertNotIn("spawns_omitted", payload["cpu_profile"])
        payload = self.payload("PRIVATE_ROWS", {"PRIVATE_ROWS": 1})
        with mock.patch.object(collector, "MAX_JSON_BYTES", 10):
            collector._diagnostics_budget(payload)
        # Only bounded lists of projected rows are truncated; an unexpected
        # shape is left untouched rather than inspected or rewritten.
        self.assertEqual(payload["cpu_profile"]["components"], "PRIVATE_ROWS")
        self.assertEqual(payload["cpu_profile"]["spawns"], {"PRIVATE_ROWS": 1})
        self.assertNotIn("components_omitted", payload["cpu_profile"])
        self.assertNotIn("spawns_omitted", payload["cpu_profile"])
        payload = {"corners": {}}
        self.assertIs(collector._cpu_profile_budget(payload), payload)


if __name__ == "__main__":
    unittest.main()
