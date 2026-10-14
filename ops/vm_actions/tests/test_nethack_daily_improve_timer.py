from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]


class NethackDailyImproveTimerContractTests(unittest.TestCase):
    def test_service_is_bounded_and_runs_only_daily_candidate_generation(self):
        service = (ROOT / "scripts/systemd/docich-nethack-daily-improve.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("Environment=DOCICH_ALLOW_REAL_AI=1", service)
        self.assertIn("WorkingDirectory=__DOCICH_ROOT__", service)
        self.assertIn("UMask=0077", service)
        self.assertIn("TimeoutStartSec=25min", service)
        self.assertIn("nethack-daily-improve", service)
        self.assertNotIn("nethack-corner", service)
        self.assertNotIn("Restart=", service)

    def test_timer_runs_daily_in_japan_time(self):
        timer = (ROOT / "scripts/systemd/docich-nethack-daily-improve.timer").read_text(
            encoding="utf-8"
        )
        self.assertIn("OnCalendar=*-*-* 05:10:00 Asia/Tokyo", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("Unit=docich-nethack-daily-improve.service", timer)

    def test_deploy_hook_is_limited_to_its_units_and_cleans_temporary_files(self):
        script = (ROOT / "ops/vm_actions/ensure_nethack_daily_improve_timer.sh").read_text(
            encoding="utf-8"
        )
        for expected in (
            "temporary_paths=()",
            "trap cleanup EXIT",
            "mktemp",
            "mv -f",
            "refusing to write a unit through a symlink",
            'systemctl --user enable --now "$timer"',
            'systemctl --user is-enabled --quiet "$timer"',
            'systemctl --user is-active --quiet "$timer"',
        ):
            self.assertIn(expected, script)
        self.assertNotIn("systemctl --user restart", script)
        self.assertNotIn("systemctl --user stop", script)
        self.assertNotIn("docich.service", script)
        self.assertNotIn("sudo", script)
        self.assertNotIn("eval ", script)

    def test_production_deploy_calls_the_fixed_control_plane_hook(self):
        workflow = (ROOT / ".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        marker = "- name: Ensure NetHack daily improvement timer"
        self.assertIn(marker, workflow)
        block = workflow.split(marker, 1)[1].split("- name:", 1)[0]
        self.assertIn("steps.auth.outputs.target == 'production'", block)
        self.assertIn("steps.deploy_initial.outcome == 'success'", block)
        self.assertIn("steps.deploy_retry.outcome == 'success'", block)
        self.assertIn("control/ops/vm_actions/ensure_nethack_daily_improve_timer.sh", block)
        self.assertIn('"exec docich production $SHA"', block)


if __name__ == "__main__":
    unittest.main()
