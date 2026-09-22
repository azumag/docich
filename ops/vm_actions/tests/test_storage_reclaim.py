import os
import subprocess
import sys
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
        argv = ["bash", str(HELPER), "--root", str(self.soren)]
        # Hermetic default: unless a test supplies its own clone, point the
        # stale-clone allowlist at an absent path so a developer machine that
        # happens to hold /tmp/opencode/docich-sync is never a test target.
        if not any(a == "--stale-clone" for a in args):
            argv += ["--stale-clone", str(self.base / "no-such-clone")]
        argv.append("--skip-system")
        argv += list(args)
        return subprocess.run(
            argv,
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

    # ---- new: manual_challenge dated siblings ---------------------------

    def test_manual_challenge_pattern_covers_dated_siblings_but_respects_age_gate(self):
        dated_old = self.make("manual_challenge_20260824_meriken", OLD)
        dated_recent = self.make("manual_challenge_20260920_recent", RECENT)

        result = self.run_helper(apply=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(dated_old.exists(), result.stdout)
        self.assertTrue(dated_recent.exists(), result.stdout)

    # ---- new: stale /tmp clone ------------------------------------------

    def age_tree(self, path, mtime):
        """Set mtime on a directory and everything inside it."""
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                os.utime(os.path.join(root, name), (mtime, mtime))
            os.utime(root, (mtime, mtime))

    def make_clone(self, name, mtime, origin="https://github.com/azumag/docich.git"):
        clone = self.base / name
        subprocess.run(["git", "init", "-q", str(clone)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if origin:
            subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", origin],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.age_tree(clone, mtime)
        return clone

    def test_stale_clone_removed_only_when_old_unreferenced_and_docich_origin(self):
        stale = self.make_clone("docich-sync", OLD)

        dry = self.run_helper("--stale-clone", str(stale))
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertTrue(any(l.startswith("DEL") and str(stale) in l for l in dry.stdout.splitlines()), dry.stdout)
        self.assertTrue(stale.exists())  # dry-run must not delete

        applied = self.run_helper("--stale-clone", str(stale), apply=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertFalse(stale.exists(), applied.stdout)

    def test_stale_clone_kept_when_recent_or_foreign_origin_or_not_a_clone(self):
        recent = self.make_clone("recent-clone", RECENT)
        foreign = self.make_clone("foreign-clone", OLD, origin="https://github.com/example/other.git")
        plain = self.base / "plain-dir"
        plain.mkdir()
        (plain / "file").write_text("x")
        self.age_tree(plain, OLD)

        for target, needle in ((recent, "touched within"), (foreign, "origin is not"), (plain, "not a git working tree")):
            result = self.run_helper("--stale-clone", str(target), apply=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.exists(), result.stdout)
            self.assertIn(needle, result.stdout)

    def test_stale_clone_kept_while_referenced_by_running_process(self):
        stale = self.make_clone("referenced-clone", OLD)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", str(stale)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.2)
            result = self.run_helper("--stale-clone", str(stale), apply=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(stale.exists(), result.stdout)
            self.assertIn("referenced by", result.stdout)
        finally:
            proc.kill()
            proc.wait(timeout=10)

        after = self.run_helper("--stale-clone", str(stale), apply=True)
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertFalse(stale.exists(), after.stdout)

    @unittest.skipUnless(os.path.isdir("/proc"), "cwd reference scan requires Linux /proc")
    def test_stale_clone_kept_while_a_process_sits_in_it_without_path_in_args(self):
        # cwd-only reference: argv deliberately does not contain the path, so
        # only the /proc/<pid>/cwd scan can catch it (production fail-closed).
        stale = self.make_clone("cwd-clone", OLD)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=str(stale), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.2)
            result = self.run_helper("--stale-clone", str(stale), apply=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(stale.exists(), result.stdout)
            self.assertIn("referenced by", result.stdout)
        finally:
            proc.kill()
            proc.wait(timeout=10)

    # ---- new: snapd download cache --------------------------------------

    def test_snap_cache_age_gated_via_explicit_root_even_with_skip_system(self):
        cache = self.base / "snap-cache"
        cache.mkdir()
        old = cache / "old-blob"
        old.write_bytes(b"o" * 64)
        os.utime(old, (OLD, OLD))
        recent = cache / "recent-blob"
        recent.write_bytes(b"r" * 64)
        os.utime(recent, (RECENT, RECENT))

        dry = self.run_helper("--snap-cache-root", str(cache))
        self.assertEqual(dry.returncode, 0, dry.stderr)
        del_lines = [l for l in dry.stdout.splitlines() if l.startswith("DEL")]
        self.assertTrue(any(str(old) in l for l in del_lines), dry.stdout)
        self.assertFalse(any(str(recent) in l for l in del_lines), dry.stdout)
        self.assertTrue(old.exists())  # dry-run must not delete

        applied = self.run_helper("--snap-cache-root", str(cache), apply=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertFalse(old.exists(), applied.stdout)
        self.assertTrue(recent.exists(), applied.stdout)
        self.assertTrue(cache.is_dir())  # the cache directory itself is kept

    def test_system_paths_are_untouched_when_skip_system_without_explicit_root(self):
        # Without --snap-cache-root, --skip-system must keep the helper away
        # from /var/lib/snapd/cache entirely (CI runners have that path).
        result = self.run_helper(apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("/var/lib/snapd/cache", result.stdout)


class ControlPlaneWiringTests(unittest.TestCase):
    def test_authorize_allows_only_production_reclaim(self):
        text = (ROOT / "ops" / "vm_actions" / "authorize.py").read_text()
        self.assertIn("'reclaim'", text)
        self.assertIn("op=='reclaim' and target!='production'", text)
        self.assertIn("op=='reclaim' and ref!='main'", text)

    def test_workflow_exposes_fixed_reclaim_operation(self):
        text = (ROOT / ".github" / "workflows" / "vm-operations.yml").read_text()
        self.assertIn("status, deploy, exec, configure_jev, disable_jev, configure_jev_route_direct, "
                     "configure_jev_route_vercel, disable_jev_route, bootstrap, diagnostics, reclaim", text)
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

    def test_docker_reclaim_is_dangling_and_age_bounded_only(self):
        # Volumes and tagged images must never be prunable from this helper:
        # the PAPER sandbox contract forbids volumes, and canary images must
        # survive until the age gate passes.
        text = HELPER.read_text()
        self.assertIn("docker image prune -f --filter", text)
        self.assertIn("docker builder prune -f --filter", text)
        self.assertIn('until=${docker_image_max_age}', text)
        self.assertIn('docker_image_max_age="168h"', text)
        self.assertIn('docker_builder_max_age="168h"', text)
        self.assertNotIn("volume prune", text)
        self.assertNotIn("image prune -a", text)
        self.assertNotIn("system prune", text)

    def test_stale_clone_targets_are_fixed_allowlist_and_origin_checked(self):
        text = HELPER.read_text()
        self.assertIn('stale_clones=("/tmp/opencode/docich-sync")', text)
        self.assertIn("azumag/docich", text)
        self.assertIn("stale_clone_refs", text)
        # The control plane must not forward these test-only path flags.
        workflow = (ROOT / ".github" / "workflows" / "vm-operations.yml").read_text()
        self.assertNotIn("--stale-clone", workflow)
        self.assertNotIn("--snap-cache-root", workflow)
        self.assertNotIn("--root", workflow.split("Run reviewed storage reclaim", 1)[-1])


if __name__ == "__main__":
    unittest.main()
