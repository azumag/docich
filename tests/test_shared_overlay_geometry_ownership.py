"""Independent layout/readiness contract for the common streaming overlay."""
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
GEOMETRY = ROOT / "overlays" / "shared_overlay_geometry.mjs"
NODE_TEST = ROOT / "tests" / "test_shared_overlay_geometry.mjs"


class BroadcastOverlayGeometryOwnershipTest(unittest.TestCase):
    def test_shared_stream_geometry_is_docich_owned_and_soren_independent(self):
        source = GEOMETRY.read_text(encoding="utf-8")
        self.assertIn("export const SHARED_OVERLAY_LAYOUT", source)
        self.assertIn("export function sharedOverlayViewportReady", source)
        self.assertNotIn("games/soviet_now", source)
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        with tempfile.TemporaryDirectory(prefix="docich-overlay-geometry-") as tmp:
            result = subprocess.run(
                ["node", "--test", str(NODE_TEST)],
                cwd=tmp, capture_output=True, text=True, timeout=10, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
