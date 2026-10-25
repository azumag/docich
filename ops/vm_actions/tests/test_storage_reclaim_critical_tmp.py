import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "storage_reclaim_critical_tmp.sh"
OLD = time.time() - 13 * 3600
RECENT = time.time() - 60


class CriticalTmpReclaimTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def age_tree(self, path, stamp):
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                os.utime(Path(root) / name, (stamp, stamp))
            os.utime(root, (stamp, stamp))

    def make_repo(self, name, stamp=OLD):
        path = self.tmp / name
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
        (path / "tracked.txt").write_text("safe\n")
        subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(path), "commit", "-qm", "seed"], check=True)
        self.age_tree(path, stamp)
        return path

    def run_helper(self, apply=False):
        env = dict(os.environ)
        env["APPLY"] = "1" if apply else "0"
        return subprocess.run(
            ["bash", str(HELPER), "--sys-tmp", str(self.tmp)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )

    def test_old_clean_unreferenced_known_worktree_is_removed(self):
        target = self.make_repo("soviet-2489-fix")
        result = self.run_helper(apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(target.exists(), result.stdout)

    def test_recent_known_worktree_is_kept(self):
        target = self.make_repo("persist-recon", RECENT)
        result = self.run_helper(apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(target.exists(), result.stdout)
        self.assertIn("modified within 720m", result.stdout)

    def test_dirty_known_worktree_is_kept(self):
        target = self.make_repo("wt1029")
        (target / "tracked.txt").write_text("changed\n")
        self.age_tree(target, OLD)
        result = self.run_helper(apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(target.exists(), result.stdout)
        self.assertIn("uncommitted changes", result.stdout)

    def test_referenced_known_worktree_is_kept(self):
        target = self.make_repo("soviet-2489-fix")
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.2)
            result = self.run_helper(apply=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.exists(), result.stdout)
            self.assertIn("referenced by", result.stdout)
        finally:
            proc.kill()
            proc.wait(timeout=10)

    def test_unknown_tmp_directory_is_never_considered(self):
        unknown = self.make_repo("other-worktree")
        result = self.run_helper(apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(unknown.exists(), result.stdout)
        self.assertNotIn(str(unknown), result.stdout)

    def test_gateway_payload_has_large_headroom(self):
        self.assertLess(len(b"APPLY=1\n") + len(HELPER.read_bytes()), 12000)


if __name__ == "__main__":
    unittest.main()
