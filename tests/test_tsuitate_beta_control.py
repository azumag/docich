import hashlib
import hmac
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import config, webui, tsuitate_beta_control as control

SECRET = "fixture-only-control-capability-not-credential"
OPERATOR = "fixture-only-operator-not-credential"
VIEWER = "fixture-only-viewer-not-credential"
ENV = {"DOCICH_BETA_CONTROL_SECRET": SECRET,
       "DOCICH_BETA_CONTROL_URL": "https://docich-tsuitate-bot.fixture-only.workers.dev"}


class TestBridge(unittest.TestCase):
    def test_exact_raw_signature_and_fixed_action(self):
        request = control.signed_request(ENV["DOCICH_BETA_CONTROL_URL"], SECRET, "start", "one", now=1791100000)
        self.assertEqual(request.data, b'{"action":"start","runId":"one"}')
        expected = hmac.new(SECRET.encode(), control.PREFIX + b"1791100000." + request.data, hashlib.sha256).hexdigest()
        self.assertEqual(dict(request.header_items())["X-beta-control-signature"], "sha256=" + expected)
        self.assertEqual(dict(request.header_items())["User-agent"], "docich-beta-control/1.0")
        recovery = control.signed_request(ENV["DOCICH_BETA_CONTROL_URL"], SECRET, "reconcile", "one", now=1791100000)
        self.assertEqual(recovery.data, b'{"action":"reconcile","runId":"one"}')
        expected = hmac.new(SECRET.encode(), control.PREFIX + b"1791100000." + recovery.data, hashlib.sha256).hexdigest()
        self.assertEqual(dict(recovery.header_items())["X-beta-control-signature"], "sha256=" + expected)
        for action, run in [("deploy", None), ("start", "../x"), ("stop", None)]:
            with self.assertRaises(control.ControlError):
                control.signed_request("https://local-only.test", SECRET, action, run)

    def test_missing_reused_or_invalid_origin_never_sends(self):
        for override in [{"DOCICH_BETA_CONTROL_SECRET": ""},
                         {"DOCICH_BETA_CONTROL_URL": "http://127.0.0.1"},
                         {"DOCICH_BETA_CONTROL_URL": "https://docich-tsuitate-bot.fixture-only.workers.dev:bad"},
                         {"DOCICH_BETA_CONTROL_URL": ENV["DOCICH_BETA_CONTROL_URL"] + "/redirect"}]:
            with mock.patch.dict(os.environ, {**ENV, **override}), mock.patch.object(control.urllib.request, "build_opener") as opener:
                with self.assertRaises(control.ControlError): control.call_beta_control("status")
                opener.assert_not_called()
        with mock.patch.dict(os.environ, ENV):
            with self.assertRaises(control.ControlError): control.call_beta_control("status", forbidden_secrets=(SECRET,))

    def test_configured_bridge_ignores_absent_or_stale_false_enable_setting(self):
        status = {"state": "stopped", "runId": None, "gameId": None, "brainVersion": "tsuitate-brain-v2",
                  "completedGames": 0, "reservedGames": 0, "stopRequested": False, "readyForNextRun": True}
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()
        for override in [{}, {"DOCICH_BETA_CONTROL_ENABLED": "false"}]:
            with self.subTest(override=override), mock.patch.dict(os.environ, {**ENV, **override}, clear=True):
                opener = mock.Mock(); opener.open.return_value = Response(json.dumps(status).encode())
                with mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
                    self.assertEqual(control.call_beta_control("start", "one")["state"], "stopped")
                self.assertEqual(json.loads(opener.open.call_args.args[0].data), {"action": "start", "runId": "one"})

    def test_response_projection_and_no_redirect_or_raw_error(self):
        status = {"state": "stopped", "runId": None, "gameId": None, "brainVersion": "tsuitate-brain-v1",
                  "completedGames": 0, "reservedGames": 0, "stopRequested": False, "readyForNextRun": True,
                  "token": "fixture-private", "opponentPieces": [1]}
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()
        opener = mock.Mock(); opener.open.return_value = Response(json.dumps(status).encode())
        with mock.patch.dict(os.environ, ENV), mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
            result = control.call_beta_control("status")
        self.assertNotIn("token", result); self.assertNotIn("opponentPieces", result)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 5)
        with self.assertRaises(control.ControlError): control.NoRedirect().redirect_request(None, None, None, None, None, None)
        opener.open.side_effect = RuntimeError("fixture-private-URL-token")
        with mock.patch.dict(os.environ, ENV), mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(control.ControlError, "^control_unavailable$"): control.call_beta_control("status")


