"""Contract for the shared game context, independent of Soviet submodule."""
import shutil
import subprocess
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "overlays" / "active_game_context.mjs"
TEST = ROOT / "tests" / "test_active_game_context.mjs"


class ActiveGameContextOwnershipTest(unittest.TestCase):
    def test_docich_game_switch_context(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("ACTIVE_GAME_CONTEXT_CONTRACT_VERSION = 1", source)
        self.assertIn("export function normalizeActiveGameContext", source)
        self.assertIn("export function loadActiveGameContext", source)
        self.assertNotIn("games/soviet_now", source)
        if shutil.which("node") is None:
            self.skipTest("Node.js unavailable")
        result = subprocess.run(
            ["node", "--test", str(TEST)], cwd=ROOT,
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
