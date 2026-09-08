from __future__ import annotations

import unittest

from ops.vm_actions.reconcile_presynced_submodule import ReconcileError, main


class PresyncedSubmoduleReconcileCliTests(unittest.TestCase):
    def test_requires_exact_argument_count(self):
        with self.assertRaises(ReconcileError):
            main(["reconcile"])


if __name__ == "__main__":
    unittest.main()
