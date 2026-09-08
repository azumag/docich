from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from ops.vm_actions.reconcile_presynced_submodule import (
    PROJECTION_UNKNOWN_CLASS_PER_PATH,
    REASON_PROJECTION_UNKNOWN_CLASS_BASE,
    REASON_PROJECTION_UNKNOWN_MASK_BASE,
    REASON_PROJECTION_UNKNOWN_STATE,
    REASON_ROOT_DRIFT,
    REASON_SUBMODULE_DRIFT,
    REASON_SUBMODULE_HEAD_MISMATCH,
    REASON_UNAPPROVED_SUBMODULE,
    ReconcileError,
    _normalize_projection,
    _projection_unknown_reason,
    reconcile,
)


class PresyncedSubmoduleReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-presync-")
        base = Path(self.tmp.name)
        self.sub_remote = base / "soren"
        self.root = base / "docich"
        self.live = base / "live"
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
        self.live.mkdir()

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

    @staticmethod
    def _mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def assert_reason(self, expected: int, fn) -> None:
        with self.assertRaises(ReconcileError) as ctx:
            fn()
        self.assertEqual(ctx.exception.code, expected)

    def test_reconcile_restores_recorded_gitlink_without_touching_root(self):
        reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now")
        sub = self.root / "games/soviet_now"
        self.assertEqual(self._git(sub, "rev-parse", "HEAD"), self.old_sub)
        self.assertEqual(self._git(self.root, "rev-parse", "HEAD"), self.old_parent)

    def test_already_at_old_gitlink_is_successful_noop(self):
        sub = self.root / "games/soviet_now"
        self._git(sub, "checkout", "--detach", "--quiet", self.old_sub)
        reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now")
        self.assertEqual(self._git(sub, "rev-parse", "HEAD"), self.old_sub)
        self.assertEqual(self._git(sub, "status", "--porcelain", "--untracked-files=no"), "")

    def test_accepts_clean_ancestor_with_identical_tree_to_reviewed_target(self):
        sub = self.root / "games/soviet_now"
        self._git(sub, "checkout", "--quiet", "-B", "reviewed-merge", self.new_sub)
        self._git(sub, "commit", "--allow-empty", "-m", "reviewed merge")
        reviewed_merge = self._git(sub, "rev-parse", "HEAD")
        self._git(sub, "checkout", "--detach", "--quiet", self.new_sub)
        reconcile(self.root, self.old_parent, self.old_sub, reviewed_merge, "games/soviet_now")
        self.assertEqual(self._git(sub, "rev-parse", "HEAD"), self.old_sub)

    def test_exact_reviewed_new_live_projection_is_normalized_to_old(self):
        path = self.live / "worker.sh"
        path.write_text("new\n", encoding="utf-8")
        reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now", self.live)
        self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(self._mode(path), 0o644)

    def test_reviewed_new_content_with_mode_drift_is_normalized(self):
        path = self.live / "worker.sh"
        path.write_text("new\n", encoding="utf-8")
        os.chmod(path, 0o600)
        reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now", self.live)
        self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(self._mode(path), 0o644)

    def test_recorded_old_content_with_mode_drift_is_repaired(self):
        path = self.live / "worker.sh"
        path.write_text("old\n", encoding="utf-8")
        os.chmod(path, 0o600)
        reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now", self.live)
        self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(self._mode(path), 0o644)

    def test_unknown_live_projection_is_refused_without_overwrite(self):
        path = self.live / "worker.sh"
        path.write_text("operator drift\n", encoding="utf-8")
        self.assert_reason(
            REASON_PROJECTION_UNKNOWN_STATE,
            lambda: reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now", self.live),
        )
        self.assertEqual(path.read_text(encoding="utf-8"), "operator drift\n")

    def test_projection_unknown_mask_encodes_small_multi_path_unknowns(self):
        self.assertEqual(
            _projection_unknown_reason([0, 1, 2, 2]),
            REASON_PROJECTION_UNKNOWN_MASK_BASE + 0b1100,
        )

    def test_projection_unknown_mask_preserves_generic_code_outside_bound(self):
        self.assertEqual(_projection_unknown_reason([2]), REASON_PROJECTION_UNKNOWN_STATE)
        self.assertEqual(_projection_unknown_reason([2, 2, 2, 2, 2]), REASON_PROJECTION_UNKNOWN_STATE)
        self.assertEqual(_projection_unknown_reason([0, 1]), REASON_PROJECTION_UNKNOWN_STATE)

    def test_single_unknown_resized_path_class_is_computed_before_any_projection_write(self):
        self._git(self.sub_remote, "checkout", "--detach", "--quiet", self.old_sub)
        (self.sub_remote / "second.sh").write_text("old second\n", encoding="utf-8")
        self._git(self.sub_remote, "add", "second.sh")
        self._git(self.sub_remote, "commit", "-m", "old two-path projection")
        old_two = self._git(self.sub_remote, "rev-parse", "HEAD")
        (self.sub_remote / "second.sh").write_text("new second\n", encoding="utf-8")
        (self.sub_remote / "worker.sh").write_text("newer worker\n", encoding="utf-8")
        self._git(self.sub_remote, "commit", "-am", "new two-path projection")
        new_two = self._git(self.sub_remote, "rev-parse", "HEAD")

        second = self.live / "second.sh"
        worker = self.live / "worker.sh"
        second.write_text("operator drift\n", encoding="utf-8")
        worker.write_text("newer worker\n", encoding="utf-8")

        # "operator drift" has a length matching neither recorded side, so the
        # single unknown path 0 refines to the resized class (90 + 0*3 + 1).
        self.assert_reason(
            REASON_PROJECTION_UNKNOWN_CLASS_BASE + 1,
            lambda: _normalize_projection(self.sub_remote, old_two, new_two, self.live),
        )
        self.assertEqual(second.read_text(encoding="utf-8"), "operator drift\n")
        self.assertEqual(worker.read_text(encoding="utf-8"), "newer worker\n")

    def test_projection_unknown_class_encodes_presence_and_length(self):
        old = {"data": b"old\n"}
        new = {"data": b"new\n"}
        self.assertEqual(
            _projection_unknown_reason([0, 2], [(1, None, old, new)]),
            REASON_PROJECTION_UNKNOWN_CLASS_BASE + PROJECTION_UNKNOWN_CLASS_PER_PATH + 0,
        )
        self.assertEqual(
            _projection_unknown_reason([2, 0], [(0, {"data": b"operator drift\n"}, old, new)]),
            REASON_PROJECTION_UNKNOWN_CLASS_BASE + 1,
        )
        self.assertEqual(
            _projection_unknown_reason([2, 0], [(0, {"data": b"xxx\n"}, old, new)]),
            REASON_PROJECTION_UNKNOWN_CLASS_BASE + 2,
        )

    def test_projection_unknown_class_codes_stay_below_signal_range(self):
        highest = REASON_PROJECTION_UNKNOWN_CLASS_BASE + 3 * PROJECTION_UNKNOWN_CLASS_PER_PATH + 2
        self.assertLess(highest, 128)
        self.assertGreaterEqual(REASON_PROJECTION_UNKNOWN_CLASS_BASE, 86)

    def test_helper_fits_production_exec_stdin_budget(self):
        # The workflow pipes this whole file to the production exec stdin,
        # which the gateway caps at 16384 bytes; growing past it rejects the
        # run before any diagnostic executes. Fail here in CI instead, with
        # room kept for the heredoc wrapper line. NUL bytes are rejected too.
        helper = Path(__file__).resolve().parent.parent / "reconcile_presynced_submodule.py"
        script = helper.read_bytes()
        self.assertNotIn(b"\0", script)
        self.assertLessEqual(len(script) + 256, 16384)

    def test_unknown_reason_keeps_mask_and_generic_codes(self):
        resized = (0, {"data": b"drift\n"}, {"data": b"old\n"}, {"data": b"new\n"})
        absent = (1, None, {"data": b"old\n"}, {"data": b"new\n"})
        self.assertEqual(
            _projection_unknown_reason([2, 2], [resized, absent]),
            REASON_PROJECTION_UNKNOWN_MASK_BASE + 0b11,
        )
        self.assertEqual(
            _projection_unknown_reason([2], [resized]),
            REASON_PROJECTION_UNKNOWN_STATE,
        )
        five = [0, 0, 0, 0, 2]
        self.assertEqual(
            _projection_unknown_reason(five, [(4, None, {"data": b"o"}, {"data": b"n"})]),
            REASON_PROJECTION_UNKNOWN_STATE,
        )
        # A single-bit mask without records keeps the historical mask code.
        self.assertEqual(
            _projection_unknown_reason([0, 2]),
            REASON_PROJECTION_UNKNOWN_MASK_BASE + 0b10,
        )

    def test_single_unknown_absent_path_reports_absence_without_write(self):
        self._git(self.sub_remote, "checkout", "--detach", "--quiet", self.old_sub)
        (self.sub_remote / "second.sh").write_text("old second\n", encoding="utf-8")
        self._git(self.sub_remote, "add", "second.sh")
        self._git(self.sub_remote, "commit", "-m", "old two-path projection")
        old_two = self._git(self.sub_remote, "rev-parse", "HEAD")
        (self.sub_remote / "second.sh").write_text("new second\n", encoding="utf-8")
        (self.sub_remote / "worker.sh").write_text("newer worker\n", encoding="utf-8")
        self._git(self.sub_remote, "commit", "-am", "new two-path projection")
        new_two = self._git(self.sub_remote, "rev-parse", "HEAD")

        worker = self.live / "worker.sh"
        worker.write_text("newer worker\n", encoding="utf-8")

        # second.sh is absent from live while both recorded sides exist.
        self.assert_reason(
            REASON_PROJECTION_UNKNOWN_CLASS_BASE + 0,
            lambda: _normalize_projection(self.sub_remote, old_two, new_two, self.live),
        )
        self.assertFalse((self.live / "second.sh").exists())
        self.assertEqual(worker.read_text(encoding="utf-8"), "newer worker\n")

    def test_single_unknown_same_length_edit_reports_same_length_without_write(self):
        self._git(self.sub_remote, "checkout", "--detach", "--quiet", self.old_sub)
        (self.sub_remote / "second.sh").write_text("old second\n", encoding="utf-8")
        self._git(self.sub_remote, "add", "second.sh")
        self._git(self.sub_remote, "commit", "-m", "old two-path projection")
        old_two = self._git(self.sub_remote, "rev-parse", "HEAD")
        (self.sub_remote / "second.sh").write_text("new second\n", encoding="utf-8")
        (self.sub_remote / "worker.sh").write_text("newer worker\n", encoding="utf-8")
        self._git(self.sub_remote, "commit", "-am", "new two-path projection")
        new_two = self._git(self.sub_remote, "rev-parse", "HEAD")

        second = self.live / "second.sh"
        worker = self.live / "worker.sh"
        second.write_text("xxx second\n", encoding="utf-8")
        worker.write_text("newer worker\n", encoding="utf-8")

        # Same 11-byte length as both recorded sides but unreviewed content.
        self.assert_reason(
            REASON_PROJECTION_UNKNOWN_CLASS_BASE + 2,
            lambda: _normalize_projection(self.sub_remote, old_two, new_two, self.live),
        )
        self.assertEqual(second.read_text(encoding="utf-8"), "xxx second\n")
        self.assertEqual(worker.read_text(encoding="utf-8"), "newer worker\n")

    def _two_path_repo_with_workflow(self):
        self._git(self.sub_remote, "checkout", "--detach", "--quiet", self.old_sub)
        ci = self.sub_remote / ".github/workflows/ci.yml"
        ci.parent.mkdir(parents=True, exist_ok=True)
        ci.write_text("old workflow\n", encoding="utf-8")
        self._git(self.sub_remote, "add", ".github/workflows/ci.yml")
        self._git(self.sub_remote, "commit", "-m", "old workflow projection")
        old_two = self._git(self.sub_remote, "rev-parse", "HEAD")
        ci.write_text("new workflow body\n", encoding="utf-8")
        (self.sub_remote / "worker.sh").write_text("newer worker\n", encoding="utf-8")
        self._git(self.sub_remote, "commit", "-am", "new workflow projection")
        new_two = self._git(self.sub_remote, "rev-parse", "HEAD")
        return old_two, new_two

    def test_pruned_repo_only_path_is_healed_to_old(self):
        old_two, new_two = self._two_path_repo_with_workflow()
        worker = self.live / "worker.sh"
        worker.write_text("newer worker\n", encoding="utf-8")

        # ci.yml is pruned from live; the reviewed worker is normalized too.
        _normalize_projection(self.sub_remote, old_two, new_two, self.live)

        restored = self.live / ".github/workflows/ci.yml"
        self.assertEqual(restored.read_text(encoding="utf-8"), "old workflow\n")
        self.assertEqual(self._mode(restored), 0o644)
        self.assertEqual(worker.read_text(encoding="utf-8"), "old\n")

    def test_edited_repo_only_path_stays_fail_closed_without_write(self):
        old_two, new_two = self._two_path_repo_with_workflow()
        ci = self.live / ".github/workflows/ci.yml"
        ci.parent.mkdir(parents=True, exist_ok=True)
        ci.write_text("operator edit\n", encoding="utf-8")
        worker = self.live / "worker.sh"
        worker.write_text("newer worker\n", encoding="utf-8")

        # Only absence heals; an edited repo-only file still refuses (bit 0).
        self.assert_reason(
            REASON_PROJECTION_UNKNOWN_CLASS_BASE + 1,
            lambda: _normalize_projection(self.sub_remote, old_two, new_two, self.live),
        )
        self.assertEqual(ci.read_text(encoding="utf-8"), "operator edit\n")
        self.assertEqual(worker.read_text(encoding="utf-8"), "newer worker\n")

    def test_healed_prune_does_not_mask_a_second_unknown(self):
        old_two, new_two = self._two_path_repo_with_workflow()
        worker = self.live / "worker.sh"
        worker.write_text("operator drift\n", encoding="utf-8")

        # ci.yml is healable, but worker.sh (bit 1, resized) refuses first;
        # preflight writes nothing, and the code names only the live unknown.
        self.assert_reason(
            REASON_PROJECTION_UNKNOWN_CLASS_BASE + PROJECTION_UNKNOWN_CLASS_PER_PATH + 1,
            lambda: _normalize_projection(self.sub_remote, old_two, new_two, self.live),
        )
        self.assertFalse((self.live / ".github/workflows/ci.yml").exists())
        self.assertEqual(worker.read_text(encoding="utf-8"), "operator drift\n")

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

    def test_refuses_tracked_root_drift(self):
        (self.root / "README.md").write_text("dirty root\n", encoding="utf-8")
        self.assert_reason(
            REASON_ROOT_DRIFT,
            lambda: reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now"),
        )

    def test_refuses_unapproved_submodule_path(self):
        self.assert_reason(
            REASON_UNAPPROVED_SUBMODULE,
            lambda: reconcile(self.root, self.old_parent, self.old_sub, self.new_sub, "games/other"),
        )


if __name__ == "__main__":
    unittest.main()
