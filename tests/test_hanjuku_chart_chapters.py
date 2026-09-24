"""Chapter encoding, measurement gate, helpers and reference tables."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_chart as chart
from docich import hanjuku_measure as measure
from docich import hanjuku_policy as policy
from docich import hanjuku_reference as ref
from docich.hanjuku_screen import Screen


def map_screen():
    value = Screen(lines=[], hand=None, text='')
    value.kind = 'map'
    return value


def test_chapter_one_orders_are_fully_measured():
    castles = chart.castles(1)
    assert chart.orders(1) == chart.CHAPTER_1_ORDERS
    for order in chart.orders(1):
        assert order['source'] in castles and order['target'] in castles
    assert chart.home_castle(1) == 'ほんじょう'
    assert chart.boss_castle(1) == 'けっかい'


@pytest.mark.parametrize('chapter', list(range(2, 13)))
def test_unmeasured_chapters_gate_orders_and_unlock_after_castles(chapter):
    assert chart.castles(chapter) == {}
    assert chart.orders(chapter) == ()
    encoded = chart.all_orders(chapter)
    assert encoded, f'chapter {chapter} must encode orders'
    # Plant a full castle map: every order's source/target must appear.
    planted = {name: (10 + i, 20) for i, name in enumerate(chart.CASTLE_NAMES[chapter])}
    chart.CASTLES[chapter] = planted
    try:
        gated = chart.orders(chapter)
        assert gated and all(o['source'] in planted and o['target'] in planted for o in gated)
        assert {o['step'] for o in gated} <= {o['step'] for o in encoded}
        # Partial map: drop one non-home castle → orders referencing it vanish.
        drop = next(n for n in planted if n not in (chart.home_castle(chapter),
                                                    chart.boss_castle(chapter)))
        partial = {k: v for k, v in planted.items() if k != drop}
        chart.CASTLES[chapter] = partial
        still = chart.orders(chapter)
        assert all(drop not in (o['source'], o['target']) for o in still)
        assert len(still) < len(encoded)
    finally:
        chart.CASTLES.pop(chapter, None)


def test_home_and_boss_labels_match_on_screen_names():
    assert chart.home_castle(8) == 'あるまむーん'
    assert chart.home_castle(2) == 'アルマムーン'
    for chapter in range(2, 13):
        assert chart.boss_castle(chapter) == 'ボス'
        names = set(chart.CASTLE_NAMES[chapter])
        assert chart.home_castle(chapter) in names
        assert chart.boss_castle(chapter) in names


def test_purchases_are_tuples_and_purchase_for_selects_month():
    for chapter in (1, 2, 3, 5, 6):
        plans = chart.purchases(chapter)
        assert isinstance(plans, tuple) and plans
        year, month = plans[0]['month']
        assert chart.purchase_for(chapter, year, month) is plans[0] or (
            chart.purchase_for(chapter, year, month) == plans[0])
    assert chart.purchases(4) == ()
    assert chart.purchase_for(4, 1, 1) is None
    assert chart.purchase_for(2, 9, 9) is None
    # chapter 3 has multiple months
    assert chart.purchase_for(3, 1, 8) and chart.purchase_for(3, 1, 11)


def test_policy_off_chart_reason_for_unmeasured_chapter():
    mem = {'chapter': 2, 'orders': {}, '_records': []}
    policy.map_step(map_screen(), mem, None)
    rec = mem['_records'][-1]
    assert rec['decision'] == 'chart_adjust_request'
    assert rec['off_chart_reason'] == 'chart_unavailable'


def test_interim_candidates_empty_without_measured_castles():
    assert policy.interim_candidates({'chapter': 2, 'captured': []}) == {}


def test_interim_candidates_use_home_and_boss_labels():
    chapter = 1
    planted = dict(chart.castles(1))
    mem = {'chapter': chapter, 'captured': [], 'orders': {}}
    candidates = policy.interim_candidates(mem)
    assert candidates
    home = chart.home_castle(chapter)
    boss = chart.boss_castle(chapter)
    for value in candidates.values():
        assert value['source'] in planted
        assert value['target'] != boss
        assert value['target'] not in set(mem['captured']) | {home}


def test_ready_all_captured_excludes_home_and_boss():
    chapter = 2
    planted = {name: (i, 0) for i, name in enumerate(chart.CASTLE_NAMES[chapter])}
    chart.CASTLES[chapter] = planted
    try:
        home = chart.home_castle(chapter)
        boss = chart.boss_castle(chapter)
        order = {'after': ('all_captured',), 'source': home, 'target': boss}
        non_boss = set(planted) - {home, boss}
        assert not policy._ready(order, {'chapter': chapter, 'captured': set()})
        assert policy._ready(order, {'chapter': chapter, 'captured': set(non_boss)})
    finally:
        chart.CASTLES.pop(chapter, None)


def test_reference_tables_and_endure_rule():
    assert ref.endure_safe_kill_hp(100) == 83
    assert ref.enemy_egg_likely([20, 20, 8])
    assert not ref.enemy_egg_likely([10, 10])
    assert ref.egg_drop_threshold(31) == 31 % 16 + 1
    assert ref.CARD_IDS['ファバード'] == 16
    assert ref.MELEE_PATTERNS['⑥']['action'] == 'use_egg'
    assert ref.HALF_RAW_LEVEL_NEED[9] == 2000
    assert '凶作' in ref.EVENT_TABLES['monthly']


def test_measure_emits_python_dict_for_partial_seed():
    class FakeCursorFrame:
        pass

    # Seed-only samples without pixel localization still place the first label.
    castles = measure.measure(2, [], seed=(100, 200))
    assert castles == {}
    # Direct unit: emit_python formats CASTLES entries.
    text = measure.emit_python(2, {'アルマムーン': (731, 805), 'ボス': (10, 20)})
    assert "    2: {" in text
    assert "'アルマムーン': (731, 805)," in text
    assert "'ボス': (10, 20)," in text


def test_is_boss_order_uses_chapter_boss_castle():
    assert policy._is_boss_order({'target': 'けっかい'}, {'chapter': 1})
    assert not policy._is_boss_order({'target': 'ボス'}, {'chapter': 1})
    assert policy._is_boss_order({'target': 'ボス'}, {'chapter': 5})
    assert not policy._is_boss_order({'target': 'けっかい'}, {'chapter': 5})
    assert not policy._is_boss_order(None, {'chapter': 1})
