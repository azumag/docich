from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]


class PaperImproveTimerContractTests(unittest.TestCase):
    def test_service_is_bounded_and_runs_only_the_gated_scheduled_entry(self):
        service = (ROOT / "scripts/systemd/docich-paper-improve.service").read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", service)
        self.assertIn("Environment=XDG_RUNTIME_DIR=%t", service)
        self.assertIn("Environment=DOCICH_ALLOW_REAL_AI=1", service)
        self.assertIn("WorkingDirectory=__DOCICH_ROOT__", service)
        self.assertIn("UMask=0077", service)
        self.assertIn("TimeoutStartSec=3600", service)
        self.assertIn(
            "ExecStart=/usr/bin/bash __DOCICH_ROOT__/ops/vm_actions/run_paper_improve_with_ai_env.sh -- "
            "__DOCICH_ROOT__/bin/docich --config __DOCICH_ROOT__/config/docich.soren-live.toml "
            "trading paper-improve-scheduled",
            service,
        )
        self.assertNotIn("Restart=", service)
        self.assertNotIn("paper-corner", service)
        self.assertTrue((ROOT / "bin/docich").is_file())
        self.assertTrue((ROOT / "ops/vm_actions/run_paper_improve_with_ai_env.sh").is_file())

    def test_timer_runs_once_daily_after_the_nethack_job(self):
        timer = (ROOT / "scripts/systemd/docich-paper-improve.timer").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* 06:20:00 Asia/Tokyo", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("AccuracySec=1min", timer)
        self.assertIn("Unit=docich-paper-improve.service", timer)
        nethack = (ROOT / "scripts/systemd/docich-nethack-daily-improve.timer").read_text(encoding="utf-8")
        self.assertIn("05:10:00", nethack)
        self.assertNotIn("06:20:00", nethack)

    def test_deploy_hook_is_limited_to_its_units_and_cleans_temporary_files(self):
        script = (ROOT / "ops/vm_actions/ensure_paper_improve_timer.sh").read_text(encoding="utf-8")
        for expected in (
            'service="docich-paper-improve.service"',
            'timer="docich-paper-improve.timer"',
            "__DOCICH_ROOT__",
            "temporary_paths=()",
            "trap cleanup EXIT",
            "mktemp",
            "mv -f",
            "refusing to write a unit through a symlink",
            "systemctl --user daemon-reload",
            'systemctl --user enable --now "$timer"',
            'systemctl --user is-enabled --quiet "$timer"',
            'systemctl --user is-active --quiet "$timer"',
        ):
            self.assertIn(expected, script)
        for forbidden in ("systemctl --user restart", "systemctl --user stop", "docich.service",
                          "sudo", "eval ", "paper-corner"):
            self.assertNotIn(forbidden, script)

    def test_production_deploy_calls_the_fixed_control_plane_hook(self):
        workflow = (ROOT / ".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        marker = "- name: Ensure PAPER scheduled improvement timer"
        self.assertIn(marker, workflow)
        block = workflow.split(marker, 1)[1].split("- name:", 1)[0]
        self.assertIn("steps.auth.outputs.target == 'production'", block)
        self.assertIn("steps.deploy_initial.outcome == 'success'", block)
        self.assertIn("steps.deploy_retry.outcome == 'success'", block)
        self.assertIn("control/ops/vm_actions/ensure_paper_improve_timer.sh", block)
        self.assertIn('"exec docich production $SHA"', block)


if __name__ == "__main__":
    unittest.main()
