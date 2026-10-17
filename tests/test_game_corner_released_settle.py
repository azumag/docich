"""#2352: the retro ``game`` adapter retires its own released failed owner.

The common rotation calls ``settle_released_failure`` only after it re-proved
the shared program slot free. These cases pin the adapter side: the call is
delegated with the exact released request and stays scoped to ``game``, so the
Meriken / NetHack owners keep their own separate recovery entries.
"""

import unittest

from docich.corner_adapters import GameCornerAdapter


class _Corner:
    def __init__(self, adapter):
        self.adapter = adapter
        self.game = "robots"
        self.id = "robots"


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


def _adapter(catalog_adapter, manager):
    # The rotation constructs real adapters with a loaded config; the decision
    # under test only reads ``corner.adapter`` and delegates to the manager.
    adapter = GameCornerAdapter.__new__(GameCornerAdapter)
    adapter.corner = _Corner(catalog_adapter)
    adapter.manager = manager
    return adapter


class GameCornerReleasedSettleTests(unittest.TestCase):
    def test_settle_delegates_the_exact_released_request_and_corner(self):
        manager = _Manager(True)

        self.assertIs(_adapter("game", manager).settle_released_failure("r-1"), True)
        # The manager re-reads the ledger by corner id, so the adapter must pass
        # the corner that owns this state, never a bare request id.
        self.assertEqual(manager.calls, [("r-1", "robots")])

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

    def test_settle_requires_a_boolean_true_from_the_manager(self):
        # A truthy placeholder must not be reported as a settled owner.
        manager = _Manager("succeeded")

        self.assertIs(_adapter("game", manager).settle_released_failure("r-1"), False)

    def test_settle_stays_scoped_to_the_game_adapter(self):
        manager = _Manager(True)

        for catalog_adapter in ("meriken", "nethack", "paper", "tsuitate", "weather"):
            with self.subTest(adapter=catalog_adapter):
                self.assertIs(
                    _adapter(catalog_adapter, manager).settle_released_failure("r-1"),
                    False,
                )
        self.assertEqual(manager.calls, [])

    def test_settle_returns_false_without_a_manager_hook(self):
        manager = _ManagerWithoutSettle()

        self.assertIs(_adapter("game", manager).settle_released_failure("r-1"), False)


if __name__ == "__main__":
    unittest.main()
