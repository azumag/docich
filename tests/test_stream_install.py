"""Unit placement tool: read-only status, two gated mutations, never start anything."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_install as inst  # noqa: E402

REAL_UNIT = ROOT / "deploy/docich-stream/docich-stream.service"
FORBIDDEN_VERBS = {"enable", "start", "restart", "stop", "disable", "reload", "kill", "mask", "link", "edit"}


class Sandbox:
    def __init__(self, testcase):
        self.tmp = tempfile.TemporaryDirectory(prefix="stream-install-")
        testcase.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.paths = inst.Paths(home=self.home, unit_dir=self.home / "etc-systemd")
        (self.home / "etc-systemd").mkdir()
        self.paths.unit_source.parent.mkdir(parents=True)
        shutil.copy(REAL_UNIT, self.paths.unit_source)
        self.paths.wrapper.parent.mkdir(parents=True)
        self.paths.wrapper.write_text("#!/bin/sh\n")
        self.paths.wrapper.chmod(0o755)
        self.paths.soren_root.mkdir()
        self.calls = []

    def run(self, argv, **kw):
        """A fake sudo/systemctl/pactl: `install` really copies; everything is recorded."""
        self.calls.append(list(argv))
        head = argv[:2] == ["sudo", "-n"] and argv[2:] or argv
        if head[0] == "install":
            shutil.copyfile(head[-2], head[-1])
        return subprocess.CompletedProcess(argv, self.returncodes.get(head[0], 0), self.stdout.get(tuple(head[:2]), ""), "")

    returncodes: dict = {}
    stdout: dict = {}


def sandbox(testcase):
    box = Sandbox(testcase)
    box.returncodes, box.stdout = {}, {}
    return box


class StatusTest(unittest.TestCase):
    def test_status_is_read_only_and_exposes_only_enums_and_hashes(self):
        box = sandbox(self)
        (box.paths.soren_root / ".env").write_text(
            "STREAM_KEY=live_SECRET_abc123\nTWITCH_TOKEN='oauth:SECRET'\nSOREN_STREAM_BACKEND=ffmpeg\n"
            "export DOCICH_STREAM_OWNER=docich\nDOCICH_STREAM_CANONICAL_LOCK_ENABLED=0\nSOREN_STREAM_BACKEND=obs\n")
        box.stdout = {("pactl", "list"): "0\tsoren_null\tmodule-null-sink.c\ts16le 2ch 44100Hz\tIDLE\n",
                      ("systemctl", "is-enabled"): "disabled\n", ("systemctl", "is-active"): "inactive\n"}
        state = inst.collect_status(box.paths, box.run)
        text = json.dumps(state)
        for secret in ("SECRET", "oauth", "live_", "rtmp"):
            self.assertNotIn(secret, text)
        self.assertEqual(state["env"], {"backend": "obs", "stream_owner": "docich", "canonical_lock_flag": "0"})
        self.assertEqual((state["unit"]["enabled"], state["unit"]["active"]), ("disabled", "inactive"))
        self.assertEqual(state["unit"]["installed"], False)
        self.assertTrue(state["wrapper_executable"])
        self.assertEqual(state["lock_dir"], "missing")
        self.assertEqual(state["pulse"], {"server_answers": True, "soren_null_sink": True})
        self.assertEqual(len(state["unit"]["source_sha256"]), 64)
        for call in box.calls:
            self.assertNotEqual(call[0], "sudo", "status never escalates")
            if call[0] == "systemctl":
                self.assertIn(call[1], inst.READONLY_SYSTEMCTL)

    def test_missing_pieces_are_reported_not_raised(self):
        box = sandbox(self)
        box.returncodes = {"pactl": 1}
        state = inst.collect_status(box.paths, box.run)
        self.assertEqual(state["env"], {"backend": "unset", "stream_owner": "unset", "canonical_lock_flag": "unset"})
        self.assertEqual(state["pulse"], {"server_answers": False, "soren_null_sink": False})
        def broken(argv, **kw):
            raise OSError("no such binary")
        state = inst.collect_status(box.paths, broken)
        self.assertEqual(state["unit"]["enabled"], "unavailable")

    def test_unusual_env_values_are_classified_not_echoed(self):
        box = sandbox(self)
        (box.paths.soren_root / ".env").write_text("SOREN_STREAM_BACKEND=\"hunter2\"\nDOCICH_STREAM_OWNER=hunter3\n")
        state = inst.collect_status(box.paths, box.run)
        self.assertEqual(state["env"]["backend"], "other")
        self.assertEqual(state["env"]["stream_owner"], "invalid")
        self.assertNotIn("hunter", json.dumps(state))

    def test_installed_unit_comparison(self):
        box = sandbox(self)
        self.assertFalse(inst.collect_status(box.paths, box.run)["unit"]["matches_source"])
        shutil.copy(box.paths.unit_source, box.paths.unit_target)
        self.assertTrue(inst.collect_status(box.paths, box.run)["unit"]["matches_source"])
        box.paths.unit_target.write_text("[Service]\n")
        self.assertFalse(inst.collect_status(box.paths, box.run)["unit"]["matches_source"])


class InstallTest(unittest.TestCase):
    def install(self, box, **kw):
        options = dict(confirm=True, run=box.run, analyze=False, require_default_home=False)
        options.update(kw)
        return inst.install_unit(box.paths, **options)

    def test_places_the_unit_and_reloads_only(self):
        box = sandbox(self)
        result = self.install(box)
        self.assertEqual(result, {"unit": "installed", "daemon_reload": True, "enabled": False, "started": False})
        self.assertEqual(box.paths.unit_target.read_bytes(), box.paths.unit_source.read_bytes())
        self.assertEqual([c[:3] for c in box.calls], [["sudo", "-n", "install"], ["sudo", "-n", "systemctl"]])
        install_call, reload_call = box.calls
        self.assertEqual(install_call[3:9], ["-o", "root", "-g", "root", "-m", "0644"])
        self.assertEqual(reload_call, ["sudo", "-n", "systemctl", "daemon-reload"])
        flat = {part for call in box.calls for part in call}
        self.assertFalse(flat & FORBIDDEN_VERBS, "nothing may be enabled, started, restarted or stopped")

    def test_a_second_run_is_a_no_op(self):
        box = sandbox(self)
        self.install(box)
        box.calls.clear()
        self.assertEqual(self.install(box), {"unit": "unchanged", "daemon_reload": False})
        self.assertEqual(box.calls, [])

    def test_every_refusal_changes_nothing(self):
        for label, mutate, kwargs, text in (
            ("no confirmation", lambda b: None, dict(confirm=False), "--confirm-unit-placement"),
            ("wrong root", lambda b: None, dict(require_default_home=True), "unexpected install root"),
            ("source missing", lambda b: b.paths.unit_source.unlink(), {}, "unit source is missing"),
            ("environment file", lambda b: b.paths.unit_source.write_text(REAL_UNIT.read_text() + "EnvironmentFile=/x\n"), {}, "EnvironmentFile"),
            ("pre-start hook", lambda b: b.paths.unit_source.write_text(REAL_UNIT.read_text() + "ExecStartPre=/x\n"), {}, "ExecStartPre"),
            ("stream url", lambda b: b.paths.unit_source.write_text(REAL_UNIT.read_text() + "# rtmp://x/y\n"), {}, "rtmp://"),
            ("kill mode", lambda b: b.paths.unit_source.write_text(REAL_UNIT.read_text().replace("KillMode=mixed", "KillMode=control-group")), {}, "control-group"),
            ("no restart policy", lambda b: b.paths.unit_source.write_text(REAL_UNIT.read_text().replace("Restart=always", "Restart=no")), {}, "Restart=always"),
            ("other exec", lambda b: b.paths.unit_source.write_text(REAL_UNIT.read_text().replace("docich-stream-run", "evil")), {}, "ExecStart"),
            ("wrapper not executable", lambda b: b.paths.wrapper.chmod(0o644), {}, "executable"),
            ("different unit installed", lambda b: b.paths.unit_target.write_text("[Service]\n"), {}, "already installed"),
            ("target is a symlink", lambda b: b.paths.unit_target.symlink_to(b.home / "elsewhere"), {}, "symlink"),
            ("analyze rejects", lambda b: None, dict(analyze=True, run=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "")), "rejected"),
        ):
            with self.subTest(label):
                box = sandbox(self)
                before = box.paths.unit_target.exists()
                mutate(box)
                with self.assertRaisesRegex(inst.InstallRefused, text):
                    self.install(box, **kwargs)
                self.assertEqual([c for c in box.calls if c[0] == "sudo"], [], "no privileged command ran")
                if label not in ("different unit installed", "target is a symlink"):
                    self.assertEqual(box.paths.unit_target.exists(), before)

    def test_a_failing_privileged_step_is_reported(self):
        box = sandbox(self)
        box.returncodes = {"install": 1}
        with self.assertRaisesRegex(inst.InstallRefused, "install failed"):
            self.install(box)
        box = sandbox(self)
        box.returncodes = {"systemctl": 1}
        with self.assertRaisesRegex(inst.InstallRefused, "systemctl failed"):
            self.install(box)

    def test_a_privileged_copy_that_does_not_match_is_caught(self):
        box = sandbox(self)
        def corrupting(argv, **kw):
            box.calls.append(list(argv))
            if "install" in argv:
                box.paths.unit_target.write_text("tampered")
            return subprocess.CompletedProcess(argv, 0, "", "")
        with self.assertRaisesRegex(inst.InstallRefused, "does not match"):
            self.install(box, run=corrupting)


class LockDirTest(unittest.TestCase):
    def test_creates_private_directories_and_is_idempotent(self):
        box = sandbox(self)
        self.assertEqual(inst.provision_lock_dir(box.paths, confirm=True), {"lock_dir": "created"})
        for directory in (box.paths.lock_dir, box.paths.lock_dir.parent):
            self.assertEqual(stat_mode(directory), 0o700)
        self.assertEqual(inst.provision_lock_dir(box.paths, confirm=True), {"lock_dir": "unchanged"})
        self.assertEqual(inst.lock_dir_state(box.paths.lock_dir), "ready")

    def test_refusals(self):
        box = sandbox(self)
        with self.assertRaisesRegex(inst.InstallRefused, "--confirm-lock-dir"):
            inst.provision_lock_dir(box.paths, confirm=False)
        self.assertFalse(box.paths.lock_dir.exists())
        for mode in (0o755, 0o770, 0o707):
            box.paths.lock_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(box.paths.lock_dir, mode)
            with self.subTest(mode=oct(mode)), self.assertRaisesRegex(inst.InstallRefused, "not a private"):
                inst.provision_lock_dir(box.paths, confirm=True)
            self.assertEqual(stat_mode(box.paths.lock_dir), mode, "an existing directory is never chmodded")
        shutil.rmtree(box.paths.lock_dir.parent)
        box.paths.lock_dir.parent.mkdir()
        box.paths.lock_dir.symlink_to(box.home)
        with self.assertRaisesRegex(inst.InstallRefused, "not a private"):
            inst.provision_lock_dir(box.paths, confirm=True)
        empty = inst.Paths(home=box.home / "nowhere")
        with self.assertRaisesRegex(inst.InstallRefused, "docich root does not exist"):
            inst.provision_lock_dir(empty, confirm=True)

    def test_a_file_in_place_of_the_directory_is_unsafe(self):
        box = sandbox(self)
        box.paths.lock_dir.parent.mkdir(parents=True)
        box.paths.lock_dir.write_text("x")
        self.assertEqual(inst.lock_dir_state(box.paths.lock_dir), "unsafe")


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


class CliTest(unittest.TestCase):
    def run_cli(self, *args, extra_env=None):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "src"), **(extra_env or {})}
        return subprocess.run([sys.executable, "-B", "-m", "docich.stream_install", *args], capture_output=True,
                              text=True, timeout=30, check=False, env=env, cwd="/")

    def test_one_action_is_required_and_the_mutations_are_gated(self):
        self.assertEqual(self.run_cli().returncode, 2)
        self.assertEqual(self.run_cli("--status", "--install").returncode, 2)
        for args in (("--install",), ("--provision-lock-dir",)):
            done = self.run_cli(*args)
            self.assertEqual((done.returncode, done.stdout), (3, ""), done.stderr)
            self.assertIn("refused", done.stderr)

    def test_read_only_actions_print_json(self):
        config = json.loads(self.run_cli("--print-config").stdout)
        self.assertEqual(config["unit_target"], "/etc/systemd/system/docich-stream.service")
        self.assertIn("start", config["never_does"])
        status = self.run_cli("--status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("unit", json.loads(status.stdout))

    def test_install_on_a_host_that_is_not_the_runtime_host_is_refused(self):
        try:
            runtime_host = Path("/home/ubuntu/docich").exists()
        except OSError:
            # Python 3.12 Path.exists re-raises EACCES; an inaccessible
            # /home/ubuntu means this is not the runtime host.
            runtime_host = False
        if runtime_host:
            self.skipTest("this is the runtime host")
        done = self.run_cli("--install", "--confirm-unit-placement")
        self.assertEqual(done.returncode, 3)
        self.assertTrue("unexpected install root" in done.stderr or "unit source" in done.stderr or "wrapper" in done.stderr)

    def test_launcher_runs_the_module(self):
        launcher = ROOT / "bin/docich-stream-install"
        self.assertTrue(os.access(launcher, os.X_OK))
        done = subprocess.run([str(launcher), "--print-config"], capture_output=True, text=True, timeout=30, check=False,
                              env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["contract"], 1)


class StaticTest(unittest.TestCase):
    def test_no_lifecycle_verb_can_reach_the_privileged_path(self):
        source = (ROOT / "src/docich/stream_install.py").read_text()
        tree = ast.parse(source)
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        # The only literal places the lifecycle verbs may appear is the human-readable never_does list.
        for verb in ("enable", "start", "restart", "stop", "disable"):
            hits = [s for s in strings if s == verb]
            self.assertEqual(len(hits), 1, f"{verb!r} must appear only in never_does")
        self.assertEqual(inst.MUTATING_SYSTEMCTL, frozenset({"daemon-reload"}))
        self.assertFalse(inst.READONLY_SYSTEMCTL & inst.MUTATING_SYSTEMCTL)


if __name__ == "__main__":
    unittest.main()
