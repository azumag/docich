import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_audio_speak.py"
SCRIPT = ROOT / "ops/vm_actions/speak_audio_worker.sh"
WF = ROOT / ".github/workflows/audio-worker-speak.yml"
QUEUE_LIB = ROOT / "games/soviet_now/lib/outbound_queue.sh"
REF = "azumag/docich/.github/workflows/audio-worker-speak.yml@refs/heads/main"


STUB_QUEUE_LIB = """
enqueue_audio_text() {
	local text="${1:-}" source="${2:-unknown}"
	local queue_dir="${COMMENT_QUEUE_DIR:-tmp/.comment_queue}"
	[ -n "$text" ] || return 1
	mkdir -p "$queue_dir"
	local f="${queue_dir}/comment_announce_$(date +%s%N)_${source}.txt"
	printf '%s\\n' "$text" > "${f}.tmp" && mv "${f}.tmp" "$f"
}
"""


def run_auth(tmp, **overrides):
    out = Path(tmp) / "out"
    out.write_text("")
    env = {
        "GITHUB_REPOSITORY": "azumag/docich",
        "GITHUB_REPOSITORY_ID": "1327276249",
        "GITHUB_REPOSITORY_OWNER": "azumag",
        "GITHUB_REPOSITORY_OWNER_ID": "9018513",
        "GITHUB_ACTOR": "azumag",
        "GITHUB_ACTOR_ID": "9018513",
        "GITHUB_TRIGGERING_ACTOR": "azumag",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_DEFAULT_BRANCH": "main",
        "GITHUB_WORKFLOW_REF": REF,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(out),
        "INPUT_TEXT": "テスト放送です",
        "INPUT_CONFIRM": "production",
    }
    env.update(overrides)
    result = subprocess.run([sys.executable, str(AUTH)], capture_output=True, text=True, env=env)
    return result, out.read_text()


class AudioSpeakAuthorizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_owner_dispatch_passes_text_only_as_base64_output(self):
        result, output = run_auth(self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"operation": "audio-speak", "target": "production", "ref": "main", "chars": 7},
        )
        self.assertNotIn("テスト", result.stdout + result.stderr)
        self.assertEqual(
            base64.b64decode(output.strip().removeprefix("text_b64=")).decode(), "テスト放送です"
        )

    def test_shell_metacharacters_are_data_not_syntax(self):
        text = "$(id) `id` ; rm -rf / && \"' \\n ${HOME}"
        result, output = run_auth(self.tmp, INPUT_TEXT=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        b64 = output.strip().removeprefix("text_b64=")
        self.assertRegex(b64, r"^[A-Za-z0-9+/]+={0,2}$")
        self.assertEqual(base64.b64decode(b64).decode(), text)

    def test_multiline_is_folded_to_one_line(self):
        _, output = run_auth(self.tmp, INPUT_TEXT="一行目\r\n\r\n 二行目 \n")
        self.assertEqual(base64.b64decode(output.strip().removeprefix("text_b64=")).decode(), "一行目 二行目")

    def test_bad_text_is_rejected_without_echo(self):
        cases = ("", "   \n ", "あ" * 241, "a\x07b", "a‮b", "a​b", "a\x1b[31mb")
        for text in cases:
            with self.subTest(text=repr(text)):
                result, output = run_auth(self.tmp, INPUT_TEXT=text)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, "")
                if text.strip():
                    self.assertNotIn(text, result.stderr)
        self.assertEqual(run_auth(self.tmp, INPUT_TEXT="あ" * 240)[0].returncode, 0)

    def test_wrong_owner_confirmation_ref_event_or_workflow_fails_closed(self):
        cases = (
            {"GITHUB_ACTOR": "other", "GITHUB_ACTOR_ID": "42"},
            {"GITHUB_TRIGGERING_ACTOR": "other"},
            {"GITHUB_REPOSITORY": "other/docich"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_REF_PROTECTED": "false"},
            {"GITHUB_DEFAULT_BRANCH": "release"},
            {"GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/other.yml@refs/heads/main"},
            {"GITHUB_WORKFLOW_REF": REF.replace("refs/heads/main", "refs/heads/feature")},
            {"GITHUB_WORKFLOW_REF": ""},
            {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_EVENT_NAME": "issue_comment"},
            {"INPUT_CONFIRM": ""},
            {"INPUT_CONFIRM": "prod"},
            {"GITHUB_SHA": "not-a-sha"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                result, output = run_auth(self.tmp, **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, "")


class AudioSpeakPolicyTests(unittest.TestCase):
    def test_workflow_is_owner_only_main_only_and_text_never_reaches_a_shell_expression(self):
        text = WF.read_text(encoding="utf-8")
        for required in (
            "workflow_dispatch:",
            "github.actor_id == 9018513",
            "github.triggering_actor == 'azumag'",
            "github.ref == 'refs/heads/main'",
            "github.ref_protected == true",
            "environment: vm-operations",
            "Require production to equal current protected main",
            "control/ops/vm_actions/authorize_audio_speak.py",
            "control/ops/vm_actions/speak_audio_worker.sh",
            "INPUT_TEXT: ${{ inputs.text }}",
            "TEXT_B64: ${{ steps.auth.outputs.text_b64 }}",
            "exec docich production $SHA",
            "persist-credentials: false",
        ):
            self.assertIn(required, text)
        # The free-form input appears exactly once, as an env value (never in run:).
        self.assertEqual(text.count("inputs.text"), 1)
        for forbidden in ("pull_request", "issue_comment", "pull_request_target", "ssh-keyscan", "inputs.command"):
            self.assertNotIn(forbidden, text)

    def test_vm_script_treats_text_as_data_and_fails_closed(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "TEXT_B64",
            "base64.b64decode(sys.stdin.read(), validate=True)",
            "audio_worker is not running",
            "COMMENT_AUDIO_DEDUP_TTL_SEC=0 enqueue_audio_text",
            "owner_speak",
            "/home/ubuntu/soren",
        ):
            self.assertIn(required, text)
        for forbidden in ("eval ", "sudo", "rm -", "$@", "bash -c", "sh -c"):
            self.assertNotIn(forbidden, text)


@unittest.skipUnless(sys.platform.startswith("linux"), "needs /proc and GNU userland")
class AudioSpeakScriptRuntimeTests(unittest.TestCase):
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
        self.script = self.tmp / "speak.sh"
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

    def run_script(self, b64, timeout=30):
        payload = f"TEXT_B64={b64}\n" + self.script.read_text()
        return subprocess.run(["bash", "--noprofile", "--norc", "-euo", "pipefail", "-s"],
                              input=payload, text=True, capture_output=True, timeout=timeout)

    def b64(self, text):
        return base64.b64encode(text.encode()).decode()

    def test_refuses_when_audio_worker_is_not_running(self):
        result = self.run_script(self.b64("こんにちは"))
        self.assertEqual(result.returncode, 66, result.stderr)
        self.assertEqual(list((self.soren / "tmp/.comment_queue").glob("*")) if (self.soren / "tmp/.comment_queue").exists() else [], [])

    def test_queues_exact_text_and_reports_consumption(self):
        self.start_worker()
        queue = self.soren / "tmp/.comment_queue"
        text = "$(touch /tmp/pwned) `x` ; あいう"

        def consume():
            deadline = time.time() + 20
            while time.time() < deadline:
                files = list(queue.glob("comment_announce_*_owner_speak.txt")) if queue.exists() else []
                if files:
                    self.seen = files[0].read_text()
                    files[0].unlink()
                    return
                time.sleep(0.2)

        import threading
        thread = threading.Thread(target=consume)
        thread.start()
        result = self.run_script(self.b64(text))
        thread.join()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.seen, text + "\n")
        self.assertFalse(Path("/tmp/pwned").exists())

    def test_same_text_twice_is_not_deduplicated(self):
        self.start_worker()
        queue = self.soren / "tmp/.comment_queue"
        import threading
        stop = threading.Event()

        def consume():
            while not stop.is_set():
                for f in (list(queue.glob("comment_announce_*.txt")) if queue.exists() else []):
                    f.unlink()
                time.sleep(0.1)

        thread = threading.Thread(target=consume)
        thread.start()
        try:
            for _ in range(2):
                self.assertEqual(self.run_script(self.b64("同じ文")).returncode, 0)
        finally:
            stop.set()
            thread.join()

    def test_vm_revalidation_rejects_bad_payloads(self):
        self.start_worker()
        for bad in ("!!!", self.b64(""), self.b64("a\x07b"), self.b64("あ" * 241), "A" * 5000,
                    base64.b64encode(b"\xff\xfe").decode()):
            with self.subTest(bad=bad[:20]):
                self.assertEqual(self.run_script(bad).returncode, 64)


if __name__ == "__main__":
    unittest.main()
