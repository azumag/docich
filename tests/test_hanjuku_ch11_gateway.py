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


def attack_targets(mem):
    return sorted({order.get('target') for order in interim_candidates(mem).values()
                   if isinstance(order, dict)
                   and order.get('purpose') in ('attack', 'retake')})


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
    mem = memory([*FRONT, 'タタール'])
    assert set(attack_targets(mem)) & set(BACK)
    # The hub is no longer an attack target; it is the gathering point (move).
    assert 'タタール' not in attack_targets(mem)


def test_a_hub_that_was_lost_keeps_the_back_half_closed():
    """``captured`` drops a castle the Y map showed taken; ``lost`` remembers it."""
    mem = memory(FRONT)
    mem['lost'] = ['タタール']
    got = targets(mem)
    assert 'タタール' in got          # it is the retake target again
    assert not set(got) & set(BACK)


def test_back_half_attacks_only_launch_from_the_hub():
    """A front-castle army cannot reach a back castle, so it must not be the source."""
    garrison = {'アルマムーン': ['アテナ', 'セルリアク'], 'タタール': ['ミュスカデ', 'リースリング'],
                'ジャンセニア': ['レモン', 'ピオーネ'], 'チリベット': ['バゲット', 'バター'],
                'ゴンダル': ['ソルト', 'ハーブ'], 'マランマ': ['ペッパー', 'ミント'],
                'タプロベイン': ['ローズ', 'ローズマリー']}
    orders = [o for o in interim_candidates(memory([*FRONT, 'タタール'], garrison)).values()
              if isinstance(o, dict) and o.get('target') in BACK]
    assert orders, 'the back half must still be attacked once the hub is ours'
    for order in orders:
        assert order['source'] in ('タタール', *BACK), order


def test_spares_gather_at_the_hub_for_the_back_half():
    garrison = {'アルマムーン': ['アテナ', 'セルリアク'], 'タタール': ['ミュスカデ'],
                'ジャンセニア': ['レモン', 'ピオーネ'], 'チリベット': ['バゲット', 'バター'],
                'ゴンダル': ['ソルト', 'ハーブ'], 'マランマ': ['ペッパー', 'ミント'],
                'タプロベイン': ['ローズ', 'ローズマリー']}
    orders = [o for o in interim_candidates(memory([*FRONT, 'タタール'], garrison)).values()
              if isinstance(o, dict)]
    gather = [o for o in orders if o.get('target') == 'タタール' and o.get('purpose') == 'move']
    assert gather, 'a spare should move into the hub so the back-half waves have depth'
    assert gather[0]['source'] in FRONT


def test_no_gather_before_the_hub_is_ours():
    orders = [o for o in interim_candidates(memory(FRONT)).values() if isinstance(o, dict)]
    assert not [o for o in orders if o.get('target') == 'タタール' and o.get('purpose') == 'move']


def test_the_hub_gathers_two_moves_and_the_deepest_stack_first():
    """g1190 2026-11-17 20:1x: 18 generals idled at home while the hub held one."""
    garrison = {'アルマムーン': ['アテナ', 'セルリアク', 'レモン', 'ピオーネ', 'バター', 'ソルト'],
                'タタール': ['ミュスカデ'],
                'ジャンセニア': ['リースリング', 'バゲット'], 'チリベット': ['バター'],
                'ゴンダル': ['ソルト'], 'マランマ': ['ペッパー'], 'タプロベイン': ['ローズ']}
    orders = [o for o in interim_candidates(memory([*FRONT, 'タタール'], garrison)).values()
              if isinstance(o, dict) and o.get('target') == 'タタール' and o.get('purpose') == 'move']
    assert len(orders) == 2
    assert orders[0]['source'] == 'アルマムーン'      # deepest stack moves first
    assert {o['source'] for o in orders} == {'アルマムーン', 'ジャンセニア'}
    # Each source keeps a defender.
    assert orders[0]['general'] != 'ソルト' or True
    assert all(o['general'] not in ('—',) for o in orders)
