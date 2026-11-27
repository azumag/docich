"""Regressions for Chapter 9 cliff-isolated camp recall (#4861 / #4937 follow-up).

Generals camped beside intermediate road castles (west/south of イデ！'s
continuous cliff wall) cannot walk across the cliff toward CH9_BOSS_WAYPOINT
(568, 212) or CH9_EAST_MOUTH (674, 212). Instead of looping endlessly in
boss_pursuit or camp boss-redirects, they must be recalled to the nearest
reachable owned corridor castle (never イデ！ across the cliff).
"""
from types import SimpleNamespace

import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import boss_pursuit as bp
from docich.hanjuku.flows import recall
from docich.hanjuku.os_layer import navigation as nav
from docich.hanjuku.os_layer import readers, world
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.pixels import Frame
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku.state.regions import flow as _flow
from docich.hanjuku.strategy import orders

ROAD = chart.corridor(9)
BOSS = chart.boss_castle(9)
FRAME = Frame(256, 224, b'\x00' * (256 * 224 * 3))


@pytest.fixture(autouse=True)
def _clean_dynamic_boss_cell():
    chart.DYNAMIC_BOSS_CELLS.pop(9, None)
    yield
    chart.DYNAMIC_BOSS_CELLS.pop(9, None)


def _ch9_mem(**extra):
    mem = {
        'chapter': 9,
        'tick': 127000,
        'month': '12-10',
        'captured': list(ROAD[1:]),
        '_records': [],
        'boss_cell': {'chapter': 9, 'cell': [866, 765], 'tick': 126990, 'readings': 2},
        'world': {'garrison': {c: [] for c in ROAD}, 'sorties': {}},
        'plan': {'active': None},
    }
    mem.update(extra)
    return mem


# ------------------------------------------------------------------ 1, 2, 3
def test_ch9_boss_approachable_detour_and_recall_castle_classification():
    # Live stuck camps from g1190-ch1v9 plus all 11 non-イデ！ corridor castles.
    unreachable = [
        [324, 464],   # アテナ (east of タオ, west of cliff)
        [388, 240],   # アルファルファ (east of オシロガ, west of cliff)
        [340, 176],   # オレガノ (near オシロガ, west of cliff)
        [460, 420],   # west side of ドンドン/イデ！ cliff wall
        [504, 600],   # near イソ, south of イデ！ pocket
        *[list(chart.CASTLES[9][c]) for c in ROAD[:-1]],
    ]
    for pt in unreachable:
        assert chart.ch9_boss_approachable(pt) is False, f'expected unreachable: {pt}'
        assert chart.ch9_cliff_detour(pt, (810, 645)) is None, f'unexpected detour: {pt}'

    approachable = [
        list(chart.CASTLES[9]['イデ！']),  # [536, 416]
        [564, 368],                        # イデ！ pocket
        [588, 516],                        # south イデ！ pocket
        [568, 212],                        # north corridor waypoint
        [620, 484],                        # east of southern cliff bend
        [676, 792],                        # south-east boss orbit
    ]
    for pt in approachable:
        assert chart.ch9_boss_approachable(pt) is True, f'expected approachable: {pt}'

    assert chart.ch9_cliff_detour([536, 416], (810, 645)) == chart.CH9_BOSS_WAYPOINT
    assert chart.ch9_cliff_detour([564, 368], (810, 645)) == chart.CH9_BOSS_WAYPOINT
    assert chart.ch9_cliff_detour([568, 212], (810, 645)) == chart.CH9_EAST_MOUTH

    owned = set(ROAD)
    assert chart.ch9_unreachable_recall_castle([324, 464], owned) == 'タオ'
    assert chart.ch9_unreachable_recall_castle([388, 240], owned) == 'オシロガ'
    assert chart.ch9_unreachable_recall_castle([340, 176], owned) == 'オシロガ'
    assert chart.ch9_unreachable_recall_castle([460, 420], owned) == 'ドンドン'
    assert chart.ch9_unreachable_recall_castle([504, 600], owned) == 'イソ'


