"""The all-generals roster panel must not hold the shop phase.

g1190 2026-11-19 67-4: the panel (「わがくにのすべてのしょうぐんをみることが
できますぞ!」) was classified as the shop's main menu and every observation held;
only one decision was recorded in seven minutes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku import orchestrator as O

TEXT = '11わ67ねん4のつき9659Gステータスしょうぐんしろきりふだシステムけんのけいこセーブわがくにのすべてのしょうぐんをみることができますぞ!'


def memory():
    return {'chapter': 11, '_records': [], 'tick': 100}


def test_the_roster_panel_is_closed_with_b(monkeypatch):
    from docich.hanjuku.os_layer import screen as screen_mod
    from docich.hanjuku.os_layer.pixels import Frame
    import docich.hanjuku.orchestrator as orch
    sc = screen_mod.Screen([], None, TEXT, kind='main_menu')
    monkeypatch.setattr(screen_mod, 'parse', lambda *a, **k: sc)
    monkeypatch.setattr(orch, 'parse', lambda *a, **k: sc, raising=False)
    frame = Frame(256, 224, bytes(256 * 224 * 3))
    actions, updated = orch.decide(frame, {'policy': memory(), 'step': 1},
                                   run_identity=None)
    assert actions == [orch.pad('b')]
    assert any(r.get('decision') == 'close_panel' for r in updated['_records'])


def _decide(mem, monkeypatch):
    from docich.hanjuku.os_layer import screen as screen_mod
    from docich.hanjuku.os_layer.pixels import Frame
    import docich.hanjuku.orchestrator as orch
    sc = screen_mod.Screen([], None, TEXT, kind='main_menu')
    monkeypatch.setattr(screen_mod, 'parse', lambda *a, **k: sc)
    monkeypatch.setattr(orch, 'parse', lambda *a, **k: sc, raising=False)
    frame = Frame(256, 224, bytes(256 * 224 * 3))
    return orch.decide(frame, {'policy': mem, 'step': 1}, run_identity=None)


def test_castle_jump_menu_is_not_closed(monkeypatch):
    """docich#4748: X opened the command menu for the castle jump; the
    しょうぐん help line matched the roster panel signature and the stray
    close-with-B rule killed the menu before the jump could reach しろ."""
    mem = memory()
    mem['castle_jump'] = {'phase': 'menu', 'step': 's1', 'source': 'ユージア',
                          'age': 1, 'total': 1}
    actions, updated = _decide(mem, monkeypatch)
    assert not any(r.get('decision') == 'close_panel' for r in updated['_records'])


def test_house_open_roster_menu_is_not_closed(monkeypatch):
    mem = memory()
    mem['house'] = {'phase': 'open_roster', 'age': 4}
    actions, updated = _decide(mem, monkeypatch)
    assert not any(r.get('decision') == 'close_panel' for r in updated['_records'])
