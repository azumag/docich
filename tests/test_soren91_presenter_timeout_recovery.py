"""Regression coverage for #2680 presenter viewer-timeout recovery."""

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
from docich.game_switch import ERROR_READINESS_TIMEOUT  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.soren91_corner import (  # noqa: E402
    GAME_NAME,
    Soren91CornerManager,
    load_soren91_corner_config,
)


TIMEOUT_DETAIL = (
    "launch failed for soren91: presenter がviewer windowを提示できませんでした "
    "(presenter_status=viewer_timeout, viewer_wait_sec=12.50, elapsed_s=15.250)"
)


class FakeCoordinator:
    def __init__(self):
        self.calls = []

    def recover(self):
        self.calls.append("recover")
        return SimpleNamespace(status="succeeded", detail=None)


class TestSoren91PresenterTimeoutRecovery(unittest.TestCase):
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
        self.now = datetime(2026, 10, 23, 22, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.coordinator = FakeCoordinator()
        self.mgr = Soren91CornerManager(
            self.g,
            config=self.cfg,
            coordinator=self.coordinator,
            now=lambda: self.now,
            sleep=lambda _seconds: None,
            active_game_reader=lambda: "sorengame",
            ensure_runtime=lambda: None,
            chat=lambda _text: None,
            voice=lambda _text: None,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def _runtime(game: str, generation: int) -> dict[str, object]:
        names = runtime_names(generation)
        return {
            "game": game,
            "adapter": "browser",
            "generation": generation,
            "runtime_id": f"g{generation}-abcdef",
            "lease_id": str(uuid.uuid4()),
            "game_window": names.game_window,
            "agent_window": names.agent_window,
            "adapter_session": names.adapter_session,
            "started_at": "2026-10-23T12:00:00Z",
        }

    def _write_failed_state(self, detail: str = TIMEOUT_DETAIL) -> tuple[str, dict[str, object]]:
        request_id = str(uuid.uuid4())
        state = self.mgr._default_state()
        state.update(
            status="failed",
            game=GAME_NAME,
            previous_game="sorengame",
            rotation_request_id=request_id,
            switch_request_id=request_id,
            switch_status="rolled_back",
            last_error=detail,
            last_error_code=ERROR_READINESS_TIMEOUT,
        )
        self.mgr._write_state(state)
        canonical = self.mgr.store.initialize()
        canonical.update(
            phase="ready",
            next_generation=2,
            operation=None,
            request_id=None,
            active=self._runtime("sorengame", 1),
            candidate=None,
            previous=None,
            retiring=[],
        )
        self.mgr.store.canonical.save(canonical)
        return request_id, state

    def _finish_timeout_receipt(self, request_id: str, detail: str = TIMEOUT_DETAIL) -> dict:
        accepted = self.mgr.store.accept_request(request_id, "switch", GAME_NAME)
        result = {
            "request_id": request_id,
            "operation": "switch",
            "status": "rolled_back",
            "from_game": "sorengame",
            "to_game": GAME_NAME,
            "generation": accepted.generation,
            "restored_generation": 1,
            "error_code": ERROR_READINESS_TIMEOUT,
            "detail": detail,
        }
        self.mgr.store.finish_request(request_id, "rolled_back", result)
        return self.mgr.store.receipts.load(request_id)

    def test_recover_failed_terminalizes_proven_presenter_timeout_without_retry(self):
        request_id, state = self._write_failed_state()
        receipt = self._finish_timeout_receipt(request_id)

        self.assertTrue(self.mgr._failed_state_is_recoverable(state))
        recovered = self.mgr.recover_failed()

        self.assertEqual(recovered.status, "succeeded")
        final = self.mgr._read_state()
        self.assertEqual(final["status"], "interrupted")
        self.assertEqual(final["end_reason"], "switch-terminal-before-corner-active")
        self.assertIsNone(final["last_error"])
        self.assertIsNone(final["last_error_code"])
        self.assertNotIn("switch_status", final)
        self.assertEqual(self.coordinator.calls, [])
        self.assertEqual(self.mgr.store.receipts.load(request_id), receipt)

    def test_recover_failed_refuses_timeout_without_terminal_receipt(self):
        _request_id, state = self._write_failed_state()

        self.assertTrue(self.mgr._failed_state_is_recoverable(state))
        recovered = self.mgr.recover_failed()

        self.assertEqual(recovered.status, "noop")
        self.assertEqual(recovered.detail, "presenter-timeout-terminal-proof-unproven")
        self.assertEqual(self.mgr._read_state()["status"], "failed")
        self.assertEqual(self.coordinator.calls, [])

    def test_elapsed_shorter_than_viewer_wait_is_not_recoverable(self):
        detail = (
            "launch failed for soren91: presenter がviewer windowを提示できませんでした "
            "(presenter_status=viewer_timeout, viewer_wait_sec=12.50, elapsed_s=12.499)"
        )
        _request_id, state = self._write_failed_state(detail)

        self.assertFalse(self.mgr._failed_state_is_recoverable(state))
        recovered = self.mgr.recover_failed()

        self.assertEqual(recovered.status, "noop")
        self.assertEqual(recovered.detail, "failed-slot-not-recoverable")
        self.assertEqual(self.mgr._read_state()["status"], "failed")
        self.assertEqual(self.coordinator.calls, [])


if __name__ == "__main__":
    unittest.main()
