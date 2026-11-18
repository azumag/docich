"""A readable battle command menu must not be held as an orphan.

g1190 2026-11-19 67-6: 「もうこうげき / たまごをつかう / きりふだ」 sat with no
planned action (the battle state was stale) and only retreat-recheck decisions
were recorded for ten minutes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku.os_layer import screen as screen_mod
from docich.hanjuku.os_layer.pixels import Frame
import docich.hanjuku.orchestrator as orch

TEXT = 'もうこうげきたまごをつかうきりふだ'


def decide_with(monkeypatch, text, kind='battle_menu'):
    sc = screen_mod.Screen([], None, text, kind=kind)
    monkeypatch.setattr(screen_mod, 'parse', lambda *a, **k: sc)
    monkeypatch.setattr(orch, 'parse', lambda *a, **k: sc, raising=False)
    frame = Frame(256, 224, bytes(256 * 224 * 3))
    mem = {'chapter': 11, '_records': [], 'tick': 100, 'battle': {}}
    return orch.decide(frame, {'policy': mem, 'step': 1}, run_identity=None)


def test_the_readable_menu_attacks(monkeypatch):
    actions, updated = decide_with(monkeypatch, TEXT)
    assert actions == [orch.pad('a')]
    assert any(r.get('decision') == 'battle_menu_attack' for r in updated['_records'])


def test_another_menu_is_not_forced(monkeypatch):
    actions, _ = decide_with(monkeypatch, 'たたかいにもどりますか?', kind='yes_no')
    assert actions != [orch.pad('a')]
