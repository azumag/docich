"""Contract tests for the VM Hanjuku month/boss audit issue timer."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SYSTEMD = ROOT / "scripts" / "systemd"
SERVICE = SYSTEMD / "docich-hanjuku-month-audit.service"
TIMER = SYSTEMD / "docich-hanjuku-month-audit.timer"
HOOK = ROOT / "ops" / "vm_actions" / "ensure_hanjuku_month_audit_timer.sh"
WRAPPER = ROOT / "ops" / "vm_actions" / "hanjuku_month_audit_watch.py"
WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations.yml"


def load_wrapper():
    spec = importlib.util.spec_from_file_location("hanjuku_audit_watch", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UnitTests(unittest.TestCase):
    def test_service_is_bounded_and_runs_the_fixed_wrapper(self):
        text = SERVICE.read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", text)
        self.assertIn("Environment=XDG_RUNTIME_DIR=%t", text)
        self.assertIn("Environment=PATH=/usr/local/bin:/usr/bin:/bin", text)
        self.assertIn("Environment=AGENT_GUARDS=%h/agent-guards", text)
        self.assertIn("WorkingDirectory=__DOCICH_ROOT__", text)
        self.assertIn(
            "ExecStart=/usr/bin/python3 __DOCICH_ROOT__/ops/vm_actions/hanjuku_month_audit_watch.py",
            text,
        )
        self.assertIn("UMask=0077", text)
        self.assertIn("Nice=10", text)
        self.assertIn("TimeoutStartSec=240", text)
        self.assertNotIn("User=", text)
        self.assertNotIn("sudo", text)

    def test_timer_is_five_minutes_and_user_scoped(self):
        text = TIMER.read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*:0/5", text)
        self.assertIn("AccuracySec=30s", text)
        self.assertIn("Persistent=false", text)
        self.assertIn("Unit=docich-hanjuku-month-audit.service", text)
        self.assertIn("WantedBy=timers.target", text)
        self.assertNotIn("User=", text)

    def test_hook_installs_only_its_units_and_checks_gh_and_scanner(self):
        text = HOOK.read_text(encoding="utf-8")
        for expected in (
            'service="docich-hanjuku-month-audit.service"',
            'timer="docich-hanjuku-month-audit.timer"',
            "command -v gh",
            "ops/vm_actions/secret_scan.py",
            "systemctl --user daemon-reload",
            'systemctl --user enable --now "$timer"',
            'systemctl --user is-enabled --quiet "$timer"',
            'systemctl --user is-active --quiet "$timer"',
            "--dry-run",
            "mktemp",
            "mv -f",
        ):
            self.assertIn(expected, text)
        for forbidden in ("sudo", "systemctl --user start", "User=root"):
            self.assertNotIn(forbidden, text)

    def test_workflow_installs_the_timer_after_a_production_deploy(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Ensure Hanjuku month audit timer"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1].split("- name:", 1)[0]
        self.assertIn("steps.auth.outputs.target == 'production'", block)
        self.assertIn("steps.deploy_initial.outcome == 'success'", block)
        self.assertIn("steps.deploy_retry.outcome == 'success'", block)
        self.assertIn("control/ops/vm_actions/ensure_hanjuku_month_audit_timer.sh", block)
        self.assertIn('"exec docich production $SHA"', block)


class StateDirTests(unittest.TestCase):
    def test_resolves_a_relative_state_dir_under_the_root(self):
        wrapper = load_wrapper()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run-soren-live").mkdir()
            config = root / "config.toml"
            config.write_text('[paths]\nstate_dir = "run-soren-live"\n', encoding="utf-8")
            self.assertEqual(wrapper.resolve_state_dir(root, config),
                             (root / "run-soren-live").resolve())

    def test_rejects_missing_and_outside_state_dirs(self):
        wrapper = load_wrapper()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text("[paths]\nstate_dir = \"../outside\"\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                wrapper.resolve_state_dir(root, config)
            config.write_text("[paths]\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                wrapper.resolve_state_dir(root, config)


if __name__ == "__main__":
    unittest.main()
