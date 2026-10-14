from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from ops.vm_actions import reconcile_presynced_submodule as presync


class PresyncedSubmoduleReviewedDriftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-subdrift-")
        base = Path(self.tmp.name)
        self.remote = base / "soren-remote"
        self.root = base / "docich"
        self._init_repo(self.remote)

        worker = self.remote / "worker.sh"
        worker.write_text("old\n", encoding="utf-8")
        self._git(self.remote, "add", "worker.sh")
        self._git(self.remote, "commit", "-m", "old")
        self.old_sub = self._git(self.remote, "rev-parse", "HEAD")

        worker.write_text("middle\n", encoding="utf-8")
        self._git(self.remote, "commit", "-am", "reviewed intermediate")
        self.middle_sub = self._git(self.remote, "rev-parse", "HEAD")

        worker.write_text("new\n", encoding="utf-8")
        self._git(self.remote, "commit", "-am", "new")
        self.new_sub = self._git(self.remote, "rev-parse", "HEAD")

        self._init_repo(self.root)
        (self.root / "games").mkdir()
        subprocess.run(
            ["git", "clone", "--quiet", str(self.remote), str(self.root / "games/soviet_now")],
            check=True,
        )
        self.sub = self.root / "games/soviet_now"
        self._git(self.sub, "checkout", "--detach", "--quiet", self.old_sub)
        self._git(
            self.root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.old_sub},games/soviet_now",
        )
        self._git(self.root, "commit", "-m", "old parent")
        self.old_parent = self._git(self.root, "rev-parse", "HEAD")

        self.previous_lineage = presync._LINEAGE
        self.previous_attested = presync._ATTESTED
        presync._LINEAGE = True
        presync._ATTESTED = []

    def tearDown(self):
        presync._LINEAGE = self.previous_lineage
        presync._ATTESTED = self.previous_attested
        self.tmp.cleanup()

    @staticmethod
    def _init_repo(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--quiet", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "config", "user.email", "tests@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(path), "config", "user.name", "vmops tests"], check=True)

    @staticmethod
    def _git(path: Path, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()

    def _reconcile(self) -> None:
        presync.reconcile(
            self.root,
            self.old_parent,
            self.old_sub,
            self.new_sub,
            "games/soviet_now",
        )

    def test_exact_reviewed_new_worktree_is_restored_to_old(self):
        worker = self.sub / "worker.sh"
        worker.write_text("new\n", encoding="utf-8")

        self._reconcile()

        self.assertEqual(worker.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(self._git(self.sub, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual(self._git(self.sub, "rev-parse", "HEAD"), self.old_sub)

    def test_exact_reviewed_intermediate_worktree_is_restored_to_old(self):
        worker = self.sub / "worker.sh"
        worker.write_text("middle\n", encoding="utf-8")

        self._reconcile()

        self.assertEqual(worker.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(self._git(self.sub, "status", "--porcelain", "--untracked-files=no"), "")

    def test_unknown_worktree_remains_fail_closed_and_untouched(self):
        worker = self.sub / "worker.sh"
        worker.write_text("operator drift\n", encoding="utf-8")

        with self.assertRaises(presync.ReconcileError) as ctx:
            self._reconcile()

        self.assertEqual(ctx.exception.code, presync.REASON_SUBMODULE_DRIFT)
        self.assertEqual(worker.read_text(encoding="utf-8"), "operator drift\n")

    def test_reviewed_bytes_with_unreviewed_mode_are_refused(self):
        worker = self.sub / "worker.sh"
        worker.write_text("new\n", encoding="utf-8")
        os.chmod(worker, 0o600)

        with self.assertRaises(presync.ReconcileError) as ctx:
            self._reconcile()

        self.assertEqual(ctx.exception.code, presync.REASON_SUBMODULE_DRIFT)
        self.assertEqual(worker.read_text(encoding="utf-8"), "new\n")
        self.assertEqual(os.stat(worker).st_mode & 0o777, 0o600)

    def test_staged_reviewed_drift_is_refused_without_touching_index(self):
        worker = self.sub / "worker.sh"
        worker.write_text("new\n", encoding="utf-8")
        self._git(self.sub, "add", "worker.sh")
        cached_before = self._git(self.sub, "diff", "--cached", "--name-only")

        with self.assertRaises(presync.ReconcileError) as ctx:
            self._reconcile()

        self.assertEqual(ctx.exception.code, presync.REASON_SUBMODULE_DRIFT)
        self.assertEqual(self._git(self.sub, "diff", "--cached", "--name-only"), cached_before)
        self.assertEqual(worker.read_text(encoding="utf-8"), "new\n")

    def test_without_lineage_opt_in_keeps_historical_refusal(self):
        presync._LINEAGE = False
        worker = self.sub / "worker.sh"
        worker.write_text("new\n", encoding="utf-8")

        with self.assertRaises(presync.ReconcileError) as ctx:
            self._reconcile()

        self.assertEqual(ctx.exception.code, presync.REASON_SUBMODULE_DRIFT)
        self.assertEqual(worker.read_text(encoding="utf-8"), "new\n")


if __name__ == "__main__":
    unittest.main()
