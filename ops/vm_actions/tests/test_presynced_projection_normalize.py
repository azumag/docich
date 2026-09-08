from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ops.vm_actions.normalize_presynced_projection import (
    REASON_UNKNOWN_LIVE_STATE,
    NormalizeError,
    normalize,
)


class PresyncedProjectionNormalizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-projection-")
        base = Path(self.tmp.name)
        self.soren = base / "soren"
        self.root = base / "docich"
        self.live = base / "live"
        self._init_repo(self.soren)
        (self.soren / "worker.sh").write_text("old\n", encoding="utf-8")
        self._git(self.soren, "add", "worker.sh")
        self._git(self.soren, "commit", "-m", "old")
        self.old_sub = self._git(self.soren, "rev-parse", "HEAD")
        (self.soren / "worker.sh").write_text("new\n", encoding="utf-8")
        self._git(self.soren, "commit", "-am", "new")
        self.new_sub = self._git(self.soren, "rev-parse", "HEAD")

        self._init_repo(self.root)
        (self.root / ".gitmodules").write_text(
            '[submodule "games/soviet_now"]\n\tpath = games/soviet_now\n\turl = https://github.com/azumag/soviet_now.git\n',
            encoding="utf-8",
        )
        self._git(self.root, "add", ".gitmodules")
        self._git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{self.old_sub},games/soviet_now")
        self._git(self.root, "commit", "-m", "old parent")
        self.old_parent = self._git(self.root, "rev-parse", "HEAD")
        (self.root / "games").mkdir(exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", str(self.soren), str(self.root / "games/soviet_now")], check=True)
        self._git(self.root / "games/soviet_now", "checkout", "--detach", "--quiet", self.old_sub)
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

    def _normalize(self):
        with mock.patch.dict(
            "ops.vm_actions.normalize_presynced_projection.ALLOWED_PROJECTIONS",
            {"games/soviet_now": self.live},
            clear=True,
        ):
            normalize(self.root, self.old_parent, self.old_sub, self.new_sub, "games/soviet_now", self.live)

    def test_exact_reviewed_new_projection_is_restored_to_old(self):
        (self.live / "worker.sh").write_text("new\n", encoding="utf-8")
        self._normalize()
        self.assertEqual((self.live / "worker.sh").read_text(encoding="utf-8"), "old\n")

    def test_existing_old_projection_is_noop(self):
        (self.live / "worker.sh").write_text("old\n", encoding="utf-8")
        self._normalize()
        self.assertEqual((self.live / "worker.sh").read_text(encoding="utf-8"), "old\n")

    def test_unknown_live_projection_is_refused_without_mutation(self):
        path = self.live / "worker.sh"
        path.write_text("operator drift\n", encoding="utf-8")
        with self.assertRaises(NormalizeError) as ctx:
            self._normalize()
        self.assertEqual(ctx.exception.code, REASON_UNKNOWN_LIVE_STATE)
        self.assertEqual(path.read_text(encoding="utf-8"), "operator drift\n")


if __name__ == "__main__":
    unittest.main()
