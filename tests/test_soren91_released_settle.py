"""#490: the released automatic NetHack slot can retire its own failed owner.

Production 2026-10-16 (slot 263): the sorengame->nethack start switch died
on the Soren round-boundary deadline, the common rotation released the slot
as ``released-unverified`` after proving the shared program slot free, and
nothing ever retired ``nethack_corner.json``. ``run_rotation`` then refused
every later NetHack dispatch with "rotation execution owner mismatch" while
``nethack-corner recover-failed-rotation`` only accepts the
``recovery_required`` latch, so the corner left the rotation permanently.

These cases pin the manager side of the #2352 settle widening: the released
NetHack owner is terminalized only on the exact released request with a
terminal prelaunch receipt, a stable canonical that never switched to
NetHack, and the durable rotation release record for this corner.
"""

import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.soren91_corner import (  # noqa: E402
    Soren91CornerManager,
    load_soren91_corner_config,
)
from docich.retro_corner import RetroCornerError  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


def _succeeded():
    return SimpleNamespace(status="succeeded", error_code=None, detail=None)


class FakeCoordinator:
    def __init__(self, current):
        self.current = current
        self.calls = []

    def start(self, game):
        self.calls.append(("start", game))
        self.current[0] = game
        return _succeeded()

    def switch(self, game):
        self.calls.append(("switch", game))
        self.current[0] = game
        return _succeeded()

    def stop(self):
        self.calls.append(("stop", None))
        self.current[0] = None
        return _succeeded()


class SentinelSwitchCoordinator(FakeCoordinator):
    """Raises after the dispatch passed the owner gate, ending the begin."""

    def switch(self, game):
        self.calls.append(("switch", game))
        raise RuntimeError("sentinel-begin-reached")


