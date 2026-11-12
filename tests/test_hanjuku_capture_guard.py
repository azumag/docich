"""A castle taken moments ago is not emptied by the next move.

g1163 2026-11-12 19:0x: ドンドン was taken twice by wave attacks and lost twice
inside the same window, because each wave left the castle empty and the enemy
walked back in.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.strategy import corridor

ROAD = chart.corridor(9)
A, B, C, D = ROAD[0], ROAD[1], ROAD[2], ROAD[3]


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(names, won=True, tick=5000, **extra):
    garrison = {A: ['どうし'],
                B: list(names),
                C: ['ヘルメス', 'エピィ']}
    history = [{'target': B, 'outcome': 'won' if won else 'lost',
                'enemy_hp': 0 if won else 40, 'tick': tick - 100}]
    mem = {'chapter': 9, 'tick': tick, 'month': '23-5',
           'captured': [A, B, C], 'lost': [], 'orders': {}, 'garrison': garrison,
           'world': {'garrison': garrison, 'launched_orders': {}, 'sorties': {}},
           'sortie_history': history, '_records': []}
    mem.update(extra)
    return mem


def moves(mem):
    with chart.map_context(mem):
        return corridor.orders(mem)


def decisions(mem, name):
    return [r for r in mem['_records'] if r.get('decision') == name]


def test_the_last_general_does_not_leave_a_fresh_capture():
    mem = memory(['リーキ'])
    with chart.map_context(mem):
        assert corridor._capture_guard_short(mem, B) is True
    assert decisions(mem, 'capture_guard_held')


def test_two_residents_are_not_held():
    mem = memory(['リーキ', 'ベーグル'])
    with chart.map_context(mem):
        assert corridor._capture_guard_short(mem, B) is False


def test_an_old_capture_does_not_hold():
    mem = memory(['リーキ'])
    mem['sortie_history'][0]['tick'] = 5000 - corridor.FLIP_WINDOW_TICKS - 10
    with chart.map_context(mem):
        assert corridor._capture_guard_short(mem, B) is False


def test_a_lost_castle_is_not_a_capture():
    mem = memory(['リーキ'], won=False)
    with chart.map_context(mem):
        assert corridor._capture_guard_short(mem, B) is False


def test_an_unread_castle_keeps_its_moves():
    mem = memory([])
    mem['world']['garrison'] = {}
    mem['garrison'] = {}
    with chart.map_context(mem):
        assert corridor._capture_guard_short(mem, B) is False


def test_a_one_general_front_does_not_hold_the_wave_for_ever():
    """g1163 2026-11-12 21:0x: 166 no-input map frames while the front sat at one."""
    mem = memory(['リーキ'])
    with chart.map_context(mem):
        assert corridor._front_guard_expired(mem, B) is False   # clock starts
    mem['tick'] += corridor.FRONT_GUARD_WAIT + 1
    with chart.map_context(mem):
        assert corridor._front_guard_expired(mem, B) is True
    assert [r for r in mem['_records'] if r.get('decision') == 'front_guard_timeout']