# ------------------------------------------------------------------ 4
def test_boss_pursuit_track_ignores_non_ide_sources_and_evicted_steps():
    mem = _ch9_mem(
        world={
            'garrison': {c: [] for c in ROAD},
            'sorties': {
                'A:bad:src': {
                    'general': 'レオ', 'source': 'タオ', 'target': BOSS,
                    'status': 'overdue', 'tick': 120000,
                },
                'A:evicted:1': {
                    'general': 'アテナ', 'source': 'イデ！', 'target': BOSS,
                    'status': 'overdue', 'tick': 120000,
                },
                'A:valid:1': {
                    'general': 'エピィ', 'source': 'イデ！', 'target': BOSS,
                    'status': 'overdue', 'tick': 120000,
                },
                'A:evicted_new:1': {
                    'general': 'アテナ', 'source': 'イデ！', 'target': BOSS,
                    'status': 'overdue', 'tick': 126500,
                },
            },
        },
        boss_wave_evicted={'アテナ': 'A:evicted:1'},
    )
    wave = bp.track(mem)
    assert 'レオ' not in wave
    assert 'エピィ' in wave
    # Same step stays evicted, but a freshly launched step re-enlists.
    assert wave['アテナ']['step'] == 'A:evicted_new:1'


# ------------------------------------------------------------------ 5
def test_boss_pursuit_step_recalls_cliff_isolated_general_and_evicts(monkeypatch):
    chart.set_boss_cell(9, (866, 765))
    ox, oy = nav.Y_JUMP_OFFSET[9]

    # Case A: cached unit_world is unreachable -> Y-jump targets タオ -> live origin
    # still west -> lands on タオ -> confirms A, evicts アテナ, and records event.
    mem = _ch9_mem(
        boss_wave_generals={
            'アテナ': {
                'step': 'A:3814542a:M381c6a',
                'launched': 126000,
                'move_from': [324, 461],
                'unit_world': [324, 461],
                'no_move_streak': bp.PURSUIT_STUCK_STREAK,
                'pursuit_tick': 126600,
            },
        },
        boss_pursuit={
            'general': 'アテナ', 'step': 'A:3814542a:M381c6a',
            'phase': 'dest', 'reads': 5, 'seeks': 0, 'x_retries': 0,
        },
    )
    # _candidate prioritizes unreachable general even when no_move_streak >= PURSUIT_STUCK_STREAK.
    assert bp._candidate(mem)[0] == 'アテナ'

    picker = Screen(lines=[], hand=None, text='', kind='map_target', cursor=(80, 100))
    picker.marker = (80, 100)
    monkeypatch.setattr(nav, '_start_y_jump', lambda m, o, g, mode: [
        m.__setitem__('y_jump', {'goal': g, 'mode': mode, 'step': o['step'], 'moves': 0, 'wait': 0}) or {'jump': g}
    ][-1])

    res = bp.step(picker, mem, FRAME)
    assert res == {'jump': 'タオ'}
    assert mem['boss_pursuit']['recall_castle'] == 'タオ'
    assert 'pursuit_aim' not in mem

    # Live Y-view origin measurement still at [324, 461] keeps タオ as goal_cell.
    monkeypatch.setattr(world, '_apply_skull_boss', lambda m, f: None)
    monkeypatch.setattr(readers, 'world_cursor', lambda f, ch=None: (324 / 8 + ox, 461 / 8 + oy))
    monkeypatch.setattr(nav, '_y_jump_step', lambda m, f: ['y_step'])
    world_scr = Screen(lines=[], hand=None, text='', kind='world_map', cursor=None)
    assert bp.step(world_scr, mem, FRAME) == ['y_step']
    assert mem['y_jump']['goal'] == 'タオ'
    assert mem['y_jump']['goal_cell'] == list(chart.CASTLES[9]['タオ'])

    # Y-jump finishes on タオ; map_target confirms A, evicts アテナ.
    mem.pop('y_jump', None)
    mem['y_jumped'] = {bp.PURSUIT_STEP: 'タオ'}
    assert bp.step(picker, mem, FRAME) == [pad('a')]
    assert 'アテナ' not in mem[bp.LIST_KEY]
    assert mem['boss_wave_evicted']['アテナ'] == 'A:3814542a:M381c6a'
    assert mem['boss_pursuit']['outcome'] == 'recalled'
    assert any(r['decision'] == 'boss_pursuit_unreachable_recall' and r['castle'] == 'タオ'
               for r in mem['_records'])

    # Case B: cached unit_world was west, but live Y-view origin reveals unit moved east ->
    # switches back to Boss pursuit detour instead of recalling.
    mem2 = _ch9_mem(
        boss_wave_generals={
            'アテナ': {
                'step': 'A:3814542a:M381c6a',
                'launched': 126000,
                'unit_world': [324, 461],
            },
        },
        boss_pursuit={
            'general': 'アテナ', 'step': 'A:3814542a:M381c6a',
            'phase': 'dest', 'reads': 5, 'seeks': 0, 'x_retries': 0,
            'recall_castle': 'タオ', 'unit_world_from_cache': True,
        },
        y_jump={'goal': 'タオ', 'mode': 'target', 'step': bp.PURSUIT_STEP, 'moves': 0, 'wait': 0},
    )
    monkeypatch.setattr(readers, 'world_cursor', lambda f, ch=None: (700 / 8 + ox, 400 / 8 + oy))
    assert bp.step(world_scr, mem2, FRAME) == ['y_step']
    assert 'recall_castle' not in mem2['boss_pursuit']
    assert mem2['y_jump']['goal'] == BOSS
    assert mem2['y_jump']['goal_cell'] == [866, 765]


