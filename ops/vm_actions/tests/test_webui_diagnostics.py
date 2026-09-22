import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"

EXPECTED_KEYS = {
    "unit_file",
    "unit_active",
    "unit_enabled",
    "main_pid",
    "n_restarts",
    "served_port",
    "served_reachable",
    "served_matches_deployed",
    "listener_is_unit",
}


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics_webui", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def deployed_index_bytes():
    text = (ROOT / "src" / "docich" / "webui.py").read_text(encoding="utf-8")
    match = re.search(r'INDEX_HTML = r"""(.*?)"""', text, re.S)
    assert match is not None, "INDEX_HTML missing from src/docich/webui.py"
    return match.group(1).encode("utf-8")


class _ServingHandler(BaseHTTPRequestHandler):
    body_holder = None

    def do_GET(self):
        body = type(self).body_holder
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class WebuiDiagnosticsTests(unittest.TestCase):
    """`webui` diagnostics: only bounded booleans/ints/nulls, never bytes."""

    def setUp(self):
        self.module = load_collector()
        _ServingHandler.body_holder = deployed_index_bytes()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ServingHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def collect(self, *, unit_active=True, main_pid=None, n_restarts=0, port=None):
        with mock.patch.object(self.module, "_webui_config_port", return_value=port or self.port), \
                mock.patch.object(self.module, "_unit_is_active", return_value=unit_active), \
                mock.patch.object(self.module, "_unit_is_enabled", return_value=True), \
                mock.patch.object(
                    self.module,
                    "_webui_unit_properties",
                    return_value=(main_pid, n_restarts),
                ):
            return self.module._collect_webui()

    def test_deployed_index_digest_matches_source(self):
        expected = hashlib.sha256(deployed_index_bytes()).digest()
        self.assertEqual(self.module._webui_deployed_index_digest(), expected)

    def test_matching_served_html_reports_matches_true(self):
        # in-process server → socket is owned by this process
        data = self.collect(main_pid=os.getpid())
        self.assertTrue(data["unit_active"])
        self.assertTrue(data["served_reachable"])
        self.assertTrue(data["served_matches_deployed"])

    def test_stale_served_html_reports_matches_false(self):
        _ServingHandler.body_holder = b"<!doctype html><html><body>old ui</body></html>"
        data = self.collect(main_pid=os.getpid())
        self.assertTrue(data["served_reachable"])
        self.assertFalse(data["served_matches_deployed"])

    def test_unreachable_endpoint_reports_unknown_match(self):
        self.server.shutdown()
        self.server.server_close()
        data = self.collect(port=1, main_pid=None)
        self.assertFalse(data["served_reachable"])
        self.assertIsNone(data["served_matches_deployed"])

    def test_payload_publishes_only_bounded_types(self):
        # path / cmdline / HTML bytes must never reach the Actions output
        data = self.collect(main_pid=os.getpid())
        self.assertEqual(set(data), EXPECTED_KEYS)
        for key, value in data.items():
            self.assertIn(type(value), (bool, int, type(None)), (key, value))
        text = json.dumps(data)
        self.assertLess(len(text), 1000)

    def test_listener_is_unit_never_publishes_a_pid_string(self):
        data = self.collect(main_pid=os.getpid())
        if Path("/proc/net/tcp").exists():
            self.assertIs(data["listener_is_unit"], True)

    @unittest.skipUnless(Path("/proc/net/tcp").exists(), "requires /proc")
    def test_listener_is_unit_is_false_for_a_foreign_pid(self):
        data = self.collect(main_pid=2_147_483_646)
        self.assertIs(data["listener_is_unit"], False)

    @unittest.skipUnless(Path("/proc/net/tcp").exists(), "requires /proc")
    def test_listener_is_unit_unknown_without_pid(self):
        data = self.collect(main_pid=None)
        # listener exists (this test process) but the unit has no pid
        self.assertIs(data["listener_is_unit"], None)

    def test_pid_owns_socket_is_false_for_a_missing_pid(self):
        # Path.iterdir() is lazy: the missing-/proc/<pid> FileNotFoundError
        # surfaces during iteration, not at construction (regression).
        self.assertIs(self.module._pid_owns_socket(2_147_483_646, {"1"}), False)
        self.assertIs(self.module._pid_owns_socket(os.getpid(), {"not-a-real-inode"}), False)


class WebuiDiagnosticsCollectorTests(unittest.TestCase):
    """Full collector run: the section is present and stays bounded."""

    def test_collector_publishes_webui_section(self):
        with tempfile.TemporaryDirectory(prefix="vmops-webui-") as tmp:
            soren = Path(tmp) / "soren"
            (soren / "tmp" / "state").mkdir(parents=True)
            proc = subprocess.run(
                ["python3", str(COLLECTOR), str(soren / "missing")],
                capture_output=True,
                text=True,
                timeout=120,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp"},
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        webui = data["webui"]
        self.assertEqual(set(webui), EXPECTED_KEYS)
        for key, value in webui.items():
            self.assertIn(type(value), (bool, int, type(None)), (key, value))
        # webui は任意コンポーネントのため severity には加算しない
        self.assertIn(data["status"], ("ok", "warn", "critical"))


if __name__ == "__main__":
    unittest.main()
