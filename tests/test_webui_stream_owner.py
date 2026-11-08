"""WebUI stream control works for either encoder owner (ADR 0007 D2)."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import stream_pause  # noqa: E402
from docich import webui  # noqa: E402


class OwnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="webui-owner-")
        self.addCleanup(self.tmp.cleanup)
        self.soren = Path(self.tmp.name) / "soren"
        (self.soren / "tmp/state").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("DOCICH_STREAM_OWNER", None)

    def dotenv(self, text):
        (self.soren / ".env").write_text(text, encoding="utf-8")

    def status(self, payload):
        d = self.soren / "tmp/state/direct_stream"
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(json.dumps(payload), encoding="utf-8")


class ConfiguredOwnerTest(OwnerCase):
    def test_default_is_the_legacy_runner(self):
        self.assertEqual(webui._configured_stream_owner(self.soren), "soren")
        self.assertEqual(webui._stream_owner(self.soren), "soren")
        self.assertEqual(webui._get_stream_status(self.soren)["owner"], "soren")

    def test_only_the_exact_value_docich_selects_docich(self):
        for text, expected in (
            ("DOCICH_STREAM_OWNER=docich\n", "docich"),
            ("export DOCICH_STREAM_OWNER=docich\n", "docich"),
            ("DOCICH_STREAM_OWNER=\"docich\"\n", "docich"),
            ("DOCICH_STREAM_OWNER=\"docich\n", "soren"),            # unbalanced quote
            ("DOCICH_STREAM_OWNER=\" docich\"\n", "soren"),
            ("DOCICH_STREAM_OWNER='docich'\n", "docich"),
            ("DOCICH_STREAM_OWNER=soren\nDOCICH_STREAM_OWNER=docich\n", "docich"),   # last assignment wins
            ("DOCICH_STREAM_OWNER=docich\nDOCICH_STREAM_OWNER=soren\n", "soren"),
            ("DOCICH_STREAM_OWNER=Docich\n", "soren"),
            ("DOCICH_STREAM_OWNER= docich\n", "soren"),
            ("DOCICH_STREAM_OWNER=\n", "soren"),
            ("# DOCICH_STREAM_OWNER=docich\n", "soren"),
            ("XDOCICH_STREAM_OWNER=docich\n", "soren"),
            ("OTHER=1\n", "soren"),
        ):
            with self.subTest(text=text):
                self.dotenv(text)
                self.assertEqual(webui._configured_stream_owner(self.soren), expected)

    def test_the_process_environment_wins_over_the_env_file(self):
        self.dotenv("DOCICH_STREAM_OWNER=docich\n")
        os.environ["DOCICH_STREAM_OWNER"] = "soren"
        self.assertEqual(webui._configured_stream_owner(self.soren), "soren")
        os.environ["DOCICH_STREAM_OWNER"] = "docich"
        self.dotenv("DOCICH_STREAM_OWNER=soren\n")
        self.assertEqual(webui._configured_stream_owner(self.soren), "docich")

    def test_a_missing_env_file_is_the_legacy_owner(self):
        self.assertEqual(webui._configured_stream_owner(self.soren / "nowhere"), "soren")


class PauseMarkerByOwnerTest(OwnerCase):
    def test_docich_owner_uses_only_its_own_marker(self):
        self.dotenv("DOCICH_STREAM_OWNER=docich\n")
        result = webui._stop_stream_runner(self.soren)
        self.assertTrue(result["stopped"])
        self.assertTrue(stream_pause.is_paused(self.soren))
        self.assertFalse((self.soren / "tmp/state/direct_stream.paused").exists(), "the legacy marker is untouched")
        status = webui._get_stream_status(self.soren)
        self.assertEqual((status["state"], status["paused"], status["owner"]), ("paused", True, "docich"))

    def test_legacy_owner_uses_only_the_legacy_marker(self):
        result = webui._stop_stream_runner(self.soren)
        self.assertTrue(result["stopped"])
        self.assertTrue((self.soren / "tmp/state/direct_stream.paused").is_file())
        self.assertFalse(stream_pause.is_paused(self.soren))

    def test_set_and_clear_by_owner(self):
        for owner, is_set in (("docich", lambda: stream_pause.is_paused(self.soren)),
                              ("soren", lambda: webui._is_worker_paused(self.soren, "direct_stream"))):
            with self.subTest(owner=owner):
                webui._set_stream_paused(self.soren, owner, True)
                self.assertTrue(is_set())
                webui._set_stream_paused(self.soren, owner, False)
                self.assertFalse(is_set())


class DocichStopCommandTest(OwnerCase):
    def test_docich_owner_stops_through_the_supervisor_not_the_legacy_script(self):
        self.dotenv("DOCICH_STREAM_OWNER=docich\n")
        docich_calls, legacy_calls = [], []
        with mock.patch.object(webui, "_run_docich_stream_stop",
                               lambda root: docich_calls.append(root) or {"ok": True, "rc": 0, "detail": ""}), \
                mock.patch.object(webui, "_run_direct_stream_stop_script",
                                  lambda root: legacy_calls.append(root) or {"ok": True}):
            result = webui._stop_stream_runner(self.soren)
        self.assertEqual((docich_calls, legacy_calls), ([self.soren], []))
        self.assertEqual(result["method"], "docich_stream_stop")

    def test_legacy_owner_still_uses_the_legacy_script(self):
        docich_calls, legacy_calls = [], []
        with mock.patch.object(webui, "_run_docich_stream_stop", lambda root: docich_calls.append(root) or {"ok": True}), \
                mock.patch.object(webui, "_run_direct_stream_stop_script",
                                  lambda root: legacy_calls.append(root) or {"ok": True, "rc": 0, "detail": ""}):
            result = webui._stop_stream_runner(self.soren)
        self.assertEqual((docich_calls, legacy_calls), ([], [self.soren]))
        self.assertEqual(result["method"], "direct_stream_stop")

    def test_the_stop_command_line_targets_the_docich_supervisor_with_the_package_on_the_path(self):
        seen = {}
        def fake_run(command, **kw):
            seen["command"], seen["env"], seen["cwd"] = command, kw["env"], kw["cwd"]
            return subprocess.CompletedProcess(command, 0, "", "")
        with mock.patch.object(webui.subprocess, "run", fake_run):
            result = webui._run_docich_stream_stop(self.soren)
        self.assertTrue(result["ok"])
        self.assertEqual(seen["command"][1:], ["-m", "docich.stream_supervisor", "--root", str(self.soren), "stop"])
        self.assertEqual(seen["cwd"], str(self.soren))
        self.assertTrue((Path(seen["env"]["PYTHONPATH"].split(os.pathsep)[0]) / "docich" / "stream_supervisor.py").is_file())

    def test_failures_are_reported_not_hidden(self):
        with mock.patch.object(webui.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("x", 25)):
            self.assertFalse(webui._run_docich_stream_stop(self.soren)["ok"])
        with mock.patch.object(webui.subprocess, "run", side_effect=OSError("nope")):
            self.assertFalse(webui._run_docich_stream_stop(self.soren)["ok"])
        fail = subprocess.CompletedProcess([], 2, "", "refusing to stop a PID not owned by docich.stream_supervisor")
        with mock.patch.object(webui.subprocess, "run", return_value=fail):
            result = webui._run_docich_stream_stop(self.soren)
        self.assertEqual((result["ok"], result["rc"]), (False, 2))
        self.assertIn("not owned", result["detail"])


class RunnerMatchingTest(OwnerCase):
    def test_matcher_accepts_the_docich_runner_and_still_rejects_strangers(self):
        def matches(argv):
            raw = b"\x00".join(a.encode() for a in argv) + b"\x00"
            with mock.patch("pathlib.Path.read_bytes", return_value=raw):
                return webui._pid_matches_stream_process(4242, self.soren)
        root = str(self.soren)
        self.assertTrue(matches(["python3", "-m", "docich.stream_supervisor", "--root", root, "run"]))
        self.assertTrue(matches(["python3", f"{root}/lib/direct_stream.py", "run"]))
        self.assertTrue(matches(["/usr/bin/ffmpeg", "-i", "x"]))
        for argv in (["python3", "-m", "docich.stream_supervisor", "--root", "/other", "run"],
                     ["python3", "-m", "docich.stream_supervisor", "status"],
                     ["sleep", "100"], ["bash", "-c", "x"]):
            with self.subTest(argv=argv):
                self.assertFalse(matches(argv))

    def test_runner_pids_include_a_docich_runner_listed_in_the_status_file(self):
        self.status({"running": True, "pid": 4242, "ffmpeg_pid": 4243})
        raw_for = {4242: ["python3", "-m", "docich.stream_supervisor", "--root", str(self.soren), "run"],
                   4243: ["ffmpeg", "-i", "x"]}
        def fake_read(self_path):
            pid = int(str(self_path).split("/")[2])
            return b"\x00".join(a.encode() for a in raw_for[pid]) + b"\x00"
        with mock.patch("pathlib.Path.read_bytes", fake_read):
            self.assertEqual(webui._stream_runner_pids(self.soren), [4242, 4243])


class LiveEvidenceTest(OwnerCase):
    def test_a_live_runner_kind_beats_the_configured_owner(self):
        self.dotenv("DOCICH_STREAM_OWNER=docich\n")                    # configured docich ...
        self.status({"running": True, "pid": os.getpid()})
        legacy_argv = ["python3", f"{self.soren}/lib/direct_stream.py", "run"]
        raw = b"\x00".join(a.encode() for a in legacy_argv) + b"\x00"
        with mock.patch("pathlib.Path.read_bytes", return_value=raw), \
                mock.patch("os.readlink", return_value=str(self.soren)):
            self.assertEqual(webui._stream_owner(self.soren), "soren")       # ... but a legacy runner is alive
            docich_argv = ["python3", "-m", "docich.stream_supervisor", "--root", str(self.soren), "run"]
        raw2 = b"\x00".join(a.encode() for a in docich_argv) + b"\x00"
        self.dotenv("DOCICH_STREAM_OWNER=soren\n")
        with mock.patch("pathlib.Path.read_bytes", return_value=raw2), \
                mock.patch("os.readlink", return_value=str(self.soren)):
            self.assertEqual(webui._stream_owner(self.soren), "docich")

    def test_a_dead_or_unreadable_runner_falls_back_to_the_configuration(self):
        self.dotenv("DOCICH_STREAM_OWNER=docich\n")
        self.status({"running": True, "pid": 3999999})
        self.assertEqual(webui._stream_owner(self.soren), "docich")
        self.status({"running": True, "pid": os.getpid()})
        with mock.patch("pathlib.Path.read_bytes", side_effect=OSError):
            self.assertEqual(webui._stream_owner(self.soren), "docich")


if __name__ == "__main__":
    unittest.main()
