"""Only runtimes whose teardown completed may be handed to the RTA finalizer.

D4: ``_finalize_locked`` used to pass every ``retiring`` entry, including the
ones whose teardown failed.  Those runtimes may still have a live bot, so
finalizing their attempts would DNF a running run and race the hook's writes.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.game_switch import GameSwitchCoordinator, GameSwitchStore  # noqa: E402


def _runtime(generation: int, game: str = "hanjuku-speedrun") -> dict:
    return {
        "game": game, "adapter": "retroarch", "generation": generation,
        "runtime_id": f"g{generation}-0123abcd",
        "lease_id": "123e4567-e89b-42d3-a456-426614174001",
        "game_window": f"game-g{generation}", "agent_window": f"agent-g{generation}",
        "adapter_session": f"docich-game-g{generation}",
        "started_at": "2026-11-15T16:00:00+00:00",
    }


class _FakeTmux:
    def __init__(self, windows=(), sessions=()):
        self.windows = set(windows)
        self.sessions = set(sessions)

    def window_target_exists(self, target, *, strict=False):
        return target in self.windows

    def session_target_exists(self, session, *, strict=False):
        return session in self.sessions


class SpeedrunFinalizeTargetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GameSwitchStore(Path(self.temp.name))

    def _state(self, runtimes):
        state = self.store.canonical.initialize()
        state.update(phase="idle", retiring=runtimes, next_generation=1216)
        self.store.canonical.save(state)

    def _capture(self, coordinator):
        captured = []

        def record(_state_dir, runtimes, warnings=None):
            captured.append([dict(runtime) for runtime in runtimes])
            return len(captured[-1])

        patch = mock.patch("docich.game_switch.finalize_speedrun_attempts",
                           side_effect=record)
        patch.start()
        self.addCleanup(patch.stop)
        return captured

    def test_failed_teardown_runtime_is_not_finalized(self):
        dead = _runtime(1214)
        alive = _runtime(1215)
        self._state([dead, alive])
        coordinator = GameSwitchCoordinator(
            self.store, lambda spec: self.fail("adapter must not be called"))
        coordinator._probe_tmux = lambda: _FakeTmux(windows={"game-g1215"})
        coordinator._write_retire_marker = lambda *a, **k: None
        coordinator._prune_hanjuku_logs = lambda *a, **k: None
        captured = self._capture(coordinator)
        with self.store.transaction() as tx:
            self.assertTrue(coordinator._finalize_locked(tx, 0.0, warnings=[]))
        state, _ = self.store.canonical.load()
        self.assertEqual([item["runtime_id"] for item in state["retiring"]],
                         ["g1215-0123abcd"])
        self.assertEqual(
            [[item["runtime_id"] for item in call] for call in captured],
            [["g1214-0123abcd"]])

    def test_successfully_torn_down_runtime_is_finalized(self):
        runtime = _runtime(1215)
        self._state([runtime])
        coordinator = GameSwitchCoordinator(
            self.store, lambda spec: self.fail("adapter must not be called"))
        coordinator._make_adapter = lambda spec, deadline: object()
        coordinator._write_retire_marker = lambda *a, **k: None
        coordinator._prune_hanjuku_logs = lambda *a, **k: None
        with mock.patch.object(coordinator, "_teardown_runtime",
                               return_value=True):
            captured = self._capture(coordinator)
            with self.store.transaction() as tx:
                self.assertFalse(coordinator._finalize_locked(
                    tx, 0.0, warnings=[]))
        state, _ = self.store.canonical.load()
        self.assertEqual(state["retiring"], [])
        self.assertEqual(
            [[item["runtime_id"] for item in call] for call in captured],
            [["g1215-0123abcd"]])


if __name__ == "__main__":
    unittest.main()
