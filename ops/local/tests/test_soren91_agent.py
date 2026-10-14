import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
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
        data = agent.service_plist("/usr/bin/python3", "/service.py", "/config.json")
        self.assertEqual(data["LimitLoadToSessionType"], "Aqua")
        self.assertTrue(data["RunAtLoad"])
        self.assertTrue(data["KeepAlive"])
        self.assertGreaterEqual(data["ThrottleInterval"], 30)
        self.assertNotIn(self.secret, plistlib.dumps(data).decode())
        self.assertNotIn("EnvironmentVariables", data)

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
        with patch.object(agent.sys, "platform", "darwin"), \
             patch.object(agent.Path, "home", return_value=self.root), \
             patch.object(agent.subprocess, "run", side_effect=[
                 subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1),
                 subprocess.CompletedProcess([], 0)]) as run, \
             patch.object(agent.socket, "socket") as sock, contextlib.redirect_stdout(io.StringIO()):
            sock.return_value.__enter__.return_value.connect_ex.return_value = 1
            agent.install(self.config)
        root, plist = agent.paths(self.root)
        self.assertEqual(run.call_args.args[0][:3], ["launchctl", "bootstrap", "gui/" + str(os.getuid())])
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
