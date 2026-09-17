import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from docich import nethack_corner_operator as operator  # noqa: E402
from docich.nethack_corner import NethackCornerError  # noqa: E402


class NetHackRecoverSafetyTests(unittest.TestCase):
    def test_recover_rejects_active_manual_corner_without_switching(self):
        fake_g = SimpleNamespace(config_path=Path("/tmp/docich.toml"))
        manager = mock.Mock()
        manager.status.return_value = {
            "status": "active",
            "previous_game": "sorengame",
        }
        manager._active_game_reader.return_value = "nethack"

        with mock.patch.object(operator, "load_global", return_value=fake_g), mock.patch.object(
            operator, "ManualNethackCornerManager", return_value=manager
        ):
            with self.assertRaisesRegex(NethackCornerError, "failed manual corner"):
                operator.recover(fake_g.config_path)

        manager._transition_to.assert_not_called()


if __name__ == "__main__":
    unittest.main()
