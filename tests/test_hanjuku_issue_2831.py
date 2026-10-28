"""Regression smoke tests for issue #2831 summon-battle rescue re-evaluation."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.hanjuku.judgment.battle import RESCUE_REENTRY_LIMIT, RESCUE_REARM_LIMIT
from docich.hanjuku.os_layer.screen import OKUNOTE_SELF_DAMAGE_CHOICES


def test_constants_match_design():
    assert RESCUE_REENTRY_LIMIT == 2
    assert RESCUE_REARM_LIMIT == 2


def test_okunote_self_damage_choice_is_yakekuso():
    assert "ヤケクソ" in OKUNOTE_SELF_DAMAGE_CHOICES


