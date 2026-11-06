"""Verify the docich-owned shared visibility gate without a Soviet-game submodule."""
import shutil
import subprocess
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/"overlays/direct_overlay_visibility.mjs"
TEST=ROOT/"tests/test_direct_overlay_visibility.mjs"

class DirectOverlayVisibilityOwnershipTest(unittest.TestCase):
    def test_node_visibility_contract(self):
        source=MODULE.read_text(encoding="utf-8")
        self.assertIn("export function directOverlaySurfaceVisible",source)
        self.assertIn("DIRECT_OVERLAY_VISIBILITY_CONTRACT_VERSION = 1",source)
        self.assertNotIn("games/soviet_now",source)
        if shutil.which("node") is None:
            self.skipTest("Node.js unavailable")
        result=subprocess.run(["node","--test",str(TEST)],cwd=ROOT,capture_output=True,text=True,timeout=15,check=False)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=="__main__":
    unittest.main()
