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
    assert chart.home_castle(1) == 'アルマムーン'
    assert chart.boss_castle(1) == 'けっかい'


@pytest.mark.parametrize('chapter', list(range(3, 13)))   # 2 measured 2026-09-28
def test_unmeasured_chapters_gate_orders_and_unlock_after_castles(chapter, monkeypatch):
    monkeypatch.setitem(chart.CASTLES, chapter, {})
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


def test_chapter_three_initial_routes_unlock_only_from_measured_castles(monkeypatch):
    """Lock the chapter 3 gate independently of any real coordinate claim.

    The planted cells are synthetic: this checks which orders the gate exposes
    once home and the three initial targets exist, not that those coordinates
    were measured. Until labeled native frames are recorded, chapter 3 must
    stay empty and emit no orders.
    """
    chapter = 3
    assert chart.castles(chapter) == {}
    assert chart.orders(chapter) == ()

    home = chart.home_castle(chapter)
    # Home plus グリン alone unlocks only the first initial route.
    monkeypatch.setitem(chart.CASTLES, chapter, {home: (100, 100), 'グリン': (108, 108)})
    assert [o['step'] for o in chart.orders(chapter)] == ['3-Z1']

    # Home plus the three initial targets unlocks exactly the first wave.
    planted = {home: (100, 100), 'グリン': (108, 108),
               'メガパタ': (116, 116), 'コラダイン': (124, 124)}
    monkeypatch.setitem(chart.CASTLES, chapter, planted)
    initial = chart.orders(chapter)
    assert {o['step'] for o in initial} == {'3-Z1', '3-C1', '3-V1'}
    assert all(o['source'] == home for o in initial)
    assert {o['target'] for o in initial} == {'グリン', 'メガパタ', 'コラダイン'}

    # Orders that need an unmeasured castle stay suppressed.
    encoded = {o['step'] for o in chart.all_orders(chapter)}
    for step in ('3-A1', '3-V2', '3-C2', '3-V3', '3-Z2', '3-B1'):
        assert step in encoded
        assert step not in {o['step'] for o in initial}


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


def test_policy_off_chart_reason_for_unmeasured_chapter(monkeypatch):
    monkeypatch.setitem(chart.CASTLES, 3, {})
    mem = {'chapter': 3, 'orders': {}, '_records': []}
    policy.map_step(map_screen(), mem, None)
    [rec] = [r for r in mem['_records'] if r['decision'] == 'chart_adjust_request']
    assert rec['off_chart_reason'] == 'chart_unavailable'


def test_interim_candidates_empty_without_measured_castles(monkeypatch):
    monkeypatch.setitem(chart.CASTLES, 3, {})
    assert policy.interim_candidates({'chapter': 3, 'captured': []}) == {}


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


def test_ready_all_captured_excludes_home_and_boss(monkeypatch):
    chapter = 2
    planted = {name: (i, 0) for i, name in enumerate(chart.CASTLE_NAMES[chapter])}
    monkeypatch.setitem(chart.CASTLES, chapter, planted)     # chapter 2 is measured: restore it
    home = chart.home_castle(chapter)
    boss = chart.boss_castle(chapter)
    order = {'after': ('all_captured',), 'source': home, 'target': boss}
    non_boss = set(planted) - {home, boss}
    assert not policy._ready(order, {'chapter': chapter, 'captured': set()})
    assert policy._ready(order, {'chapter': chapter, 'captured': set(non_boss)})


def test_ready_all_captured_keeps_unmeasured_castles_locked(monkeypatch):
    """A partial chapter must not release the boss order on measured castles only."""
    chapter = 3                                  # 3-12 ship with empty CASTLES
    home = chart.home_castle(chapter)
    boss = chart.boss_castle(chapter)
    normal = set(chart.CASTLE_NAMES[chapter]) - {home, boss}
    measured = {home, boss, sorted(normal)[0]}
    planted = {name: (i, 0) for i, name in enumerate(sorted(measured))}
    monkeypatch.setitem(chart.CASTLES, chapter, planted)
    order = {'after': ('all_captured',), 'source': home, 'target': boss}
    # Capturing every measured castle (the old gate's castle set) is not enough:
    # the chapter's unmeasured normal castles are still unconquered.
    assert not policy._ready(order, {'chapter': chapter, 'captured': set(measured)})
    assert not policy._ready(order, {'chapter': chapter, 'captured': set(measured) - {boss}})
    assert not policy._ready(order, {'chapter': chapter, 'captured': set(normal) - {sorted(normal)[0]}})
    assert policy._ready(order, {'chapter': chapter, 'captured': set(normal)})


