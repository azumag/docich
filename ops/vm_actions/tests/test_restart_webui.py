import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "restart_webui.sh"

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$SYSTEMCTL_CALLS_LOG"
printf '%s\\n' "${XDG_RUNTIME_DIR:-}" >> "$SYSTEMCTL_XDG_LOG"
case "$*" in
  *"--user daemon-reload"*)
    exit "${FAKE_SYSTEMCTL_DAEMON_RELOAD_EXIT:-0}"
    ;;
  *"--user restart"*)
    exit "${FAKE_SYSTEMCTL_RESTART_EXIT:-0}"
    ;;
  *"--property=ActiveState --value"*)
    printf '%s\\n' "${FAKE_ACTIVE_STATE:-active}"
    ;;
  *"--property=ExecStart"*)
    exec_start="${FAKE_EXEC_START:-/opt/elsewhere/docich/bin/docich}"
    printf 'ExecStart={ path=%s ; argv[]=%s webui ; }\n' "$exec_start" "$exec_start"
    ;;
  *"--user show"*)
    printf 'ActiveState=%s\nSubState=running\nMainPID=4242\nExecMainStartTimestamp=Wed 2026-09-23 00:00:00 JST\n' "${FAKE_ACTIVE_STATE:-active}"
    ;;
  *)
    echo "unexpected systemctl invocation: $*" >&2
    exit 9
    ;;
esac
"""

SERVER = """#!/usr/bin/env python3
import http.server, sys
path = sys.argv[1]
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try: body = open(path, "rb").read()
        except OSError: body = b""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
