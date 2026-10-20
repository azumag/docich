"""携行札の在庫計算で、同じ1回の使用を二重に差し引かない (#2364 二重控除)。

選択時に ``cards_selected`` へ入った札が、使用の告知を確認できず
``cards_unclassified`` にも入ると、同じ1回の使用が両方に現れる。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku.judgment.battle import _egg_inventory


def row(rows, name):
    return next(r for r in rows if r['name'] == name)


def test_one_use_selected_then_unclassified_consumes_one_carried_copy():
    # 携行2枚・1回使用 → 残りは1枠であるべき。選択と未分類の両方に載っても1回。
    mem = {'card_stock': {'グルミー': 2}}
    cur = {'cards_selected': ['グルミー'], 'cards_unclassified': ['グルミー'],
           'cards_used': []}
    r = row(_egg_inventory(mem, cur, ['グルミー', 'グルミー']), 'グルミー')
    assert r['carried'] == 2
    assert r['available'] == 1
    assert r['usable'] is True


def test_confirmed_use_recorded_in_both_lists_is_still_one_use():
    mem = {'card_stock': {'グルミー': 2}}
    cur = {'cards_selected': ['グルミー'], 'cards_used': ['グルミー'],
           'cards_unclassified': []}
    r = row(_egg_inventory(mem, cur, ['グルミー', 'グルミー']), 'グルミー')
    assert r['available'] == 1


def test_two_separate_uses_still_consume_two_copies():
    mem = {'card_stock': {'グルミー': 2}}
    cur = {'cards_selected': ['グルミー', 'グルミー'], 'cards_unclassified': ['グルミー'],
           'cards_used': []}
    r = row(_egg_inventory(mem, cur, ['グルミー', 'グルミー']), 'グルミー')
    assert r['available'] == 0
    assert r['unavailable'] == 'used'
