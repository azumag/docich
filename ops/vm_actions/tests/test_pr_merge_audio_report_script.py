import base64
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops/vm_actions/report_pr_merge_audio.sh"
QUEUE_LIB = ROOT / "games/soviet_now/lib/outbound_queue.sh"

STUB_QUEUE_LIB = """
enqueue_audio_text() {
	local text="${1:-}" source="${2:-unknown}"
	local queue_dir="${COMMENT_QUEUE_DIR:-tmp/.comment_queue}"
	[ -n "$text" ] || return 1
	mkdir -p "$queue_dir"
	local f="${queue_dir}/comment_announce_$(date +%s%N)_${source}.txt"
	printf '%s\\n' "$text" > "${f}.tmp" && mv "${f}.tmp" "${f}"
}
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "needs /proc and GNU userland")
class ReportScriptRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.soren = self.tmp / "soren"
        (self.soren / "lib").mkdir(parents=True)
        (self.soren / "workers").mkdir()
        (self.soren / "tmp/state").mkdir(parents=True)
        if QUEUE_LIB.exists():  # real function when the submodule is checked out
            shutil.copy(QUEUE_LIB, self.soren / "lib/outbound_queue.sh")
        else:  # CI checks out without submodules: same file contract, no dedup
            (self.soren / "lib/outbound_queue.sh").write_text(STUB_QUEUE_LIB)
        (self.soren / "workers/audio_worker.sh").write_text("#!/bin/bash\nsleep 120\n")
        self.script = self.tmp / "report.sh"
        self.script.write_text(
            SCRIPT.read_text(encoding="utf-8").replace(
                "soren_root=/home/ubuntu/soren", f"soren_root={self.soren}", 1
            )
        )
        self.worker = None

    def tearDown(self):
        if self.worker:
            self.worker.kill()
            self.worker.wait()

    def start_worker(self):
        self.worker = subprocess.Popen(["bash", str(self.soren / "workers/audio_worker.sh")])
        (self.soren / "tmp/state/audio_worker.pid").write_text(f"{self.worker.pid}\n")

    def run_script(self, b64, timeout=20):
        payload = f"TEXT_B64={b64}\n" + self.script.read_text()
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-s"],
            input=payload,
            text=True,
            capture_output=True,
            timeout=timeout,
        )

    @staticmethod
    def b64(text):
        return base64.b64encode(text.encode()).decode()

    def queue_files(self):
        queue = self.soren / "tmp/.comment_queue"
        return sorted(queue.glob("comment_announce_*.txt")) if queue.exists() else []

    def test_queues_exact_text_without_waiting_for_playback(self):
        self.start_worker()
        text = "$(touch /tmp/pwned) `x` ; あいう"
        result = self.run_script(self.b64(text))
        self.assertEqual(result.returncode, 0, result.stderr)
        files = self.queue_files()
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0].name, r"_pr_merge_report\.[0-9a-f]{16}\.txt$")
        self.assertEqual(files[0].read_text(), text + "\n")
        self.assertFalse(Path("/tmp/pwned").exists())
        self.assertIn("queued", result.stdout)

    def test_refuses_when_audio_worker_is_not_running(self):
        result = self.run_script(self.b64("こんにちは"))
        self.assertEqual(result.returncode, 66, result.stderr)
        self.assertEqual(self.queue_files(), [])

    def test_enqueue_failure_is_reported(self):
        self.start_worker()
        (self.soren / "lib/outbound_queue.sh").write_text("enqueue_audio_text() { return 1; }\n")
        result = self.run_script(self.b64("積めない文"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("nothing was queued", result.stderr)

    def test_vm_revalidation_rejects_bad_payloads(self):
        self.start_worker()
        for bad in (
            "!!!",
            self.b64(""),
            self.b64("a\x07b"),
            self.b64("あ" * 241),
            "A" * 5000,
            base64.b64encode(b"\xff\xfe").decode(),
        ):
            with self.subTest(bad=bad[:20]):
                self.assertEqual(self.run_script(bad).returncode, 64)


class ReportScriptPolicyTests(unittest.TestCase):
    def test_script_is_fixed_and_treats_text_as_data(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "TEXT_B64",
            "base64.b64decode(sys.stdin.read(), validate=True)",
            "audio_worker is not running",
            "COMMENT_AUDIO_DEDUP_TTL_SEC=0 enqueue_audio_text",
            "pr_merge_report.$nonce",
            "/dev/urandom",
            "/home/ubuntu/soren",
        ):
            self.assertIn(required, text)
        for forbidden in ("eval ", "sudo", "rm -", "$@", "bash -c", "sh -c"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
