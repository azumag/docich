import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops/vm_actions/restart_direct_stream_overlay.sh"
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
