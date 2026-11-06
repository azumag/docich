"""Run common browser-stage DOM contracts from docich without the Soviet submodule."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/"overlays/direct_game_stage_dom.mjs"
TEST=ROOT/"tests/test_direct_game_stage_dom.mjs"

class DirectGameStageDomOwnershipTest(unittest.TestCase):
    def test_stage_dom_contract(self):
        source=MODULE.read_text(encoding="utf-8")
        self.assertIn("export function applyDirectGameStageDom(",source)
        self.assertIn("DIRECT_GAME_STAGE_DOM_CONTRACT_VERSION = 1",source)
        self.assertNotIn("games/soviet_now",source)
        if not shutil.which("node"):
            self.skipTest("Node.js unavailable")
        result=subprocess.run(
            ["node","--test",str(TEST)],cwd=ROOT,
            capture_output=True,text=True,timeout=20,check=False,
        )
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
