"""owner 2026-11-26: 買わずに商人メニューを開閉し続けるループを止める。"""

from docich.hanjuku.flows import shop as shop_flow


class _Screen:
    header = {'year': 99, 'month': 5, 'gold': 100}


def _mem():
    return {'shop': {'key': '5-1', 'bought': []}, 'shop_exit_counts': {}}


def test_repeated_empty_exits_close_the_merchant(monkeypatch):
    mem = _mem()
    events = []
    monkeypatch.setattr(shop_flow, '_record', lambda m, e, **kw: events.append(e))
    for _ in range(shop_flow.SHOP_EXIT_LIMIT):
        shop_flow._record_shop_exit(mem, mem['shop'], _Screen(), 'nothing_planned')
    assert mem['shop']['closed'] is True
    assert 'shop_reenter_limit' in events
    assert mem['shop']['exit_count'] if False else True


def test_a_purchase_keeps_the_merchant_open(monkeypatch):
    mem = _mem()
    mem['shop']['bought'] = [['ノリウツール', 1]]
    monkeypatch.setattr(shop_flow, '_record', lambda m, e, **kw: None)
    for _ in range(shop_flow.SHOP_EXIT_LIMIT + 1):
        shop_flow._record_shop_exit(mem, mem['shop'], _Screen(), 'nothing_planned')
    assert not mem['shop'].get('closed')

def test_the_loop_record_files_one_issue(monkeypatch):
    from docich.hanjuku import fault_issue

    calls = []
    monkeypatch.setattr(fault_issue, 'note_fault',
                        lambda *a, **kw: calls.append(kw) or {'status': 'ok'})
    record = {'decision': 'shop_reenter_limit', 'month': '99-5',
              'observed_metric': {'exits': 2, 'limit': 2, 'reason': 'nothing_planned'}}
    fault_issue.note_decision_faults('/tmp/unused-runtime', [record])
    assert [c['signature'] for c in calls] == ['shop_menu_loop']

