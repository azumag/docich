"""A held sortie stays held across observations until the castle's list changes.

g1075-1d5f65e9 2026-10-30 JST, chapter 4: the adjusted step A:6d0149e9:J1
(アルカ from アルマムーン) met the hero-castle guard with [どうし, ピスタチオ,
アンディーブ] and was started and cancelled 11 times from 01:20:38 until a
recruit joined at 01:29; A:dd76b62b:J2 was cancelled 15 times before it.
The cancel gate stored the list as a tuple and the policy came back from
JSON as a list, so the gate never matched; and the third cancel's
``failed`` was put back to ``pending`` for an adjusted step.
"""
import json
from types import SimpleNamespace

from docich import hanjuku_policy as policy

HOME = 'アルマムーン'
PRESENT = ['どうし', 'ピスタチオ', 'アンディーブ']
ORDER = {'step': 'A:6d0149e9:J1', 'general': 'どうし', 'source': HOME, 'target': 'アルカ',
         'cards': [], 'after': None}


def memory():
    return {'chapter': 4, 'month': '25-4', 'tick': 1,
            'captured': ['ネクスドリア', 'ビクトリア', 'フヌイヌム', 'マラコビア', 'ミヒラギアン'],
            'garrison': {HOME: list(PRESENT)}, 'orders': {}}


def reload(mem):
    return json.loads(json.dumps(mem, ensure_ascii=False))


def hold(mem, monkeypatch, present=PRESENT):
    monkeypatch.setattr(policy, '_present_generals', lambda screen: list(present))
    return policy._keep_hero_guard(SimpleNamespace(), mem, dict(ORDER))


def test_a_hero_guard_hold_still_suppresses_after_the_policy_reload(monkeypatch):
    mem = memory()
    assert hold(mem, monkeypatch)
    mem = reload(mem)
    assert policy._source_defender_suppressed(ORDER, mem) is True
    assert not policy._ready(dict(ORDER), mem)


def test_a_joiner_on_the_list_releases_the_hold(monkeypatch):
    mem = memory()
    assert hold(mem, monkeypatch)
    mem = reload(mem)
    mem['garrison'][HOME] = [*PRESENT, 'タピオカ']
    assert policy._source_defender_suppressed(ORDER, mem) is False


def test_the_third_hold_retires_an_adjusted_step(monkeypatch):
    mem = memory()
    for month in ('25-4', '25-5', '25-6'):
        mem = reload(mem)
        mem['month'] = month
        assert hold(mem, monkeypatch)
    assert mem['orders'][ORDER['step']] == 'failed'
