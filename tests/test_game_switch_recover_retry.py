"""Regression tests for recover() entry-acquire retry wait (#2375 follow-up)."""
import itertools
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.game_switch import (  # noqa: E402
    GameSwitchBusyError,
    GameSwitchCoordinator,
    GameSwitchStore,
)


class _BusyContext:
    def __enter__(self):
        raise GameSwitchBusyError("busy")

    def __exit__(self, *exc):
        return False


class TestRecoverEntryAcquireRetry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameSwitchStore(Path(self.temp.name))
        self.store.canonical.initialize()
        self.coordinator = GameSwitchCoordinator(
            self.store, lambda spec: self.fail("adapter must not be called")
        )
        self.original_transaction = self.store.transaction
        self.sleeps = []

    def tearDown(self):
        self.temp.cleanup()

    def _patch_time(self, values):
        iterator = itertools.chain(values, itertools.repeat(values[-1]))
        game_switch = sys.modules["docich.game_switch"]
        game_switch.time.monotonic = lambda: next(iterator)
        game_switch.time.sleep = self.sleeps.append

    def test_busy_retry_sleeps_and_returns_busy(self):
        import docich.game_switch as game_switch

        self.store.transaction = lambda: _BusyContext()
        self._patch_time([0.0, 0.0, 0.0, 0.0, 0.05, 1.1])

        result = self.coordinator.recover(timeout_s=5.0)

        self.assertEqual(result.status, "busy")
        self.assertEqual(result.error_code, "busy")
        self.assertTrue(self.sleeps, "recover must sleep while retrying")
        self.assertLessEqual(self.sleeps[0], game_switch.ENTRY_ACQUIRE_RETRY_S)

    def test_busy_then_success_recovers(self):
        import docich.game_switch as game_switch

        calls = {"n": 0}

        def transaction():
            calls["n"] += 1
            if calls["n"] == 1:
                return _BusyContext()
            return self.original_transaction()

        self.store.transaction = transaction
        # First attempt busy at t=0.05, then success at t=0.2.
        self._patch_time([0.0, 0.0, 0.0, 0.0, 0.05, 0.2])

        result = self.coordinator.recover(timeout_s=5.0)

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(calls["n"], 2)
        self.assertTrue(self.sleeps, "recover must sleep before the successful retry")


if __name__ == "__main__":
    unittest.main()
