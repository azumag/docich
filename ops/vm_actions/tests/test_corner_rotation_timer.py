import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CANONICAL_SERVICE = "docich-corner-rotation.service"
CANONICAL_TIMER = "docich-corner-rotation.timer"
LEGACY_SERVICE = "docich-retro-corner.service"
LEGACY_TIMER = "docich-retro-corner.timer"
SYSTEMD_DIR = ROOT / "scripts" / "systemd"


class UnitTemplateContractTests(unittest.TestCase):
    def test_canonical_service_template_contract(self):
        service = (SYSTEMD_DIR / CANONICAL_SERVICE).read_text(encoding="utf-8")
        self.assertIn("Environment=XDG_RUNTIME_DIR=%t", service)
        self.assertIn("WorkingDirectory=__DOCICH_ROOT__", service)
        self.assertIn(
            "ExecStart=__DOCICH_ROOT__/bin/docich --config "
            "__DOCICH_ROOT__/config/docich.soren-live.toml corner-rotation tick",
            service,
        )
        self.assertIn("TimeoutStartSec=infinity", service)
        self.assertNotIn("ExecStartPre=", service)
        self.assertNotIn("[Install]", service)

    def test_canonical_timer_starts_only_the_canonical_service(self):
        timer = (SYSTEMD_DIR / CANONICAL_TIMER).read_text(encoding="utf-8")
        self.assertIn("OnActiveSec=30s", timer)
        self.assertIn("OnBootSec=30s", timer)
        self.assertIn("OnUnitActiveSec=60s", timer)
        self.assertIn("AccuracySec=5s", timer)
        self.assertIn("Persistent=false", timer)
        self.assertNotIn("OnCalendar=", timer)
        self.assertIn("Unit=docich-corner-rotation.service", timer)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertNotIn("docich-retro-corner", timer)

    def test_legacy_units_stay_as_reviewed_compatibility_templates(self):
        service = (SYSTEMD_DIR / LEGACY_SERVICE).read_text(encoding="utf-8")
        timer = (SYSTEMD_DIR / LEGACY_TIMER).read_text(encoding="utf-8")
        self.assertIn("Environment=XDG_RUNTIME_DIR=%t", service)
        self.assertIn("corner-rotation tick", service)
        self.assertIn("Unit=docich-retro-corner.service", timer)

    def test_legacy_per_corner_tick_templates_carry_the_user_bus_env(self):
        # These timers still exist in production and delegate to the common
        # rotation tick; a tick they run spawns follow-up jobs with
        # systemd-run --user, which needs XDG_RUNTIME_DIR (#947).
        for name in (
            "docich-paper-corner.service",
            "docich-soren91-corner.service",
            "docich-nethack-corner.service",
        ):
            with self.subTest(unit=name):
                service = (SYSTEMD_DIR / name).read_text(encoding="utf-8")
                self.assertIn("Environment=XDG_RUNTIME_DIR=%t", service)
                self.assertIn("WorkingDirectory=__DOCICH_ROOT__", service)

    def test_stage2_reviewed_epoch_enables_the_production_migration(self):
        # The reviewed epoch is what turns the deploy hook's migration branch
        # on; it must stay a regular file so a deploy can never follow a
        # symlink to an unreviewed source.
        epoch = ROOT / "ops/vm_actions/corner_rotation_timer_migration_epoch"
        self.assertTrue(epoch.is_file())
        self.assertFalse(epoch.is_symlink())
        self.assertRegex(
            epoch.read_text(encoding="utf-8").strip(),
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$",
        )


