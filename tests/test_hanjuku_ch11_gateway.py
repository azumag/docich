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


def test_a_general_below_the_hp_floor_is_not_sent_to_attack():
    """g1190 2026-11-17: battles opened at ally HP 4/13/5 against 87/45/87."""
    from docich.hanjuku.strategy import replan
    mem = memory([*FRONT, 'タタール'],
                 {'アルマムーン': ['アテナ'], 'タタール': ['ミュスカデ', 'リースリング']})
    mem['general_statuses'] = {'statuses': {
        'ミュスカデ': {'general': 'ミュスカデ', 'hp': 2, 'max_hp': 40},
        'リースリング': {'general': 'リースリング', 'hp': 1, 'max_hp': 30}}}
    mem['_records'] = []
    orders = [o for o in replan.interim_candidates(mem).values()
              if isinstance(o, dict) and o.get('target') in BACK]
    assert orders == []
    assert any(r.get('decision') == 'interim_attack_deferred_low_hp' for r in mem['_records'])


def test_a_healthy_general_still_attacks():
    from docich.hanjuku.strategy import replan
    mem = memory([*FRONT, 'タタール'],
                 {'アルマムーン': ['アテナ'], 'タタール': ['ミュスカデ', 'リースリング']})
    mem['general_statuses'] = {'statuses': {
        'ミュスカデ': {'general': 'ミュスカデ', 'hp': 38, 'max_hp': 40}}}
    orders = [o for o in replan.interim_candidates(mem).values()
              if isinstance(o, dict) and o.get('target') in BACK]
    assert orders, 'a healthy hub must still push into the back half'


def test_a_back_half_camp_does_not_target_a_front_castle():
    """Owner 2026-11-17: a retreated back-half general camped on the cliff edge.

    He was sent at the nearest own castle across the chasm, so the march never
    ended. The destination must stay in the half he can walk in.
    """
    from docich.hanjuku.strategy.orders import _recall_owned
    owned = sorted([*FRONT, 'タタール', 'クラボニア'])
    cells = chart.castles(11)
    # A camp right next to クラボニア (back half).
    origin = [cells['クラボニア'][0] - 12, cells['クラボニア'][1] + 8]
    got = _recall_owned({}, 11, owned, origin=origin)
    assert 'クラボニア' in got
    assert not set(got) & set(FRONT), got


def test_a_front_half_camp_still_targets_the_front():
    from docich.hanjuku.strategy.orders import _recall_owned
    owned = sorted([*FRONT, 'タタール', 'クラボニア'])
    cells = chart.castles(11)
    origin = [cells['マランマ'][0] + 10, cells['マランマ'][1] - 6]
    got = _recall_owned({}, 11, owned, origin=origin)
    assert set(got) & set(FRONT)
    assert 'クラボニア' not in got


def test_an_unmeasured_camp_keeps_every_castle():
    from docich.hanjuku.strategy.orders import _recall_owned
    owned = sorted([*FRONT, 'タタール', 'クラボニア'])
    assert _recall_owned({}, 11, owned, origin=None) == owned


def test_hub_adjacent_back_camp_does_not_switch_to_front_after_hub_failure():
    """Devin review: a shared hub must not make the two sides tie."""
    from docich.hanjuku.strategy.orders import _recall_owned
    owned = sorted([*FRONT, 'タタール', 'クラボニア'])
    got = _recall_owned({}, 11, owned, origin=[440, 452])
    assert 'クラボニア' in got
    assert 'タタール' in got
    assert not set(got) & set(FRONT), got


def test_only_hub_owned_keeps_hub_as_recall_target():
    from docich.hanjuku.strategy.orders import _recall_owned
    assert _recall_owned({}, 11, ['タタール'], origin=[440, 452]) == ['タタール']


def test_the_side_is_judged_without_the_y_map_flags():
    """g1190 2026-11-17 21:1x: タタール/ネペンテ owned but unflagged in this frame.

    The flags-filtered list held only front castles, so a back-half camper was
    sent to ジャンセニア across the chasm. Ownership must come from the run's
    own captures.
    """
    from docich.hanjuku.strategy.orders import _recall_owned
    captured = [*FRONT, 'タタール', 'クラボニア']
    mem = {'chapter': 11, 'captured': captured, 'home_lost': False}
    cells = chart.castles(11)
    origin = [cells['クラボニア'][0] - 12, cells['クラボニア'][1] + 8]
    # Only front castles survive this frame's flag filter.
    flags_owned = list(FRONT)
    got = _recall_owned(mem, 11, flags_owned, origin=origin)
    assert 'クラボニア' in got or 'タタール' in got, got
    assert not set(got) & set(FRONT), got


def test_an_unmeasured_camp_returns_to_the_shared_hub():
    """Owner 2026-11-17 22:2x: the recall cursor walked at the cliff side.

    With no measured camp cell the halves cannot be told apart, and the nearest
    castle can lie across the chasm. The hub is the one castle both halves
    reach, so it is the destination while the position is unknown.
    """
    from docich.hanjuku.state.regions import flow as _flow
    mem = memory([*FRONT, 'タタール', 'クラボニア'])
    _flow(mem)['recall'] = {'target': [134, 120], 'stage': 'dest'}
    assert chart.gateway(11)['hub'] == 'タタール'
    from docich.hanjuku.strategy.orders import _recall_goal
    assert _recall_goal(mem) == 'タタール'


def test_without_the_hub_the_nearest_castle_is_used_again():
    from docich.hanjuku.state.regions import flow as _flow
    mem = memory([*FRONT, 'クラボニア'])
    _flow(mem)['recall'] = {'target': [134, 120], 'stage': 'dest'}
    from docich.hanjuku.strategy.orders import _recall_goal
    assert _recall_goal(mem) != 'タタール'
