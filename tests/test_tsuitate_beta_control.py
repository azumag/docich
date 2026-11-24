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

# 実 HTTP fixture の共有ヘルパー (issue #2346)。応答後の書込み (audit event) が
# tmp の削除と競合しないよう、handler の完了を待ってから cleanup する。
from _webui_http_fixture import (  # noqa: E402
    assert_no_stuck_handlers,
    shutdown_test_server,
    tracking_server_class,
)

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
        status = {"state": "playing", "runId": "one", "gameId": "game-one", "brainVersion": "tsuitate-brain-v1",
                  "completedGames": 0, "reservedGames": 1, "stopRequested": False, "readyForNextRun": False,
                  "token": "fixture-private", "opponentPieces": [1],
                  "playerView": {
                      "yourColor": "sente",
                      "yourPieces": [{"square": "5i", "role": "king", "private": "drop-me"}],
                      "yourHand": {"pawn": 2},
                      "turn": "sente", "moveNumber": 7,
                      "clocks": {"senteMs": 123000, "goteMs": 125000, "running": "sente", "serverTime": 1000},
                      "fouls": {"you": 1, "opponent": 2},
                      "youInCheck": False, "opponentInCheck": True, "status": "playing",
                      "opponentPieces": [{"square": "5a", "role": "king"}],
                      "rawCheckpoint": "do-not-proxy",
                  }}
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()
        opener = mock.Mock(); opener.open.return_value = Response(json.dumps(status).encode())
        with mock.patch.dict(os.environ, ENV), mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
            result = control.call_beta_control("status")
        self.assertNotIn("token", result); self.assertNotIn("opponentPieces", result)
        self.assertEqual(result["playerView"], {
            "yourColor": "sente", "yourPieces": [{"square": "5i", "role": "king"}],
            "yourHand": {"pawn": 2}, "turn": "sente", "moveNumber": 7,
            "clocks": {"senteMs": 123000, "goteMs": 125000, "running": "sente", "serverTime": 1000},
            "fouls": {"you": 1, "opponent": 2}, "youInCheck": False,
            "opponentInCheck": True, "status": "playing",
        })
        self.assertNotIn("opponentPieces", result["playerView"])
        self.assertNotIn("rawCheckpoint", result["playerView"])
        self.assertNotIn("private", result["playerView"]["yourPieces"][0])
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 5)

        malformed = dict(status, playerView={**status["playerView"], "moveNumber": 0})
        opener.open.return_value = Response(json.dumps(malformed).encode())
        with mock.patch.dict(os.environ, ENV), mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
            degraded = control.call_beta_control("status")
        self.assertIsNone(degraded["playerView"])
        opener.open.return_value = Response(json.dumps(malformed).encode())
        with mock.patch.dict(os.environ, ENV), mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
            stopped = control.call_beta_control("stop", "one")
        self.assertNotIn("playerView", stopped)
        with self.assertRaises(control.ControlError): control.NoRedirect().redirect_request(None, None, None, None, None, None)
        opener.open.side_effect = RuntimeError("fixture-private-URL-token")
        with mock.patch.dict(os.environ, ENV), mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(control.ControlError, "^control_unavailable$"): control.call_beta_control("status")


    def test_player_view_rejects_malformed_known_fields(self):
        valid = {
            "yourColor": "sente", "yourPieces": [{"square": "5i", "role": "king"}],
            "yourHand": {}, "turn": "sente", "moveNumber": 1,
            "clocks": {"senteMs": 1, "goteMs": 1, "running": "sente", "serverTime": 1},
            "fouls": {"you": 0, "opponent": 0}, "youInCheck": False,
            "opponentInCheck": False, "status": "playing",
        }
        self.assertEqual(control.project_player_view(valid)["yourPieces"], [{"square": "5i", "role": "king"}])
        for bad in [
            {**valid, "yourColor": []},
            {**valid, "yourPieces": [{"square": "5z", "role": "king"}]},
            {**valid, "yourPieces": [{"square": "5i", "role": []}]},
            {**valid, "yourHand": {"king": 1}},
            {**valid, "moveNumber": 0},
            {**valid, "clocks": {**valid["clocks"], "running": []}},
            {**valid, "clocks": {**valid["clocks"], "running": "gote"}},
            {**valid, "fouls": {"you": 11, "opponent": 0}},
        ]:
            with self.assertRaisesRegex(control.ControlError, "^control_unavailable$"):
                control.project_player_view(bad)


    def test_game_result_projection_is_a_closed_public_allowlist(self):
        valid = {"outcome": "win", "reason": "checkmate", "endedAt": "2026-10-06T00:05:00.000Z",
                 "moveNumber": 9, "resultConfidence": "verified"}
        self.assertEqual(control.project_game_result(valid), valid)
        self.assertIsNone(control.project_game_result(None))
        for bad in [
            {},
            {**valid, "outcome": "victory"},
            {**valid, "reason": "because-the-opponent-blamed-us"},
            {**valid, "endedAt": "2026-10-06 00:05:00"},
            {**valid, "endedAt": "2026-10-06T00:05:00+09:00"},
            {**valid, "moveNumber": -1},
            {**valid, "moveNumber": True},
            {**valid, "moveNumber": 100001},
            {**valid, "moveNumber": "9"},
            {**valid, "resultConfidence": "public_replay"},
            {**valid, "endedAt": ""},
        ]:
            with self.assertRaisesRegex(control.ControlError, "^control_unavailable$"):
                control.project_game_result(bad)
        # Unknown keys are dropped rather than forwarded, matching PlayerView.
        self.assertEqual(
            control.project_game_result({**valid, "decisions": [{"usi": "2h2g"}], "raw": "x"}),
            valid,
        )

    def test_status_projects_the_settled_result_and_start_stop_do_not(self):
        result = {"outcome": "loss", "reason": "resign", "endedAt": "2026-10-06T00:05:00.000Z",
                  "moveNumber": 41, "resultConfidence": "verified"}
        status = {"state": "finished", "runId": "one", "gameId": "game-one",
                  "brainVersion": "tsuitate-brain-v9", "completedGames": 1, "reservedGames": 1,
                  "stopRequested": False, "readyForNextRun": True, "terminalResult": result,
                  "playerView": None}

        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()

        opener = mock.Mock(); opener.open.return_value = Response(json.dumps(status).encode())
        with mock.patch.dict(os.environ, ENV), mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(control.call_beta_control("status")["gameResult"], result)
        # Start/stop replies carry no result, so a lifecycle command cannot be
        # influenced by a display-only field.
        for action, run in (("start", "one"), ("stop", "one"), ("reconcile", "one")):
            opener.open.return_value = Response(json.dumps(status).encode())
            with mock.patch.dict(os.environ, ENV), mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
                self.assertNotIn("gameResult", control.call_beta_control(action, run))
        # A malformed optional result degrades to null without failing status.
        opener.open.return_value = Response(json.dumps({**status, "terminalResult": {"outcome": "win"}}).encode())
        with mock.patch.dict(os.environ, ENV), mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
            self.assertIsNone(control.call_beta_control("status")["gameResult"])


