"""Owner 2026-11-05: track each sortie's win/loss/retreat by diff.

The record used to stay 'en_route' after a defeat, so the left status chart
kept showing a march that was already over and the ledger never closed it.
"""
from docich.hanjuku.flows import battle
from docich.hanjuku.state.regions import world as _world


def memory(ally_hp, enemy_hp, castle='ボス', target='ボス', side='attack', retreat=False):
    cur = {'ally': 'ペコリーノ', 'enemy': 'ハードロボ', 'castle': castle,
           'side': side, 'step': 'A:x:M1', 'ally_hp': ally_hp, 'enemy_hp': enemy_hp,
           'away': 1, 'card_consumption_complete': True,
           'cards_used': [], 'cards_selected': []}
    if retreat:
        cur['hero_retreat'] = {'selected': True}
    return {
        'chapter': 6, 'tick': 1000, '_records': [], 'orders': {'A:x:M1': 'launched'},
        'garrison': {'アルマムーン': ['ペコリーノ']},
        'world': {'sorties': {'A:x:M1': {'general': 'ペコリーノ', 'source': 'アルマムーン',
                               'target': target, 'status': 'en_route', 'tick': 900}}},
        'battle': cur,
    }


def outcomes(mem):
    return [r for r in mem.get('_records', []) if r['decision'] == 'sortie_outcome']


def test_a_loss_at_the_target_closes_the_sortie():
    mem = memory(0, 500)
    battle.battle_end(mem, 'field')
    assert 'A:x:M1' not in (_world(mem).get('sorties') or {})
    assert mem['sortie_history'][-1]['outcome'] == 'lost'
    assert outcomes(mem)


def test_a_retreat_is_tracked_as_retreated():
    mem = memory(20, 500, retreat=True)
    battle.battle_end(mem, 'field')
    assert 'A:x:M1' not in (_world(mem).get('sorties') or {})
    assert mem['sortie_history'][-1]['outcome'] == 'retreated'


def test_a_win_at_the_target_closes_the_sortie_as_won():
    mem = memory(30, 0)
    battle.battle_end(mem, 'field')
    assert 'A:x:M1' not in (_world(mem).get('sorties') or {})
    assert mem['sortie_history'][-1]['outcome'] == 'won'


def test_a_road_battle_leaves_the_march_running():
    # A field encounter at another castle is not the end of the sortie.
    mem = memory(0, 500, castle='トウトウ')
    battle.battle_end(mem, 'field')
    assert 'A:x:M1' in (_world(mem).get('sorties') or {})
