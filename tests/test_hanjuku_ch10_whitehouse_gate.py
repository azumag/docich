"""docich#4922: chapter 10's White House opens only at its south gate.

The walled ring admits a march only through the red carpet at (492,436);
the measured boss cell (482,365) is the roof centre, so a sortie aimed
straight at it camps against the walls forever. Boss waves therefore aim
their first hop at the south gate, a camped general's boss_move RECALL
jump picks the next walkable leg (corner -> gate -> front door), the wave
origin prefers castles south of the wall, and once the castle has spawned
(all normal castles held or its cell measured) a later recapture does not
close it again — while the #4912/#4915 pre-spawn guards stay.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import post_month_status as post
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.os_layer import navigation as nav
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku.state.regions import flow as _flow
from docich.hanjuku.strategy import orders, replan


BOSS = chart.boss_castle(10)
# CASTLES_APPROX holds the normal castles only; the boss alias 'ボス' lives
# in castles() (its chart cell is (488, 384), the roof-centre approx).
BOSS_CELL = tuple(chart.castles(10)[BOSS])
NORMAL = sorted(set(chart.CASTLE_NAMES[10])
                - {chart.home_castle(10), BOSS})


@pytest.fixture(autouse=True)
def _no_dynamic_boss_cell():
    chart.DYNAMIC_BOSS_CELLS.pop(10, None)
    yield
    chart.DYNAMIC_BOSS_CELLS.pop(10, None)


def mem(**fields):
    return {'chapter': 10, 'month': '42-6', 'tick': 100,
            'captured': list(NORMAL), 'lost': [], 'orders': {},
            'garrison': {}, 'sorties': {}, 'launched_orders': {},
            '_records': [], 'house_eggs': {}, 'general_location_unknown': [],
            **fields}


class _Frame:
    def digest(self):
        return 'f1'

    def png_bytes(self):
        return b''


def _jump_mem(step, **fields):
    return mem(y_jump={'goal': BOSS, 'mode': 'target', 'step': step,
                       'moves': 0, 'wait': 0, 'tick': 100}, **fields)


def _patch_view(monkeypatch, world_cell):
    ox, oy = nav.Y_JUMP_OFFSET[10]
    monkeypatch.setattr(nav, 'world_cursor',
                        lambda f, ch: (world_cell[0] / 8 + ox,
                                       world_cell[1] / 8 + oy))
    monkeypatch.setattr(nav, 'world_flags', lambda f, ch, c: {})


def test_10b1_marches_from_the_southern_castle():
    # ケントルム sits behind the ring's north-east wall; トルストリア is on
    # the open southern field.
    b1 = next(o for o in chart.all_orders(10) if o['step'] == '10-B1')
    assert b1['source'] == 'トルストリア'
    assert chart.castles(10)['トルストリア'][1] >= chart.CH10_BOSS_WAYPOINT[1]
    assert chart.castles(10)['ケントルム'][1] < chart.CH10_BOSS_WAYPOINT[1]


def test_whitehouse_leg_gate_and_carpet_enter_straight():
    assert chart.ch10_whitehouse_leg(
        chart.CH10_BOSS_WAYPOINT, BOSS_CELL) == BOSS_CELL
    assert chart.ch10_whitehouse_leg((482, 400), BOSS_CELL) == BOSS_CELL


def test_whitehouse_leg_relays_the_outer_ring_to_the_gate():
    wp, sw, se, nw, ne = (chart.CH10_BOSS_WAYPOINT, chart.CH10_WALL_SW,
                         chart.CH10_WALL_SE, chart.CH10_WALL_NW,
                         chart.CH10_WALL_NE)
    # The measured west detour: outer road -> south road (y=452, clear of bastion) -> gate -> boss.
    assert chart.ch10_whitehouse_leg((300, 400), BOSS_CELL) == sw
    assert chart.ch10_whitehouse_leg((395, 380), BOSS_CELL) == sw
    # Alcove/corner positions above y=444 still clear south to y=452 first instead of hitting the L bastion.
    assert chart.ch10_whitehouse_leg((388, 432), BOSS_CELL) == sw
    assert chart.ch10_whitehouse_leg((408, 424), BOSS_CELL) == sw
    assert chart.ch10_whitehouse_leg((388, 444), BOSS_CELL) == wp
    assert chart.ch10_whitehouse_leg(sw, BOSS_CELL) == wp
    assert chart.ch10_whitehouse_leg(wp, BOSS_CELL) == BOSS_CELL
    # The eastern mirror and a camp anywhere along the south road (including
    # far south of the cliff/lava on the x=492 column).
    assert chart.ch10_whitehouse_leg((700, 350), BOSS_CELL) == se
    assert chart.ch10_whitehouse_leg((575, 380), BOSS_CELL) == se
    assert chart.ch10_whitehouse_leg((596, 288), BOSS_CELL) == se
    assert chart.ch10_whitehouse_leg((596, 424), BOSS_CELL) == se
    assert chart.ch10_whitehouse_leg((596, 446), BOSS_CELL) == wp
    assert chart.ch10_whitehouse_leg(se, BOSS_CELL) == wp
    assert chart.ch10_whitehouse_leg((350, 444), BOSS_CELL) == wp
    assert chart.ch10_whitehouse_leg((492, 560), BOSS_CELL) == wp
    # Inside the SW/SE alcoves (420 < x < 468 / 516 < x < 564, oy < 444), step straight south to y=452.
    assert chart.ch10_whitehouse_leg((456, 416), BOSS_CELL) == (456, 452)
    assert chart.ch10_whitehouse_leg((456, 452), BOSS_CELL) == wp
    assert chart.ch10_whitehouse_leg((540, 416), BOSS_CELL) == (540, 452)
    assert chart.ch10_whitehouse_leg((540, 452), BOSS_CELL) == wp
    assert chart.ch10_whitehouse_leg((450, 400), BOSS_CELL) == (450, 452)
    # Behind the north wall the nearer northern corner comes first.
    assert chart.ch10_whitehouse_leg((400, 250), BOSS_CELL) == nw
    assert chart.ch10_whitehouse_leg((550, 250), BOSS_CELL) == ne
    assert chart.ch10_whitehouse_leg(nw, BOSS_CELL) == sw
    assert chart.ch10_whitehouse_leg(ne, BOSS_CELL) == se
    # Nothing is answered for an unmeasured origin or cell.
    assert chart.ch10_whitehouse_leg(None, BOSS_CELL) is None
    assert chart.ch10_whitehouse_leg((400, 250), None) is None
    assert chart.ch10_whitehouse_leg((400,), BOSS_CELL) is None


def test_boss_probe_cell_routes_the_first_hop_to_the_gate():
    m = mem()
    assert nav.boss_probe_cell(m, BOSS, '10-B1') == chart.CH10_BOSS_WAYPOINT
    # The RECALL/PURSUIT jumps need the real cell: their leg computation
    # anchors on it and a probe-jittered cell would corrupt the apron test.
    assert nav.boss_probe_cell(m, BOSS, 'RECALL') == BOSS_CELL
    assert nav.boss_probe_cell(m, BOSS, 'PURSUIT') == BOSS_CELL
    # A non-boss target keeps its own chart cell.
    assert nav.boss_probe_cell(m, 'トルストリア', '10-A1') == chart.castles(10)['トルストリア']


def test_order_jump_freezes_the_south_gate(monkeypatch):
    m = _jump_mem('10-B1')
    _patch_view(monkeypatch, (392, 656))          # standing at トルストリア
    nav._y_jump_step(m, _Frame())
    assert m['y_jump']['goal_cell'] == list(chart.CH10_BOSS_WAYPOINT)


def test_recall_jump_from_the_gate_enters_the_front_door(monkeypatch):
    m = _jump_mem('RECALL')
    _patch_view(monkeypatch, chart.CH10_BOSS_WAYPOINT)
    nav._y_jump_step(m, _Frame())
    assert m['y_jump']['goal_cell'] == list(BOSS_CELL)


def test_recall_jump_from_the_west_wall_takes_the_corner_first(monkeypatch):
    m = _jump_mem('RECALL')
    _patch_view(monkeypatch, (300, 400))           # pinned on the west wall
    nav._y_jump_step(m, _Frame())
    assert m['y_jump']['goal_cell'] == list(chart.CH10_WALL_SW)


def test_recall_jump_from_the_south_road_reaches_the_gate(monkeypatch):
    m = _jump_mem('RECALL')
    _patch_view(monkeypatch, chart.CH10_WALL_SW)   # arrived at the corner
    nav._y_jump_step(m, _Frame())
    assert m['y_jump']['goal_cell'] == list(chart.CH10_BOSS_WAYPOINT)


def test_recall_jump_behind_the_north_wall_rounds_the_ring(monkeypatch):
    m = _jump_mem('RECALL')
    _patch_view(monkeypatch, (400, 250))           # pinned on the north wall
    nav._y_jump_step(m, _Frame())
    assert m['y_jump']['goal_cell'] == list(chart.CH10_WALL_NW)


def test_whitehouse_stays_open_after_a_normal_castle_is_recaptured():
    b1 = next(o for o in chart.all_orders(10) if o['step'] == '10-B1')
    m = mem(boss_cell={'chapter': 10, 'cell': [482, 365], 'tick': 100})
    # One castle falls back to the enemy after the White House spawned.
    m['captured'].remove('ケントルム')
    m['lost'] = ['ケントルム']
    assert not orders._all_normal_captured(m)
    assert orders._whitehouse_open(m)
    assert orders._boss_path_captured(m)
    assert orders._boss_target_unlocked(b1, m)
    assert orders._ready(b1, m)
    assert post._boss_unlocked(m)


def test_whitehouse_without_a_measured_cell_keeps_the_lock():
    """#4912/#4915: before the castle has spawned, a partial capture locks."""
    b1 = next(o for o in chart.all_orders(10) if o['step'] == '10-B1')
    m = mem(captured=['マカリア', 'コッカーニュ', 'ファルゲ'])
    assert not orders._whitehouse_open(m)
    assert not orders._boss_path_captured(m)
    assert not orders._ready(b1, m)
    assert not post._boss_unlocked(m)
    # A stale reading from another chapter never opens the White House.
    stale = mem(captured=['マカリア'],
                boss_cell={'chapter': 9, 'cell': [770, 629], 'tick': 100})
    assert not orders._whitehouse_open(stale)
    assert not post._boss_unlocked(stale)
    # Chapters 11-12 keep the all-normal rule even with a cell measured.
    other = dict(m, chapter=11,
                 boss_cell={'chapter': 11, 'cell': [100, 100], 'tick': 100})
    assert not post._boss_unlocked(other)