class Soren91ReleasedSettleTestBase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "config" / "games").mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            """
[paths]
state_dir = "run"
games_dir = "config/games"

[soren91_corner]
enabled = true
start_hour = 21
duration_minutes = 30
timezone = "Asia/Tokyo"
""",
            encoding="utf-8",
        )
        (self.root / "config" / "games" / "soren91.toml").write_text(
            """
[game]
name = "soren91"
title = "Soren91"
adapter = "soren91"
[agent]
enabled = false
""",
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        self.cfg = load_soren91_corner_config(self.g)
        self.now_value = datetime(2026, 9, 6, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    def tearDown(self):
        self.tempdir.cleanup()

    def manager(self, current, **kwargs):
        coordinator = kwargs.pop("coordinator", FakeCoordinator(current))
        defaults = {
            "config": self.cfg,
            "coordinator": coordinator,
            "now": lambda: self.now_value,
            "sleep": lambda seconds: None,
            "active_game_reader": lambda: current[0],
            "ensure_runtime": lambda: None,
        }
        defaults.update(kwargs)
        return Soren91CornerManager(self.g, **defaults), coordinator

    @staticmethod
    def _runtime(game, generation):
        names = runtime_names(generation)
        return {
            "game": game, "adapter": "cli", "generation": generation,
            "runtime_id": f"g{generation}-abcdef", "lease_id": str(uuid.uuid4()),
            "game_window": names.game_window, "agent_window": names.agent_window,
            "adapter_session": names.adapter_session,
            "started_at": "2026-10-16T12:00:00Z",
        }

    def _setup_released_failed_start(self, *, state_error_code=None,
                                     receipt_error="timeout"):
        """Model the production slot: deadline on the Soren boundary wait.

        The switch never made NetHack active, so the outgoing Soren runtime
        stayed alive and the request finished its own receipt as terminal
        ``failed``. The corner wrapped the raw deadline exception, which can
        lose ``last_error_code`` — both that shape and the explicit ``timeout``
        code must settle.
        """
        import shutil
        shutil.rmtree(self.g.state_dir, ignore_errors=True)
        mgr, coordinator = self.manager(["sorengame"])
        request_id = str(uuid.uuid4())
        state = mgr._default_state()
        state.update(
            status="failed",
            game="soren91",
            previous_game="sorengame",
            rotation_request_id=request_id,
            switch_request_id=request_id,
            started_at=self.now_value.isoformat(),
            completed_at=self.now_value.isoformat(),
            last_error=("sorengame->soren91 switch に失敗しました: "
                        "Soren lifecycle call のdeadlineを超過しました"),
            last_error_code=state_error_code,
        )
        mgr._write_state(state)
        canonical = mgr.store.initialize()
        canonical.update(phase="ready", next_generation=2, operation=None,
                         request_id=None, deadline_at=None,
                         active=self._runtime("sorengame", 1))
        mgr.store.canonical.save(canonical)
        accepted = mgr.store.accept_request(request_id, "switch", "soren91")
        result = {
            "request_id": request_id, "operation": "switch", "status": "failed",
            "from_game": "sorengame", "to_game": "soren91",
            "generation": accepted.generation, "error_code": receipt_error,
            "detail": "Soren lifecycle call のdeadlineを超過しました",
        }
        mgr.store.finish_request(request_id, "failed", result)
        canonical, _ = mgr.store.canonical.load()
        canonical.update(
            phase="ready", next_generation=max(accepted.generation + 1, 8),
            operation=None, request_id=None, deadline_at=None,
            active=self._runtime("sorengame", 1), candidate=None,
            previous=None, retiring=[], last_result=result,
        )
        mgr.store.canonical.save(canonical)
        return mgr, coordinator, request_id

    def _released_ledger(self, mgr, request_id, *, corner="meriken",
                         adapter="meriken", game="soren91",
                         pointer_request=None, audit_resolution="released-unverified",
                         known=True, released=True, audit=True):
        """Write the durable rotation ledger the settle must re-read (#2352)."""
        ledger = {
            "schema": 1,
            "status": "waiting",
            "known_corners": (
                {corner: {"id": corner, "adapter": adapter, "game": game}}
                if known else {}
            ),
            "released_requests": (
                {corner: {"request_id": pointer_request or request_id, "at": 1.0}}
                if released else {}
            ),
            "failed_corners": (
                [{"corner": corner, "request_id": request_id, "at": 1.0,
                  "resolution": audit_resolution,
                  "reason_code": "failed-corner-slot-released",
                  "error_kind": "execution-error"}]
                if audit else []
            ),
        }
        (Path(mgr.g.state_dir) / "corner_rotation.json").write_text(
            json.dumps(ledger), encoding="utf-8")
        return ledger


class TestSoren91ReleasedSettle(Soren91ReleasedSettleTestBase):
    def test_settle_terminalizes_the_exact_released_nethack_request(self):
        # The production shape: the deadline lost the corner's error code and
        # only the receipt names the failure.
        mgr, coordinator, request_id = self._setup_released_failed_start()
        self._released_ledger(mgr, request_id)

        self.assertIs(mgr.settle_released_failure(request_id, corner_id="meriken"), True)

        state = mgr._read_state()
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["end_reason"], "switch-terminal-before-corner-active")
        self.assertIsNone(state["last_error"])
        self.assertIsNone(state["last_error_code"])
        self.assertNotIn("switch_status", state)
        self.assertEqual(coordinator.calls, [])

    def test_settle_accepts_the_explicit_timeout_code_shape(self):
        mgr, _coordinator, request_id = self._setup_released_failed_start(
            state_error_code="timeout")
        self._released_ledger(mgr, request_id)

        self.assertIs(mgr.settle_released_failure(request_id, corner_id="meriken"), True)
        self.assertEqual(mgr._read_state()["status"], "interrupted")

    def test_settle_uses_the_audit_row_when_the_pointer_moved_on(self):
        # The per-corner pointer already names the NEXT request; only the
        # bounded audit row still proves the original request's release.
        mgr, _coordinator, request_id = self._setup_released_failed_start()
        self._released_ledger(mgr, request_id, pointer_request=str(uuid.uuid4()))

        self.assertIs(mgr.settle_released_failure(request_id, corner_id="meriken"), True)
        self.assertEqual(mgr._read_state()["status"], "interrupted")

    def test_settle_rejects_a_newer_requests_release_record(self):
        # A release record naming the NEW request never retires the OLDER
        # NetHack owner that still holds the state file.
        mgr, _coordinator, request_id = self._setup_released_failed_start()
        newer = str(uuid.uuid4())
        self._released_ledger(mgr, request_id, pointer_request=newer, audit=False)
        before = mgr._read_state()

        self.assertIs(mgr.settle_released_failure(newer, corner_id="meriken"), False)
        self.assertIs(mgr.settle_released_failure(request_id, corner_id="meriken"), False)

        self.assertEqual(mgr._read_state(), before)

    def test_settle_refuses_without_a_release_record(self):
        mgr, _coordinator, request_id = self._setup_released_failed_start()
        self._released_ledger(mgr, request_id, released=False, audit=False)
        before = mgr._read_state()

        self.assertIs(mgr.settle_released_failure(request_id, corner_id="meriken"), False)

        self.assertEqual(mgr._read_state(), before)

    def test_settle_refuses_a_foreign_adapter_release_record(self):
        # A ``game`` record never becomes permission for the NetHack owner:
        # each manager family settles only the adapters it owns.
        mgr, _coordinator, request_id = self._setup_released_failed_start()
        self._released_ledger(mgr, request_id, adapter="game")
        before = mgr._read_state()

        self.assertIs(mgr.settle_released_failure(request_id, corner_id="meriken"), False)

        self.assertEqual(mgr._read_state(), before)

    def test_settle_refuses_another_released_request(self):
        mgr, _coordinator, _request_id = self._setup_released_failed_start()
        before = mgr._read_state()

        self.assertIs(
            mgr.settle_released_failure(str(uuid.uuid4()), corner_id="meriken"), False)

        self.assertEqual(mgr._read_state(), before)

    def test_settle_never_touches_a_live_owner(self):
        mgr, _coordinator, request_id = self._setup_released_failed_start()
        self._released_ledger(mgr, request_id)
        state = mgr._read_state()
        state["status"] = "starting"
        mgr._write_state(state)

        self.assertIs(mgr.settle_released_failure(request_id, corner_id="meriken"), False)

        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_settled_owner_accepts_the_next_rotation_dispatch(self):
        # Before the settle this raised "rotation execution owner mismatch"
        # forever; after it, the next dispatch passes the owner gate and only
        # the (injected) begin failure stops it.
        mgr, _coordinator, request_id = self._setup_released_failed_start()
        self._released_ledger(mgr, request_id)
        self.assertIs(mgr.settle_released_failure(request_id, corner_id="meriken"), True)

        current = ["sorengame"]
        sentinel = SentinelSwitchCoordinator(current)
        mgr2, _coordinator2 = self.manager(
            current, coordinator=sentinel, active_game_reader=lambda: current[0])
        next_request = str(uuid.uuid4())

        with self.assertRaises(RetroCornerError) as caught:
            mgr2.run_rotation(next_request)

        self.assertNotIn("owner mismatch", str(caught.exception))
        self.assertIn("sentinel-begin-reached", str(caught.exception))
        self.assertEqual(sentinel.calls, [("switch", "soren91")])
        state = mgr2._read_state()
        self.assertEqual(state["rotation_request_id"], next_request)
        self.assertIn("sentinel-begin-reached", state["last_error"])

    def test_unreleased_owner_still_refuses_the_next_dispatch(self):
        # The widening must not weaken the owner inspection itself: without a
        # proven release the failed owner keeps refusing new dispatches.
        mgr, _coordinator, request_id = self._setup_released_failed_start()
        self._released_ledger(mgr, request_id, released=False, audit=False)

        current = ["sorengame"]
        sentinel = SentinelSwitchCoordinator(current)
        mgr2, _coordinator2 = self.manager(
            current, coordinator=sentinel, active_game_reader=lambda: current[0])

        with self.assertRaises(RetroCornerError) as caught:
            mgr2.run_rotation(str(uuid.uuid4()))

        self.assertIn("owner mismatch", str(caught.exception))
        self.assertEqual(sentinel.calls, [])


if __name__ == "__main__":
    unittest.main()