print(srv.server_address[1], flush=True)
srv.serve_forever()
"""

DEPLOYED_UI = 'INDEX_HTML = r"""<!doctype html><html><body>chain pause</body></html>"""\n'
UNIT_TEMPLATE = """[Service]
WorkingDirectory=__DOCICH_ROOT__
ExecStart=__DOCICH_ROOT__/bin/docich --config __DOCICH_ROOT__/config/docich.soren-live.toml webui
"""


class RestartWebuiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="restart-webui-")
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)
        self.fake_home = self.base / "home"
        self.fake_home.mkdir()
        self.fake_bin = self.base / "bin"
        self.fake_bin.mkdir()
        fake = self.fake_bin / "systemctl"
        fake.write_text(FAKE_SYSTEMCTL, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.calls_log = self.base / "calls.log"
        self.calls_log.write_text("", encoding="utf-8")
        self.xdg_log = self.base / "xdg.log"
        self.xdg_log.write_text("", encoding="utf-8")

        self.prod = self.base / "prod"
        (self.prod / "config").mkdir(parents=True)
        (self.prod / "src" / "docich").mkdir(parents=True)
        (self.prod / "scripts" / "systemd").mkdir(parents=True)
        (self.prod / "scripts" / "systemd" / "docich-webui.service").write_text(
            UNIT_TEMPLATE, encoding="utf-8"
        )
        (self.prod / "src" / "docich" / "webui.py").write_text(
            DEPLOYED_UI, encoding="utf-8"
        )

        self.served = self.base / "served.html"
        self.served.write_text(
            "<!doctype html><html><body>chain pause</body></html>", encoding="utf-8"
        )
        self.server_script = self.base / "server.py"
        self.server_script.write_text(SERVER, encoding="utf-8")
        self.server = subprocess.Popen(
            ["python3", str(self.server_script), str(self.served)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(self._stop_server)
        self.port = int(self.server.stdout.readline().strip())
        (self.prod / "config" / "docich.soren-live.toml").write_text(
            f"[webui]\nport = {self.port}\n", encoding="utf-8"
        )

    def _stop_server(self):
        if getattr(self, "server", None) is None:
            return
        if self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server.kill()
        if self.server.stdout:
            self.server.stdout.close()

    def set_port(self, port: int) -> None:
        (self.prod / "config" / "docich.soren-live.toml").write_text(
            f"[webui]\nport = {port}\n", encoding="utf-8"
        )

    def run_helper(self, *, cwd=None, args=(), **overrides):
        env = dict(os.environ)
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env["HOME"] = str(self.fake_home)
        env["SYSTEMCTL_CALLS_LOG"] = str(self.calls_log)
        env["SYSTEMCTL_XDG_LOG"] = str(self.xdg_log)
        env.pop("XDG_RUNTIME_DIR", None)
        env.update(overrides)
        return subprocess.run(
            ["bash", str(HELPER), *args],
            text=True,
            capture_output=True,
            env=env,
            cwd=str(cwd or self.prod),
            timeout=60,
        )

    def calls(self):
        return [
            line
            for line in self.calls_log.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def installed_unit(self):
        return self.fake_home / ".config" / "systemd" / "user" / "docich-webui.service"

    def test_reconciles_reviewed_unit_before_restart(self):
        p = self.run_helper()
        self.assertEqual(p.returncode, 0, p.stderr)
        text = self.installed_unit().read_text(encoding="utf-8")
        self.assertNotIn("__DOCICH_ROOT__", text)
        # restart_webui.sh renders the unit with `pwd -P` (physical path), so
        # compare against the resolved production root rather than the raw
        # tempdir path (which may go through symlinks such as /var -> /private/var).
        self.assertIn(str(self.prod.resolve()), text)
        self.assertIn("config/docich.soren-live.toml webui", text)
        calls = self.calls()
        self.assertEqual(calls[0], "--user daemon-reload")
        self.assertEqual(calls[1], "--user restart docich-webui.service")

    def test_daemon_reload_failure_fails_closed_before_restart(self):
        p = self.run_helper(FAKE_SYSTEMCTL_DAEMON_RELOAD_EXIT="1")
        self.assertEqual(p.returncode, 15)
        self.assertEqual(self.calls(), ["--user daemon-reload"])

    def test_missing_reviewed_template_fails_closed(self):
        (self.prod / "scripts" / "systemd" / "docich-webui.service").unlink()
        p = self.run_helper()
        self.assertEqual(p.returncode, 15)
        self.assertEqual(self.calls(), [])

    def test_served_html_matching_deployed_index_succeeds(self):
        p = self.run_helper()
        self.assertEqual(p.returncode, 0, p.stderr)
        calls = self.calls()
        self.assertTrue(
            any("ActiveState,SubState,MainPID,ExecMainStartTimestamp" in c for c in calls)
        )
        self.assertFalse(any("--property=ExecStart" in c for c in calls))

    def test_sets_xdg_runtime_dir_for_sessionless_exec(self):
        p = self.run_helper()
        self.assertEqual(p.returncode, 0, p.stderr)
        xdg = [
            line
            for line in self.xdg_log.read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertTrue(xdg)
        self.assertTrue(all(line.startswith("/run/user/") for line in xdg), xdg)

    def test_restart_failure_fails_closed(self):
        p = self.run_helper(FAKE_SYSTEMCTL_RESTART_EXIT="1")
        self.assertEqual(p.returncode, 10)
        self.assertEqual(self.calls()[-1], "--user restart docich-webui.service")

    def test_inactive_unit_fails_closed(self):
        p = self.run_helper(FAKE_ACTIVE_STATE="failed")
        self.assertEqual(p.returncode, 11)
        self.assertIn("not active", p.stderr)

    def test_stale_served_html_with_production_exec_start_is_distinct(self):
        self.served.write_text(
            "<!doctype html><html><body>old ui</body></html>", encoding="utf-8"
        )
        p = self.run_helper(FAKE_EXEC_START=f"{self.prod}/bin/docich")
        self.assertEqual(p.returncode, 14)
        self.assertIn("another process holds the port", p.stderr)
        self.assertTrue(any("--property=ExecStart" in c for c in self.calls()))

    def test_stale_served_html_with_foreign_exec_start_is_distinct(self):
        self.served.write_text(
            "<!doctype html><html><body>old ui</body></html>", encoding="utf-8"
        )
        p = self.run_helper(FAKE_EXEC_START="/opt/elsewhere/docich/bin/docich")
        self.assertEqual(p.returncode, 12)
        self.assertIn("ExecStart does not run the production root", p.stderr)

    def test_unreachable_local_webui_is_distinct(self):
        self.set_port(1)
        p = self.run_helper()
        self.assertEqual(p.returncode, 13)
        self.assertIn("not reachable", p.stderr)

    def test_missing_deployed_index_is_distinct(self):
        (self.prod / "src" / "docich" / "webui.py").unlink()
        p = self.run_helper()
        self.assertEqual(p.returncode, 12)

    def test_rejects_arguments(self):
        p = self.run_helper(args=("extra",))
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_source_is_bounded_to_the_fixed_unit_and_template(self):
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn('unit="docich-webui.service"', text)
        self.assertIn('template="$root/scripts/systemd/docich-webui.service"', text)
        self.assertIn("systemctl --user daemon-reload", text)
        self.assertIn('systemctl --user restart "$unit"', text)
        self.assertIn("config/docich.soren-live.toml", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("eval ", text)


if __name__ == "__main__":
    unittest.main()
