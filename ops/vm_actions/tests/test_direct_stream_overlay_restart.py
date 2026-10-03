import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops/vm_actions/restart_direct_stream_overlay.sh"
REFRESH_HELPER = ROOT / "ops/vm_actions/refresh_soren_inline_overlay.sh"
WATCHER_HELPER = ROOT / "ops/vm_actions/restart_soren_overlay_watchers.sh"
WORKFLOW = ROOT / ".github/workflows/vm-operations.yml"


class DirectStreamOverlayRestartTests(unittest.TestCase):
    def test_helper_is_inert_outside_ffmpeg_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text("SOREN_STREAM_BACKEND=obs\n", encoding="utf-8")
            proc = subprocess.run(
                ["bash", str(HELPER), "--root", str(root)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_inline_refresh_helper_is_inert_outside_ffmpeg_dashboard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "tools").mkdir()
            (root / "tools/refresh_inline_broadcast_overlay.mjs").write_text("", encoding="utf-8")
            (root / ".env").write_text(
                "SOREN_STREAM_BACKEND=obs\n"
                "SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED=1\n"
                "SOREN_DIRECT_STAGE_LAYOUT=dashboard\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["bash", str(REFRESH_HELPER), "--root", str(root)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_workflow_refreshes_active_inline_rails_after_soren_gitlink_change(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Refresh active Soren91 GAME OPS rails after reviewed Soren update"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1].split("- name: Restart docich webui systemd unit", 1)[0]
        self.assertIn("games/soviet_now", block)
        self.assertIn("soren_inline_overlay_refresh_epoch", block)
        self.assertIn("refresh_soren_inline_overlay.sh", block)
        self.assertIn("github.event_name == 'push'", block)
        self.assertIn("steps.auth.outputs.target == 'production'", block)

    def test_overlay_watcher_helper_is_inert_when_watchers_are_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text(
                "SOREN_STATUS_OVERLAY_WATCHERS_ENABLED=0\n"
                "SOREN_UNIFIED_OVERLAY_ENABLED=1\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["bash", str(WATCHER_HELPER), "--root", str(root)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_workflow_restarts_status_watchers_after_soren_gitlink_change(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Restart reviewed Soren status overlay watchers after Soren update"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1].split("- name: Restart docich webui systemd unit", 1)[0]
        self.assertIn("games/soviet_now", block)
        self.assertIn("restart_soren_overlay_watchers.sh", block)
        self.assertIn("github.event_name == 'push'", block)
        self.assertIn("steps.auth.outputs.target == 'production'", block)

    def test_workflow_restart_is_epoch_gated(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Restart direct stream for reviewed overlay runtime epoch"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1].split("- name: Restart docich webui systemd unit", 1)[0]
        self.assertIn("direct_stream_overlay_restart_epoch", block)
        self.assertIn("restart_direct_stream_overlay.sh", block)
        self.assertIn("github.event_name == 'push'", block)
        self.assertIn("steps.auth.outputs.target == 'production'", block)


if __name__ == "__main__":
    unittest.main()
