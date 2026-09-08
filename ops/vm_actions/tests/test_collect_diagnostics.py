import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CollectorFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-diag-")
        self.soren = Path(self.tmp.name) / "soren"
        (self.soren / "tmp" / "state").mkdir(parents=True)
        self.now = int(time.time())

    def tearDown(self):
        self.tmp.cleanup()

    def alive_pid(self):
        proc = subprocess.Popen(["sleep", "60"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        return proc.pid

    def write_pid(self, name, pid):
        (self.soren / "tmp" / "state" / f"{name}.pid").write_text(f"{pid}\n")

    def write_required_alive(self, skip=()):
        (self.soren / "tmp" / ".soren_loop.lock").mkdir(parents=True, exist_ok=True)
        (self.soren / "tmp" / ".soren_loop.lock" / "pid").write_text(f"{self.alive_pid()}\n")
        for name in ("improve_daemon", "chat_worker", "audio_worker", "deadline_monitor",
                     "radio_worker", "prediction_worker"):
            if name not in skip:
                self.write_pid(name, self.alive_pid())

    def write_lock(self, lane, pid, label="RADIO test", age_sec=10, token="tok"):
        lock = self.soren / "tmp" / "state" / ".ai_generation_locks" / lane
        lock.mkdir(parents=True, exist_ok=True)
        (lock / "owner").write_text(
            f"token={token}\npid={pid}\nlabel={label}\nstarted_at=2026-01-01 00:00:00\n"
        )
        stamp = self.now - age_sec
        os.utime(lock, (stamp, stamp))

    def write_stats(self, events):
        stats = self.soren / "tmp" / "state" / "ai_stats"
        stats.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y%m%d", time.localtime(self.now))
        with open(stats / f"{day}.jsonl", "a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def event(self, kind, label="RADIO", agent="opencode-go:deepseek-v4-flash", rc="", **extra):
        data = {
            "ts": self.now - 60,
            "day": "20260101",
            "event": kind,
            "label": label,
            "agent": agent,
            "rc": rc,
            "resolved_model": "",
        }
        data.update(extra)
        return data

    def snapshot(self):
        before = {}
        for path in sorted((self.soren / "tmp").rglob("*")):
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                before[str(path)] = (stat.st_mtime_ns, stat.st_size)
        return before

    def run_collector(self, *args):
        return subprocess.run(
            ["python3", str(COLLECTOR), *(args or [str(self.soren)])],
            capture_output=True,
            text=True,
            timeout=120,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp"},
        )


class CollectorContractTests(CollectorFixture):
    def test_healthy_runtime_reports_ok(self):
        self.write_required_alive(skip=("radio_worker", "chat_worker", "audio_worker"))
        self.write_pid("radio_worker", self.alive_pid())
        self.write_pid("chat_worker", self.alive_pid())
        self.write_pid("audio_worker", self.alive_pid())
        self.write_lock("radio", self.alive_pid())
        self.write_stats([self.event("attempt"), self.event("ok")])
        before = self.snapshot()
        proc = self.run_collector()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["workers"]["running"] >= 3, True)
        self.assertEqual(data["queues"]["lanes"]["radio"]["locked"], True)
        self.assertEqual(data["queues"]["lanes"]["radio"]["owner_alive"], True)
        self.assertEqual(data["ai"]["attempts"], 1)
        after = {}
        for path in sorted((self.soren / "tmp").rglob("*")):
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                after[str(path)] = (stat.st_mtime_ns, stat.st_size)
        self.assertEqual(before, after)

    def test_missing_soren_root_does_not_crash(self):
        proc = self.run_collector(str(self.soren / "nonexistent"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIn(data["status"], ("ok", "warn", "critical"))
        self.assertEqual(data["meta"]["soren_root_exists"], False)

    def test_stopped_required_worker_is_critical(self):
        proc = self.run_collector()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "critical")
        self.assertIn("radio_worker", data["workers"]["required_down"])

    def test_stale_pid_file_is_reported(self):
        self.write_required_alive(skip=("radio_worker",))
        (self.soren / "tmp" / "state" / "radio_worker.pid").write_text("99999999\n")
        (self.soren / "tmp" / "state" / "garbage.pid").write_text("not-a-pid\n")
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertIn("radio_worker", data["workers"]["stale_pid_files"])
        self.assertIn("radio_worker", data["workers"]["required_stale"])
        self.assertEqual(data["status"], "critical")

    def test_duplicate_workers_are_reported(self):
        self.write_required_alive(skip=("radio_worker", "chat_worker"))
        self.write_pid("radio_worker", self.alive_pid())
        self.write_pid("chat_worker", self.alive_pid())
        dup = {
            "status": "duplicate",
            "updated_at": self.now,
            "managed": {},
            "counts": {},
            "duplicates": [
                {
                    "name": "radio_worker",
                    "count": 2,
                    "managed_pid": str(os.getpid()),
                    "pids": [str(os.getpid()), "12346"],
                    "extra_pids": ["12346"],
                }
            ],
        }
        (self.soren / "tmp" / "state" / "worker_duplicates.json").write_text(json.dumps(dup))
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertIn("radio_worker", data["workers"]["duplicates"])
        self.assertNotIn("chat_worker", data["workers"]["duplicates"])
        self.assertEqual(data["status"], "warn")

    def test_unregistered_pid_file_is_warn(self):
        self.write_required_alive()
        self.write_pid("custom_new_worker", self.alive_pid())
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertIn("custom_new_worker", data["workers"]["unregistered"])
        self.assertEqual(data["status"], "warn")

    def test_paused_required_worker_is_warn_not_critical(self):
        self.write_required_alive(skip=("radio_worker",))
        self.write_pid("radio_worker", self.alive_pid())
        (self.soren / "tmp" / "state" / "radio_worker.paused").write_text("{}\n")
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertIn("radio_worker", data["workers"]["paused"])
        self.assertNotIn("radio_worker", data["workers"]["required_down"])
        self.assertEqual(data["status"], "warn")

    def test_stale_queue_lock_is_suspected_but_kept(self):
        self.write_required_alive()
        self.write_lock("radio", 99999999, age_sec=2000)
        before = (self.soren / "tmp" / "state" / ".ai_generation_locks" / "radio" / "owner").read_bytes()
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        lane = data["queues"]["lanes"]["radio"]
        self.assertEqual(lane["locked"], True)
        self.assertEqual(lane["owner_alive"], False)
        self.assertEqual(lane["stale_suspected"], True)
        self.assertEqual(data["queues"]["stale_locks"], 1)
        after = (self.soren / "tmp" / "state" / ".ai_generation_locks" / "radio" / "owner").read_bytes()
        self.assertEqual(before, after)

    def test_owner_token_never_leaves_the_vm(self):
        self.write_required_alive()
        self.write_lock("comment", self.alive_pid(), token="tok_SUPERSECRET_abc123")
        proc = self.run_collector()
        self.assertNotIn("SUPERSECRET", proc.stdout)
        data = json.loads(proc.stdout)
        self.assertNotIn("token", json.dumps(data["queues"]))

    def test_rate_limit_fallback_allfailed_and_giveup(self):
        self.write_required_alive()
        self.write_stats(
            [
                self.event("attempt", label="RADIO"),
                self.event("fail", label="RADIO", agent="opencode:m1", rc="1", error="boom"),
                self.event("fail", label="RADIO", agent="opencode:m1", rc="79", error="429 slow down"),
                self.event("winner", label="RADIO", agent="amd:DeepSeek-V4-Flash", rc="0"),
                self.event("attempt", label="COMMENT"),
                self.event("all_failed", label="COMMENT", agent=""),
                self.event("queue_giveup", label="RADIO"),
            ]
        )
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        ai = data["ai"]
        self.assertEqual(ai["attempts"], 2)
        self.assertEqual(ai["failures"], 2)
        self.assertEqual(ai["rate_limits"], 1)
        self.assertEqual(ai["fallbacks"], 1)
        self.assertEqual(ai["all_failed"], 1)
        self.assertEqual(ai["queue_giveups"], 1)
        self.assertEqual(data["queues"]["queue_giveups_15m"], 1)
        kinds = [e["event"] for e in ai["recent_events"]]
        self.assertIn("fail", kinds)
        self.assertIn("winner", kinds)
        self.assertIn("all_failed", kinds)
        self.assertNotIn("attempt", kinds)
        fail_event = next(e for e in ai["recent_events"] if e["event"] == "fail" and e["rc"] == "79")
        self.assertEqual(fail_event["provider"], "opencode")
        self.assertEqual(data["status"], "warn")

    def test_malformed_telemetry_is_counted_not_fatal(self):
        self.write_required_alive()
        stats = self.soren / "tmp" / "state" / "ai_stats"
        stats.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y%m%d", time.localtime(self.now))
        with open(stats / f"{day}.jsonl", "w", encoding="utf-8") as handle:
            handle.write("not json at all\n")
            handle.write("[1,2,3]\n")
            handle.write(json.dumps(self.event("ok")) + "\n")
        proc = self.run_collector()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["ai"]["malformed_lines"], 2)
        self.assertEqual(data["ai"]["successes"], 1)

    def test_secret_like_error_preview_is_redacted(self):
        self.write_required_alive()
        self.write_stats(
            [self.event("fail", rc="1", error="call failed api_key=AKIAIOSFODNN7EXAMPLE Bearer abc.def.ghi")]
        )
        proc = self.run_collector()
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", proc.stdout)
        self.assertNotIn("abc.def.ghi", proc.stdout)
        data = json.loads(proc.stdout)
        preview = next(e for e in data["ai"]["recent_events"] if e["event"] == "fail")["error_preview"]
        self.assertIn("[REDACTED]", preview)

    def test_huge_telemetry_stays_bounded(self):
        self.write_required_alive()
        stats = self.soren / "tmp" / "state" / "ai_stats"
        stats.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y%m%d", time.localtime(self.now))
        with open(stats / f"{day}.jsonl", "w", encoding="utf-8") as handle:
            for _ in range(60000):
                handle.write(json.dumps(self.event("fail", rc="1", error="x" * 5000)) + "\n")
        proc = self.run_collector()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLessEqual(len(proc.stdout.encode()), 32768)
        data = json.loads(proc.stdout)
        self.assertLessEqual(len(data["ai"]["recent_events"]), 20)

    def test_improve_loop_states(self):
        self.write_required_alive()
        (self.soren / "tmp" / "improve.lock").write_text("")
        (self.soren / "tmp" / "state" / "improve_state.json").write_text(
            json.dumps(
                {
                    "status": "running",
                    "pid": self.alive_pid(),
                    "phase": "harvest",
                    "started_at": self.now - 100,
                    "updated_at": self.now - 10,
                }
            )
        )
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        imp = data["improvement"]
        self.assertEqual(imp["running"], True)
        self.assertEqual(imp["pid_alive"], True)
        self.assertEqual(imp["stale"], False)
        self.assertEqual(imp["lock_present"], True)

    def test_stale_improve_state_is_warn(self):
        self.write_required_alive()
        (self.soren / "tmp" / "state" / "improve_state.json").write_text(
            json.dumps({"status": "running", "pid": 99999999, "started_at": self.now - 9000,
                        "updated_at": self.now - 9000})
        )
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertEqual(data["improvement"]["stale"], True)
        self.assertEqual(data["status"], "warn")

    def test_backoff_and_retry_signals(self):
        self.write_required_alive()
        (self.soren / "tmp" / "state" / "rate_limit_backoff").write_text("x")
        (self.soren / "tmp" / "state" / "improve_retry_batch.json").write_text("{}")
        proc = self.run_collector()
        data = json.loads(proc.stdout)
        self.assertEqual(data["improvement"]["backing_off"], True)
        self.assertEqual(data["improvement"]["retry_pending"], True)
        self.assertEqual(data["status"], "warn")

    def test_usage_error_without_args(self):
        direct = subprocess.run(["python3", str(COLLECTOR)], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(direct.returncode, 0)


if __name__ == "__main__":
    unittest.main()
