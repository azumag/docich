"""Regression tests for finite general-list hold (#3306)."""
from types import SimpleNamespace

from docich.hanjuku.flows import deploy


def _screen():
    return SimpleNamespace(kind='general_list', hand=None, lines=[], text='')


def _memory():
    return {'chapter': 4, 'tick': 100, 'active': '4-B1',
            'orders': {'4-B1': 'pending'}, 'retry_context': {
                '4-B1': {'strategy_variant': 'retry_with_opening_cards'}}}


def _order():
    return {'step': '4-B1', 'general': 'アルテミス', 'source': 'ミヒラギアン'}


def test_unreadable_roster_hold_is_bounded(monkeypatch):
    events = []
    monkeypatch.setattr(deploy, '_deploy_context', lambda *a, **kw: {})
    monkeypatch.setattr(deploy, '_deploy_cards', lambda *a: [])
    monkeypatch.setattr(deploy, '_options', lambda *a: [])
    monkeypatch.setattr(deploy, '_record', lambda mem, decision, **kw:
                        events.append((decision, kw)))
    monkeypatch.setattr(deploy, '_finish_order', lambda mem, status, **kw:
                        events.append(('finished', {'status': status, **kw})))
    mem, screen, order = _memory(), _screen(), _order()
    for i in range(deploy.GENERAL_LIST_HOLD_LIMIT - 1):
        assert deploy._hold_general_list_bounded(
            screen, mem, order, 'unreadable', 'roster_unreadable') == []
    assert mem['general_list_hold']['4-B1'] == deploy.GENERAL_LIST_HOLD_LIMIT - 1
    assert deploy._hold_general_list_bounded(
        screen, mem, order, 'unreadable', 'roster_unreadable') == [
            deploy.pad('b'), deploy.pad('b')]
    assert '4-B1' not in mem['general_list_hold']
    assert any(kind == 'finished' and kw['status'] == 'failed'
               for kind, kw in events)
    assert any(kind == 'situation_held'
               and kw['observed_metric']['hold_variant'] == 'roster_unreadable'
               for kind, kw in events)
    assert any(kind == 'general_list_hold_exhausted'
               and kw['observed_metric']['hold_count'] == deploy.GENERAL_LIST_HOLD_LIMIT
               for kind, kw in events)


def test_general_list_hold_counters_are_per_step(monkeypatch):
    monkeypatch.setattr(deploy, '_deploy_context', lambda *a, **kw: {})
    monkeypatch.setattr(deploy, '_deploy_cards', lambda *a: [])
    monkeypatch.setattr(deploy, '_options', lambda *a: [])
    monkeypatch.setattr(deploy, '_record', lambda *a, **kw: None)
    mem, screen, order = _memory(), _screen(), _order()
    assert deploy._hold_general_list_bounded(
        screen, mem, order, 'cursor', 'cursor_unreachable') == []
    other = {**order, 'step': '4-B2'}
    assert deploy._hold_general_list_bounded(
        screen, mem, other, 'cursor', 'cursor_unreachable') == []
    assert mem['general_list_hold'] == {'4-B1': 1, '4-B2': 1}
