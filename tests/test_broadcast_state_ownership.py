"""Source-of-truth contract for the moved multigame broadcast-state module."""
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "overlays" / "direct_broadcast_overlay_state.mjs"

class BroadcastStateOwnershipTest(unittest.TestCase):
    def test_module_is_owned_by_docich_and_can_load_without_soviet(self):
        source = STATE.read_text(encoding="utf-8")
        self.assertIn("export function buildDirectBroadcastOverlayState", source)
        self.assertIn("export function extractLegacyOverlayText", source)
        self.assertIn("export function parseLegacyEventOverlayDocument", source)
        self.assertNotIn("from '../games/soviet_now", source)
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        script = (
            "const m=await import(process.argv[1]);"
            "if(m.DIRECT_BROADCAST_VERSION!==1)process.exit(2);"
            "if(m.DIRECT_BROADCAST_STATE_ROUTE!=='/__soren_overlay/broadcast/state')process.exit(3);"
            "const state=m.buildDirectBroadcastOverlayState({sources:{}}, 0);"
            "if(state.version!==1||!state.feeds||!state.notifications)process.exit(4);"
        )
        with tempfile.TemporaryDirectory(prefix="docich-broadcast-state-") as tmp:
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script, STATE.as_uri()],
                cwd=tmp, capture_output=True, text=True, timeout=10, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == "__main__":
    unittest.main()
