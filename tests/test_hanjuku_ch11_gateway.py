"""Chapter 11's two halves are joined only by the central hub タタール.

Owner 2026-11-17: from the front castles (アルマムーン/チリベット/タプロベイン/
ジャンセニア/ゴンダル/マランマ) the back castles (クラボニア/トリフェ/
アエンタール/ネペンテ) cannot be reached; every back-half sortie has to start
from タタール. g1190 kept sending ゴンダル/マランマ/アルマムーン armies at
トリフェ/ネペンテ, so each one marched out and came back.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart as chart
from docich.hanjuku.strategy.replan import interim_candidates

FRONT = ['チリベット', 'マランマ', 'ゴンダル', 'ジャンセニア', 'タプロベイン']
BACK = ['クラボニア', 'トリフェ', 'アエンタール', 'ネペンテ']


def memory(captured, garrison=None):
    garrison = garrison or {'アルマムーン': ['アテナ', 'セルリアク', 'レモン', 'ピオーネ']}
    for castle in captured:
        garrison.setdefault(castle, ['ミュスカデ', 'リースリング'])
    return {'chapter': 11, 'month': '61-2', 'tick': 100,
            'captured': list(captured), 'garrison': garrison, 'lost': [],
            'egg_types': {}, 'egg_uses': {}, 'house_eggs': {}}


def targets(mem):
    return sorted({order.get('target') for order in interim_candidates(mem).values()
                   if isinstance(order, dict)})


def test_the_gateway_table_only_describes_chapter_11():
    gate = chart.gateway(11)
    assert gate['hub'] == 'タタール'
    assert set(gate['front']) == set(FRONT) | {'アルマムーン'}
    assert set(gate['back']) == set(BACK)
    assert chart.gateway(9) is None
    assert chart.gateway(12) is None


def test_back_half_is_not_targeted_while_the_hub_is_the_enemys():
    got = targets(memory(FRONT))
    assert got == ['タタール']
    assert not set(got) & set(BACK)


def test_the_back_half_opens_once_the_hub_is_ours():
    got = targets(memory([*FRONT, 'タタール']))
    assert set(got) & set(BACK)
    assert 'タタール' not in got


def test_a_hub_that_was_lost_keeps_the_back_half_closed():
    """``captured`` drops a castle the Y map showed taken; ``lost`` remembers it."""
    mem = memory(FRONT)
    mem['lost'] = ['タタール']
    got = targets(mem)
    assert 'タタール' in got          # it is the retake target again
    assert not set(got) & set(BACK)
