"""A failed camp cooldown must not abandon every other visible camp."""

from docich import hanjuku_policy as p
from docich.hanjuku_screen import Screen


MAP = Screen([], None, '', kind='map', cursor=(120, 100))


def test_failed_camp_records_a_scoped_cooldown(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [])
    mem = {'chapter': 1, 'tick': 100,
           'recall': {'stage': 'to_camp', 'steps': 0, 'target': [120, 100]}}

    for _ in range(3):
        assert p.camp_recall_step(MAP, mem, object()) == []

    assert mem['recall_skip'] == {'tick': 100}
    assert mem['recall_skip_target'] == {'chapter': 1, 'target': [120, 100]}
    assert 'recall' not in mem


def test_scoped_cooldown_skips_only_the_failed_camp(monkeypatch):
    camps = [{'target': (120, 100)}, {'target': (200, 100)}]
    monkeypatch.setattr(p, 'own_camps', lambda _frame: camps)
    mem = {'chapter': 1, 'tick': 101,
           'recall_skip': {'tick': 100},
           'recall_skip_target': {'chapter': 1, 'target': [120, 100]}}

    assert p.camp_recall_step(MAP, mem, object()) == [p.pad('right', 8)]
    assert mem['recall']['target'] == [200, 100]


def test_scoped_cooldown_still_blocks_the_same_camp(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (124, 104)}])
    mem = {'chapter': 1, 'tick': 101,
           'recall_skip': {'tick': 100},
           'recall_skip_target': {'chapter': 1, 'target': [120, 100]}}

    assert p.camp_recall_step(MAP, mem, object()) is None
    assert 'recall' not in mem


def test_legacy_tick_only_cooldown_remains_fail_closed(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (200, 100)}])
    mem = {'chapter': 1, 'tick': 101, 'recall_skip': {'tick': 100}}

    assert p.camp_recall_step(MAP, mem, object()) is None
    assert 'recall' not in mem


def test_expired_scoped_cooldown_allows_the_same_camp_again(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (120, 100)}])
    mem = {'chapter': 1, 'tick': 100 + p.RECALL_SKIP_TICKS,
           'recall_skip': {'tick': 100},
           'recall_skip_target': {'chapter': 1, 'target': [120, 100]}}

    assert p.camp_recall_step(MAP, mem, object()) == [p.pad('a')]
    assert mem['recall']['stage'] == 'menu'
    assert 'recall_skip' not in mem
