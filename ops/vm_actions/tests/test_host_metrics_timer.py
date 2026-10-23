"""Contract tests for the docich#2578 host metrics systemd units and deploy hook.

The machine-readable contract lives in
``ops/vm_actions/host_metrics_contract.md``; these checks keep the fixed
bounds, the timer scheduling footguns and the deploy wiring from drifting.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SYSTEMD_DIR = ROOT / "scripts" / "systemd"
WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations.yml"
HOOK = ROOT / "ops" / "vm_actions" / "ensure_host_metrics_timer.sh"

SERVICE = "docich-host-metrics.service"
TIMER = "docich-host-metrics.timer"
NETHACK_STEP = "- name: Ensure NetHack daily improvement timer"
HOST_STEP = "- name: Ensure host metrics timer"
RESTART_STEP = "- name: Restart radio worker after reviewed Soren runtime update"


def parse_unit(text):
    """Parse a systemd unit into ``{section: {key: value}}``.

    Raises AssertionError for an indented key, the classic footgun that turns a
    ``[Timer]`` key into a parser error or a silently different setting.
    """
    sections = {}
    current = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith(("#", ";")):
            continue
        if raw != raw.lstrip():
            raise AssertionError("indented unit key: %r" % raw)
        if raw.startswith("[") and raw.endswith("]"):
            current = raw[1:-1]
            sections[current] = {}
            continue
        if current is None:
            raise AssertionError("key outside a unit section: %r" % raw)
        key, separator, value = raw.partition("=")
        if not separator:
            raise AssertionError("unit line without '=': %r" % raw)
        sections[current][key.strip()] = value.strip()
    return sections


class HostMetricsUnitTests(unittest.TestCase):

    def setUp(self):
        self.service = parse_unit((SYSTEMD_DIR / SERVICE).read_text(
            encoding="utf-8"))
        self.timer = parse_unit((SYSTEMD_DIR / TIMER).read_text(
            encoding="utf-8"))

    def test_service_runs_the_fixed_sampler_once_with_hard_bounds(self):
        service = self.service["Service"]
        text = (SYSTEMD_DIR / SERVICE).read_text(encoding="utf-8")
        self.assertEqual(service["Type"], "oneshot")
        self.assertEqual(
            service["ExecStart"],
            "/usr/bin/python3 __DOCICH_ROOT__/ops/vm_actions/host_metrics.py",
        )
        self.assertEqual(service["WorkingDirectory"], "__DOCICH_ROOT__")
        # Both environment entries are explicit: the user bus socket and a fixed
        # PATH so the six fixed `systemctl` probes never depend on inheritance.
        self.assertIn("Environment=XDG_RUNTIME_DIR=%t\n", text)
        self.assertIn("Environment=PATH=/usr/local/bin:/usr/bin:/bin\n", text)
        self.assertEqual(service["UMask"], "0077")
        self.assertEqual(service["Nice"], "10")
        self.assertEqual(service["IOSchedulingClass"], "idle")
        self.assertRegex(service["MemoryMax"], r"^\d+[MG]$")
        self.assertRegex(service["TimeoutStartSec"], r"^\d+$")
        self.assertLessEqual(int(service["TimeoutStartSec"]), 120)
        self.assertNotIn("Install", self.service)
        self.assertNotIn("ExecStartPre", service)

    def test_production_invocation_stays_argument_free(self):
        # ``--state-dir`` exists only for tests and debugging; the unit must let
        # the sampler resolve the runtime user's passwd home itself.
        for name in (SERVICE, TIMER):
            text = (SYSTEMD_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("--state-dir", text)
        self.assertNotIn("--print", (SYSTEMD_DIR / SERVICE).read_text(
            encoding="utf-8"))

    def test_timer_keys_sit_in_the_timer_section_with_the_fixed_cadence(self):
        timer = self.timer["Timer"]
        self.assertEqual(timer["OnCalendar"], "*:*:00")
        self.assertEqual(timer["AccuracySec"], "1s")
        self.assertEqual(timer["Unit"], SERVICE)
        self.assertEqual(self.timer["Install"]["WantedBy"], "timers.target")
        self.assertNotIn("WantedBy", timer)
        self.assertNotIn("OnCalendar", self.timer["Unit"])
        self.assertNotIn("OnCalendar", self.timer["Install"])

    def test_no_unit_escalates_to_a_fixed_root_user(self):
        for name, sections in ((SERVICE, self.service), (TIMER, self.timer)):
            with self.subTest(unit=name):
                for section in sections.values():
                    self.assertNotIn("User", section)
                    self.assertNotIn("Group", section)
        for name in (SERVICE, TIMER):
            text = (SYSTEMD_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("User=root", text)
            self.assertNotIn("sudo", text)

    def test_readme_documents_both_units(self):
        readme = (SYSTEMD_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn("`%s`" % SERVICE, readme)
        self.assertIn("`%s`" % TIMER, readme)
        self.assertIn("~/.local/state/docich/host-metrics/ring.jsonl", readme)


class DeployHookContractTests(unittest.TestCase):

    def setUp(self):
        self.script = HOOK.read_text(encoding="utf-8")

    def test_hook_installs_only_the_fixed_units_and_state_path(self):
        for required in (
            'service="%s"' % SERVICE,
            'timer="%s"' % TIMER,
            'state_dir="$home/.local/state/docich/host-metrics"',
            'ring="$state_dir/ring.jsonl"',
            'root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"',
            'export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"',
            'sed "s#__DOCICH_ROOT__#$root#g"',
            "chmod 0644",
            "mv -f",
            "mktemp",
            "[[ -f \"$source\" && ! -L \"$source\" && ! -L \"$target\" ]]",
            "systemctl --user daemon-reload",
            'systemctl --user enable --now "$timer"',
            'systemctl --user is-enabled --quiet "$timer"',
            'systemctl --user is-active --quiet "$timer"',
            'systemctl --user start "$service"',
            "stat -c '%a' -- \"$ring\"",
            "stat -c '%a' -- \"$state_dir\"",
        ):
            self.assertIn(required, self.script)
        for forbidden in ("sudo", "eval ", "systemctl --user restart",
                          "systemctl --user stop", "docich-corner-rotation",
                          "docich-webui", "docich-nethack", "docich-paper",
                          "docich-opencode", "soren-runtime",
                          "--state-dir", "--print"):
            self.assertNotIn(forbidden, self.script)

    def test_hook_resolves_the_home_from_passwd_not_HOME(self):
        # The gateway exec channel scrubs HOME; the hook and the sampler must
        # agree on the passwd home or the deploy checks a different ring than
        # the timer writes.
        self.assertIn('command -v getent', self.script)
        self.assertIn('getent passwd "$(id -u)"', self.script)
        self.assertIn("cut -d: -f6", self.script)
        self.assertIn('/home/ubuntu', self.script)
        self.assertNotIn('ring="$HOME', self.script)
        self.assertNotIn("$HOME/.local/state", self.script)
        # The unit tree must be under the same passwd home; a scrubbed HOME
        # fails closed instead of installing the timer somewhere else.
        self.assertIn('[[ "$unit_dir" == "$home"/* ]] || exit 2', self.script)

    def test_hook_uses_the_documented_exit_codes(self):
        self.assertIn("set -euo pipefail", self.script)
        # 2: missing or misplaced units, failed installation.
        self.assertGreaterEqual(self.script.count("|| exit 2"), 3)
        # 3: the first sample could not be verified.
        self.assertGreaterEqual(self.script.count("|| exit 3"), 1)
        self.assertIn("[[ -s \"$ring\" ]] || exit 3", self.script)
        self.assertIn('== "600"', self.script)
        self.assertIn('== "700"', self.script)

    @unittest.skipUnless(shutil.which("bash"), "bash is not available")
    def test_hook_parses_as_bash(self):
        result = subprocess.run(
            ["bash", "-n", str(HOOK)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class WorkflowHookTests(unittest.TestCase):

    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def block(self, marker, end_marker):
        self.assertIn(marker, self.workflow)
        return self.workflow.split(marker, 1)[1].split(end_marker, 1)[0]

    def guard(self, block):
        for line in block.splitlines():
            if line.strip().startswith("if:"):
                return line.strip()
        self.fail("workflow step has no if: guard")

    def test_host_metrics_step_follows_nethack_with_the_same_guard(self):
        nethack = self.block(NETHACK_STEP, HOST_STEP)
        host = self.block(HOST_STEP, RESTART_STEP)
        self.assertEqual(self.guard(host), self.guard(nethack))
        self.assertIn("steps.deploy_initial.outcome == 'success'", host)
        self.assertIn("steps.deploy_retry.outcome == 'success'", host)
        self.assertIn("steps.auth.outputs.target == 'production'", host)
        # The new step is inserted directly after the NetHack step.
        self.assertNotIn("- name:", nethack)

    def test_host_metrics_step_invokes_the_fixed_hook_via_production_exec(self):
        host = self.block(HOST_STEP, RESTART_STEP)
        nethack = self.block(NETHACK_STEP, HOST_STEP)
        self.assertIn("control/ops/vm_actions/ensure_host_metrics_timer.sh",
                      host)
        self.assertIn('"exec docich production $SHA"', host)
        # The hook is parsed before it is handed to the gateway.
        self.assertIn(
            "bash -n control/ops/vm_actions/ensure_host_metrics_timer.sh",
            host)
        self.assertEqual(
            [line.strip() for line in host.splitlines()
             if line.strip().startswith("ssh_args=")],
            [line.strip() for line in nethack.splitlines()
             if line.strip().startswith("ssh_args=")],
        )
        self.assertEqual(
            [line.strip() for line in host.splitlines()
             if line.strip().startswith("env:")],
            ["env:"],
        )


if __name__ == "__main__":
    unittest.main()
