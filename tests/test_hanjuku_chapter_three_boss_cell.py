"""#2993: chapter 3's boss castle is the building under the skull, not the cave.

Live frames from g1075-1d5f65e9 (2026-10-29 JST, every ordinary castle ours):
- g1075-ch3-y-view-boss-skull.png  14:28:12 frame-002, the 3-B1 target Y view
  as it opened (G on パタゴーヌ). The skull (grey, view 101-107 x 64-69) is
  ❾完熟プリンス城; the dashed cliff ring holds the cave (blue 136-138 x 127-129).
- g1075-boss-target-enclosure.png  13:31:26, the field right after the early
  close: the marker on the ice-mountain cave entrance inside the cliff ring.
Synthetic, the G sprite of the 14:29:04 Y view pasted onto the 14:28:12 view:
- g1075-ch3-y-view-g-over-cave.png  G at the v278 aim (137.5, 127.5). With no
  enemy flag left it covered the cave's blue and was the only blue on the
  view, so before v293 the view read map_target and the next Y closed it.
- g1075-ch3-y-view-g-on-boss-aim.png  G at the v295 aim (101.25, 65.125).
Live field frames after the v293 jump (aim at the skull centre (104, 66.5)):
- g1075-ch3-boss-building-marker-beside.png  14:56:32, the free cursor right
  after 3-B1 launched: 6 px right of the tall building under the skull.
- g1075-ch3-camp-beside-boss-building.png    15:00:36, アポロン's tent on that
  spot (he camped instead of entering) with the cursor on it. The recall had
  pressed A on it at 14:57:27; a defense message swallowed the A and the
  'menu' stage waited silently on frames like this one until 15:07:06.
"""
from pathlib import Path

from docich import hanjuku_chart as chart
from docich.hanjuku.flows.map import TARGET_MISS_LIMIT, _unverified_target
from docich.hanjuku.flows.recall import MENU_OPEN_WAIT, MENU_REOPEN_LIMIT, _camp_recall_step
from docich.hanjuku.orchestrator import decide, pad
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.readers import world_cursor, world_flags
from docich.hanjuku.os_layer.screen import _target_marker, own_camps, parse
from docich.hanjuku.strategy.orders import BOSS_TARGET_CANCEL_LIMIT, _boss_retry_due

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'
SKULL_VIEW = FIXTURES / 'g1075-ch3-y-view-boss-skull.png'
OVER_CAVE = FIXTURES / 'g1075-ch3-y-view-g-over-cave.png'
ON_AIM = FIXTURES / 'g1075-ch3-y-view-g-on-boss-aim.png'
BESIDE = FIXTURES / 'g1075-ch3-boss-building-marker-beside.png'
CAMP = FIXTURES / 'g1075-ch3-camp-beside-boss-building.png'
ENCLOSURE = FIXTURES / 'g1075-boss-target-enclosure.png'

SKULL_GREY = (164, 165, 164)
CAVE_BLUE = (57, 181, 246)
OLD_CELL = (596, 640)             # v278: the cave
SKULL_CELL = (328, 152)           # v293: the skull centre
BOSS = next(o for o in chart.orders(3) if o['step'] == '3-B1')


def aim(cell):
    ox, oy = chart.Y_JUMP_OFFSET[3]
    return cell[0] / 8 + ox, cell[1] / 8 + oy


def bbox(frame, colour, x0, y0, x1, y1):
    pts = [(x, y) for x in range(x0, x1) for y in range(y0, y1) if frame.pixel(x, y) == colour]
    return (min(x for x, _ in pts), min(y for _, y in pts),
            max(x for x, _ in pts), max(y for _, y in pts))


def inside(point, box):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def jump_state(moves=0):
    return {'screen_kind': 'world_map',
            'policy': {'chapter': 3, 'tick': 100,
                       'y_jump': {'goal': 'ボス', 'mode': 'target', 'step': '3-B1',
                                  'moves': moves, 'wait': 0, 'tick': 99, 'seen': True}}}


def decisions(state):
    return [r.get('decision') for r in state.get('_records', [])]