def _whitehouse_camp_state(world, **mem_fields):
    m = mem(**mem_fields)
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(150, 100))
    state = {'stage': 'to_camp', 'target': [150, 100],
             'camp_world': list(world), 'steps': 0}
    return m, screen, state


def test_a_camp_in_the_ring_is_a_whitehouse_camp():
    m, screen, state = _whitehouse_camp_state(chart.CH10_BOSS_WAYPOINT)
    assert recall_flow._whitehouse_camp(m, screen, state, object())


def test_a_camp_before_spawn_or_on_another_chapter_is_ordinary():
    locked = {'captured': ['マカリア']}
    m, screen, state = _whitehouse_camp_state(
        chart.CH10_BOSS_WAYPOINT, **locked)
    assert not recall_flow._whitehouse_camp(m, screen, state, object())
    m, screen, state = _whitehouse_camp_state(
        chart.CH10_BOSS_WAYPOINT, chapter=11,
        boss_cell={'chapter': 11, 'cell': [100, 100], 'tick': 100})
    assert not recall_flow._whitehouse_camp(m, screen, state, object())


def test_a_measured_cell_opens_the_camp_gate_before_full_capture():
    m, screen, state = _whitehouse_camp_state(
        chart.CH10_BOSS_WAYPOINT, captured=['マカリア'],
        boss_cell={'chapter': 10, 'cell': [482, 365], 'tick': 100})
    assert recall_flow._whitehouse_camp(m, screen, state, object())


