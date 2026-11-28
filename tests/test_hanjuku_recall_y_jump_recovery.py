"""docich#5788: RECALL Y-jump failures recover instead of reopening forever."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.os_layer import navigation as nav
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.state.regions import flow as _flow
from docich.hanjuku_screen import Screen


MAP = Screen([], None, '', kind='map')


class ChangingFrame:
    def __init__(self, serial):
        self.serial = serial

    def digest(self):
        return f'frame-{self.serial}'


def memory(captured, goal='アルマムーン'):
    mem = {'chapter': 1, 'tick': 100, '_records': [], 'captured': list(captured)}
    _flow(mem)['recall'] = {
        'stage': 'y_route', 'steps': 0, 'goal': goal,
        'target': [134, 120], 'origin': [500, 700],
    }
    return mem


def close_with_residual(monkeypatch, mem, goal):
    x, y = chart.castles(1)[goal]
    ox, oy = nav.Y_JUMP_OFFSET[1]
    cursor = (x / 8 + ox, y / 8 + oy - 6.0)
    monkeypatch.setattr(nav, 'world_cursor', lambda *_: cursor)
    monkeypatch.setattr(nav, 'world_flags', lambda *_: {})
    for serial in range(12):
        nav._y_jump_step(mem, ChangingFrame(serial))
        if not mem.get('y_jump'):
            break
    assert mem['y_jump_last']['why'] == 'residual'


def open_and_fail_once(monkeypatch, mem):
    state = _flow(mem)['recall']
    assert recall_flow._y_route_step(MAP, mem, state) == [pad('y')]
    close_with_residual(monkeypatch, mem, state['goal'])


def decisions(mem, name):
    return [r for r in mem['_records'] if r['decision'] == name]


def test_repeated_residual_unreachable_stops_the_single_castle_retry(monkeypatch):
    mem = memory(['アルマムーン'])
    open_and_fail_once(monkeypatch, mem)
    assert _flow(mem)['recall_goal_failures']['134,120']['アルマムーン'] == 1

    open_and_fail_once(monkeypatch, mem)
    assert _flow(mem)['recall_goal_failures']['134,120']['アルマムーン'] == 2
    assert nav.y_jump_unreachable(mem, 'アルマムーン') is True

    assert recall_flow._y_route_step(MAP, mem, _flow(mem)['recall']) == []
    assert 'recall' not in _flow(mem)
    assert decisions(mem, 'camp_recall_y_unreachable')
    assert not mem.get('y_jump')


def test_predispatch_failure_switches_the_next_jump_to_another_castle(monkeypatch):
    mem = memory(['アルマムーン', 'キカンドン', 'ゴーメン'])
    open_and_fail_once(monkeypatch, mem)

    actions = recall_flow._y_route_step(MAP, mem, _flow(mem)['recall'])
    assert actions == [pad('y')]
    assert mem['y_jump']['goal'] == 'キカンドン'
    assert _flow(mem)['recall']['goal'] == 'キカンドン'
    assert decisions(mem, 'camp_recall_goal_failed')
    assert decisions(mem, 'camp_recall_y_goal_changed')


def test_a_marked_unreachable_goal_is_not_reopened(monkeypatch):
    mem = memory(['アルマムーン', 'キカンドン'])
    mem['y_jump_unreachable'] = {
        'アルマムーン': {'count': 2, 'residual': [0.0, 6.0], 'tick': 100},
    }

    assert recall_flow._y_route_step(MAP, mem, _flow(mem)['recall']) == [pad('y')]
    assert mem['y_jump']['goal'] == 'キカンドン'
    assert _flow(mem)['recall_goal_failures']['134,120']['アルマムーン'] == 1


def test_the_recall_y_route_budget_is_enforced():
    cases = (
        {'y_jumps': {'RECALL:target': nav.Y_JUMP_LIMIT}},
        {'y_jumps_total': {'RECALL:target': nav.Y_JUMP_TOTAL_LIMIT}},
        {'tally': {'yjump:RECALL:target': nav.Y_JUMP_TOTAL_LIMIT}},
    )
    for counters in cases:
        mem = memory(['アルマムーン', 'キカンドン'])
        mem.update(counters)

        assert recall_flow._y_route_step(MAP, mem, _flow(mem)['recall']) == []
        assert 'recall' not in _flow(mem)
        assert decisions(mem, 'camp_recall_y_budget_exhausted')
        assert not mem.get('y_jump')
