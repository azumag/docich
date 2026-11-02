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
    """Lock the chapter 3 gate independently of the measured coordinates.

    The planted cells are synthetic: this checks which orders the gate exposes
    once home and the three initial targets exist. The real cells (isolated
    probe, 2026-10-28; boss from the live Y view, 2026-10-29) expose every
    order, and 3-B1 still waits for ``all_captured``.
    """
    chapter = 3
    assert set(chart.castles(chapter)) == set(chart.CASTLE_NAMES[chapter])
    assert [o['step'] for o in chart.orders(chapter)] == [
        o['step'] for o in chart.all_orders(chapter)]

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
    # Owner 2026-11-02 (並行で攻め込める): a castle off the boss chain no
    # longer gates it — chapter 3's chain is アルマムーン→パタゴーヌ→ボス only.
    assert policy._ready(order, {'chapter': chapter, 'captured': set(normal) - {sorted(normal)[0]}})
    assert not policy._ready(order, {'chapter': chapter, 'captured': set(normal) - {'パタゴーヌ'}})
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


def test_chapter_3_cells_put_every_enemy_flag_on_the_y_view_grid():
    # Isolated probe 2026-10-28 (g1067 savestate, 9ねん1のつき): enemy flag
    # top-left pixels on the Y view; every cell is the free cursor on that
    # castle's roof after a Y jump, confirmed by the castle's own name.
    flags = {'グリン': (171, 85), 'コラダイン': (147, 97), 'イーピヨル': (161, 127),
             'ドンダム': (135, 149), 'メガパタ': (121, 127), 'パタゴーヌ': (107, 99),
             'アバロン': (93, 137), 'リグア': (81, 81)}
    ox, oy = chart.WORLD_MAP_OFFSET[3]
    for name, (fx, fy) in flags.items():
        x, y = chart.CASTLES[3][name]
        assert abs(round(x / 8 + ox) - fx) <= 1 and abs(round(y / 8 + oy) - fy) <= 1, name
    assert chart.Y_JUMP_OFFSET[3] == chart.Y_JUMP_OFFSET[2]


def test_chapter_3_boss_cell_puts_the_marker_on_the_building_under_the_skull():
    # Live g1075 2026-10-29 JST, every ordinary castle captured: the skull at
    # Y view (101-107, 64-69) marks ❾完熟プリンス城, a tall building. The v293
    # aim at the skull centre, G read at (104.5, 66.5), left the field marker
    # centred at (138, 123), 6 px right of the building (98-125 x 82-142), and
    # the general camped. The aim moves by the gap to the building centre.
    # The first cell aimed at the cave inside the cliff ring, (137.5, 127.5).
    # No A press confirmed the name (read-only runtime).
    from docich.hanjuku.strategy.orders import _ready
    ox, oy = chart.Y_JUMP_OFFSET[3]
    bx, by = chart.CASTLES[3]['ボス']
    assert (bx / 8 + ox, by / 8 + oy) == (101.25, 65.125)
    landed = ((104.5 - ox) * 8, (66.5 - oy) * 8)        # the v293 jump's cell
    gap = (111.5 - 138, 112 - 123)                      # marker centre -> building centre
    assert abs(bx - (landed[0] + gap[0])) <= 0.5 and by == landed[1] + gap[1]
    fx, fy = chart.WORLD_MAP_OFFSET[3]
    boss_flag = (round(bx / 8 + fx), round(by / 8 + fy))
    for name, (x, y) in chart.CASTLES[3].items():
        if name != 'ボス':
            # The owner-flag boxes (8x7 view px) of the boss and of every
            # ordinary castle never overlap on the Y view.
            assert max(abs(round(x / 8 + fx) - boss_flag[0]), abs(round(y / 8 + fy) - boss_flag[1])) >= 8, name
    assert chart.castles(3)['ボス'] == (bx, by) and chart.boss_castle(3) == 'ボス'
    boss_order = next(o for o in chart.orders(3) if o['step'] == '3-B1')
    assert boss_order['target'] == 'ボス' and boss_order['after'] == ('all_captured',)
    ordinary = [n for n in chart.CASTLE_NAMES[3] if n not in (chart.home_castle(3), 'ボス')]
    assert len(ordinary) == 8
    # Owner 2026-11-02 (並行で攻め込める): the boss gate only covers its own
    # chain (アルマムーン→パタゴーヌ→ボス) — a side castle may lag behind.
    assert _ready(boss_order, {'chapter': 3, 'captured': ordinary[:-1]})
    assert not _ready(boss_order, {'chapter': 3, 'captured': [c for c in ordinary if c != 'パタゴーヌ']})
    assert _ready(boss_order, {'chapter': 3, 'captured': ordinary})


# WRAM 0x1700 castle records ([id, x, y, ...], 16 bytes, 8 px tiles) read from
# isolated probe savestates on 2026-10-28, in record order.
WRAM_TILES = {
    1: {'アルマムーン': (88, 96), 'ナキューメラ': (86, 76), 'キカンドン': (68, 86), 'ジョンリギ': (66, 68),
        'カストーラ': (72, 44), 'スペンソニア': (46, 44), 'ゴーメン': (34, 72)},
    2: {'アルマムーン': (62, 102), 'ハドリバーグ': (90, 88), 'フーリック': (42, 86), 'グロン': (92, 56),
        'ドミノーラ': (58, 58), 'ウラノポリス': (32, 58), 'スペランザ': (78, 40), 'アウスパジア': (34, 32)},
    3: {'アルマムーン': (86, 16), 'グリン': (104, 36), 'コラダイン': (80, 48), 'イーピヨル': (94, 78),
        'ドンダム': (68, 100), 'メガパタ': (54, 78), 'パタゴーヌ': (40, 50), 'アバロン': (26, 88),
        'リグア': (14, 32)},
}


def test_source_flags_are_the_games_own_castle_tiles_so_later_chapters_use_them():
    from docich.hanjuku_map_sources import SOURCE_CASTLES
    for chapter, tiles in WRAM_TILES.items():
        assert {n: (x + 2, y + 1) for n, (x, y) in tiles.items()} == SOURCE_CASTLES[chapter]
        for name, cell in chart.CASTLES[chapter].items():
            if name in tiles:
                approx = chart.CASTLES_APPROX.get(chapter, {}).get(name) or tuple(
                    8 * (v + d) for v, d in zip(SOURCE_CASTLES[chapter][name], (1, 3)))
                limit = 16 if (chapter, name) == (3, 'アルマムーン') else 8
                assert max(abs(a - b) for a, b in zip(cell, approx)) <= limit, (chapter, name)
    for chapter in range(4, 13):
        # Chapter 4's 四季王 sites come from the live Y view, not the table.
        ordinary = {n: c for n, c in chart.CASTLES[chapter].items() if n not in chart.SEASON_SITE_CELLS}
        assert ordinary == {
            n: (8 * (left + 1), 8 * (top + 3)) for n, (left, top) in SOURCE_CASTLES[chapter].items()}
        assert set(chart.CASTLES[chapter]) - set(ordinary) == (set(chart.boss_sites(4)) if chapter == 4 else set())
        assert 'ボス' not in chart.CASTLES[chapter]
        assert chart.Y_JUMP_OFFSET[chapter] == (63.0, 47.5) and chapter in chart.WORLD_MAP_OFFSET
        assert chart.orders(chapter)
        assert not any(o['target'] == 'ボス' for o in chart.orders(chapter))
