"""Ensure broadcast stage layout is docich-owned without soviet_now checkout."""
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "overlays" / "direct_stage_config.mjs"
TEST = ROOT / "tests" / "test_direct_stage_config.mjs"

class DirectStageConfigOwnershipTest(unittest.TestCase):
    def test_docich_owns_stage_layout_without_game_or_browser(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn("export function loadDirectStageConfig", source)
        self.assertIn("DIRECT_STAGE_CONTRACT_VERSION = 1", source)
        self.assertNotIn("games/soviet_now", source)
        if shutil.which("node") is None:
            self.skipTest("Node.js not installed")
        with tempfile.TemporaryDirectory(prefix="docich-stage-") as tmp:
            run = subprocess.run(["node", "--test", str(TEST)], cwd=tmp,
                capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

if __name__ == "__main__":
    unittest.main()
