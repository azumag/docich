"""#4456: a defense battle models the castle stock as the carried kit."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku.judgment import battle


def test_defense_battle_uses_card_stock_as_kit():
    mem = {'card_stock': {'イッテツーン': 3, 'ヌルシード': 1, 'ソーマス': 0}}
    cur = {'side': 'defense', 'step': 'defense_observed'}
    assert sorted(battle._carried_kit(mem, cur)) == ['イッテツーン'] * 3 + ['ヌルシード']


def test_attack_battle_without_order_still_has_no_kit():
    cur = {'side': 'attack', 'step': 'unknown-step'}
    assert battle._carried_kit({'card_stock': {'イッテツーン': 3}}, cur) is None
