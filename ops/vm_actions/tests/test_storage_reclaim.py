import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "storage_reclaim.sh"

OLD = time.time() - 60 * 86400
RECENT = time.time() - 1 * 86400


class StorageReclaimTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.soren = self.base / "soren"
        self.tmp = self.soren / "tmp"
        self.tmp.mkdir(parents=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def make(self, rel, mtime, content=b"x" * 32):
        path = self.tmp / rel
        path.mkdir(parents=True)
        (path / "data.bin").write_bytes(content)
        os.utime(path, (mtime, mtime))
        return path

    def run_helper(self, *args, apply=False):
        env = dict(os.environ)
        env["APPLY"] = "1" if apply else "0"
        return subprocess.run(
            ["bash", str(HELPER), "--root", str(self.soren), "--skip-system", *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60, check=False,
        )

    def test_dry_run_plans_only_old_allowlisted_paths(self):
        old = self.make("direct_av_sync", OLD)
        recent = self.make("manual_challenge", RECENT)
        protected = self.make("soviet_local_chromium_profile", OLD)
        state = self.make("state", OLD)

        result = self.run_helper()

        self.assertEqual(result.returncode, 0, result.stderr)
        del_lines = [l for l in result.stdout.splitlines() if l.startswith("DEL")]
        self.assertTrue(any(str(old) in l for l in del_lines), result.stdout)
        self.assertFalse(any(str(recent) in l for l in del_lines), result.stdout)
        self.assertFalse(any(str(protected) in l for l in del_lines), result.stdout)
        self.assertFalse(any(str(state) in l for l in del_lines), result.stdout)
        # Dry-run must not delete anything.
        for path in (old, recent, protected, state):
            self.assertTrue(path.exists(), path)

    def test_apply_removes_only_old_allowlisted_paths(self):
        old = self.make("direct_stream_benchmark", OLD)
        recent = self.make("game-lifecycle-e2e", RECENT)
        protected = self.make("debug", OLD)

        result = self.run_helper(apply=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(protected.exists())

    def test_deploy_backups_prunes_only_old_entries_and_keeps_directory(self):
        root = self.tmp / "deploy-backups"
        root.mkdir()
        old = root / "old-release"
        recent = root / "recent-release"
        for path, stamp in ((old, OLD), (recent, RECENT)):
            path.mkdir()
            (path / "x").write_text("y")
            os.utime(path, (stamp, stamp))

        result = self.run_helper(apply=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(root.is_dir())
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_voicevox_archive_requires_opt_in_and_engine(self):
        voicevox = self.base / "voicevox"
        (voicevox / "current").mkdir(parents=True)
        engine = voicevox / "current" / "run"
        engine.write_text("#!/bin/sh\n")
        engine.chmod(0o755)
        archive = voicevox / "voicevox.7z.001"
        archive.write_bytes(b"z" * 1024)

        without = self.run_helper("--voicevox-root", str(voicevox))
        self.assertEqual(without.returncode, 0, without.stderr)
        self.assertNotIn(str(archive), without.stdout)

        withopt = self.run_helper("--voicevox-root", str(voicevox), "--include-voicevox-archive")
        self.assertEqual(withopt.returncode, 0, withopt.stderr)
        self.assertIn(str(archive), withopt.stdout)
        self.assertTrue(archive.exists())  # still dry-run

    def test_rejects_unknown_option(self):
        result = self.run_helper("--nope")
        self.assertEqual(result.returncode, 2)


class ControlPlaneWiringTests(unittest.TestCase):
    def test_authorize_allows_only_production_reclaim(self):
        text = (ROOT / "ops" / "vm_actions" / "authorize.py").read_text()
        self.assertIn("'reclaim'", text)
        self.assertIn("op=='reclaim' and target!='production'", text)
        self.assertIn("op=='reclaim' and ref!='main'", text)

    def test_workflow_exposes_fixed_reclaim_operation(self):
        text = (ROOT / ".github" / "workflows" / "vm-operations.yml").read_text()
        self.assertIn("status, deploy, exec, bootstrap, diagnostics, reclaim", text)
        self.assertIn("control/ops/vm_actions/storage_reclaim.sh", text)
        self.assertIn("APPLY", text)
        # Arbitrary exec must stay behind the public-repo guard in authorize.py.
        self.assertIn("arbitrary VM exec is disabled when the repository is public", (ROOT / "ops" / "vm_actions" / "authorize.py").read_text())

    def test_logrotate_config_sets_owner_for_group_writable_log_dir(self):
        # /home/ubuntu/soren/logs is group-writable, so logrotate needs an
        # explicit owner or it skips every file as insecure.
        text = HELPER.read_text()
        self.assertIn("su ubuntu ubuntu", text)
        self.assertIn("copytruncate", text)


if __name__ == "__main__":
    unittest.main()
