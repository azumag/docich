"""WebUI LIVE snapshot, owner switch, and initial-policy setup (Issue #3623).

The snapshot tests use the real LIVE ledger/control/status files in a temp state dir; the
HTTP tests use the same real ``ThreadingHTTPServer`` fixture as ``tests/test_webui.py``.
No network, no sleeps.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, webui  # noqa: E402
from docich.trading.live import control as live_control  # noqa: E402
from docich.trading.live import initial as live_initial  # noqa: E402
from docich.trading.live import lease as live_lease  # noqa: E402
from docich.trading.live import policy as live_policy  # noqa: E402
from docich.trading.live.canonical import canonical_json  # noqa: E402
from docich.trading.live.ledger import LiveLedger  # noqa: E402
from docich.trading.live.paths import live_layout  # noqa: E402
from docich.trading.strategy_lab import experiment_from_mapping  # noqa: E402
from docich.trading.live_worker.status import (  # noqa: E402
    ALLOWED_KEYS, status_path, write_status,
)
from docich.webui_live import live_snapshot, set_control  # noqa: E402

from _webui_http_fixture import (  # noqa: E402
    assert_no_stuck_handlers as _assert_no_stuck_handlers,
    shutdown_test_server as _shutdown_test_server,
    tracking_server_class as _tracking_server_class,
)
from test_trading_live_support import Workspace, synthetic_mapping  # noqa: E402

NOW = 1_800_000_000.0
ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_KEYS = {"control", "status", "worker_alive", "ledger_present", "overview"}
_CONFIRM_UNSET = object()


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


INITIAL_ID = "snapback_harvest_v25"


def _initial_mapping(**changes):
    mapping = synthetic_mapping()
    mapping["experiment_id"] = INITIAL_ID
    mapping.update(changes)
    return mapping


def _initial_spec(**changes):
    return experiment_from_mapping(_initial_mapping(**changes))


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

    # ---- initial policy setup -------------------------------------------------
    def _history(self, entries):
        trading = self.g.state_dir / "trading"
        trading.mkdir(parents=True, exist_ok=True)
        path = trading / "paper_strategy_evaluation_history.json"
        path.write_text(json.dumps({"schema_version": 1, "entries": entries}), encoding="utf-8")
        return path

    def _initial_history(self, mapping=None):
        mapping = _initial_mapping() if mapping is None else mapping
        return self._history([{"experiment_id": INITIAL_ID, "experiment": mapping}])

    def _state_bytes(self):
        return {str(path.relative_to(self.g.state_dir)): path.read_bytes()
                for path in self.g.state_dir.rglob("*") if path.is_file()}

    def _preview_initial(self, *, source="history", snapshot=None, digest=None):
        body = {"source": source, "operator": "owner", "note": "initial setup"}
        if snapshot is not None:
            body["snapshot"] = snapshot
        if digest is not None:
            body["experiment_digest"] = digest
        return self._request("POST", "/api/live/initial-policy/preview", body)

    def _confirm_initial(self, preview, *, source="history", snapshot=None, digest=None,
                         confirm=_CONFIRM_UNSET):
        body = {"source": source, "operator": "owner", "note": "initial setup",
                "confirm": preview["token"] if confirm is _CONFIRM_UNSET else confirm}
        if snapshot is not None:
            body["snapshot"] = snapshot
        if digest is not None:
            body["experiment_digest"] = digest
        return self._request("POST", "/api/live/initial-policy/activate", body)

    def _activation_rows(self):
        ledger = LiveLedger.open_readonly(live_layout(self.g.state_dir))
        try:
            with ledger.read_cursor() as cur:
                return cur.execute("SELECT reason FROM policy_activations ORDER BY activation_id").fetchall()
        finally:
            ledger.close()

    def _ledger_counts(self):
        ledger = LiveLedger.open_readonly(live_layout(self.g.state_dir))
        try:
            with ledger.read_cursor() as cur:
                return {
                    "activations": cur.execute("SELECT COUNT(*) FROM policy_activations").fetchone()[0],
                    "events": cur.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    "policy_sets": cur.execute("SELECT COUNT(*) FROM policy_sets").fetchone()[0],
                }
        finally:
            ledger.close()

    def _import_candidate(self, mapping):
        spec = experiment_from_mapping(mapping)
        return (canonical_json(live_policy.experiment_payload(spec)).decode(),
                live_policy.experiment_digest(spec))

    def test_initial_export_is_canonical_and_excludes_activated_at(self):
        mapping = _initial_mapping(activated_at=123.0)
        self._history([
            {"experiment_id": "newer_v99", "experiment": synthetic_mapping()},
            {"experiment_id": INITIAL_ID, "experiment": mapping},
        ])
        spec = experiment_from_mapping(mapping)
        status, data = self._request("GET", "/api/live/initial-policy/export")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["experiment_id"], INITIAL_ID)
        self.assertEqual(data["experiment_digest"], live_policy.experiment_digest(spec))
        self.assertEqual(len(data["experiment_digest"]), 64)
        self.assertEqual(data["symbols"], list(live_policy.INITIAL_SYMBOLS))
        self.assertEqual(json.loads(data["snapshot"]), live_policy.experiment_payload(spec))
        self.assertEqual(data["snapshot"], canonical_json(live_policy.experiment_payload(spec)).decode())
        self.assertNotIn("activated_at", data["snapshot"])
        self.assertFalse((self.g.state_dir / "trading-live").exists())

    def test_initial_preview_and_confirm_activate_without_control_change(self):
        self._initial_history()
        live_control.write_control(self.g.state_dir, state="off", mode="shadow",
                                   updated_by="owner", now=NOW)
        control_path = live_layout(self.g.state_dir).control_path
        before = control_path.read_bytes()
        status, preview = self._preview_initial()
        self.assertEqual(status, 200, preview)
        self.assertRegex(preview["token"], r"^[0-9a-f]{10}$")
        self.assertFalse(preview["already_active"])
        self.assertFalse(live_layout(self.g.state_dir).ledger_path.exists())
        status, result = self._confirm_initial(preview)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["activated"])
        self.assertEqual(result["reason"], "owner_initial")
        spec = _initial_spec()
        ps = live_policy.build_policy_set(spec, live_policy.INITIAL_SYMBOLS)
        self.assertEqual(result["policy_set_digest"], ps.digest)
        self.assertEqual(self._activation_rows(), [("owner_initial",)])
        self.assertEqual(control_path.read_bytes(), before)
        self.assertFalse(live_control.describe_control(self.g.state_dir, now=NOW)["state"] == "on")
        self.assertFalse((live_layout(self.g.state_dir).root / "live_status.json").exists())

    def test_initial_repeat_same_policy_is_a_safe_no_op(self):
        self._initial_history()
        _, preview = self._preview_initial()
        status, _ = self._confirm_initial(preview)
        self.assertEqual(status, 200)
        _, again = self._preview_initial()
        self.assertTrue(again["already_active"])
        status, result = self._confirm_initial(again)
        self.assertEqual(status, 200, result)
        self.assertFalse(result["activated"])
        self.assertTrue(result["already_active"])
        self.assertEqual(self._activation_rows(), [("owner_initial",)])

    def test_initial_lost_response_retry_after_a_new_ledger_is_already_active(self):
        self._initial_history()
        status, preview = self._preview_initial()
        self.assertEqual(status, 200, preview)
        self.assertFalse(preview["ledger_present"])
        status, first = self._confirm_initial(preview)
        self.assertEqual(status, 200, first)
        self.assertTrue(first["activated"])
        counts = self._ledger_counts()
        # The same token was previewed before the ledger existed.  The first success
        # changed both the ledger id and the active digest; the lost response/second
        # click must still be an idempotent success with no extra write.
        status, retry = self._confirm_initial(preview)
        self.assertEqual(status, 200, retry)
        self.assertFalse(retry["activated"])
        self.assertTrue(retry["already_active"])
        self.assertEqual(self._ledger_counts(), counts)
        status, data = self._confirm_initial(preview, confirm="0" * 10)
        self.assertEqual((status, data["error"]), (409, "stale_preview"))
        self.assertEqual(self._ledger_counts(), counts)

    def test_initial_retry_after_an_existing_empty_ledger_is_already_active(self):
        self._initial_history()
        layout = live_layout(self.g.state_dir)
        LiveLedger.open(layout, paper_dir=self.g.state_dir / "trading", now=NOW).close()
        status, preview = self._preview_initial()
        self.assertEqual(status, 200, preview)
        self.assertTrue(preview["ledger_present"])
        status, first = self._confirm_initial(preview)
        self.assertEqual(status, 200, first)
        counts = self._ledger_counts()
        status, retry = self._confirm_initial(preview)
        self.assertEqual(status, 200, retry)
        self.assertTrue(retry["already_active"])
        self.assertFalse(retry["activated"])
        self.assertEqual(self._ledger_counts(), counts)

    def test_initial_stale_preview_after_an_empty_ledger_appears(self):
        self._initial_history()
        _, preview = self._preview_initial()
        layout = live_layout(self.g.state_dir)
        LiveLedger.open(layout, paper_dir=self.g.state_dir / "trading", now=NOW).close()
        status, data = self._confirm_initial(preview)
        self.assertEqual((status, data["error"]), (409, "stale_preview"))
        self.assertEqual(self._activation_rows(), [])
        status, fresh = self._preview_initial()
        self.assertEqual(status, 200, fresh)
        self.assertTrue(fresh["ledger_present"])
        status, result = self._confirm_initial(fresh)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["activated"])

    def test_initial_rejects_non_string_fields_and_non_ascii_tokens(self):
        self._initial_history()
        cases = (
            ({"source": "history", "operator": 123, "note": "n"}, "operator_invalid"),
            ({"source": "history", "operator": ["owner"], "note": "n"}, "operator_invalid"),
            ({"source": "history", "operator": "owner", "note": 123}, "note_invalid"),
            ({"source": "history", "operator": "owner", "note": {"text": "n"}}, "note_invalid"),
        )
        for body, code in cases:
            status, data = self._request("POST", "/api/live/initial-policy/preview", body)
            self.assertEqual((status, data["error"]), (400, code), body)
        status, data = self._preview_initial(source="import", snapshot=123, digest="0" * 64)
        self.assertEqual((status, data["error"]), (400, "snapshot_invalid"))
        status, data = self._preview_initial(source="import", snapshot="{}", digest=123)
        self.assertEqual((status, data["error"]), (400, "digest_invalid"))
        _, preview = self._preview_initial()
        for bad in ("0" * 9, "0" * 11, "G" * 10, "é" * 10, 123, None, ["0" * 10]):
            status, data = self._confirm_initial(preview, confirm=bad)
            self.assertEqual((status, data["error"]), (409, "stale_preview"), bad)
        self.assertFalse(live_layout(self.g.state_dir).ledger_path.exists())
        status, result = self._confirm_initial(preview)
        self.assertEqual(status, 200, result)

    def test_initial_import_fallback_must_match_the_history_digest(self):
        approved = _initial_mapping(thesis="owner approved")
        self._initial_history(approved)
        approved_snapshot, approved_digest = self._import_candidate(approved)
        different = _initial_mapping(thesis="different proposal")
        different_snapshot, different_digest = self._import_candidate(different)
        status, data = self._preview_initial(source="import", snapshot=different_snapshot,
                                             digest=different_digest)
        self.assertEqual((status, data["error"]), (409, "import_mismatch"))
        status, data = self._preview_initial(source="import", snapshot=approved_snapshot,
                                             digest=approved_digest)
        self.assertEqual(status, 200, data)
        # Corrupt or ambiguous history must not be bypassed by the import fallback.
        history = self.g.state_dir / "trading" / "paper_strategy_evaluation_history.json"
        history.write_text("{not-json", encoding="utf-8")
        status, data = self._preview_initial(source="import", snapshot=approved_snapshot,
                                             digest=approved_digest)
        self.assertEqual((status, data["error"]), (400, "history_invalid"))
        self._history([
            {"experiment_id": INITIAL_ID, "experiment": approved},
            {"experiment_id": INITIAL_ID, "experiment": different},
        ])
        status, data = self._preview_initial(source="import", snapshot=approved_snapshot,
                                             digest=approved_digest)
        self.assertEqual((status, data["error"]), (409, "candidate_ambiguous"))
        self.assertFalse(live_layout(self.g.state_dir).ledger_path.exists())

    def test_initial_downloaded_snapshot_and_separate_digest_round_trip_via_import(self):
        self._initial_history()
        status, exported = self._request("GET", "/api/live/initial-policy/export")
        self.assertEqual(status, 200, exported)
        download = Path(self.tmp.name) / "downloaded-initial.json"
        download.write_text(exported["snapshot"], encoding="utf-8")
        downloaded = download.read_text(encoding="utf-8")
        parsed = json.loads(downloaded)
        self.assertEqual(parsed, live_policy.experiment_payload(_initial_spec()))
        self.assertNotIn("snapshot", parsed)  # the downloaded file is the snapshot itself
        history = self.g.state_dir / "trading" / "paper_strategy_evaluation_history.json"
        history.unlink()
        status, preview = self._preview_initial(source="import", snapshot=downloaded,
                                                digest=exported["experiment_digest"])
        self.assertEqual(status, 200, preview)
        status, result = self._confirm_initial(preview, source="import", snapshot=downloaded,
                                               digest=exported["experiment_digest"])
        self.assertEqual(status, 200, result)
        self.assertTrue(result["activated"])
        counts = self._ledger_counts()
        status, retry = self._confirm_initial(preview, source="import", snapshot=downloaded,
                                              digest=exported["experiment_digest"])
        self.assertEqual(status, 200, retry)
        self.assertTrue(retry["already_active"])
        self.assertFalse(retry["activated"])
        self.assertEqual(self._ledger_counts(), counts)

    def test_initial_concurrent_different_imports_never_overwrite(self):
        first = _initial_mapping(thesis="contender A")
        second = _initial_mapping(thesis="contender B")
        first_snapshot, first_digest = self._import_candidate(first)
        second_snapshot, second_digest = self._import_candidate(second)
        status, first_preview = self._preview_initial(source="import", snapshot=first_snapshot,
                                                      digest=first_digest)
        self.assertEqual(status, 200, first_preview)
        status, second_preview = self._preview_initial(source="import", snapshot=second_snapshot,
                                                       digest=second_digest)
        self.assertEqual(status, 200, second_preview)
        barrier = threading.Barrier(2)

        def contender(preview, snapshot, digest):
            barrier.wait(timeout=5)
            try:
                return live_initial.activate_policy(
                    self.g.state_dir, source="import", snapshot=snapshot,
                    experiment_digest=digest, operator="owner", note="initial setup",
                    confirm=preview["token"], now=NOW)
            except live_initial.InitialSetupError as exc:
                return exc.code

        previous_umask = os.umask(0o022)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(contender, first_preview, first_snapshot, first_digest),
                           pool.submit(contender, second_preview, second_snapshot, second_digest)]
                results = [future.result(timeout=10) for future in futures]
        finally:
            os.umask(previous_umask)
        winners = [result for result in results
                   if isinstance(result, dict) and result.get("activated")]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(self._activation_rows(), [("owner_initial",)])
        ledger = LiveLedger.open_readonly(live_layout(self.g.state_dir))
        try:
            active = ledger.active_policy_set_digest()
        finally:
            ledger.close()
        self.assertEqual(active, winners[0]["policy_set_digest"])
        for result in results:
            if isinstance(result, str):
                self.assertIn(result, {"already_active", "active_policy_conflict",
                                       "lease_held", "stale_preview",
                                       "ledger_unavailable", "activation_refused"}, results)

    def test_initial_mutations_enforce_csrf_origin_and_operator_identity(self):
        self._initial_history()
        _, preview = self._preview_initial()
        preview_body = {"source": "history", "operator": "owner", "note": "initial setup"}
        activate_body = {**preview_body, "confirm": preview["token"]}
        for path, body in (("/api/live/initial-policy/preview", preview_body),
                           ("/api/live/initial-policy/activate", activate_body)):
            status, data = self._request("POST", path, body,
                                         headers={"Origin": "https://evil.example"})
            self.assertEqual((status, data["error"]), (403, "invalid_origin"), data)
            self.assertNotIn(str(self.g.state_dir), json.dumps(data))
        status, data = self._request("POST", "/api/live/initial-policy/activate",
                                     activate_body, csrf=False)
        self.assertEqual((status, data["error"]), (403, "csrf_missing"), data)
        self.g.webui.read_only_token = "viewer-secret"
        try:
            status, data = self._request(
                "POST", "/api/live/initial-policy/activate", activate_body,
                headers={"Authorization": "Bearer viewer-secret"})
        finally:
            self.g.webui.read_only_token = ""
        self.assertEqual((status, data["error"]), (403, "read_only"), data)
        self.assertFalse(live_layout(self.g.state_dir).ledger_path.exists())

    def test_initial_reads_only_the_configured_paper_dir_and_rejects_symlinks(self):
        wrong = self.g.state_dir / "paper_strategy_evaluation_history.json"
        wrong.write_text(json.dumps({"schema_version": 1, "entries": [
            {"experiment_id": INITIAL_ID, "experiment": _initial_mapping()}]}), encoding="utf-8")
        status, data = self._request("GET", "/api/live/initial-policy/export")
        self.assertEqual((status, data["error"]), (404, "history_missing"))
        self.assertFalse((self.g.state_dir / "trading").exists())
        self.assertFalse((self.g.state_dir / "trading-live").exists())
        real = Path(self.tmp.name) / "real-paper"
        real.mkdir()
        (self.g.state_dir / "trading").mkdir()
        (self.g.state_dir / "trading").rmdir()
        (self.g.state_dir / "trading").symlink_to(real, target_is_directory=True)
        status, data = self._request("GET", "/api/live/initial-policy/export")
        self.assertEqual((status, data["error"]), (400, "history_unreadable"))

    def test_initial_stale_preview_is_refused_after_the_candidate_changes(self):
        self._initial_history()
        _, preview = self._preview_initial()
        status, data = self._confirm_initial(preview, confirm="0" * 10)
        self.assertEqual((status, data["error"]), (409, "stale_preview"))
        changed = _initial_mapping(thesis="changed after the operator preview")
        self._initial_history(changed)
        status, data = self._confirm_initial(preview)
        self.assertEqual(status, 409, data)
        self.assertEqual(data["error"], "stale_preview")
        self.assertFalse(live_layout(self.g.state_dir).ledger_path.exists())

    def test_initial_missing_unsupported_ambiguous_and_invalid_are_refused(self):
        status, data = self._request("GET", "/api/live/initial-policy/export")
        self.assertEqual((status, data["error"]), (404, "history_missing"))

        self._history([])
        status, data = self._request("GET", "/api/live/initial-policy/export")
        self.assertEqual((status, data["error"]), (404, "candidate_missing"))

        self._history([{"experiment_id": "synthetic_v1", "experiment": synthetic_mapping()}])
        status, data = self._request("GET", "/api/live/initial-policy/export")
        self.assertEqual((status, data["error"]), (404, "candidate_unsupported"))

        self._history([
            {"experiment_id": INITIAL_ID, "experiment": _initial_mapping()},
            {"experiment_id": INITIAL_ID, "experiment": _initial_mapping(thesis="another winner")},
        ])
        status, data = self._request("GET", "/api/live/initial-policy/export")
        self.assertEqual((status, data["error"]), (409, "candidate_ambiguous"))

        broken = _initial_mapping()
        broken["unknown_key"] = 1
        self._history([{"experiment_id": INITIAL_ID, "experiment": broken}])
        status, data = self._request("GET", "/api/live/initial-policy/export")
        self.assertEqual((status, data["error"]), (400, "candidate_invalid"))

    def test_initial_import_uses_the_supplied_digest_and_checks_the_id(self):
        spec = _initial_spec(activated_at=999.0)
        snapshot = canonical_json(live_policy.experiment_payload(spec)).decode()
        digest = live_policy.experiment_digest(spec)
        status, preview = self._preview_initial(source="import", snapshot=snapshot, digest=digest)
        self.assertEqual(status, 200, preview)
        status, result = self._confirm_initial(preview, source="import", snapshot=snapshot, digest=digest)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["activated"])
        self.assertEqual(self._activation_rows(), [("owner_initial",)])

        wrong_id = synthetic_mapping()
        status, data = self._preview_initial(source="import", snapshot=json.dumps(wrong_id),
                                             digest=live_policy.experiment_digest(experiment_from_mapping(wrong_id)))
        self.assertEqual((status, data["error"]), (400, "candidate_unsupported"))
        status, data = self._preview_initial(source="import", snapshot=snapshot, digest="0" * 64)
        self.assertEqual((status, data["error"]), (400, "digest_mismatch"))
        status, data = self._preview_initial(source="import", snapshot=snapshot, digest=digest.upper())
        self.assertEqual((status, data["error"]), (400, "digest_invalid"))
        status, data = self._preview_initial(source="import", snapshot=" " * (live_initial.MAX_SNAPSHOT_BYTES + 1),
                                             digest=digest)
        self.assertEqual((status, data["error"]), (400, "snapshot_too_large"))
        broken = _initial_mapping()
        broken["unknown_key"] = 1
        status, data = self._preview_initial(source="import", snapshot=json.dumps(broken), digest=digest)
        self.assertEqual((status, data["error"]), (400, "snapshot_invalid"))

    def test_initial_existing_different_active_policy_is_not_overwritten(self):
        self._initial_history()
        _, preview = self._preview_initial()
        other_spec = experiment_from_mapping({"schema_version": 1, "experiment_id": "other_v1",
                                              "name": "other", "thesis": "other",
                                              "entry_rules": synthetic_mapping()["entry_rules"],
                                              "exit_rules": synthetic_mapping()["exit_rules"],
                                              "max_pair_correlation": "0.35", "activated_at": 1.0})
        other = live_policy.build_policy_set(other_spec, live_policy.INITIAL_SYMBOLS)
        layout = live_layout(self.g.state_dir)
        ledger = LiveLedger.open(layout, paper_dir=self.g.state_dir / "trading", now=NOW)
        writer = live_lease.acquire_writer_lease(ledger, layout, now=NOW)
        try:
            ledger.record_owner_activation(writer, other, operator="owner", note="existing", now=NOW)
        finally:
            live_lease.release_writer_lease(ledger, writer, now=NOW)
            ledger.close()
        status, data = self._confirm_initial(preview)
        self.assertEqual(status, 409, data)
        self.assertEqual(data["error"], "stale_preview")
        status, data = self._preview_initial()
        self.assertEqual(status, 409, data)
        self.assertEqual(data["error"], "active_policy_conflict")
        self.assertEqual(self._activation_rows(), [("owner_initial",)])
        ledger = LiveLedger.open_readonly(layout)
        try:
            self.assertEqual(ledger.active_policy_set_digest(), other.digest)
        finally:
            ledger.close()

    def test_initial_activation_refuses_a_writer_lease(self):
        self._initial_history()
        layout = live_layout(self.g.state_dir)
        ledger = LiveLedger.open(layout, paper_dir=self.g.state_dir / "trading", now=NOW)
        try:
            _, preview = self._preview_initial()
            writer = live_lease.acquire_writer_lease(ledger, layout, now=NOW)
            try:
                status, data = self._confirm_initial(preview)
            finally:
                live_lease.release_writer_lease(ledger, writer, now=NOW)
            self.assertEqual(status, 409, data)
            self.assertEqual(data["error"], "lease_held")
            with ledger.read_cursor() as cur:
                self.assertEqual(cur.execute("SELECT COUNT(*) FROM policy_activations").fetchone()[0], 0)
        finally:
            ledger.close()

    def test_initial_preview_requires_csrf_and_writes_nothing(self):
        self._initial_history()
        before = self._state_bytes()
        self.client.request("POST", "/api/live/initial-policy/preview",
                            body=json.dumps({"source": "history", "operator": "owner", "note": "n"}).encode(),
                            headers={"Content-Type": "application/json"})
        res = self.client.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        self.assertEqual(res.status, 403, data)
        self.assertEqual(data["error"], "csrf_missing")
        self.assertEqual(self._state_bytes(), before)

    def test_initial_viewer_is_blocked_and_reads_do_not_write(self):
        before = self._state_bytes()
        self.g.webui.read_only_token = "viewer-secret"
        try:
            status, data = self._request("GET", "/api/live/initial-policy/export",
                                         headers={"Authorization": "Bearer viewer-secret"})
            self.assertEqual(status, 403, data)
            status, data = self._request("POST", "/api/live/initial-policy/preview",
                                         {"source": "history", "operator": "owner", "note": "n"},
                                         headers={"Authorization": "Bearer viewer-secret"})
            self.assertEqual(status, 403, data)
        finally:
            self.g.webui.read_only_token = ""
        self.assertEqual(self._state_bytes(), before)

        self._initial_history()
        before = self._state_bytes()
        status, data = self._request("GET", "/api/live/initial-policy/export")
        self.assertEqual(status, 200, data)
        status, data = self._request("POST", "/api/live/initial-policy/preview",
                                     {"source": "history", "operator": "owner", "note": "initial setup"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self._state_bytes(), before)
        self.assertFalse((self.g.state_dir / "trading-live").exists())

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
            "live-initial-load", "live-initial-download", "live-initial-preview",
            "live-initial-import-json", "live-initial-import-digest",
            "live-initial-import-preview", "live-initial-confirm",
            "live-initial-experiment", "live-initial-digest", "live-initial-policy-digest",
            "live-initial-active", "live-initial-json", "live-initial-operator",
            "live-initial-note", "live-initial-msg",
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
        self.assertIn("activeDigest", script)
        self.assertNotIn("activePrefix", script)
        self.assertIn("confirmBtn.disabled=true", script)
        self.assertIn("import_mismatch", script)
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
