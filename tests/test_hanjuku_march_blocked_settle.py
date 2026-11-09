"""A march blocked by our own castle is closed, not re-issued.

g1163 chapter 7 2026-11-09: アルマムーン → ロキュータ was sent twice after
レオナール had been captured on that line, and アルマムーン → フィロメラ once
behind ベンゴディ; every attempt stopped inside the blocking castle and expired
as overdue.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.state.regions import world as _world
from docich.hanjuku.strategy import orders

# Owned exactly as measured at 2026-11-09 17:08 in chapter 7: レオナール and
# ベンゴディ are ours (they are the castles that block those two lines).
OWNED = {'アルマムーン', 'レオナール', 'ベンゴディ'}
TICK = 100_000


def memory(source='アルマムーン', target='ロキュータ', status='en_route', step='A:x:M1'):
    return {'chapter': 7, 'tick': TICK, '_records': [],
            'captured': ['ベンゴディ', 'レオナール'],
            'orders': {step: 'launched'},
            'sorties': {step: {'general': 'キール', 'source': source, 'target': target,
                               'status': status, 'tick': 0}},
            'world': {'launched_orders': {step: {'source': source, 'target': target}}}}


def test_a_blocked_march_is_closed_instead_of_reissued():
    mem = memory()
    orders._relaunch_overdue_march(mem)
    assert mem['orders']['A:x:M1'] == 'failed'
    # The world region owns sorties after the first access.
    assert (_world(mem).get('sorties') or {})['A:x:M1']['status'] == 'overdue'
    assert any(r['decision'] == 'sortie_march_blocked_settled' for r in mem['_records'])
    assert not any(r['decision'] == 'sortie_march_overdue' for r in mem['_records'])


def test_a_clear_march_still_relaunches():
    mem = memory(target='リデンブロック')
    orders._relaunch_overdue_march(mem)
    assert mem['orders']['A:x:M1'] == 'pending'
    assert any(r['decision'] == 'sortie_march_overdue' for r in mem['_records'])