def test_ready_all_captured_fails_closed_without_chapter_names():
    """An unknown chapter must never look fully captured."""
    order = {'after': ('all_captured',), 'source': 'アルマムーン', 'target': 'ボス'}
    mem = {'chapter': 99, 'captured': {'アルマムーン', 'ボス', 'みしらずじょう'}}
    assert not policy._ready(order, mem)


def test_reference_tables_and_endure_rule():
    assert ref.endure_safe_kill_hp(100) == 83
    assert ref.enemy_egg_likely([20, 20, 8])
    assert not ref.enemy_egg_likely([10, 10])
    assert ref.egg_drop_threshold(31) == 31 % 16 + 1
    # 卵落 > HP合計 mod 16 drops the egg (gcgx); the local combo table's
    # イッテツーン/グリンボー/ブラッキー thresholds 7/3/2 agree.
    assert ref.egg_drop_value('イッテツーン') == 8
    assert ref.can_drop_egg('イッテツーン', 16 * 3 + 7)
    assert not ref.can_drop_egg('イッテツーン', 16 * 3 + 8)
    assert ref.can_drop_egg('グリンボー', 16 * 3 + 3)
    assert not ref.can_drop_egg('ブラッキー', 16 * 3 + 3)
    assert not ref.can_drop_egg('クースカン', 0)          # 卵落0 never drops
    assert ref.CARD_IDS['ファバード'] == 31
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


def test_restored_native_intro_requires_both_independent_lines():
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_screen import parse
    fixtures = Path(__file__).parent / 'fixtures/hanjuku'
    queen = parse(read_png(fixtures / 'resume-intro-queen.png').resized())
    double = parse(read_png(fixtures / 'resume-intro-double.png').resized())
    assert queen.kind == double.kind == 'text'
    assert queen.header is double.header is None
    for first, second in ((queen, double), (double, queen)):
        mem = {'resume_chapter_pending': True, 'name': {'done': True}}
        policy.observe_events(first, mem)
        assert not mem.get('chapter')
        policy.observe_events(second, mem)
        assert mem['chapter'] == 2
        assert mem['chapter_evidence'] == 'native_chapter_2_intro'
        assert 'resume_chapter_pending' not in mem
        assert 'resume_intro_clues' not in mem
        assert not mem.get('garrison') and not mem.get('captured')
    # Ordinary dialogue and established chapters cannot be reassigned.
    for mem in ({}, {'chapter': 6}, {'chapter': 6, 'resume_chapter_pending': True}):
        before = mem.get('chapter')
        policy.observe_events(queen, mem); policy.observe_events(double, mem)
        assert mem.get('chapter') == before


def test_native_resumed_party_and_intro_reach_map_with_actions():
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_screen import parse
    from docich.hanjuku_bot import decide
    from docich.hanjuku_starter import rows
    fixtures = Path(__file__).parent / 'fixtures/hanjuku'
    selected = parse(read_png(fixtures / 'resume-starter-three.png').resized())
    assert selected.kind == 'starter_confirm'
    assert len(rows(selected, selected=True)) == 3
    state = {'policy': {'name': {'done': True}, 'resume_chapter_pending': True}}
    for name in ('resume-intro-queen.png', 'resume-intro-double.png'):
        _, state = decide(read_png(fixtures / name).resized(), state)
    assert state['policy']['chapter'] == 2
    actions, state = decide(read_png(fixtures / 'resume-chapter-2-map.png').resized(), state)
    assert actions, 'the actual post-party map must not remain chapterless and held'
    assert state['policy']['chapter'] == 2