def test_the_v293_aim_was_the_skull_centre_and_the_v278_aim_the_cave():
    frame = read_png(SKULL_VIEW)
    skull = bbox(frame, SKULL_GREY, 80, 50, 130, 90)
    cave = bbox(frame, CAVE_BLUE, 120, 110, 160, 140)
    assert skull == (101, 64, 107, 69) and cave == (136, 127, 138, 129)
    assert aim(SKULL_CELL) == ((skull[0] + skull[2]) / 2, (skull[1] + skull[3]) / 2) == (104.0, 66.5)
    assert inside(aim(OLD_CELL), cave)
    assert inside(aim(chart.castles(3)['ボス']), skull) and not inside(aim(chart.castles(3)['ボス']), cave)


def building_bbox(frame):
    """Columns/rows of the tall building at least 20 px off the grass."""
    grass = {frame.pixel(x, y) for x in range(60, 95) for y in range(60, 150)}
    cols = [x for x in range(70, 160) if sum(frame.pixel(x, y) not in grass for y in range(60, 150)) > 30]
    rows = [y for y in range(50, 160) if sum(frame.pixel(x, y) not in grass for x in range(cols[0], cols[-1] + 1)) >= 20]
    return cols[0], rows[0], cols[-1], rows[-1]


def test_the_v293_marker_landed_beside_the_building_and_v295_moves_by_the_gap():
    frame = read_png(BESIDE)
    screen = parse(frame, phase=classify(frame))
    assert screen.kind == 'map' and screen.cursor == (130, 115)
    x0, y0, x1, y1 = building_bbox(frame)
    assert (x0, y0, x1, y1) == (98, 82, 125, 142)
    marker = (screen.cursor[0] + 8, screen.cursor[1] + 8)
    assert screen.cursor[0] + 1 - x1 == 6                         # bracket 6 px right of the wall
    gap = ((x0 + x1) / 2 - marker[0], (y0 + y1) / 2 - marker[1])
    assert gap == (-26.5, -11.0)
    ox, oy = chart.Y_JUMP_OFFSET[3]
    landed = ((104.5 - ox) * 8, (66.5 - oy) * 8)                   # G read at the v293 confirm
    bx, by = chart.castles(3)['ボス']
    assert abs(bx - (landed[0] + gap[0])) <= 0.5 and by == landed[1] + gap[1]


def test_the_cave_no_longer_reads_as_an_enemy_boss_flag(monkeypatch):
    frame = read_png(SKULL_VIEW)
    assert 'ボス' not in world_flags(frame, 3, world_cursor(frame))
    # The v278 cell put the boss flag box on the cave's blue gate: ボス 'enemy'.
    monkeypatch.setitem(chart.CASTLES[3], 'ボス', OLD_CELL)
    assert world_flags(frame, 3, world_cursor(frame))['ボス'] == 'enemy'


def test_the_y_view_with_the_g_over_the_cave_is_the_world_map():
    frame = read_png(OVER_CAVE)
    assert _target_marker(frame) is not None           # the lone blue G reads as a marker
    screen = parse(frame, phase=classify(frame))
    assert screen.kind == 'world_map'
    assert screen.marker is None
    assert world_cursor(frame) == (138.5, 126.0)


def test_the_open_boss_jump_over_the_cave_is_steered_not_dropped():
    actions, state = decide(read_png(OVER_CAVE), jump_state(moves=2))
    assert 'y_jump_failed' not in decisions(state)
    assert pad('y') not in actions
    assert {a['buttons'][0] for a in actions} == {'left', 'up'}
    assert state['policy']['y_jump']['moves'] == 3


def test_the_boss_jump_confirms_with_the_g_on_the_boss_aim():
    frame = read_png(ON_AIM)
    assert parse(frame, phase=classify(frame)).kind == 'world_map'
    assert world_cursor(frame) == (101.5, 65.5)
    actions, state = decide(frame, jump_state(moves=3))
    assert actions == [pad('a')]
    assert 'y_jump_confirm' in decisions(state)
    assert state['policy']['y_jumped'] == {'3-B1': 'ボス'}
    assert state['policy']['cursor'] == list(chart.castles(3)['ボス'])


def test_the_field_after_the_early_close_had_the_marker_on_the_cave():
    frame = read_png(ENCLOSURE)
    screen = parse(frame, phase=classify(frame))
    assert screen.kind == 'map_target' and screen.marker == (135, 119)


def cancel_mem(**extra):
    return {'chapter': 3, 'tick': 76000, 'active': '3-B1',
            'orders': {'3-B1': 'pending'}, **extra}


