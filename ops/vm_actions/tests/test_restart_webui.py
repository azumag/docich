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
if [[ "${1:-}" == "--user" && "${2:-}" == "restart" ]]; then
  exit "${FAKE_SYSTEMCTL_RESTART_EXIT:-0}"
fi
if [[ "${1:-}" == "--user" && "${2:-}" == "show" ]]; then
  case "$*" in
    *--property=ActiveState\ --value*) printf '%s\\n' "${FAKE_ACTIVE_STATE:-active}" ;;
    *) printf '%s\\n' "ActiveState=${FAKE_ACTIVE_STATE:-active}" ;;
  esac
  exit 0
fi
echo "unexpected systemctl invocation: $*" >&2
exit 9
"""


class RestartWebuiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="restart-webui-")
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)
        self.fake_bin = self.base / "bin"
        self.fake_bin.mkdir()
        fake = self.fake_bin / "systemctl"
        fake.write_text(FAKE_SYSTEMCTL, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.calls_log = self.base / "calls.log"
        self.calls_log.write_text("", encoding="utf-8")
        self.xdg_log = self.base / "xdg.log"
        self.xdg_log.write_text("", encoding="utf-8")

    def run_helper(self, **overrides):
        env = dict(os.environ)
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env["SYSTEMCTL_CALLS_LOG"] = str(self.calls_log)
        env["SYSTEMCTL_XDG_LOG"] = str(self.xdg_log)
        env.pop("XDG_RUNTIME_DIR", None)
        env.update(overrides)
        return subprocess.run(
            ["bash", str(HELPER)],
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )

    def calls(self):
        return [line for line in self.calls_log.read_text(encoding="utf-8").splitlines() if line]

    def test_restarts_fixed_unit_and_confirms_active(self):
        p = self.run_helper()
        self.assertEqual(p.returncode, 0, p.stderr)
        calls = self.calls()
        self.assertEqual(calls[0], "--user restart docich-webui.service")
        self.assertTrue(any("--user show docich-webui.service" in c and "--property=ActiveState --value" in c for c in calls))
        self.assertTrue(any("ActiveState,SubState,MainPID,ExecMainStartTimestamp" in c for c in calls))
        self.assertIn("ActiveState=active", p.stdout)
        # no root/sudo
        self.assertNotIn("sudo", p.stdout)
        self.assertNotIn("sudo", p.stderr)

    def test_sets_xdg_runtime_dir_for_sessionless_exec(self):
        p = self.run_helper()
        self.assertEqual(p.returncode, 0, p.stderr)
        xdg = [line for line in self.xdg_log.read_text(encoding="utf-8").splitlines() if line]
        self.assertTrue(xdg)
        self.assertTrue(all(line.startswith("/run/user/") for line in xdg), xdg)

    def test_restart_failure_fails_closed(self):
        p = self.run_helper(FAKE_SYSTEMCTL_RESTART_EXIT="1")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.calls(), ["--user restart docich-webui.service"])

    def test_inactive_unit_fails_closed(self):
        p = self.run_helper(FAKE_ACTIVE_STATE="failed")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not active", p.stderr)

    def test_rejects_arguments(self):
        env = dict(os.environ)
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env["SYSTEMCTL_CALLS_LOG"] = str(self.calls_log)
        p = subprocess.run(
            ["bash", str(HELPER), "extra"],
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_source_is_bounded_to_the_fixed_unit(self):
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn('unit="docich-webui.service"', text)
        self.assertIn('systemctl --user restart "$unit"', text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("eval ", text)


if __name__ == "__main__":
    unittest.main()
