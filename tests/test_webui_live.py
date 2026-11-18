"""WebUI LIVE snapshot and owner switch (Issue #3623, PR-C).

The snapshot tests use the real LIVE ledger/control/status files in a temp state dir; the
HTTP tests use the same real ``ThreadingHTTPServer`` fixture as ``tests/test_webui.py``.
No network, no sleeps.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, webui  # noqa: E402
from docich.trading.live.paths import live_layout  # noqa: E402
from docich.trading.live_worker.status import (  # noqa: E402
    ALLOWED_KEYS, status_path, write_status,
)
from docich.webui_live import live_snapshot, set_control  # noqa: E402

from _webui_http_fixture import (  # noqa: E402
    assert_no_stuck_handlers as _assert_no_stuck_handlers,
    shutdown_test_server as _shutdown_test_server,
    tracking_server_class as _tracking_server_class,
)
from test_trading_live_support import Workspace  # noqa: E402

NOW = 1_800_000_000.0
ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_KEYS = {"control", "status", "worker_alive", "ledger_present", "overview"}


class TestLiveSnapshot(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def test_no_files_reports_unknown_and_missing_ledger_without_raising(self):
        snapshot = live_snapshot(self.ws.state, now=NOW)

        self.assertEqual(set(snapshot), SNAPSHOT_KEYS)
        self.assertEqual(snapshot["control"]["state"], "unknown")
        self.assertIsNone(snapshot["status"])
        self.assertFalse(snapshot["worker_alive"])
        self.assertFalse(snapshot["ledger_present"])
        self.assertFalse(snapshot["overview"]["ledger_readable"])
        self.assertEqual(snapshot["overview"]["pnl"]["total"], "0")

    def test_control_status_and_ledger_are_combined_without_raw_bytes(self):
        self.ws.write_control(state="on", mode="trade", updated_by="owner", now=NOW)
        write_status(self.ws.state, {
            "phase": "running",
            "cycle": 3,
            "mode": "trade",
            "control_state": "on",
            "control_mode": "trade",
            "balance_warnings": 2,
            "policy_set_digest": "0123456789abcdef" + "0" * 48,
            "api_key": "SENTINEL-API-KEY",
        }, now=NOW)
        ledger = self.ws.open_ledger(now=NOW)
        ledger.close()

        # The writer already drops unknown keys; inject one to prove the reader filters too.
        path = status_path(self.ws.state)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["unknown_key"] = "SENTINEL-UNKNOWN"
        raw["balance"] = "12345"
        path.write_text(json.dumps(raw), encoding="utf-8")

        snapshot = live_snapshot(self.ws.state, now=NOW)

        self.assertEqual(snapshot["control"]["state"], "on")
        self.assertEqual(snapshot["control"]["mode"], "trade")
        self.assertEqual(snapshot["control"]["updated_by"], "owner")
        self.assertTrue(snapshot["ledger_present"])
        self.assertTrue(snapshot["overview"]["ledger_readable"])
        self.assertEqual(snapshot["overview"]["pnl"]["total"], "0")
        self.assertTrue(snapshot["worker_alive"])
        self.assertEqual(snapshot["status"]["cycle"], 3)
        self.assertEqual(snapshot["status"]["balance_warnings"], 2)
        self.assertEqual(snapshot["status"]["policy_set_digest"], "0123456789ab")
        self.assertLessEqual(set(snapshot["status"]), ALLOWED_KEYS)
        self.assertNotIn("unknown_key", snapshot["status"])
        self.assertNotIn("balance", snapshot["status"])
        self.assertNotIn("api_key", snapshot["status"])
        self.assertNotIn("SENTINEL", json.dumps(snapshot))

    def test_worker_alive_uses_the_status_write_time_and_the_180s_bound(self):
        self.ws.write_control(state="off", mode="shadow", updated_by="owner", now=NOW)
        write_status(self.ws.state, {"phase": "running"}, now=NOW)

        self.assertTrue(live_snapshot(self.ws.state, now=NOW + 179)["worker_alive"])
        self.assertFalse(live_snapshot(self.ws.state, now=NOW + 180)["worker_alive"])
        self.assertFalse(live_snapshot(self.ws.state, now=NOW + 181)["worker_alive"])

    def test_worker_alive_uses_the_configured_600s_interval(self):
        self.ws.write_control(state="off", mode="shadow", updated_by="owner", now=NOW)
        write_status(self.ws.state, {"phase": "running", "interval_ms": 600_000}, now=NOW)

        # 3 x 600 s replaces the legacy 3 x 60 s bound.
        self.assertTrue(live_snapshot(self.ws.state, now=NOW + 1799)["worker_alive"])
        self.assertFalse(live_snapshot(self.ws.state, now=NOW + 1800)["worker_alive"])

        # A retrying 600 s worker keeps the runner's next_wait(600, 4) = 600 s window.
        write_status(self.ws.state, {"phase": "running", "interval_ms": 600_000,
                                     "consecutive_failures": 4}, now=NOW)
        self.assertTrue(live_snapshot(self.ws.state, now=NOW + 2399)["worker_alive"])
        self.assertFalse(live_snapshot(self.ws.state, now=NOW + 2400)["worker_alive"])

    def test_worker_alive_bounds_a_malformed_interval_ms_to_the_legacy_180s(self):
        self.ws.write_control(state="off", mode="shadow", updated_by="owner", now=NOW)
        path = status_path(self.ws.state)
        for interval_ms in (600_000.0, 29_999, 3_600_001, 10**100, True):
            with self.subTest(interval_ms=repr(interval_ms)):
                path.write_text(json.dumps({
                    "phase": "running", "written_at": int(NOW),
                    "interval_ms": interval_ms,
                }), encoding="utf-8")
                self.assertTrue(live_snapshot(self.ws.state, now=NOW + 179)["worker_alive"])
                self.assertFalse(live_snapshot(self.ws.state, now=NOW + 180)["worker_alive"])

    def test_a_legacy_updated_at_conversion_never_raises(self):
        self.ws.write_control(state="off", mode="shadow", updated_by="owner", now=NOW)
        path = status_path(self.ws.state)
        for alias in (10**4000, float("nan"), "not-a-number", ["1"], None):
            with self.subTest(alias=repr(alias)[:20]):
                path.write_text(json.dumps({"phase": "running", "updated_at": alias}),
                                encoding="utf-8")
                snapshot = live_snapshot(self.ws.state, now=NOW)
                self.assertFalse(snapshot["worker_alive"])
                self.assertIsNone((snapshot["status"] or {}).get("written_at"))

        # A readable numeric alias still anchors the age, as before.
        path.write_text(json.dumps({"phase": "running", "updated_at": int(NOW)}),
                        encoding="utf-8")
        snapshot = live_snapshot(self.ws.state, now=NOW)
        self.assertTrue(snapshot["worker_alive"])
        self.assertEqual(snapshot["status"]["written_at"], int(NOW))

    def test_worker_alive_requires_an_active_phase_and_a_non_future_timestamp(self):
        self.ws.write_control(state="off", mode="shadow", updated_by="owner", now=NOW)

        # A freshly written terminal status is not a live worker, however recent it is.
        for phase in ("exited", "inert"):
            with self.subTest(phase=phase):
                write_status(self.ws.state, {"phase": phase}, now=NOW)
                self.assertFalse(live_snapshot(self.ws.state, now=NOW)["worker_alive"])
                self.assertFalse(live_snapshot(self.ws.state, now=NOW + 1)["worker_alive"])

        # starting/stopping are active phases with the regular bound.
        for phase in ("starting", "stopping"):
            with self.subTest(phase=phase):
                write_status(self.ws.state, {"phase": phase}, now=NOW)
                self.assertTrue(live_snapshot(self.ws.state, now=NOW)["worker_alive"])

        # A future timestamp is not freshness; neither is a missing/unknown phase.
        write_status(self.ws.state, {"phase": "running"}, now=NOW + 1)
        self.assertFalse(live_snapshot(self.ws.state, now=NOW)["worker_alive"])
        write_status(self.ws.state, {"phase": "mystery"}, now=NOW)
        self.assertFalse(live_snapshot(self.ws.state, now=NOW)["worker_alive"])
        # A malformed phase shape is not an active phase and must not raise.
        write_status(self.ws.state, {"phase": ["running"]}, now=NOW)
        malformed = live_snapshot(self.ws.state, now=NOW)
        self.assertEqual(malformed["status"]["phase"], ["running"])
        self.assertFalse(malformed["worker_alive"])

    def test_worker_alive_allows_the_bounded_retry_wait_for_a_running_worker(self):
        self.ws.write_control(state="off", mode="shadow", updated_by="owner", now=NOW)

        # next_wait(60, 4) is capped at 300 s, so a running status stays alive through a
        # 300 s retry plus the regular 180 s freshness window.
        write_status(self.ws.state, {"phase": "running", "consecutive_failures": 4}, now=NOW)
        self.assertTrue(live_snapshot(self.ws.state, now=NOW + 300)["worker_alive"])
        self.assertTrue(live_snapshot(self.ws.state, now=NOW + 479)["worker_alive"])
        self.assertFalse(live_snapshot(self.ws.state, now=NOW + 480)["worker_alive"])

        # A larger failure count stays bounded by the same 300 s cap.
        write_status(self.ws.state, {"phase": "running", "consecutive_failures": 99}, now=NOW)
        self.assertTrue(live_snapshot(self.ws.state, now=NOW + 479)["worker_alive"])
        self.assertFalse(live_snapshot(self.ws.state, now=NOW + 480)["worker_alive"])

        # next_wait(60, 3) is 240 s, not the cap.
        write_status(self.ws.state, {"phase": "running", "consecutive_failures": 3}, now=NOW)
        self.assertTrue(live_snapshot(self.ws.state, now=NOW + 419)["worker_alive"])
        self.assertFalse(live_snapshot(self.ws.state, now=NOW + 420)["worker_alive"])

        # Without retry information the regular 180 s crash detection is unchanged.
        write_status(self.ws.state, {"phase": "running"}, now=NOW)
        self.assertFalse(live_snapshot(self.ws.state, now=NOW + 300)["worker_alive"])

        # A terminal phase never borrows the retry window.
        write_status(self.ws.state, {"phase": "exited", "consecutive_failures": 99}, now=NOW)
        self.assertFalse(live_snapshot(self.ws.state, now=NOW)["worker_alive"])

    def test_a_malformed_status_file_is_reported_as_none(self):
        self.ws.write_control(state="on", mode="shadow", updated_by="owner", now=NOW)
        path = status_path(self.ws.state)
        path.write_text("{not json", encoding="utf-8")

        snapshot = live_snapshot(self.ws.state, now=NOW)

        self.assertIsNone(snapshot["status"])
        self.assertFalse(snapshot["worker_alive"])


class TestSetControl(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def test_off_needs_no_confirmation_and_writes_a_private_file(self):
        view = set_control(self.ws.state, state="off", mode="trade", confirm=None,
                           actor="alice", now=NOW)

        self.assertEqual(view["state"], "off")
        self.assertEqual(view["mode"], "trade")
        self.assertEqual(view["updated_by"], "webui:alice")
        path = live_layout(self.ws.state).control_path
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_shadow_needs_no_confirmation(self):
        view = set_control(self.ws.state, state="on", mode="shadow", confirm=None,
                           actor="alice", now=NOW)
        self.assertEqual(view["state"], "on")
        self.assertEqual(view["mode"], "shadow")

    def test_trade_without_the_typed_confirmation_is_refused_before_writing(self):
        with self.assertRaisesRegex(ValueError, "^confirm_required$"):
            set_control(self.ws.state, state="on", mode="trade", confirm=None,
                        actor="alice", now=NOW)
        self.assertFalse(live_layout(self.ws.state).control_path.exists())

        with self.assertRaisesRegex(ValueError, "^confirm_required$"):
            set_control(self.ws.state, state="on", mode="trade", confirm="trade",
                        actor="alice", now=NOW)
        self.assertFalse(live_layout(self.ws.state).control_path.exists())

    def test_trade_with_typed_confirmation_writes_the_webui_actor(self):
        view = set_control(self.ws.state, state="on", mode="trade", confirm="TRADE",
                           actor="alice", now=NOW)

        self.assertEqual(view["state"], "on")
        self.assertEqual(view["mode"], "trade")
        self.assertEqual(view["updated_by"], "webui:alice")
        path = live_layout(self.ws.state).control_path
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["state"], "on")

    def test_invalid_state_or_mode_is_a_value_error(self):
        for kwargs in (
            {"state": "maybe", "mode": "shadow", "confirm": None},
            {"state": "on", "mode": "monitor", "confirm": "TRADE"},
        ):
            with self.assertRaises(ValueError):
                set_control(self.ws.state, actor="alice", now=NOW, **kwargs)
        self.assertFalse(live_layout(self.ws.state).control_path.exists())


def _make_global(repo_root: Path) -> config.GlobalConfig:
    return config.load_global(repo_root)


class TestLiveHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.soren = self.repo_root / "soren"
        (self.soren / "tmp/state").mkdir(parents=True)
        (self.soren / "eloop_lib.sh").write_text("# x\n")
        self.g = _make_global(self.repo_root)
        # Do not let a developer's DOCICH_WEBUI_TOKEN* variables change these tests.
        self.g.webui.token_env = "DOCICH_TEST_WEBUI_LIVE_TOKEN"
        self.g.webui.read_only_token_env = "DOCICH_TEST_WEBUI_LIVE_READONLY_TOKEN"
        self.g.state_dir = self.repo_root / "run-live"
        self.g.state_dir.mkdir(exist_ok=True)
        self.server = _tracking_server_class()(("127.0.0.1", 0), webui._Handler)
        webui._Handler.g = self.g
        webui._Handler.soren_root = self.soren
        webui._Handler.read_only = False
        webui._Handler.start_time = time.time()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.csrf_token = self._fetch_csrf()

    def tearDown(self):
        self.client.close()
        stuck = _shutdown_test_server(self.server, self.thread)
        _assert_no_stuck_handlers(self, stuck)
        self.tmp.cleanup()

    def _fetch_csrf(self):
        self.client.request("GET", "/api/csrf")
        res = self.client.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        return data["csrf_token"]

    def _request(self, method, path, body=None, headers=None, csrf=True):
        hdrs = dict(headers or {})
        if method in ("POST", "PUT", "DELETE") and csrf:
            hdrs.setdefault("X-CSRF-Token", self.csrf_token)
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        self.client.request(method, path, body=body, headers=hdrs)
        res = self.client.getresponse()
        return res.status, json.loads(res.read().decode("utf-8"))

    def test_get_live_with_operator_auth(self):
        self.g.webui.token = "operator-secret"
        try:
            status, data = self._request(
                "GET", "/api/live", headers={"Authorization": "Bearer operator-secret"})
        finally:
            self.g.webui.token = ""
        self.assertEqual(status, 200, data)
        self.assertEqual(data["control"]["state"], "unknown")
        self.assertFalse(data["ledger_present"])

    def test_post_without_csrf_is_403(self):
        body = json.dumps({"state": "on", "mode": "shadow"}).encode("utf-8")
        self.client.request("POST", "/api/live/control", body=body,
                            headers={"Content-Type": "application/json"})
        res = self.client.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        self.assertEqual(res.status, 403, data)
        self.assertEqual(data["error"], "csrf_missing")
        self.assertFalse(live_layout(self.g.state_dir).control_path.exists())

    def test_viewer_identity_cannot_operate(self):
        self.g.webui.read_only_token = "viewer-secret"
        try:
            status, data = self._request(
                "POST", "/api/live/control", {"state": "off", "mode": "shadow"},
                headers={"Authorization": "Bearer viewer-secret"})
        finally:
            self.g.webui.read_only_token = ""
        self.assertEqual(status, 403, data)
        self.assertEqual(data["error"], "read_only")
        self.assertFalse(live_layout(self.g.state_dir).control_path.exists())

    def test_read_only_webui_cannot_operate(self):
        webui._Handler.read_only = True
        try:
            status, data = self._request(
                "POST", "/api/live/control", {"state": "off", "mode": "shadow"})
        finally:
            webui._Handler.read_only = False
        self.assertEqual(status, 403, data)
        self.assertEqual(data["error"], "read_only")
        self.assertFalse(live_layout(self.g.state_dir).control_path.exists())

    def test_ok_path_writes_control_and_the_next_get_reflects_it(self):
        status, data = self._request(
            "POST", "/api/live/control", {"state": "on", "mode": "shadow"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["state"], "on")
        self.assertEqual(data["mode"], "shadow")
        self.assertEqual(data["updated_by"], "webui:operator")

        path = live_layout(self.g.state_dir).control_path
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        status, snapshot = self._request("GET", "/api/live")
        self.assertEqual(status, 200, snapshot)
        self.assertEqual(snapshot["control"]["state"], "on")
        self.assertEqual(snapshot["control"]["mode"], "shadow")

        # The response never carries the control document or a filesystem path.
        encoded = json.dumps(data)
        self.assertNotIn("docich-live-control", encoded)
        self.assertNotIn("schema_version", encoded)
        self.assertNotIn("live_control.json", encoded)
        self.assertNotIn(str(self.g.state_dir), encoded)
        self.assertNotIn("docich-live-control", json.dumps(snapshot))

    def test_trade_requires_typed_confirmation(self):
        status, data = self._request(
            "POST", "/api/live/control", {"state": "on", "mode": "trade"})
        self.assertEqual(status, 400, data)
        self.assertEqual(data["error"], "confirm_required")
        self.assertFalse(live_layout(self.g.state_dir).control_path.exists())

        status, data = self._request(
            "POST", "/api/live/control",
            {"state": "on", "mode": "trade", "confirm": "TRADE"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["state"], "on")
        self.assertEqual(data["mode"], "trade")


class TestLiveFrontend(unittest.TestCase):
    def test_live_tab_panel_and_manifest_are_pinned(self):
        html = (ROOT / "src/docich/webui_resources/index.html").read_text(encoding="utf-8")
        nav = html.split('<nav id="tabs">', 1)[1].split("</nav>", 1)[0]
        self.assertLess(nav.index('data-tab="corners"'), nav.index('data-tab="live"'))
        self.assertLess(nav.index('data-tab="live"'), nav.index('data-tab="stream"'))
        self.assertIn('id="tab-live"', html)
        for element_id in (
            "live-state", "live-mode", "live-worker", "live-last-cycle", "live-warnings",
            "live-trips", "live-policy", "live-ledger", "live-on-shadow", "live-on-trade",
            "live-off", "live-trade-confirm", "live-refresh", "live-pnl-total",
            "live-pnl-round-trips", "live-pnl-win-rate", "live-pnl-symbols",
            "live-positions", "live-fills", "live-opening",
        ):
            self.assertIn(f'id="{element_id}"', html)

        panel = html.split("<!-- LIVE -->", 1)[1].split("<!-- STREAM -->", 1)[0]
        self.assertNotIn("alert(", panel)
        self.assertNotIn("confirm(", panel)
        script = html.split("// webui-live:start", 1)[1].split("// webui-live:end", 1)[0]
        self.assertNotIn("alert(", script)
        self.assertNotIn("confirm(", script)
        self.assertIn("function liveWorkerLabel", script)
        for label in ("起動中", "稼働中 (再試行待ち", "停止処理中",
                      "停止または応答なし", "終了 (code ", "待機 ("):
            self.assertIn(label, script)
        self.assertIn("ラウンドトリップと勝率", panel)
        self.assertIn("約定による純収支", panel)
        self.assertIn("未決済の買い", panel)
        self.assertNotIn("realized", panel)
        self.assertIn("pollGate(\"live\",loadLive)", html)
        self.assertIn('tab==="live"', html)

        resources = ROOT / "src/docich/webui_resources"
        manifest = json.loads((resources / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["files"]["index.html"],
            hashlib.sha256((resources / "index.html").read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
