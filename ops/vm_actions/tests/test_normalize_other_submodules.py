from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ops.vm_actions.normalize_other_submodules import (
    REASON_INVALID_SHA,
    REASON_OLD_GITLINK_MISMATCH,
    REASON_OTHER_SUBMODULE_DRIFT,
    REASON_ROOT_MOVED,
    REASON_SUBMODULE_MISSING,
    NormalizeError,
    normalize,
)

HANJUKU = "games/hanjuku-sfc-speedrun"
SOVIET = "games/soviet_now"


class NormalizeOtherSubmodulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-other-sub-")
        base = Path(self.tmp.name)
        self.han_remote = base / "hanjuku"
        self.sov_remote = base / "soviet"
        self.root = base / "docich"
        self.live = base / "soren-live"
        self._init_repo(self.han_remote)

        (self.han_remote / "rom.txt").write_text("v1\n", encoding="utf-8")
        self._git(self.han_remote, "add", "rom.txt")
        self._git(self.han_remote, "commit", "-m", "v1")
        self.h1 = self._git(self.han_remote, "rev-parse", "HEAD")
        (self.han_remote / "rom.txt").write_text("v2\n", encoding="utf-8")
        self._git(self.han_remote, "commit", "-am", "v2")
        self.h2 = self._git(self.han_remote, "rev-parse", "HEAD")

        self._init_repo(self.sov_remote)
        for rel, body in (
            ("deploy/title-day/soren-title-day.service", "old unit\n"),
            ("deploy/title-day/soren-title-day.timer", "old timer\n"),
        ):
            target = self.sov_remote / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        self._git(self.sov_remote, "add", "deploy")
        self._git(self.sov_remote, "commit", "-m", "old candidates")
        self.old_sov = self._git(self.sov_remote, "rev-parse", "HEAD")

        self._init_repo(self.root)
        (self.root / ".gitmodules").write_text(
            '[submodule "games/hanjuku-sfc-speedrun"]\n'
            "\tpath = games/hanjuku-sfc-speedrun\n"
            "\turl = https://github.com/azumag/hanjuku-sfc-speedrun.git\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text("root\n", encoding="utf-8")
        self._git(self.root, "add", ".gitmodules", "README.md")
        self._git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{self.h1},{HANJUKU}")
        self._git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{self.old_sov},{SOVIET}")
        self._git(self.root, "commit", "-m", "root recorded gitlink")
        self.old_parent = self._git(self.root, "rev-parse", "HEAD")

        (self.root / "games").mkdir(exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", str(self.han_remote), str(self.root / HANJUKU)],
            check=True,
        )
        sub = self.root / HANJUKU
        self._git(sub, "config", "user.email", "tests@example.invalid")
        self._git(sub, "config", "user.name", "vmops tests")
        subprocess.run(
            ["git", "clone", "--quiet", str(self.sov_remote), str(self.root / SOVIET)],
            check=True,
        )
        for rel, body in (
            ("deploy/title-day/soren-title-day.service", "old unit\n"),
            ("deploy/title-day/soren-title-day.timer", "old timer\n"),
        ):
            target = self.live / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")

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
        with self.assertRaises(NormalizeError) as ctx:
            fn()
        self.assertEqual(ctx.exception.code, expected)

    def _sub(self) -> Path:
        return self.root / HANJUKU

    def test_matching_clean_checkout_is_successful_noop(self):
        self._git(self._sub(), "checkout", "--detach", "--quiet", self.h1)
        normalize(self.root, self.old_parent, self.live)
        self.assertEqual(self._git(self._sub(), "rev-parse", "HEAD"), self.h1)

    def test_moved_clean_checkout_returns_to_recorded_gitlink(self):
        self._git(self._sub(), "checkout", "--detach", "--quiet", self.h2)
        normalize(self.root, self.old_parent, self.live)
        self.assertEqual(self._git(self._sub(), "rev-parse", "HEAD"), self.h1)
        self.assertEqual(self._git(self._sub(), "status", "--porcelain", "--untracked-files=no"), "")

    def test_dirty_checkout_is_refused_without_touching_it(self):
        self._git(self._sub(), "checkout", "--detach", "--quiet", self.h2)
        (self._sub() / "rom.txt").write_text("operator edit\n", encoding="utf-8")
        self.assert_reason(45, lambda: normalize(self.root, self.old_parent, self.live))
        self.assertEqual(REASON_OTHER_SUBMODULE_DRIFT, 45)
        self.assertEqual(self._git(self._sub(), "rev-parse", "HEAD"), self.h2)
        self.assertEqual((self._sub() / "rom.txt").read_text(encoding="utf-8"), "operator edit\n")

    def test_missing_checkout_is_refused(self):
        import shutil

        shutil.rmtree(self._sub())
        self.assert_reason(REASON_SUBMODULE_MISSING, lambda: normalize(self.root, self.old_parent, self.live))

    def test_moved_root_is_refused(self):
        (self.root / "README.md").write_text("root2\n", encoding="utf-8")
        self._git(self.root, "commit", "-am", "moved")
        self.assert_reason(REASON_ROOT_MOVED, lambda: normalize(self.root, self.old_parent, self.live))

    def test_invalid_sha_is_refused(self):
        self.assert_reason(REASON_INVALID_SHA, lambda: normalize(self.root, "not-a-sha", self.live))

    def test_missing_gitlink_is_refused(self):
        self._git(self.root, "rm", "--quiet", ".gitmodules")
        self._git(
            self.root,
            "update-index",
            "--force-remove",
            HANJUKU,
        )
        self._git(self.root, "commit", "-m", "drop gitlink")
        dropped = self._git(self.root, "rev-parse", "HEAD")
        self.assert_reason(REASON_OLD_GITLINK_MISMATCH, lambda: normalize(self.root, dropped, self.live))

    def test_script_fits_production_exec_stdin_budget(self):
        script = (Path(__file__).resolve().parent.parent / "normalize_other_submodules.py").read_bytes()
        self.assertNotIn(b"\0", script)
        self.assertLessEqual(len(script) + 256, 16384)

    def test_matching_candidates_keep_success(self):
        self._git(self._sub(), "checkout", "--detach", "--quiet", self.h1)
        normalize(self.root, self.old_parent, self.live)
        self.assertEqual(self._git(self._sub(), "rev-parse", "HEAD"), self.h1)

    def test_absent_candidate_reports_ordinal_without_writing(self):
        (self.live / "deploy/title-day/soren-title-day.service").unlink()
        before = (self.live / "deploy/title-day/soren-title-day.timer").read_bytes()
        self.assert_reason(102, lambda: normalize(self.root, self.old_parent, self.live))
        self.assertFalse((self.live / "deploy/title-day/soren-title-day.service").exists())
        self.assertEqual((self.live / "deploy/title-day/soren-title-day.timer").read_bytes(), before)

    def test_mode_drifted_candidate_is_canonicalized(self):
        import os
        import stat

        timer = self.live / "deploy/title-day/soren-title-day.timer"
        os.chmod(timer, 0o600)
        normalize(self.root, self.old_parent, self.live)
        self.assertEqual(timer.read_text(encoding="utf-8"), "old timer\n")
        self.assertEqual(stat.S_IMODE(timer.stat().st_mode), 0o644)

    def test_mixed_mode_and_content_drift_refuses_before_any_write(self):
        import os
        import stat

        service = self.live / "deploy/title-day/soren-title-day.service"
        timer = self.live / "deploy/title-day/soren-title-day.timer"
        os.chmod(service, 0o600)
        timer.write_text("drift\n", encoding="utf-8")
        # state [1, 2]: the mode repair must not run before the refusal.
        self.assert_reason(107, lambda: normalize(self.root, self.old_parent, self.live))
        self.assertEqual(stat.S_IMODE(service.stat().st_mode), 0o600)
        self.assertEqual(timer.read_text(encoding="utf-8"), "drift\n")

    def test_hanjuku_heal_and_mode_repair_compose(self):
        import os
        import stat

        self._git(self._sub(), "checkout", "--detach", "--quiet", self.h2)
        timer = self.live / "deploy/title-day/soren-title-day.timer"
        os.chmod(timer, 0o600)
        normalize(self.root, self.old_parent, self.live)
        self.assertEqual(self._git(self._sub(), "rev-parse", "HEAD"), self.h1)
        self.assertEqual(stat.S_IMODE(timer.stat().st_mode), 0o644)

    def test_edited_candidates_report_pair_code_without_writing(self):
        (self.live / "deploy/title-day/soren-title-day.service").write_text("drift\n", encoding="utf-8")
        (self.live / "deploy/title-day/soren-title-day.timer").write_text("drift\n", encoding="utf-8")
        self.assert_reason(108, lambda: normalize(self.root, self.old_parent, self.live))
        self.assertEqual((self.live / "deploy/title-day/soren-title-day.service").read_text(encoding="utf-8"), "drift\n")


if __name__ == "__main__":
    unittest.main()
