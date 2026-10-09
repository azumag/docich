"""A strict owner recovery fence must be checked under the canonical writer lock."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.game_switch import (  # noqa: E402
    ERROR_SOURCE_FENCE_LOST,
    GameSwitchCoordinator,
    GameSwitchStore,
)


class TestFencedCoordinatorRecover(unittest.TestCase):
    def test_changed_snapshot_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = GameSwitchStore(Path(temp))
            coordinator = GameSwitchCoordinator(
                store, lambda spec: self.fail("adapter must not be called")
            )
            before = dict(store.canonical.initialize())
            after = {**before, "next_generation": before["next_generation"] + 1}
            after = store.canonical.save(after)

            result = coordinator.recover(
                expected_snapshot=before, timeout_s=0.5,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, ERROR_SOURCE_FENCE_LOST)
            self.assertTrue(result.cleanup_pending)
            persisted, missing = store.canonical.load()
            self.assertFalse(missing)
            self.assertEqual(persisted, after)

    def test_matching_idle_snapshot_preserves_existing_recover_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            store = GameSwitchStore(Path(temp))
            coordinator = GameSwitchCoordinator(
                store, lambda spec: self.fail("adapter must not be called")
            )
            matching = dict(store.canonical.initialize())

            result = coordinator.recover(
                expected_snapshot=matching, timeout_s=5.0,
            )

            self.assertEqual(result.status, "succeeded")
            self.assertFalse(result.cleanup_pending)
            current, missing = store.canonical.load()
            self.assertFalse(missing)
            self.assertEqual(current["phase"], "idle")
