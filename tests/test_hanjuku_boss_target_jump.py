"""#2976: the boss target is reached by a Y jump on every attempt.

g1075-1d5f65e9 2026-10-29 JST, boss sortie 3-B1: the 13:31 target view closed
early, the immediate second Y showed nothing (0 moves) and that y_jump_last
record outlived the cancel and the restart; the 13:36 retry walked to ボス
without a jump and stopped there, since a boss target is only confirmed by a
jump. g1075-boss-target-enclosure.png is 13:31:26: the marker inside the
walled enclosure right after the early close.
"""
from pathlib import Path

from docich import hanjuku_chart as chart
from docich.hanjuku.flows.map import TARGET_MISS_LIMIT, _unverified_target
from docich.hanjuku.os_layer.navigation import (Y_JUMP_LIMIT, _forget_failed_jump,
                                                 _want_y_jump)
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

ROOT = Path(__file__).resolve().parents[1]
ENCLOSURE = ROOT / 'tests' / 'fixtures' / 'hanjuku' / 'g1075-boss-target-enclosure.png'
BOSS = next(o for o in chart.orders(3) if o['step'] == '3-B1')
CLOSED = {'mode': 'target', 'moves': 0, 'step': '3-B1', 'why': 'view_closed'}
CELL = list(chart.castles(3)['ボス'])
NEAR = [CELL[0], CELL[1] - 12]      # the 13:37 walked estimate was 12 px north of the cell


def mem(**extra):
    return {'chapter': 3, 'tick': 71600, 'orders': {'3-B1': 'pending'}, **extra}


def test_a_failed_jump_of_a_finished_attempt_is_forgotten():
    m = mem(y_jump_last=dict(CLOSED), cursor=[341, 425])
    assert not _want_y_jump(m, BOSS, 'ボス', 'target')
    _forget_failed_jump(m, '3-B1')
    assert 'y_jump_last' not in m
    assert _want_y_jump(m, BOSS, 'ボス', 'target')


def test_only_the_same_order_and_mode_is_forgotten():
    m = mem(y_jump_last=dict(CLOSED))
    _forget_failed_jump(m, '3-B1', 'map')
    _forget_failed_jump(m, '3-C1')
    assert m['y_jump_last'] == CLOSED


def test_the_boss_target_jump_is_not_skipped_on_a_near_estimate():
    # The 13:37 estimate after walking was [596, 628] against the cell then (596, 640).
    assert _want_y_jump(mem(cursor=NEAR), BOSS, 'ボス', 'target')


def test_a_boss_target_already_placed_by_a_jump_is_not_jumped_again():
    m = mem(cursor=CELL, y_jumped={'3-B1': 'ボス'})
    assert not _want_y_jump(m, BOSS, 'ボス', 'target')


def test_spent_jumps_and_the_in_attempt_guard_still_bound_the_boss():
    assert not _want_y_jump(mem(cursor=NEAR, y_jumps={'3-B1:target': Y_JUMP_LIMIT}),
                            BOSS, 'ボス', 'target')
    assert not _want_y_jump(mem(cursor=NEAR, y_jump_last=dict(CLOSED)),
                            BOSS, 'ボス', 'target')


def test_an_ordinary_target_near_its_estimate_still_walks():
    order = next(o for o in chart.orders(3) if o['step'] == '3-Z2')
    assert not _want_y_jump(mem(cursor=list(chart.castles(3)['リグア'])), order, 'リグア', 'target')


def test_the_saved_frame_is_the_target_map_with_the_marker_inside_the_walls():
    frame = read_png(ENCLOSURE)
    screen = parse(frame, phase=classify(frame))
    assert screen.kind == 'map_target'
    assert screen.marker == (135, 119)


def test_a_cancelled_boss_attempt_starts_the_next_one_with_jumps_allowed():
    frame = read_png(ENCLOSURE)
    screen = parse(frame, phase=classify(frame))
    m = mem(y_jump_last=dict(CLOSED), cursor=CELL, active='3-B1',
            y_jumps={'3-B1:target': 2, '3-B1:map': 1})
    for _ in range(TARGET_MISS_LIMIT):
        actions = _unverified_target(screen, m, BOSS)
    assert actions and actions[0]['buttons'] == ['b']          # cancelled, general stays
    assert m['orders']['3-B1'] == 'pending'
    assert 'y_jump_last' not in m
    assert not any(k.startswith('3-B1:') for k in m['y_jumps'])
    assert _want_y_jump(m, BOSS, 'ボス', 'target')
