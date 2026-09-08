from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ops.vm_actions.reconcile_presynced_submodule import (
    REASON_ROOT_DRIFT,
    REASON_SUBMODULE_DRIFT,
    REASON_SUBMODULE_HEAD_MISMATCH,
    REASON_UNAPPROVED_SUBMODULE,
    ReconcileError,
    reconcile,
)


class PresyncedSubmoduleReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-presync-")
        base = Path(self.tmp.name)
        self.sub_remote = base / "soren"
        self.root = base / "docich"
        self._init_repo(self.sub_remote)

        (self.sub_remote / "worker.sh").write_text("old\n", encoding="utf-8")
        self._git(self.sub_remote, "add", "worker.sh")
        self._git(self.sub_remote, "commit", "-m", "old")
        self.old_sub = self._git(self.sub_remote, "rev-parse", "HEAD")

        (self.sub_remote / "worker.sh").write_text("new\n", encoding="utf-8")
        self._git(self.sub_remote, "commit", "-am", "new")
        self.new_sub = self._git(self.sub_remote, "rev-parse", "HEAD")

        self._init_repo(self.root)
        (self.root / ".gitmodules").write_text(
            '[submodule "games/soviet_now"]\n'
            "\tpath = games/soviet_now\n"
            "\turl = https://github.com/azumag/soviet_now.git\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text("root\n", encoding="utf-8")
        self._git(self.root, "add", ".gitmodules", "README.md")
        self._git(
            self.root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.old_sub},games/soviet_now",
        )
        self._git(self.root, "commit", "-m", "root old gitlink")
        self.old_parent = self._git(self.root, "rev-parse", "HEAD")

        (self.root / "games").mkdir(exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", str(self.sub_remote), str(self.root / "games/soviet_now")],
            check=True,
        )
        sub = self.root / "games/soviet_now"
        self._git(sub, "config", "user.email", "tests@example.invalid")
        self._git(sub, "config", "user.name", "vmops tests")
        self._git(sub, "checkout", "--detach", "--quiet", self.new_sub)

    def tearDown(self):
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

    def assert_reason(self, expected: int, fn) -> None:
        with self.assertRaises(ReconcileError) as ctx:
            fn()
        self.assertEqual(ctx.exception.code, expected)

    def test_reconcile_restores_recorded_gitlink_without_touching_root(self):
        reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now")
        sub = self.root / "games/soviet_now"
        self.assertEqual(self._git(sub, "rev-parse", "HEAD"), self.old_sub)
        self.assertEqual(self._git(self.root, "rev-parse", "HEAD"), self.old_parent)
        self.assertEqual(
            self._git(self.root, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"),
            "",
        )

    def test_refuses_unknown_submodule_head_without_mutating_it(self):
        sub = self.root / "games/soviet_now"
        (sub / "worker.sh").write_text("third\n", encoding="utf-8")
        self._git(sub, "commit", "-am", "third")
        third = self._git(sub, "rev-parse", "HEAD")
        self.assert_reason(
            REASON_SUBMODULE_HEAD_MISMATCH,
            lambda: reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now"),
        )
        self.assertEqual(self._git(sub, "rev-parse", "HEAD"), third)

    def test_refuses_tracked_submodule_drift(self):
        sub = self.root / "games/soviet_now"
        (sub / "worker.sh").write_text("dirty\n", encoding="utf-8")
        self.assert_reason(
            REASON_SUBMODULE_DRIFT,
            lambda: reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now"),
        )
        self.assertEqual(self._git(sub, "rev-parse", "HEAD"), self.new_sub)

    def test_refuses_tracked_root_drift(self):
        (self.root / "README.md").write_text("dirty root\n", encoding="utf-8")
        self.assert_reason(
            REASON_ROOT_DRIFT,
            lambda: reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now"),
        )
        self.assertEqual(self._git(self.root / "games/soviet_now", "rev-parse", "HEAD"), self.new_sub)

    def test_refuses_unapproved_submodule_path(self):
        self.assert_reason(
            REASON_UNAPPROVED_SUBMODULE,
            lambda: reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/other"),
        )
        self.assertEqual(self._git(self.root / "games/soviet_now", "rev-parse", "HEAD"), self.new_sub)


if __name__ == "__main__":
    unittest.main()
