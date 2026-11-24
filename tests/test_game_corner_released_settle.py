"""#2352, #2480: the ``game``, NetHack and Meriken adapters retire their own
released owners.

The common rotation calls ``settle_released_failure`` only after it re-proved
the shared program slot free. These cases pin the adapter side: the call is
delegated with the exact released request, stays scoped to ``game``,
``nethack`` and ``meriken``, and every other adapter keeps its own separate
recovery entry.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.corner_adapters import GameCornerAdapter, MerikenCornerAdapter  # noqa: E402


class _Corner:
    def __init__(self, adapter, corner_id="robots", game="robots"):
        self.adapter = adapter
        self.game = game
        self.id = corner_id


class _Manager:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def settle_released_failure(self, request_id, *, corner_id):
        self.calls.append((request_id, corner_id))
        return self.result


class _ManagerWithoutSettle:
    def __init__(self):
        self.calls = []


def _adapter(catalog_adapter, manager, *, corner_id="robots", game="robots"):
    # The rotation constructs real adapters with a loaded config; the decision
    # under test only reads ``corner.adapter`` and delegates to the manager.
    adapter = GameCornerAdapter.__new__(GameCornerAdapter)
    adapter.corner = _Corner(catalog_adapter, corner_id=corner_id, game=game)
    adapter.manager = manager
    return adapter


class GameCornerReleasedSettleTests(unittest.TestCase):
    def test_settle_delegates_the_exact_released_request_and_corner(self):
        manager = _Manager(True)

        self.assertIs(_adapter("game", manager).settle_released_failure("r-1"), True)
        # The manager re-reads the ledger by corner id, so the adapter must pass
        # the corner that owns this state, never a bare request id.
        self.assertEqual(manager.calls, [("r-1", "robots")])

    def test_settle_delegates_the_nethack_owner_unchanged(self):
        # The automatic NetHack slot owns nethack_corner.json; its released
        # failed owner is retired by the same hook with the nethack corner id
        # (production 2026-10-16: released slot never dispatched again).
        manager = _Manager(True)

        self.assertIs(
            _adapter("nethack", manager, corner_id="nethack", game="nethack")
            .settle_released_failure("r-1"),
            True,
        )
        self.assertEqual(manager.calls, [("r-1", "nethack")])

    def test_settle_delegates_the_meriken_owner_unchanged(self):
        # The automatic Meriken (soren91) slot owns soren91_corner.json; its
        # released failed owner is retired by the same hook with the meriken
        # corner id (#2480: same gap as NetHack #2478).
        manager = _Manager(True)

        self.assertIs(
            _adapter("meriken", manager, corner_id="meriken", game="soren91")
            .settle_released_failure("r-1"),
            True,
        )
        self.assertEqual(manager.calls, [("r-1", "meriken")])

    def test_settle_returns_false_when_the_manager_refuses(self):
        manager = _Manager(False)

        self.assertIs(_adapter("game", manager).settle_released_failure("r-1"), False)
        self.assertEqual(manager.calls, [("r-1", "robots")])

    def test_settle_forwards_the_deferred_answer_unchanged(self):
        # ``None`` means "proved but not retired yet": the rotation must keep
        # this shared state file out of selection and retry (#2354 review).
        manager = _Manager(None)

        self.assertIsNone(_adapter("game", manager).settle_released_failure("r-1"))
        self.assertEqual(manager.calls, [("r-1", "robots")])

    def test_settle_forwards_the_nethack_deferred_answer_unchanged(self):
        manager = _Manager(None)

        self.assertIsNone(
            _adapter("nethack", manager, corner_id="nethack", game="nethack")
            .settle_released_failure("r-1")
        )
        self.assertEqual(manager.calls, [("r-1", "nethack")])

    def test_settle_forwards_the_meriken_deferred_answer_unchanged(self):
        manager = _Manager(None)

        self.assertIsNone(
            _adapter("meriken", manager, corner_id="meriken", game="soren91")
            .settle_released_failure("r-1")
        )
        self.assertEqual(manager.calls, [("r-1", "meriken")])

    def test_settle_requires_a_boolean_true_from_the_manager(self):
        # A truthy placeholder must not be reported as a settled owner.
        manager = _Manager("succeeded")

        self.assertIs(_adapter("game", manager).settle_released_failure("r-1"), False)

    def test_settle_stays_scoped_to_the_game_nethack_and_meriken_adapters(self):
        manager = _Manager(True)

        for catalog_adapter in ("paper", "tsuitate", "weather", "yoso"):
            with self.subTest(adapter=catalog_adapter):
                self.assertIs(
                    _adapter(catalog_adapter, manager).settle_released_failure("r-1"),
                    False,
                )
        self.assertEqual(manager.calls, [])

    def test_settle_returns_false_without_a_manager_hook(self):
        manager = _ManagerWithoutSettle()

        self.assertIs(_adapter("game", manager).settle_released_failure("r-1"), False)

    def test_settle_returns_false_for_a_nethack_corner_without_a_manager_hook(self):
        manager = _ManagerWithoutSettle()

        self.assertIs(
            _adapter("nethack", manager, corner_id="nethack", game="nethack")
            .settle_released_failure("r-1"),
            False,
        )

    def test_settle_returns_false_for_a_meriken_corner_without_a_manager_hook(self):
        manager = _ManagerWithoutSettle()

        self.assertIs(
            _adapter("meriken", manager, corner_id="meriken", game="soren91")
            .settle_released_failure("r-1"),
            False,
        )

    def test_settle_delegates_through_the_real_meriken_adapter_class(self):
        # #2480 review nit: pin the decision on the REAL MerikenCornerAdapter
        # class, not only the GameCornerAdapter.__new__ shape, so a future
        # inherited override of settle_released_failure cannot silently
        # narrow the meriken scope again. The (heavy) soren91 manager is
        # replaced by a stand-in; the class under test only wires
        # corner->manager and delegates.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            (root / "config" / "docich.toml").write_text(
                '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n',
                encoding="utf-8",
            )
            g = config.load_global(root)
            with mock.patch("docich.soren91_corner.Soren91CornerManager"):
                adapter = MerikenCornerAdapter(
                    g, _Corner("meriken", corner_id="meriken", game="soren91"))
        manager = _Manager(True)
        adapter.manager = manager

        self.assertIs(adapter.settle_released_failure("r-1"), True)
        self.assertEqual(manager.calls, [("r-1", "meriken")])


if __name__ == "__main__":
    unittest.main()