class DeployHookContractTests(unittest.TestCase):
    def test_deploy_hook_is_fixed_atomic_and_gated_by_the_reviewed_epoch(self):
        script = (ROOT / "ops/vm_actions/ensure_corner_rotation_timer.sh").read_text(
            encoding="utf-8"
        )
        for required in (
            "docich-retro-corner.service",
            "docich-retro-corner.timer",
            "docich-corner-rotation.service",
            "docich-corner-rotation.timer",
            "corner_rotation_timer_migration_epoch",
            "migrate_corner_rotation_timer.sh",
            'bash "$migration_helper"',
            "mktemp",
            "mv -f",
            "refusing to write a unit through a symlink",
            "systemctl --user daemon-reload",
            'systemctl --user enable --now "$canonical_timer"',
            'systemctl --user enable --now "$legacy_timer"',
            'systemctl --user is-enabled --quiet "$timer"',
            'systemctl --user is-active --quiet "$timer"',
        ):
            self.assertIn(required, script)
        self.assertNotIn("systemctl --user restart", script)
        self.assertNotIn("systemctl --user stop", script)
        self.assertNotIn("docich.service", script)
        self.assertNotIn("sudo", script)
        self.assertNotIn("eval ", script)

    def test_migration_and_rollback_helpers_exist_and_are_fixed(self):
        migrate = (ROOT / "ops/vm_actions/migrate_corner_rotation_timer.sh").read_text(
            encoding="utf-8"
        )
        rollback = (ROOT / "ops/vm_actions/rollback_corner_rotation_timer.sh").read_text(
            encoding="utf-8"
        )
        for text in (migrate, rollback):
            self.assertIn("docich-retro-corner.service", text)
            self.assertIn("docich-corner-rotation.service", text)
            self.assertIn("mktemp", text)
            self.assertIn("mv -f", text)
            self.assertIn("systemctl --user daemon-reload", text)
            self.assertNotIn("systemctl --user restart", text)
            self.assertNotIn("systemctl --user stop", text)
            self.assertNotIn("sudo", text)
            self.assertNotIn("eval ", text)
        self.assertIn("disable --now", migrate)
        self.assertIn("is-active --quiet", migrate)
        self.assertIn("disable --now", rollback)

    def test_vm_deploy_reconciles_the_timer_after_successful_production_deploy(self):
        workflow = (ROOT / ".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        marker = "- name: Ensure corner rotation timer"
        self.assertIn(marker, workflow)
        block = workflow.split(marker, 1)[1].split(
            "- name: Restart radio worker after reviewed Soren runtime update", 1
        )[0]
        self.assertIn("steps.deploy_initial.outcome == 'success'", block)
        self.assertIn("steps.deploy_retry.outcome == 'success'", block)
        self.assertIn("control/ops/vm_actions/ensure_corner_rotation_timer.sh", block)
        self.assertIn("exec docich production $SHA", block)
        self.assertIn("corner_rotation_timer_migration_epoch", workflow)


class SystemdAnalyzeVerifyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze is not available")
    def test_rendered_units_pass_systemd_analyze_verify(self):
        with tempfile.TemporaryDirectory(prefix="corner-rotation-verify-") as tmp:
            root = Path(tmp) / "docich"
            (root / "bin").mkdir(parents=True)
            for binary in ("docich", "docich-nethack-corner"):
                executed = root / "bin" / binary
                executed.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executed.chmod(0o755)
            (root / "config").mkdir()
            (root / "config" / "docich.soren-live.toml").write_text(
                '[paths]\nstate_dir="run"\n', encoding="utf-8"
            )
            units_dir = Path(tmp) / "units"
            units_dir.mkdir()
            rendered = []
            for name in (
                CANONICAL_SERVICE,
                CANONICAL_TIMER,
                LEGACY_SERVICE,
                LEGACY_TIMER,
                "docich-paper-corner.service",
                "docich-soren91-corner.service",
                "docich-nethack-corner.service",
            ):
                text = (SYSTEMD_DIR / name).read_text(encoding="utf-8").replace(
                    "__DOCICH_ROOT__", str(root)
                )
                path = units_dir / name
                path.write_text(text, encoding="utf-8")
                rendered.append(str(path))
            env = dict(os.environ)
            # $SYSTEMD_UNIT_PATH overrides the compiled-in search path, so keep
            # the standard directories for built-in targets like timers.target.
            unit_paths = [str(units_dir)]
            for standard in ("/usr/lib/systemd/system", "/lib/systemd/system", "/etc/systemd/system"):
                if Path(standard).is_dir():
                    unit_paths.append(standard)
            env["SYSTEMD_UNIT_PATH"] = ":".join(unit_paths)
            result = subprocess.run(
                ["systemd-analyze", "verify", *rendered],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
