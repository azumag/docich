import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.soren91_corner import ANNOUNCE_TEXT  # noqa: E402


def test_announce_describes_a_91_player_match_without_implying_91_opponents():
    assert "91人対戦" in ANNOUNCE_TEXT
    assert "91人を相手に" not in ANNOUNCE_TEXT