class TestWebUiGate(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        g = config.load_global(Path(self.directory.name))
        g.webui.token, g.webui.read_only_token = OPERATOR, VIEWER
        class Handler(webui._Handler): pass
        Handler.g = g; Handler.soren_root = Path(self.directory.name); Handler.read_only = False
        Handler.csrf_secret = b"fixture-csrf-only-not-a-credential"; Handler.start_time = time.time()
        self.server = webui.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.port = self.server.server_address[1]
        self.client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.csrf = webui._make_csrf_token(Handler.csrf_secret)

    def tearDown(self):
        self.client.close(); self.server.shutdown(); self.server.server_close(); self.directory.cleanup()

    def request(self, method="POST", payload=None, headers=None):
        h = {"Authorization": "Bearer " + OPERATOR, "Origin": f"http://127.0.0.1:{self.port}",
             "Content-Type": "application/json", "X-CSRF-Token": self.csrf, **(headers or {})}
        self.client.request(method, "/api/tsuitate-beta", body=json.dumps(payload) if payload is not None else None, headers=h)
        response = self.client.getresponse(); return response.status, json.loads(response.read())

    def test_operator_routes_fixed_commands_and_replays_identical_run_id(self):
        with mock.patch.object(webui, "call_beta_control", return_value={"state": "queued"}) as bridge:
            for _ in range(2):
                self.assertEqual(self.request(payload={"action": "start", "runId": "one", "confirm": True})[0], 200)
            self.assertEqual(bridge.call_args.args, ("start", "one"))
            self.assertEqual(bridge.call_count, 2)
            self.assertEqual(self.request("GET")[0], 200)
            self.assertEqual(bridge.call_args.args, ("status", None))
            self.assertEqual(bridge.call_args.kwargs["forbidden_secrets"], (OPERATOR, VIEWER))
            self.assertEqual(self.request(payload={"action": "reconcile", "runId": "one", "confirm": True})[0], 200)
            self.assertEqual(bridge.call_args.args, ("reconcile", "one"))

    def test_unauthenticated_viewer_csrf_origin_host_and_confirmation_rejected_before_bridge(self):
        good = {"action": "start", "runId": "one", "confirm": True}
        cases = [({"Authorization": ""}, 401), ({"Authorization": "Bearer " + VIEWER}, 403),
                 ({"X-CSRF-Token": ""}, 403), ({"Origin": "https://evil.example"}, 403),
                 ({"Host": "evil.example"}, 400), ({"Content-Type": "text/plain"}, 415)]
        with mock.patch.object(webui, "call_beta_control") as bridge:
            for action in ("start", "reconcile"):
                for headers, expected in cases: self.assertEqual(self.request(payload={**good, "action": action}, headers=headers)[0], expected)
            self.assertEqual(self.request(payload={"action": "reconcile", "runId": "one"})[0], 428)
            self.assertEqual(self.request(payload={"action": "start", "runId": "one"})[0], 428)
            self.assertEqual(self.request("GET", headers={"Authorization": "Bearer " + VIEWER})[0], 403)
            for payload in [{**good, "action": "deploy"}, {**good, "url": "https://evil.example"}, {**good, "maxGames": 2}]:
                self.assertEqual(self.request(payload=payload)[0], 400)
            bridge.assert_not_called()

    def test_unconfigured_bridge_returns_fixed_error(self):
        with mock.patch.dict(os.environ, {"DOCICH_BETA_CONTROL_SECRET": ""}, clear=True):
            self.assertEqual(self.request("GET"), (503, {"error": "control_not_configured"}))


if __name__ == "__main__": unittest.main()