def test_an_open_wave_march_is_a_whitehouse_camp_wherever_it_stands():
    m, screen, state = _whitehouse_camp_state(
        (900, 900), sorties={'s1': {'target': BOSS, 'status': 'en_route',
                                    'general': 'ゼウス'}},
        camp_status_seen={'actors': {'ゼウス': True}})
    assert recall_flow._whitehouse_camp(m, screen, state, object())


def test_a_far_camp_without_a_wave_stays_an_ordinary_recall():
    m, screen, state = _whitehouse_camp_state((900, 900))
    assert not recall_flow._whitehouse_camp(m, screen, state, object())


def test_a_world_cell_is_bound_from_the_screen_delta():
    m = mem()
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(100, 100))
    state = {'stage': 'to_camp', 'target': [150, 150], 'steps': 0}
    m['cursor'] = [400, 300]
    assert recall_flow._whitehouse_camp(m, screen, state, object())
    assert state['camp_world'] == [450, 350]


def test_a_queued_recall_is_reclassified_into_a_boss_move(monkeypatch):
    """The monthly recall task starts an ordinary state; the open White
    House re-points it at the gate relay before the destination is chosen."""
    monkeypatch.setattr(recall_flow, 'own_camps', lambda _frame: [])
    m = mem(boss_cell={'chapter': 10, 'cell': [482, 365], 'tick': 100})
    _flow(m)['recall'] = {'stage': 'to_camp', 'target': [150, 100],
                          'camp_world': list(chart.CH10_BOSS_WAYPOINT),
                          'steps': 0}
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(150, 100))
    recall_flow.camp_recall_step(screen, m, object())
    state = _flow(m)['recall']
    assert state['boss_move'] is True
    assert state['goal'] == BOSS


