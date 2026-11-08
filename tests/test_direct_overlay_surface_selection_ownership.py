"""Run the docich-owned overlay surface-selection contract without Soviet source."""
import shutil
import subprocess
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/"overlays/direct_overlay_surface_selection.mjs"
TEST=ROOT/"tests/test_direct_overlay_surface_selection.mjs"

class OverlaySurfaceSelectionOwnershipTest(unittest.TestCase):
    def test_surface_selection_contract(self):
        source=MODULE.read_text(encoding="utf-8")
        self.assertIn("export function selectDirectOverlaySurfaces(",source)
        self.assertIn("DIRECT_OVERLAY_SURFACE_SELECTION_CONTRACT_VERSION = 1",source)
        self.assertNotIn("games/soviet_now",source)
        if shutil.which("node") is None:
            self.skipTest("Node.js unavailable")
        run=subprocess.run(["node","--test",str(TEST)],cwd=ROOT,
                           text=True,capture_output=True,check=False,timeout=20)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)

if __name__=="__main__":
    unittest.main()
