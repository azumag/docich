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
        # Hermetic defaults: point every production-root allowlist at a temp
        # dir (or an absent path) so a developer machine that happens to hold
        # /tmp/opencode/docich-sync or /home/ubuntu leftovers is never a test
        # target, and CI never evaluates its real /tmp.
        if not any(a == "--stale-clone" for a in args):
            argv += ["--stale-clone", str(self.base / "no-such-clone") + "|github.com/azumag/docich"]
        if not any(a == "--home-root" for a in args):
            argv += ["--home-root", str(self.base / "fakehome")]
        if not any(a == "--sys-tmp" for a in args):
            argv += ["--sys-tmp", str(self.base / "faketmp")]
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

    def test_deploy_and_radio_quarantine_join_stale_patterns(self):
        # audited 2026-09-23: soren/tmp/deploy (8/16, only self-references and
        # stale caption artifacts) and radio_quarantine (8/12) are safe to
        # retire under the same 21-day gate as the other stale patterns.
        deploy_old = self.make("deploy", OLD)
        quarantine_old = self.make("radio_quarantine", OLD)

        result = self.run_helper(apply=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(deploy_old.exists(), result.stdout)
        self.assertFalse(quarantine_old.exists(), result.stdout)

    def test_deploy_recent_is_kept_by_age_gate(self):
        deploy = self.make("deploy", RECENT)
        result = self.run_helper(apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(deploy.exists(), result.stdout)
        self.assertIn("KEEP", result.stdout)

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

        spec = f"{stale}|github.com/azumag/docich"
        dry = self.run_helper("--stale-clone", spec)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertTrue(any(l.startswith("DEL") and str(stale) in l for l in dry.stdout.splitlines()), dry.stdout)
        self.assertTrue(stale.exists())  # dry-run must not delete

        applied = self.run_helper("--stale-clone", spec, apply=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertFalse(stale.exists(), applied.stdout)

    def test_stale_clone_kept_when_recent_or_foreign_origin_or_not_a_clone(self):
        recent = self.make_clone("recent-clone", RECENT)
        foreign = self.make_clone("foreign-clone", OLD, origin="https://github.com/example/other.git")
        plain = self.base / "plain-dir"
        plain.mkdir()
        (plain / "file").write_text("x")
        self.age_tree(plain, OLD)

        docich = "github.com/azumag/docich"
        cases = (
            (recent, f"{recent}|{docich}", "touched within"),
            (foreign, f"{foreign}|{docich}", "origin does not match"),
            (plain, f"{plain}|{docich}", "not a git working tree"),
        )
        for target, spec, needle in cases:
            result = self.run_helper("--stale-clone", spec, apply=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.exists(), result.stdout)
            self.assertIn(needle, result.stdout)

    def test_stale_clone_kept_when_origin_spec_missing(self):
        stale = self.make_clone("no-spec-clone", OLD)
        result = self.run_helper("--stale-clone", str(stale), apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(stale.exists(), result.stdout)
        self.assertIn("without origin", result.stdout)

    def test_stale_clone_kept_when_dirty(self):
        dirty = self.make_clone("dirty-clone", OLD)
        (dirty / "uncommitted.txt").write_text("work in progress")
        # Age the new file too, so the freshness gate passes and the dirty
        # check itself is what saves the clone.
        self.age_tree(dirty, OLD)
        spec = f"{dirty}|github.com/azumag/docich"
        result = self.run_helper("--stale-clone", spec, apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(dirty.exists(), result.stdout)
        self.assertIn("uncommitted changes", result.stdout)

    def test_soren_src_entry_requires_soviet_now_origin(self):
        # soren-src (soviet_now clone) must only go when its own origin matches.
        wrong = self.make_clone("soren-src-wrong", OLD)  # docich origin
        spec = f"{wrong}|github.com/azumag/soviet_now"
        result = self.run_helper("--stale-clone", spec, apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(wrong.exists(), result.stdout)
        self.assertIn("origin does not match", result.stdout)

        right = self.make_clone("soren-src-right", OLD, origin="https://github.com/azumag/soviet_now.git")
        spec2 = f"{right}|github.com/azumag/soviet_now"
        applied = self.run_helper("--stale-clone", spec2, apply=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertFalse(right.exists(), applied.stdout)

    def test_stale_clone_kept_while_referenced_by_running_process(self):
        stale = self.make_clone("referenced-clone", OLD)
        spec = f"{stale}|github.com/azumag/docich"
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", str(stale)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.2)
            result = self.run_helper("--stale-clone", spec, apply=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(stale.exists(), result.stdout)
            self.assertIn("referenced by", result.stdout)
        finally:
            proc.kill()
            proc.wait(timeout=10)

        after = self.run_helper("--stale-clone", spec, apply=True)
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertFalse(stale.exists(), after.stdout)

    @unittest.skipUnless(os.path.isdir("/proc"), "cwd reference scan requires Linux /proc")
    def test_stale_clone_kept_while_a_process_sits_in_it_without_path_in_args(self):
        # cwd-only reference: argv deliberately does not contain the path, so
        # only the /proc/<pid>/cwd scan can catch it (production fail-closed).
        stale = self.make_clone("cwd-clone", OLD)
        spec = f"{stale}|github.com/azumag/docich"
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=str(stale), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.2)
            result = self.run_helper("--stale-clone", spec, apply=True)
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

    # ---- new: fixed stale_paths (HOME + system /tmp) --------------------

    def stale_roots(self):
        home = self.base / "fakehome"
        systmp = self.base / "faketmp"
        home.mkdir(exist_ok=True)
        systmp.mkdir(exist_ok=True)
        return home, systmp

    def test_stale_paths_removed_when_old_and_unreferenced(self):
        home, systmp = self.stale_roots()
        old_home = home / "soren91-r97"
        old_home.mkdir()
        (old_home / "f").write_text("x")
        self.age_tree(old_home, OLD)
        old_tmp = systmp / "soren91-phase1-rx.ts"
        old_tmp.write_text("const x = 1;")
        os.utime(old_tmp, (OLD, OLD))

        applied = self.run_helper(apply=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertFalse(old_home.exists(), applied.stdout)
        self.assertFalse(old_tmp.exists(), applied.stdout)

    def test_stale_paths_kept_when_recent(self):
        home, systmp = self.stale_roots()
        recent = home / "soren91-corner-verify"
        recent.mkdir()
        (recent / "f").write_text("x")
        self.age_tree(recent, RECENT)  # now, not OLD

        result = self.run_helper(apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(recent.exists(), result.stdout)
        self.assertIn("touched within", result.stdout)

    def test_stale_paths_kept_while_referenced_by_running_process(self):
        home, systmp = self.stale_roots()
        target = systmp / "issue303_srt_recv.ts"
        target.write_text("data")
        os.utime(target, (OLD, OLD))
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", str(target)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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

        after = self.run_helper(apply=True)
        self.assertFalse(target.exists(), after.stdout)

    def test_stale_paths_evaluated_only_under_flag_roots(self):
        # The production defaults must never be evaluated during tests: real
        # /home/ubuntu paths (e.g. the live encoder at build/) stay out of the
        # plan because --home-root points elsewhere.
        result = self.run_helper("--home-root", str(self.base / "fakehome"),
                                 "--sys-tmp", str(self.base / "faketmp"))
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            self.assertNotIn("/home/ubuntu/build", line)
            self.assertNotIn("/home/ubuntu/soren91-r97", line)
            self.assertNotIn("/tmp/s91test", line)

    # ---- new: AivisSpeech opt-in ----------------------------------------

    def aivis_fixture(self):
        voicevox = self.base / "voicevox"
        (voicevox / "current").mkdir(parents=True, exist_ok=True)
        run = voicevox / "current" / "run"
        run.write_text("#!/bin/sh\n")
        run.chmod(0o755)
        aivis = self.base / "AivisSpeech-Engine"
        (aivis / "engine.bin").parent.mkdir(parents=True, exist_ok=True)
        (aivis / "engine.bin").write_bytes(b"e" * 1024)
        return voicevox, aivis

    def test_aivis_engine_requires_opt_in(self):
        voicevox, aivis = self.aivis_fixture()
        result = self.run_helper("--aivis-root", str(aivis),
                                 "--voicevox-root", str(voicevox), apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(aivis.exists(), "must not remove without opt-in")
        self.assertNotIn(str(aivis), "\n".join(
            l for l in result.stdout.splitlines() if l.startswith("DEL")))

    def test_aivis_engine_removed_only_with_opt_in_and_voicevox_intact(self):
        voicevox, aivis = self.aivis_fixture()
        applied = self.run_helper("--aivis-root", str(aivis),
                                  "--voicevox-root", str(voicevox),
                                  "--include-aivis-engine", apply=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertFalse(aivis.exists(), applied.stdout)

        # Guard: VOICEVOX engine missing -> keep Aivis (never remove both).
        _, aivis2 = self.aivis_fixture()
        broken_vv = self.base / "voicevox-broken"
        broken_vv.mkdir()
        keep = self.run_helper("--aivis-root", str(aivis2),
                               "--voicevox-root", str(broken_vv),
                               "--include-aivis-engine", apply=True)
        self.assertEqual(keep.returncode, 0, keep.stderr)
        self.assertTrue(aivis2.exists(), keep.stdout)
        self.assertIn("voicevox not intact", keep.stdout)


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
        # Both production clone entries are path|origin specs (no bare paths).
        self.assertIn('"/tmp/opencode/docich-sync|github.com/azumag/docich"', text)
        self.assertIn('"/home/ubuntu/soren-src|github.com/azumag/soviet_now"', text)
        self.assertIn("expected_origin", text)
        self.assertIn("stale_clone_refs", text)
        self.assertIn("uncommitted changes present", text)
        # The live streaming encoder must never join the reclaim allowlist.
        self.assertNotIn('"/home/ubuntu/build"', text)
        self.assertNotIn('"$home_root/build"', text)
        # The control plane must not forward these test-only path flags.
        workflow = (ROOT / ".github" / "workflows" / "vm-operations.yml").read_text()
        self.assertNotIn("--stale-clone", workflow)
        self.assertNotIn("--snap-cache-root", workflow)
        self.assertNotIn("--home-root", workflow)
        self.assertNotIn("--sys-tmp", workflow)
        self.assertNotIn("--aivis-root", workflow)
        self.assertNotIn("--root", workflow.split("Run reviewed storage reclaim", 1)[-1])

    def test_aivis_engine_is_an_explicit_control_plane_flag(self):
        # Mirror of the VOICEVOX contract: helper reads AIVIS_ENGINE, the
        # workflow exposes a boolean input and forwards it via stdin preamble.
        helper = HELPER.read_text()
        self.assertIn("AIVIS_ENGINE", helper)
        self.assertIn("--include-aivis-engine", helper)
        self.assertIn("voicevox not intact", helper)
        workflow = (ROOT / ".github" / "workflows" / "vm-operations.yml").read_text()
        self.assertIn("aivis_engine", workflow)
        self.assertIn("AIVIS_ENGINE=%s", workflow)
        self.assertIn("AIVIS_ENGINE: ${{ inputs.aivis_engine }}", workflow)

    def test_stale_paths_entries_are_absolute_allowlisted_and_live_encoder_excluded(self):
        text = HELPER.read_text()
        self.assertIn("stale_paths=(", text)
        self.assertIn('"$sys_tmp/s91test"', text)
        self.assertIn('"$home_root/soren91-r97"', text)
        # soren-persist is referenced by strategy/persist.sh -> excluded.
        self.assertNotIn('"$home_root/soren-persist"', text)
        self.assertIn("live streaming encoder", text)

    def test_helper_payload_fits_gateway_stdin_cap(self):
        # gateway execute() reads at most 16384 bytes from stdin; the control
        # plane prepends a 3-line preamble (APPLY / VOICEVOX_ARCHIVE /
        # AIVIS_ENGINE). Exceeding the cap makes every production reclaim run
        # fail with operation_rejected (observed 2026-09-23 on PR #1005's
        # first apply attempt: helper had grown to 16919 bytes).
        preamble = b"APPLY=0\nVOICEVOX_ARCHIVE=0\nAIVIS_ENGINE=0\n"
        payload = preamble + HELPER.read_bytes()
        self.assertLessEqual(
            len(payload), 16384,
            f"helper + preamble = {len(payload)} bytes exceeds the 16384-byte "
            "gateway exec stdin cap; trim comments or split the helper",
        )
        # Leave headroom for future flag additions (warn before it is too late).
        self.assertLessEqual(len(payload), 16000,
                             "helper is within 384 bytes of the gateway cap; trim now")


if __name__ == "__main__":
    unittest.main()