def test_boss_waves_launch_from_south_of_the_wall():
    m = mem(garrison={'ケントルム': ['グレナデン', 'バジル', 'マンゴスチン'],
                      'トルストリア': ['シフォン', 'エピィ', 'リースリング']})
    cands = list(replan.interim_candidates(m).values())
    wave = next(c for c in cands if c.get('target') == BOSS)
    assert wave['source'] == 'トルストリア'


def test_boss_waves_continue_while_a_castle_is_being_retaken(monkeypatch):
    # Production (mechanical) behaviour: the speedrun chart was abolished
    # 2026-11-02, but the suite keeps it on by default — and its chart chain
    # (10-B1 <- トルストリア) holds even while ケントルム is lost, so the wave
    # would never wait there. Turn it off for this assertion, like
    # test_hanjuku_mechanical_conquest.py does.
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    m = mem(captured=[c for c in NORMAL if c != 'ケントルム'],
            lost=['ケントルム'],
            garrison={'トルストリア': ['シフォン', 'エピィ', 'リースリング']},
            boss_cell={'chapter': 10, 'cell': [482, 365], 'tick': 100})
    cands = list(replan.interim_candidates(m).values())
    assert any(c.get('target') == 'ケントルム' for c in cands)
    assert any(c.get('target') == BOSS for c in cands)
    # And without the measured cell the wave waits on the retake (#4915).
    m.pop('boss_cell')
    cands = list(replan.interim_candidates(m).values())
    assert not any(c.get('target') == BOSS for c in cands)


def test_ch10_boss_move_requires_y_jump_before_confirm(monkeypatch):
    from docich.hanjuku.flows import map as _map
    monkeypatch.setattr(_map, '_target_roof_under_marker', lambda s, m, f, p: True)
    m = mem()
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(128, 112))
    state = {'boss_move': True, 'goal': BOSS, 'target': [128, 112]}
    actions = recall_flow._boss_move_dest_step(screen, m, _Frame(), state, (128, 112))
    assert m.get('y_jump', {}).get('goal') == BOSS
    assert actions and actions[0]['buttons'] == ['y']
    # Once the Y jump receipt is recorded, the confirm fires.
    m.pop('y_jump', None)
    m['y_jumped'] = {'RECALL': BOSS}
    confirm = recall_flow._boss_move_dest_step(screen, m, _Frame(), state, (128, 112))
    assert confirm and confirm[0]['buttons'] == ['a']


