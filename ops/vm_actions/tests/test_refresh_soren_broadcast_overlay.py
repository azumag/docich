import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "refresh_soren_broadcast_overlay.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations.yml"


class RefreshSorenBroadcastOverlayTests(unittest.TestCase):
    def test_helper_sources_runtime_env_and_invokes_only_fixed_node_helper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "soren"
            (root / "tools").mkdir(parents=True)
            (root / "tools" / "refresh_inline_broadcast_overlay.mjs").write_text(
                "// fixture\n", encoding="utf-8"
            )
            (root / ".env").write_text(
                "SOREN_STREAM_BACKEND=ffmpeg\n"
                "SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED=1\n",
                encoding="utf-8",
            )
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            capture = Path(td) / "capture.txt"
            node = fakebin / "node"
            node.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s|%s\\n' \"$1\" \"$SOREN_STREAM_BACKEND\" > \"$CAPTURE\"\n",
                encoding="utf-8",
            )
            node.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fakebin}:/usr/bin:/bin"
            env["CAPTURE"] = str(capture)
            result = subprocess.run(
                ["bash", str(HELPER), "--root", str(root)],
                text=True,
                capture_output=True,
                env=env,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            value = capture.read_text(encoding="utf-8").strip()
            self.assertEqual(
                value,
                f"{root / 'tools' / 'refresh_inline_broadcast_overlay.mjs'}|ffmpeg",
            )

    def test_missing_projected_helper_is_safe_noop(self):
        with tempfile.TemporaryDirectory() as td:
            result = subprocess.run(
                ["bash", str(HELPER), "--root", td],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"reason":"helper_missing"', result.stdout)

    def test_shell_helper_has_no_restart_or_signal_path(self):
        source = HELPER.read_text(encoding="utf-8")
        self.assertIn('timeout 15s node "$overlay_helper"', source)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("kill ", source)
        self.assertNotIn("SIGTERM", source)
        self.assertNotIn("SIGKILL", source)

    def test_production_deploy_refreshes_only_when_soren_gitlink_changed(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Refresh live Soren broadcast rails after reviewed runtime update"
        self.assertIn(marker, source)
        block = source.split(marker, 1)[1].split(
            "- name: Restart radio worker after reviewed Soren runtime update", 1
        )[0]
        self.assertIn("steps.deploy_initial.outcome == 'success'", block)
        self.assertIn("steps.deploy_retry.outcome == 'success'", block)
        self.assertIn('git -C candidate diff --quiet "$BEFORE_SHA" "$SHA" -- games/soviet_now', block)
        self.assertIn("refresh_soren_broadcast_overlay.sh", block)
        self.assertIn('"exec docich production $SHA"', block)


if __name__ == "__main__":
    unittest.main()