def cancel_once(mem):
    frame = read_png(ENCLOSURE)
    screen = parse(frame, phase=classify(frame))
    mem['active'] = '3-B1'
    for _ in range(TARGET_MISS_LIMIT):
        actions = _unverified_target(screen, mem, BOSS)
    assert actions and actions[0]['buttons'] == ['b']


def test_cancels_counted_before_cells_were_kept_leave_the_boss_retry_due():
    # Production g1075 at 14:37:42: four cancels at the cave, 3-B1 failed.
    mem = cancel_mem(orders={'3-B1': 'failed'}, target_cancel={'3-B1': BOSS_TARGET_CANCEL_LIMIT})
    assert _boss_retry_due(BOSS, mem)


def test_cancels_at_a_withdrawn_cell_leave_the_boss_retry_due():
    mem = cancel_mem(orders={'3-B1': 'failed'}, target_cancel={'3-B1': BOSS_TARGET_CANCEL_LIMIT},
                     boss_cancel_cell={'3-B1': list(OLD_CELL)})
    assert _boss_retry_due(BOSS, mem)


def test_the_first_cancel_at_the_new_cell_counts_from_one():
    mem = cancel_mem(target_cancel={'3-B1': BOSS_TARGET_CANCEL_LIMIT})
    cancel_once(mem)
    assert mem['target_cancel']['3-B1'] == 1
    assert mem['boss_cancel_cell']['3-B1'] == list(chart.castles(3)['ボス'])
    assert mem['orders']['3-B1'] == 'pending'


def test_the_cancel_limit_at_the_current_cell_still_fails_the_boss_order():
    mem = cancel_mem()
    for _ in range(BOSS_TARGET_CANCEL_LIMIT):
        cancel_once(mem)
    assert mem['orders']['3-B1'] == 'failed'
    assert mem['target_cancel']['3-B1'] == BOSS_TARGET_CANCEL_LIMIT
    assert not _boss_retry_due(BOSS, mem)


def test_a_failure_without_target_cancels_is_not_a_boss_retry():
    assert not _boss_retry_due(BOSS, cancel_mem(orders={'3-B1': 'failed'}))


def camp_menu_mem(**extra):
    return {'chapter': 3, 'tick': 76200,
            'recall': {'stage': 'menu', 'target': [130, 115], 'steps': 2, **extra}}


def recall_step(mem):
    frame = read_png(CAMP)
    return _camp_recall_step(parse(frame, phase=classify(frame)), mem, frame)


def recorded(mem):
    return [r.get('decision') for r in mem.get('_records', [])]


def test_the_tent_beside_the_building_is_our_camp_under_the_cursor():
    frame = read_png(CAMP)
    screen = parse(frame, phase=classify(frame))
    assert screen.kind == 'map' and screen.cursor == (130, 115)
    assert own_camps(frame) == [{'target': (130, 115), 'clipped': False}]


def test_a_tent_menu_that_never_opened_gets_a_second_a():
    mem = camp_menu_mem()
    for _ in range(MENU_OPEN_WAIT - 1):
        assert recall_step(mem) == []
        assert mem['recall']['stage'] == 'menu'
    assert recall_step(mem) == []
    assert mem['recall']['stage'] == 'to_camp' and mem['recall']['menu_reopens'] == 1
    assert 'camp_menu_not_opened' in recorded(mem)
    assert recall_step(mem) == [pad('a')]                          # the cursor is on the tent
    assert mem['recall']['stage'] == 'menu' and recorded(mem)[-1] == 'camp_enter'


def test_the_second_a_is_bounded_per_recall():
    mem = camp_menu_mem(menu_reopens=MENU_REOPEN_LIMIT)
    for _ in range(MENU_OPEN_WAIT):
        recall_step(mem)
    assert 'recall' not in mem
    assert mem['recall_skip'] == {'tick': 76200} and recorded(mem)[-1] == 'camp_recall_aborted'


def test_the_hero_select_route_keeps_waiting_in_the_menu_stage():
    mem = camp_menu_mem(hero=True, steps=0)
    for _ in range(MENU_OPEN_WAIT + 1):
        assert recall_step(mem) == []
    assert mem['recall']['stage'] == 'menu' and 'menu_reopens' not in mem['recall']