def test_broken_or_low_hp_camper_not_hijacked_into_whitehouse():
    m, screen, state = _whitehouse_camp_state(chart.CH10_WALL_NE)
    state['general'] = 'カシュー'
    m['post_month_status'] = {'statuses': {
        'カシュー': {'broken': True, 'hp': 7, 'max_hp': 39},
    }}
    assert not recall_flow._whitehouse_camp(m, screen, state, object())


def test_fort_in_whitehouse_ring_uses_boss_move_instead_of_home_recall():
    # Without camp_world or castle roofs, a fort holding a general whose roster
    # marker mapped near White House (including [328, 384] quantized west road)
    # turns into a boss_move upon entering 'dest'.
    m = mem(post_month_status={'statuses': {
        'フェットチーネ': {
            'broken': False, 'hp': 67, 'max_hp': 67,
            'marker': {'castle_candidate': None, 'marker_field': [328, 384]},
        },
    }})
    _flow(m)['recall'] = {
        'stage': 'fort_status', 'target': [133, 116],
        'fort_members': ['フェットチーネ', 'カペリーニ'],
        'general': 'フェットチーネ', 'steps': 0,
    }
    screen = Screen(lines=[], hand=None, text='', kind='map_target',
                    cursor=(133, 116), marker=(133, 116))
    recall_flow.camp_recall_step(screen, m, None)
    st = _flow(m)['recall']
    assert st['fort'] is True
    assert st['boss_move'] is True
    assert st['goal'] == BOSS


def test_whitehouse_recall_leg_detours_north_camp_to_home():
    home = tuple(chart.castles(10)['アルマムーン'])
    nw, ne, sw, se = (chart.CH10_WALL_NW, chart.CH10_WALL_NE,
                      chart.CH10_WALL_SW, chart.CH10_WALL_SE)
    # Behind north wall (e.g. カシュー east half -> アルマムーン): NE -> SE -> アルマムーン.
    assert chart.ch10_whitehouse_recall_leg((520, 280), home) == ne
    assert chart.ch10_whitehouse_recall_leg((590, 294), home) == se
    assert chart.ch10_whitehouse_recall_leg(se, home) is None
    # West half behind north wall -> アルマムーン: NW -> SW -> アルマムーン.
    assert chart.ch10_whitehouse_recall_leg((440, 270), home) == nw
    assert chart.ch10_whitehouse_recall_leg((392, 294), home) == sw
    assert chart.ch10_whitehouse_recall_leg(sw, home) is None
    # Normal south-of-White-House recall stays direct.
    assert chart.ch10_whitehouse_recall_leg((492, 520), home) is None
    assert chart.ch10_whitehouse_recall_leg(chart.CH10_BOSS_WAYPOINT, home) is None


def test_recall_jump_for_north_camp_detours_via_whitehouse_corners(monkeypatch):
    home_name = chart.home_castle(10)
    m = mem(y_jump={'goal': home_name, 'mode': 'target', 'step': 'RECALL',
                    'moves': 0, 'wait': 0, 'tick': 100})
    _patch_view(monkeypatch, (520, 280))
    nav._y_jump_step(m, _Frame())
    assert m['y_jump']['goal_cell'] == list(chart.CH10_WALL_NE)

    # Normal southern recall keeps アルマムーン's actual cell.
    m2 = mem(y_jump={'goal': home_name, 'mode': 'target', 'step': 'RECALL',
                     'moves': 0, 'wait': 0, 'tick': 100})
    _patch_view(monkeypatch, (492, 520))
    nav._y_jump_step(m2, _Frame())
    assert m2['y_jump']['goal_cell'] == list(chart.castles(10)[home_name])
