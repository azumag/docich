from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from ops.vm_actions import reconcile_presynced_submodule as presync


class PresyncedSubmoduleObjectFetchTests(unittest.TestCase):
    def setUp(self):
        self.previous_lineage = presync._LINEAGE
        presync._LINEAGE = True

    def tearDown(self):
        presync._LINEAGE = self.previous_lineage

    def test_missing_reviewed_object_fetches_only_fixed_public_main(self):
        missing = presync.ReconcileError(presync.REASON_NEW_OBJECT_MISSING, "missing")
        with mock.patch.object(presync, "_git", side_effect=[missing, ""]) as git, \
             mock.patch.object(presync.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
             mock.patch.object(presync, "_is_ancestor", return_value=True) as ancestor:
            presync._ensure_reviewed_new_object(
                Path("/srv/soren"), "a" * 40, "games/soviet_now"
            )

        self.assertEqual(git.call_count, 2)
        argv = run.call_args.args[0]
        self.assertIn("fetch", argv)
        self.assertIn("--no-recurse-submodules", argv)
        self.assertIn("--no-tags", argv)
        self.assertIn("https://github.com/azumag/soviet_now.git", argv)
        self.assertIn("refs/heads/main", argv)
        self.assertNotIn("checkout", argv)
        self.assertNotIn("reset", argv)
        ancestor.assert_called_once_with(Path("/srv/soren"), "a" * 40, "FETCH_HEAD")

    def test_missing_object_without_lineage_opt_in_remains_fail_closed(self):
        presync._LINEAGE = False
        missing = presync.ReconcileError(presync.REASON_NEW_OBJECT_MISSING, "missing")
        with mock.patch.object(presync, "_git", side_effect=missing), \
             mock.patch.object(presync.subprocess, "run") as run:
            with self.assertRaises(presync.ReconcileError) as ctx:
                presync._ensure_reviewed_new_object(
                    Path("/srv/soren"), "a" * 40, "games/soviet_now"
                )
        self.assertEqual(ctx.exception.code, presync.REASON_NEW_OBJECT_MISSING)
        run.assert_not_called()

    def test_fetched_object_not_reachable_from_upstream_main_is_refused(self):
        missing = presync.ReconcileError(presync.REASON_NEW_OBJECT_MISSING, "missing")
        with mock.patch.object(presync, "_git", side_effect=[missing, ""]), \
             mock.patch.object(presync.subprocess, "run", return_value=mock.Mock(returncode=0)), \
             mock.patch.object(presync, "_is_ancestor", return_value=False):
            with self.assertRaises(presync.ReconcileError) as ctx:
                presync._ensure_reviewed_new_object(
                    Path("/srv/soren"), "a" * 40, "games/soviet_now"
                )
        self.assertEqual(ctx.exception.code, presync.REASON_NEW_OBJECT_MISSING)

    def test_fetch_failure_is_sanitized_to_fixed_reason(self):
        missing = presync.ReconcileError(presync.REASON_NEW_OBJECT_MISSING, "missing")
        with mock.patch.object(presync, "_git", side_effect=missing), \
             mock.patch.object(
                 presync.subprocess,
                 "run",
                 side_effect=subprocess.CalledProcessError(1, ["git", "fetch"]),
             ):
            with self.assertRaises(presync.ReconcileError) as ctx:
                presync._ensure_reviewed_new_object(
                    Path("/srv/soren"), "a" * 40, "games/soviet_now"
                )
        self.assertEqual(ctx.exception.code, presync.REASON_NEW_OBJECT_MISSING)


if __name__ == "__main__":
    unittest.main()
