"""A castle lost in a defense battle is recorded as a loss (v575).

g1163 chapter 7 2026-11-09: マンシー and リデンブロック fell and no
`castle_lost_observed` was ever written, because the defense-loss path only
appended to ``lost``. Without that record the diagnostics cannot show why a
castle fell or what garrison it had.
"""
from docich.hanjuku.flows import battle as B
from docich.hanjuku.state.regions import world as _world


def memory():
    return {'chapter': 7, 'tick': 500, '_records': [],
            'captured': ['マンシー', 'ベンゴディ'], 'lost': [],
            'world': {'garrison': {'マンシー': ['アテナ'], 'ベンゴディ': ['キール']}},
            'battle': {'ally': 'アテナ', 'enemy': 'ナストリーニ', 'castle': 'マンシー',
                       'side': 'defense', 'step': 'A:x:M1',
                       'ally_hp': 0, 'enemy_hp': 40, 'away': 1},
            'stats': {'wins': 0, 'losses': 0, 'unclassified': 0, 'cards_used': 0,
                      'generals_lost': None, 'cards_confirmed': 0, 'card_evidence_version': 1}}


def test_a_defense_loss_records_the_castle_and_its_garrison():
    mem = memory()
    B.battle_end(mem, 'map')
    losses = [r for r in mem['_records'] if r['decision'] == 'castle_lost_observed']
    assert losses, [r['decision'] for r in mem['_records']]
    metric = losses[-1]['observed_metric']
    assert metric['cause'] == 'defense_loss'
    assert metric['garrison'] == ['アテナ']
    assert 'マンシー' in mem['lost']
    assert not (_world(mem).get('garrison') or {}).get('マンシー')