class TestWebUiGate(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        g = config.load_global(Path(self.directory.name))
        g.webui.token, g.webui.read_only_token = OPERATOR, VIEWER
        class Handler(webui._Handler): pass
        Handler.g = g; Handler.soren_root = Path(self.directory.name); Handler.read_only = False
        Handler.csrf_secret = b"fixture-csrf-only-not-a-credential"; Handler.start_time = time.time()
        self.server = tracking_server_class()(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.port = self.server.server_address[1]
        self.client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.csrf = webui._make_csrf_token(Handler.csrf_secret)

    def tearDown(self):
        self.client.close()
        # ``_guard_mutation`` は拒否応答の後に authorization audit event を書く。
        # daemon handler は server_close() で join されないため、完了を待たずに
        # tmp を消すと rmtree と競合する (PR #2340 の full-suite flake)。
        _stop_gate_fixture(self.server, self.thread, self.directory, self)

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


class _PostResponseWriterHandler(webui._Handler):
    """応答を返した後に書く handler (``_guard_mutation`` の audit event 相当)。

    応答送信後に ``post_response_started`` を set し、``release_post_response``
    が set されるまで保留する。テスト側はこれで「handler が応答後の書込み途中に
    いる」状態を決定的に作れる。解放後の書込み先は tmp 配下なので、後片付けが
    先に tmp を消せば ``write_text`` が失敗して ``post_response_written`` が
    set されない (= full-suite flake の再現)。
    """

    POST_RESPONSE_PATH = "/test/tsuitate-post-response-write"
    WRITE_RELATIVE = "tmp/debug/tsuitate-post-response-audit.json"

    def do_POST(self):
        if self.path != self.POST_RESPONSE_PATH:
            return super().do_POST()
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.server.post_response_started.set()
        if not self.server.release_post_response.wait(timeout=5):
            return
        target = Path(self.soren_root) / self.WRITE_RELATIVE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("post-response write completed", encoding="utf-8")
        self.server.post_response_written.set()


def _stop_gate_fixture(server, thread, directory, case):
    """``TestWebUiGate`` の後片付け: 応答後 writer を待ってから tmp を消す。

    期限付きで handler の完了を待ち、残った handler は黙って見逃さず
    ``case`` の失敗として報告する (issue #2346)。
    """
    stuck = shutdown_test_server(server, thread)
    assert_no_stuck_handlers(case, stuck)
    directory.cleanup()


class TestWebUiGateTeardownWaitsForPostResponseWriter(unittest.TestCase):
    """応答後に書く handler が残っていても、後片付けが先に tmp を消さないこと。

    issue #2346 の決定的な回帰テスト。``_stop_gate_fixture`` から待ちを外す
    (``shutdown`` + ``server_close`` だけにする) と、このテストは
    ``assertFalse(stopped...)`` で fail する。
    """

    def test_teardown_waits_for_handler_that_writes_after_the_response(self):
        directory = tempfile.TemporaryDirectory()
        g = config.load_global(Path(directory.name))

        class Handler(_PostResponseWriterHandler):
            pass

        Handler.g = g
        Handler.soren_root = Path(directory.name)
        Handler.read_only = False
        Handler.csrf_secret = b"fixture-csrf-only-not-a-credential"
        Handler.start_time = time.time()
        server = tracking_server_class()(("127.0.0.1", 0), Handler)
        server.post_response_started = threading.Event()
        server.release_post_response = threading.Event()
        server.post_response_written = threading.Event()
        serve_thread = threading.Thread(target=server.serve_forever, daemon=True)
        serve_thread.start()
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        stop_result = {}
        stopped = threading.Event()

        def _stop_server():
            try:
                _stop_gate_fixture(server, serve_thread, directory, self)
            except Exception as exc:  # pragma: no cover - assertion で理由を出す
                stop_result["error"] = exc
            finally:
                stopped.set()

        stopper = threading.Thread(target=_stop_server, daemon=True)
        stopper_started = False
        try:
            conn.request("POST", _PostResponseWriterHandler.POST_RESPONSE_PATH)
            res = conn.getresponse()
            self.assertEqual(200, res.status)
            res.read()
            self.assertTrue(
                server.post_response_started.wait(timeout=5),
                "handler did not reach the post-response write",
            )

            stopper.start()
            stopper_started = True
            # 応答後 writer は保留中なので、後片付けはここで完了してはならない。
            # 完了するなら handler を待たずに tmp を消した (= 競合) ということ。
            self.assertFalse(
                stopped.wait(timeout=1.0),
                "teardown removed the tmp dir without waiting for the post-response writer",
            )
            self.assertTrue(
                Path(directory.name).exists(),
                "handler の完了前に tmp dir が消えた",
            )
        finally:
            server.release_post_response.set()
            if stopper_started:
                stopper.join(timeout=5)
            else:
                shutdown_test_server(server, serve_thread)
            conn.close()

        self.assertNotIn("error", stop_result, stop_result.get("error"))
        self.assertTrue(
            server.post_response_written.is_set(),
            "post-response write did not complete before cleanup",
        )


if __name__ == "__main__": unittest.main()
