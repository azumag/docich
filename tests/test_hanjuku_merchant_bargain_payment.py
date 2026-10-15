"""Payment-time bounds are independent of the earlier optional plan."""
import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.strategy import bargains as b
from test_hanjuku_merchant_reserve import memory, quote


@pytest.mark.parametrize('metadata', [None, True, 1, 'ノリウツール', [[]], [{}]])
def test_bad_extra_metadata_cannot_reach_a_paid_confirmation(metadata):
    mem = memory(complete=True)
    mem['shop']['bargain_cards'] = metadata
    assert p.quantity_step(quote(999), mem) == [p.pad('b')]
    assert not mem['shop'].get('bought')


def test_cached_quantity_above_per_card_target_is_declined(monkeypatch):
    monkeypatch.setattr(b, '_charted_purchase_ahead', lambda mem, header: False)
    mem = memory(complete=True)
    mem['shop'].update(bargain_cards=['ノリウツール'],
                       items=[['ノリウツール', 4]], want=['ノリウツール', 4, 18])
    assert p.quantity_step(quote(999, count=4, cost=72), mem) == [p.pad('b')]
    assert not mem['shop'].get('bought')


def test_cached_discount_quantity_cannot_exceed_remaining_monthly_copies(monkeypatch):
    monkeypatch.setattr(b, '_charted_purchase_ahead', lambda mem, header: False)
    mem = memory(complete=True)
    bought = [['ノリウツール', 3, 1], ['クースカン', 2, 1]]
    mem['shop'].update(bargain_cards=['ノリウツール', 'クースカン', 'ゼンマイン'],
                       items=[['ゼンマイン', 2]], want=['ゼンマイン', 2, 1], bought=bought)
    assert b.merchant_card_spendable(mem, mem['shop'], quote(999).header, 'ゼンマイン', 1) == 1
    assert p.quantity_step(quote(999, count=2, cost=2), mem) == [p.pad('b')]
    assert len(mem['shop']['bought']) == 2
