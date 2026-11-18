"""A collapsed army outranks the deferred-egg recruit gate.

g1190 2026-11-18 64-10: the month menu kept deferring recruitment
(recruit=deferred_egg) because recruit_priority is set by _prioritise_recruit,
which returns early while the month is already deferred_egg. With a measured
army at or below half the target, the roster must still be allowed to grow.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku.strategy import economy as e

ID = {'game': 'hanjuku-hero', 'runtime_id': 'r', 'generation': 1, 'lease_id': 'l'}


def memory(count, *, gold=9999):
    return {'chapter': 11, 'month': '64-10', 'gold': gold, '_run_identity': dict(ID),
            'country_general_count': {'identity': dict(ID), 'count': count,
                                      'scope': [11, '64-10']},
            'world': {'garrison': {'ゴンダル': ['シフォン']}}}


def shop(**extra):
    state = {'egg': 'skipped', 'recruit': 'deferred_egg', 'recruit_priority': False,
             'key': '64-10'}
    state.update(extra)
    return state


def test_a_collapsed_army_releases_the_deferred_egg_gate(monkeypatch):
    monkeypatch.setattr(e, '_no_empty_egg_to_recover', lambda *_: True)
    monkeypatch.setattr(e, '_egg_recovery_targets', lambda *_: [])
    mem = memory(3)
    assert e._severe_recruit_shortage(mem) is True
    assert e._recruit_egg_ready(mem, shop()) is True


def test_a_mild_shortage_still_waits_for_the_priority_flag(monkeypatch):
    monkeypatch.setattr(e, '_no_empty_egg_to_recover', lambda *_: True)
    monkeypatch.setattr(e, '_egg_recovery_targets', lambda *_: [])
    mem = memory(18)          # above half of the target
    assert e._severe_recruit_shortage(mem) is False
    assert e._recruit_egg_ready(mem, shop()) is False
    assert e._recruit_egg_ready(mem, shop(recruit_priority=True)) is True


def test_a_pending_egg_recheck_still_holds(monkeypatch):
    monkeypatch.setattr(e, '_no_empty_egg_to_recover', lambda *_: True)
    monkeypatch.setattr(e, '_egg_recovery_targets', lambda *_: ['どうし'])
    mem = memory(3)
    mem['egg_recheck'] = ['どうし']
    assert e._recruit_egg_ready(mem, shop()) is False
