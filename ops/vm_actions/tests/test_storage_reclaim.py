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

    def test_voicevox_archive_is_an_explicit_control_plane_flag(self):
        helper = HELPER.read_text()
        self.assertIn("VOICEVOX_ARCHIVE", helper)
        workflow = (ROOT / ".github" / "workflows" / "vm-operations.yml").read_text()
        self.assertIn("voicevox_archive", workflow)
        self.assertIn("VOICEVOX_ARCHIVE=%s", workflow)

    def test_logrotate_config_sets_owner_for_group_writable_log_dir(self):
        # /home/ubuntu/soren/logs is group-writable and holds both ubuntu- and
        # root-owned logs, so logrotate must run with an explicit owner that can
        # read every file and is not disabled by the insecure-permission check.
        text = HELPER.read_text()
        self.assertIn("su root root", text)
        self.assertIn("copytruncate", text)


class OpenCodeDbRotationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.soren = self.base / "soren"
        (self.soren / "tmp").mkdir(parents=True)
        self.db = self.base / "opencode.db"
        import sqlite3

        con = sqlite3.connect(self.db)
        con.executescript(
            """
            create table session(id text primary key, time_created integer);
            create table event_sequence(aggregate_id text primary key, seq integer);
            create table event(id text primary key, aggregate_id text references event_sequence(aggregate_id) on delete cascade);
            create table message(id text primary key, session_id text references session(id) on delete cascade);
            create table part(id text primary key, message_id text references message(id) on delete cascade, session_id text);
            """
        )
        now = int(time.time() * 1000)
        old, recent = now - 10 * 86400000, now - 1 * 86400000
        for sid, stamp in (("old", old), ("new", recent)):
            con.execute("insert into session values (?,?)", (sid, stamp))
            con.execute("insert into event_sequence values (?,1)", (sid,))
            con.execute("insert into event values (?,?)", (f"e-{sid}", sid))
            con.execute("insert into message values (?,?)", (f"m-{sid}", sid))
            con.execute("insert into part values (?,?,?)", (f"p-{sid}", f"m-{sid}", sid))
        con.commit()
        con.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def counts(self):
        import sqlite3

        con = sqlite3.connect(self.db)
        out = {
            t: con.execute(f"select count(*) from {t}").fetchone()[0]
            for t in ("session", "event", "event_sequence", "message", "part")
        }
        con.close()
        return out

    def run_helper(self, *args, apply=False):
        env = dict(os.environ)
        env["APPLY"] = "1" if apply else "0"
        return subprocess.run(
            ["bash", str(HELPER), "--root", str(self.soren), "--skip-system",
             "--opencode-rotate", "--opencode-db", str(self.db),
             "--opencode-retention-days", "3", "--opencode-force", *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60, check=False,
        )

    def test_dry_run_counts_old_sessions_without_deleting(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OPENCODE old_sessions=1", result.stdout)
        self.assertEqual(self.counts()["session"], 2)

    def test_apply_removes_only_old_sessions_and_children(self):
        result = self.run_helper(apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = self.counts()
        self.assertEqual(counts["session"], 1)
        self.assertEqual(counts["event"], 1)
        self.assertEqual(counts["event_sequence"], 1)
        self.assertEqual(counts["message"], 1)
        self.assertEqual(counts["part"], 1)

    def test_workflow_exposes_opencode_rotation_flags(self):
        workflow = (ROOT / ".github" / "workflows" / "vm-operations.yml").read_text()
        self.assertIn("opencode_db_rotate", workflow)
        self.assertIn("opencode_retention_days", workflow)
        self.assertIn("OPENCODE_DB_ROTATE=%s", workflow)


if __name__ == "__main__":
    unittest.main()
