"""The align servo must not pace around the same spot (owner 2026-11-06)."""
from types import SimpleNamespace

from docich.hanjuku.os_layer import navigation
from docich.hanjuku_screen import Screen

FRAME = SimpleNamespace(width=256, height=224)


def marker(x):
    return Screen([], None, '', kind='map_target', marker=(x, 50))


def test_same_offset_corrections_stop_and_accept_the_close_marker(monkeypatch):
    roof = {'kind': 'enemy', 'target': (100, 50), 'clipped': False}
    monkeypatch.setattr(navigation, 'castle_roofs', lambda _frame: [roof])
    mem = {}
    first = navigation._align_on_roof(marker(94), mem, FRAME, ('enemy',), 'k')
    assert first and first[0]['buttons'] == ['right']
    assert navigation._align_on_roof(marker(94), mem, FRAME, ('enemy',), 'k')
    # The same offset for two observations is not progress: stop correcting.
    assert navigation._align_on_roof(marker(94), mem, FRAME, ('enemy',), 'k') == 'on'
    assert 'k' not in mem.get('align_steps', {})


def test_a_far_stalled_marker_gives_up_instead_of_pacing(monkeypatch):
    roof = {'kind': 'enemy', 'target': (100, 50), 'clipped': False}
    monkeypatch.setattr(navigation, 'castle_roofs', lambda _frame: [roof])
    mem = {}
    # 20 px away: the marker cannot move in this fixture, so the reading is
    # noise. After two no-progress corrections the jump must be redone.
    assert navigation._align_on_roof(marker(80), mem, FRAME, ('enemy',), 'k')
    assert navigation._align_on_roof(marker(80), mem, FRAME, ('enemy',), 'k')
    assert navigation._align_on_roof(marker(80), mem, FRAME, ('enemy',), 'k') is None
    assert 'k' not in mem.get('align_steps', {})


def test_a_shrinking_offset_keeps_correcting(monkeypatch):
    roof = {'kind': 'enemy', 'target': (100, 50), 'clipped': False}
    monkeypatch.setattr(navigation, 'castle_roofs', lambda _frame: [roof])
    mem = {}
    assert navigation._align_on_roof(marker(90), mem, FRAME, ('enemy',), 'k')
    assert navigation._align_on_roof(marker(94), mem, FRAME, ('enemy',), 'k')
    # 3 px or less selects the castle.
    assert navigation._align_on_roof(marker(98), mem, FRAME, ('enemy',), 'k') == 'on'

def test_the_owner_limit_is_two_corrections():
    from docich.hanjuku.os_layer.navigation import ALIGN_LIMIT
    assert ALIGN_LIMIT == 2
