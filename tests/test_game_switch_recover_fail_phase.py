"""A failed recovery must settle from the live phase, not a stale snapshot.

g1163 2026-11-14: the canonical was `failed`; `recover` started a sorengame
rollback (phase `rolling_back`), the rollback failed, and `_recover_fail_locked`
tried to transition from its caller's stale `failed` snapshot. `failed -> failed`
is not a transition, so it silently did nothing and the canonical stayed at
`rolling_back` with no driver left. The official `gameswitch-recover` only
declares `failed`/`recovery_required`, so the state became unrecoverable.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.game_switch import GameSwitchCoordinator, GameSwitchStore  # noqa: E402

REQUEST_ID = "123e4567-e89b-42d3-a456-426614174000"


class RecoverFailPhaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GameSwitchStore(Path(self.temp.name))
        state = self.store.canonical.initialize()
        self.previous = {
            "game": "sorengame", "adapter": "retroarch", "generation": 4,
            "runtime_id": "g4-0123abcd", "lease_id": "123e4567-e89b-42d3-a456-426614174001",
            "game_window": "game-g4", "agent_window": "agent-g4",
            "adapter_session": "docich-game-g4", "started_at": "2026-11-14T08:00:00+00:00",
        }
        state.update(
            phase="rolling_back", operation="switch", request_id=REQUEST_ID,
            active=None, candidate=None, previous=self.previous, retiring=[],
            next_generation=5,
            last_result={"request_id": REQUEST_ID, "operation": "switch",
                         "status": "failed", "from_game": "hanjuku-hero",
                         "to_game": "sorengame", "generation": 4,
                         "error_code": "start_failed"},
            last_error={"error_code": "start_failed", "detail": "start failed"},
        )
        self.store.canonical.save(state)
        self.coordinator = GameSwitchCoordinator(
            self.store, lambda spec: self.fail("adapter must not be called"))

    def test_a_failed_rollback_settles_the_live_rolling_back_to_failed(self):
        with self.store.transaction() as tx:
            result = self.coordinator._recover_fail_locked(
                tx, {"phase": "failed"}, [], "previous runtimeの復旧に失敗しました",
                "rollback_failed")
        current, missing = self.store.canonical.load()
        self.assertFalse(missing)
        self.assertEqual(current["phase"], "failed")
        self.assertEqual(current["last_error"]["error_code"], "rollback_failed")
        self.assertEqual(current["previous"], self.previous)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "rollback_failed")

    def test_a_stable_failed_phase_is_left_as_is(self):
        state, _ = self.store.canonical.load()
        state.update(phase="failed", operation=None, request_id=None)
        self.store.canonical.save(state)
        with self.store.transaction() as tx:
            self.coordinator._recover_fail_locked(
                tx, {"phase": "failed"}, [], "detail", "rollback_failed")
        current, _ = self.store.canonical.load()
        self.assertEqual(current["phase"], "failed")


if __name__ == "__main__":
    unittest.main()
