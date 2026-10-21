"""Our own launched record without a step is an attack of ours, not an enemy's (g839 step 4210).

The mis-read ``side='enemy'`` switched off the value-aware retreat policy
(``retreat._live_melee`` requires ``side == 'attack'``).
"""
from docich import hanjuku_policy as p
from test_hanjuku_sortie_interruption import screen


def entry(mem):
    p.message_step(screen('attack_started', ['アマンディーヌしょうぐんがドミノーラじょうにのりこんだ']), mem)
    return mem['attack']


def test_own_launched_record_without_a_step_is_our_attack_not_an_enemy_one():
    mem = {'chapter': 2, 'launched': {'ドミノーラ': {'general': 'アマンディーヌ', 'step': None}}}
    attack = entry(mem)
    assert attack['side'] == 'attack' and attack['general'] == 'アマンディーヌ'


def test_an_entry_with_no_record_of_ours_is_still_an_enemy_attack():
    assert entry({'chapter': 2})['side'] == 'enemy'


def test_a_launched_record_for_another_general_does_not_make_it_ours():
    mem = {'chapter': 2, 'launched': {'ドミノーラ': {'general': 'ゴーメン', 'step': None}}}
    assert entry(mem)['side'] == 'enemy'
