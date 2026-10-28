"""Regression smoke tests for issue #2831 summon-battle rescue re-evaluation."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.hanjuku.judgment.battle import _rescue_decision, RESCUE_REENTRY_LIMIT, RESCUE_REARM_LIMIT
from docich.hanjuku.os_layer.screen import OKUNOTE_SELF_DAMAGE_CHOICES


def test_constants_match_design():
    assert RESCUE_REENTRY_LIMIT == 2
    assert RESCUE_REARM_LIMIT == 2


def test_okunote_self_damage_choice_is_yakekuso():
    assert "ヤケクソ" in OKUNOTE_SELF_DAMAGE_CHOICES


def test_rescue_decision_unknown_without_battle():
    assert _rescue_decision({}, None, {})["action"] == "unknown"


def test_rescue_decision_guard_rejects_all_self_damage():
    cur = {"ally_hp": 10, "enemy_hp": 50, "start_ally_hp": 90,
           "side": "defense", "ally": "ゼウス", "enemy": "ミント",
           "castle": "アルマムーン", "okunote_only_observed": True,
           "survival": {"exhausted": True}}
    mem = {"chapter": 1, "battle": cur}
    ctx = {"owner": "ally", "okunote_choices": ["ヤケクソ"]}
    d = _rescue_decision(mem, cur, ctx)
    assert d.get("candidate") != "okunote"
