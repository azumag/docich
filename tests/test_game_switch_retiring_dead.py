"""A retiring runtime whose tmux targets are all provably gone must be
dismissed instead of wedging every later operation in cleanup_pending.

g1211/g1215 2026-11-15: failed candidates were tracked in `retiring`, but the
adapter factory could not construct an adapter for the already-dead runtimes,
so `_finalize_locked` kept them in `retiring` forever with `cleanup_pending`
— and every operation then refused with `recovery_required`. Manual removal
of the stale entries was needed three times.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.game_switch import GameSwitchCoordinator, GameSwitchStore  # noqa: E402


def _runtime(generation: int = 1215) -> dict:
    return {
        "game": "sorengame", "adapter": "soren", "generation": generation,
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


class RetiringTargetsAbsentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GameSwitchStore(Path(self.temp.name))
        self.coordinator = GameSwitchCoordinator(
            self.store, lambda spec: self.fail("adapter must not be called"))

    def _absent(self, runtime, tmux):
        self.coordinator._probe_tmux = lambda: tmux
        return self.coordinator._retiring_targets_absent(runtime)

    def test_all_targets_gone_is_absent(self):
        self.assertTrue(self._absent(_runtime(), _FakeTmux()))

    def test_living_window_is_not_absent(self):
        self.assertFalse(self._absent(_runtime(), _FakeTmux(windows={"game-g1215"})))

    def test_living_session_is_not_absent(self):
        self.assertFalse(self._absent(
            _runtime(), _FakeTmux(sessions={"docich-game-g1215"})))

    def test_no_recorded_targets_is_not_absent(self):
        runtime = {"game": "sorengame", "runtime_id": "g1-x"}
        self.assertFalse(self._absent(runtime, _FakeTmux()))

    def test_probe_error_is_not_absent(self):
        class Broken(_FakeTmux):
            def window_target_exists(self, target, *, strict=False):
                raise RuntimeError("tmux down")

        self.assertFalse(self._absent(_runtime(), Broken()))


class FinalizeDeadRetiringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GameSwitchStore(Path(self.temp.name))
        state = self.store.canonical.initialize()
        state.update(phase="idle", retiring=[_runtime()], next_generation=1216)
        self.store.canonical.save(state)

    def test_dead_retiring_is_dismissed(self):
        coordinator = GameSwitchCoordinator(
            self.store, lambda spec: self.fail("adapter must not be called"))
        coordinator._probe_tmux = lambda: _FakeTmux()
        coordinator._write_retire_marker = lambda *a, **k: None
        coordinator._prune_hanjuku_logs = lambda *a, **k: None
        with self.store.transaction() as tx:
            pending = coordinator._finalize_locked(tx, 0.0, warnings=[])
        state, _ = self.store.canonical.load()
        self.assertFalse(pending)
        self.assertEqual(state["retiring"], [])

    def test_alive_retiring_stays_pending(self):
        coordinator = GameSwitchCoordinator(
            self.store, lambda spec: self.fail("adapter must not be called"))
        coordinator._probe_tmux = lambda: _FakeTmux(windows={"game-g1215"})
        coordinator._prune_hanjuku_logs = lambda *a, **k: None
        with self.store.transaction() as tx:
            pending = coordinator._finalize_locked(tx, 0.0, warnings=[])
        state, _ = self.store.canonical.load()
        self.assertTrue(pending)
        self.assertEqual(len(state["retiring"]), 1)


if __name__ == "__main__":
    unittest.main()