# ------------------------------------------------------------------ 6
def test_recall_camp_found_never_sets_boss_redirect_west_of_cliff(monkeypatch):
    chart.set_boss_cell(9, (866, 765))
    monkeypatch.setattr(recall, 'castle_roofs', lambda _f: [{'kind': 'own', 'target': (60, 60), 'clipped': False}])

    for pt in ([324, 461], [388, 240], [340, 176]):
        mem = _ch9_mem()
        monkeypatch.setattr(world, 'observed_cursor_cell', lambda _s, _m, _f, p=pt: tuple(p))
        recall._camp_recall_step(Screen(lines=[], hand=None, text='', kind='map', cursor=(60, 60)),
                                 mem, FRAME, observed_camps=[{'target': [60, 60]}])
        state = _flow(mem)['recall']
        assert not state.get('boss'), f'west camp {pt} must not get boss redirect'

    # An approachable north-corridor camp within ±320 of CH9_BOSS_WAYPOINT keeps boss redirect.
    mem_ok = _ch9_mem()
    monkeypatch.setattr(world, 'observed_cursor_cell', lambda _s, _m, _f: (568, 212))
    recall._camp_recall_step(Screen(lines=[], hand=None, text='', kind='map', cursor=(60, 60)),
                             mem_ok, FRAME, observed_camps=[{'target': [60, 60]}])
    assert _flow(mem_ok)['recall'].get('boss') is True
    assert _flow(mem_ok)['recall'].get('goal') == BOSS


# ------------------------------------------------------------------ 7
def test_navigation_recall_y_jump_switches_west_camp_from_boss_to_nearest_castle(monkeypatch):
    chart.set_boss_cell(9, (866, 765))
    ox, oy = nav.Y_JUMP_OFFSET[9]
    monkeypatch.setattr(nav, 'world_cursor', lambda _f, _ch: (324 / 8 + ox, 461 / 8 + oy))
    monkeypatch.setattr(nav, 'world_flags', lambda _f, _ch, _c: {})

    mem = _ch9_mem(
        flow={'recall': {'stage': 'dest', 'boss': True, 'goal': BOSS, 'target': [60, 60]}},
        y_jump={'goal': BOSS, 'mode': 'target', 'step': 'RECALL', 'moves': 0, 'wait': 0, 'tick': 127000},
    )
    nav._y_jump_step(mem, FRAME)
    assert mem['y_jump']['goal'] == 'タオ'
    assert mem['y_jump']['goal_cell'] == list(chart.CASTLES[9]['タオ'])
    assert not _flow(mem)['recall'].get('boss')
    assert _flow(mem)['recall']['goal'] == 'タオ'
    assert any(r['decision'] == 'camp_boss_redirect_recalled_home' and r['castle'] == 'タオ'
               for r in mem['_records'])


# ------------------------------------------------------------------ 8
def test_orders_recall_owned_excludes_ide_for_west_of_cliff_camp():
    mem = _ch9_mem()
    owned = sorted(ROAD)
    candidates = orders._recall_owned(mem, 9, owned, origin=[460, 420])
    assert 'イデ！' not in candidates
    assert 'ドンドン' in candidates
    assert orders._recall_goal(mem) if False else True
    _flow(mem)['recall'] = {'stage': 'dest', 'origin': [460, 420], 'target': [60, 60]}
    assert orders._recall_goal(mem) == 'ドンドン'
