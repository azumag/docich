import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

SCRIPT = Path(__file__).resolve().parents[1] / "soren91_agent.py"
spec = importlib.util.spec_from_file_location("soren91_agent", SCRIPT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class AgentServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="soren91-service-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        repo = self.root / "repo"
        (repo / "tools").mkdir(parents=True)
        (repo / "tools/soren91_local_agent.mjs").write_text("// fixture\n")
        self.token = self.root / "token"
        self.secret = "test-only-never-print-this-token"
        self.token.write_text(self.secret + "\n")
        self.token.chmod(0o600)
        self.config = dict(repo=str(repo), node="/bin/sh", ffmpeg="/bin/sh",
                           token_file=str(self.token), host="100.66.1.2", port=19191)

    def test_credential_file_owner_permissions_shape_and_symlink(self):
        self.assertEqual(agent.read_token(self.token), self.secret)
        self.token.chmod(0o644)
        with self.assertRaises(ValueError):
            agent.read_token(self.token)
        self.token.chmod(0o600)
        link = self.root / "link"
        link.symlink_to(self.token)
        with self.assertRaises(OSError):
            agent.read_token(link)
        for bad in ["short", self.secret + "\nsecond-line", "a" * 4097,
                    self.secret + "\x00"]:
            self.token.write_text(bad)
            with self.subTest(bad_length=len(bad)), self.assertRaises(ValueError):
                agent.read_token(self.token)

    def test_network_exposure_fails_closed(self):
        for host in ["0.0.0.0", "127.0.0.1", "192.168.1.1", "::", "example.org"]:
            with self.subTest(host=host), self.assertRaises(ValueError):
                agent.validate(dict(self.config, host=host))
        for port in [True, 80, 65536, "19191"]:
            with self.subTest(port=port), self.assertRaises(ValueError):
                agent.validate(dict(self.config, port=port))

    def test_environment_keeps_existing_secret_but_drops_shell_overrides(self):
        env = agent.environment(self.config, {"HOME": str(self.root),
            "SOREN91_LOCAL_SESSION_MODE": "session", "NODE_OPTIONS": "--inspect=0.0.0.0",
            "SOREN91_LOCAL_AUDIO_GAIN": "9", "HTTP_PROXY": "http://untrusted",
            "UNRELATED_SECRET": "not-copied"})
        self.assertEqual(env["SOREN91_LOCAL_AGENT_TOKEN"], self.secret)
        self.assertEqual(env["SOREN91_LOCAL_SESSION_MODE"], "cdp-host")
        self.assertEqual(env["SOREN91_LOCAL_FFMPEG_BIN"], "/bin/sh")
        self.assertEqual(env["SOREN91_LOCAL_AUDIO_GAIN"], "1.4")
        self.assertNotIn("NODE_OPTIONS", env)
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("UNRELATED_SECRET", env)

    def test_launchd_gui_restart_contract_has_no_secret(self):
        app = Path("/Users/x/Library/Application Support/docich/soren91-agent/Soren91Renderer.app")
        data = agent.service_plist(app)
        self.assertEqual(data["LimitLoadToSessionType"], "Aqua")
        self.assertTrue(data["RunAtLoad"])
        self.assertTrue(data["KeepAlive"])
        self.assertGreaterEqual(data["ThrottleInterval"], 30)
        # LaunchServices must own the app identity for the TCC grant.
        self.assertEqual(data["ProgramArguments"][:2], ["/usr/bin/open", "-W"])
        self.assertIn(str(app), data["ProgramArguments"])
        self.assertNotIn(self.secret, plistlib.dumps(data).decode())
        self.assertNotIn("EnvironmentVariables", data)

    def test_app_bundle_identity_is_the_tcc_grant_target(self):
        info = agent.app_info_plist()
        self.assertEqual(info["CFBundleIdentifier"], "com.docich.soren91-renderer")
        self.assertEqual(info["CFBundleExecutable"], "Soren91Renderer")
        self.assertEqual(info["CFBundlePackageType"], "APPL")
        self.assertTrue(info["LSBackgroundOnly"])
        self.assertNotIn("LSUIElement", info)
        self.assertEqual(agent.app_bundle("/root") , Path("/root/Soren91Renderer.app"))

    def test_launcher_source_bakes_absolute_paths_as_c_literals(self):
        source = agent.launcher_source("/usr/bin/python3", "/srv/agent/service.py",
                                       "/srv/agent/config.json")
        self.assertIn('"/usr/bin/python3"', source)
        self.assertIn('"/srv/agent/service.py"', source)
        self.assertIn('"/srv/agent/config.json"', source)
        # The launcher must fork, not exec, to keep the app as responsible.
        self.assertIn("fork()", source)
        self.assertIn("waitpid(", source)
        self.assertNotIn("execve(", source)
        self.assertIn('"run"', source)
        # bootout stops `open`; the app must stop its child on SIGTERM itself.
        self.assertIn("sigaction(SIGTERM", source)
        self.assertIn("kill((pid_t)child_pid, SIGTERM)", source)

    def test_c_string_escapes_and_rejects_relative_paths(self):
        self.assertEqual(agent._c_string('/a"b\\c'), '"/a\\"b\\\\c"')
        self.assertEqual(agent._c_string("/a/ゆ"), '"/a/\\343\\202\\206"')
        for bad in ("relative/path", "", "/a\x00b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                agent._c_string(bad)

    def test_build_app_is_darwin_only(self):
        with patch.object(agent.sys, "platform", "linux"):
            with self.assertRaisesRegex(ValueError, "macos-required"):
                agent.build_app(self.root, Path("/usr/bin/python3"), Path("/s.py"),
                                Path("/c.json"))

    def test_build_app_rejects_interpreter_on_external_volume(self):
        with patch.object(agent.sys, "platform", "darwin"), \
             patch.object(agent, "CLANG", "/bin/sh"), \
             patch.object(agent, "CODESIGN", "/bin/sh"):
            with self.assertRaisesRegex(ValueError, "removable-volume"):
                agent.build_app(self.root, Path("/Volumes/ext/python3"), Path("/s.py"),
                                Path("/c.json"))

    @unittest.skipUnless(sys.platform == "darwin" and Path("/usr/bin/clang").is_file(),
                         "macOS toolchain required")
    def test_build_app_compiles_and_signs_the_launcher(self):
        root = self.root / "install"
        root.mkdir()
        script = root / "service.py"
        script.write_text("# fixture\n")
        config = root / "config.json"
        config.write_text("{}\n")
        python = Path("/usr/bin/python3")
        if not python.is_file():
            self.skipTest("system python3 required")
        app = agent.build_app(root, python, script, config)
        binary = app / "Contents/MacOS/Soren91Renderer"
        self.assertTrue(binary.is_file())
        self.assertEqual(binary.stat().st_mode & 0o111, 0o111)
        self.assertIn(binary.read_bytes()[:4], (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"))
        detail = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=4", str(app)],
                                capture_output=True, text=True, check=True)
        self.assertIn("Identifier=" + agent.APP_BUNDLE_ID, detail.stdout + detail.stderr)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True)

    def test_status_is_authenticated_and_projects_only_fixed_fields(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "ok": True, "backend": "local-macos", "mode": "cdp-host", "running": False,
            "lastExit": self.secret, "untrusted": self.secret}).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(agent.urllib.request, "build_opener", return_value=opener):
            result = agent.status(self.config)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.secret)
        self.assertEqual(result["status"], "healthy")
        self.assertFalse(result["renderer_running"])
        self.assertNotIn(self.secret, json.dumps(result))

    def test_install_refuses_existing_listener_without_killing_or_bootstrap(self):
        with patch.object(agent.sys, "platform", "darwin"), \
             patch.object(agent.subprocess, "run", side_effect=[
                 subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1)]) as run, \
             patch.object(agent.socket, "socket") as sock:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(ValueError, "port-occupied"):
                agent.install(self.config)
        self.assertEqual(run.call_count, 2)

    def test_install_uses_gui_domain_and_keeps_credentials_out_of_artifacts(self):
        app = self.root / "built" / "Soren91Renderer.app"

        def fake_build(root, python, script, config):
            app.parent.mkdir(parents=True, exist_ok=True)
            app.mkdir()
            return app

        with patch.object(agent.sys, "platform", "darwin"), \
             patch.object(agent.Path, "home", return_value=self.root), \
             patch.object(agent, "build_app", side_effect=fake_build) as build, \
             patch.object(agent.subprocess, "run", side_effect=[
                 subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1),
                 subprocess.CompletedProcess([], 0)]) as run, \
             patch.object(agent.socket, "socket") as sock, contextlib.redirect_stdout(io.StringIO()):
            sock.return_value.__enter__.return_value.connect_ex.return_value = 1
            agent.install(self.config)
        root, plist = agent.paths(self.root)
        build.assert_called_once()
        self.assertEqual(build.call_args.args[0], root)
        self.assertEqual(run.call_args.args[0][:3], ["launchctl", "bootstrap", "gui/" + str(os.getuid())])
        data = plistlib.loads(plist.read_bytes())
        self.assertEqual(data["ProgramArguments"][:2], ["/usr/bin/open", "-W"])
        self.assertIn(str(app), data["ProgramArguments"])
        for p in [root / "config.json", plist]:
            self.assertNotIn(self.secret, p.read_text())
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_cli_failure_does_not_print_exception_or_secret(self):
        path = self.root / "config.json"
        path.write_text(json.dumps(self.config))
        output = io.StringIO()
        with patch.object(agent.sys, "argv", [str(SCRIPT), "status", "--config", str(path)]), \
             patch.object(agent, "status", side_effect=RuntimeError(self.secret)), \
             contextlib.redirect_stderr(output):
            self.assertEqual(agent.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "failed", "operation": "status"})
        self.assertNotIn(self.secret, output.getvalue())


if __name__ == "__main__":
    unittest.main()
