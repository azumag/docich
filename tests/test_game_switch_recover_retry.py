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
        original_monotonic = game_switch.time.monotonic
        original_sleep = game_switch.time.sleep
        game_switch.time.monotonic = lambda: next(iterator)
        game_switch.time.sleep = self.sleeps.append

        def _restore():
            game_switch.time.monotonic = original_monotonic
            game_switch.time.sleep = original_sleep

        self.addCleanup(_restore)

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

    def test_time_functions_are_restored_after_the_retry(self):
        """The scoped time patch must not leak into the rest of the suite (#2379 review).

        ``game_switch.time`` is the stdlib ``time`` module itself, so a patch that
        is not restored freezes ``monotonic`` and turns ``sleep`` into a no-op for
        every later test in the same pytest worker; deadline/retry suites can then
        misjudge or never finish.  Exercise the retry path, run the scoped cleanup
        explicitly, and prove the stdlib functions are back and the real clock
        advances again.
        """
        import docich.game_switch as game_switch

        original_monotonic = time.monotonic
        original_sleep = time.sleep

        self.store.transaction = lambda: _BusyContext()
        # A constant fake clock keeps the acquire window at zero, so the first
        # busy attempt already exceeds it and recover() returns without looping.
        self._patch_time([0.0])

        # The instrumentation is active while the retry test runs...
        self.assertIsNot(game_switch.time.monotonic, original_monotonic)
        self.assertIsNot(game_switch.time.sleep, original_sleep)
        self.assertEqual(self.coordinator.recover(timeout_s=0.0).status, "busy")

        # ...and the scoped cleanup puts the stdlib functions back.
        self.doCleanups()
        self.assertIs(game_switch.time.monotonic, original_monotonic)
        self.assertIs(game_switch.time.sleep, original_sleep)

        # The real clock advances, so a later deadline test cannot hang.
        before = game_switch.time.monotonic()
        game_switch.time.sleep(0.01)
        self.assertGreater(game_switch.time.monotonic(), before)


if __name__ == "__main__":
    unittest.main()
