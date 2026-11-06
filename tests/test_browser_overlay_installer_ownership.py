"""Run docich-owned browser iframe installer contracts without the Soviet submodule."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "overlays/browser_overlay_installer.mjs"
TEST = ROOT / "tests/test_browser_overlay_installer.mjs"


class BrowserOverlayOwnershipTest(unittest.TestCase):
    def test_node_callback_contract(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn("export function overlayInstaller(surfaces)", source)
        self.assertIn("BROWSER_OVERLAY_INSTALLER_CONTRACT_VERSION = 1", source)
        self.assertNotIn("games/soviet_now", source)
        if not shutil.which("node"):
            self.skipTest("Node.js unavailable")
        with tempfile.TemporaryDirectory(prefix="docich-overlay-test-") as tmp:
            completed = subprocess.run(
                ["node", "--test", str(TEST)], cwd=tmp,
                capture_output=True, text=True, check=False, timeout=20,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
