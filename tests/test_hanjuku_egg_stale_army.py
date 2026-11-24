"""A covered battle panel must not spend the egg while the last panel led."""
from copy import deepcopy

from docich import hanjuku_policy as p
from test_hanjuku_survival import menu

IDENT = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef12', 'generation': 1,
         'lease_id': 'l'}


def stale_memory(**over):
    cur = {'ally': 'ヴィーナス', 'enemy': 'ココット', 'ally_hp': 70, 'enemy_hp': 16,
           'start_ally_hp': 82, 'start_enemy_hp': 24,
           'ally_soldiers': 5, 'enemy_soldiers': 1,
           'card_soldiers_current': False, 'card_hp_unread': True,
           'card_context_unclassified': True,
           'melee_opening_scope': [dict(IDENT), 'ヴィーナス', 'ココット', 82, 24],
           'cards_used': [], 'cards_selected': [], 'planned_cards': [],
           'card_evidence_version': 1,
           'survival': {'opens': 1, 'menu_ticks': 1, 'cards_checked': 0,
                        'cards_attempted': [], 'egg_attempted': False}}
    cur.update(over)
    return {'chapter': 1, '_run_identity': dict(IDENT), 'battle': cur}


def test_a_covered_panel_holds_the_egg_when_the_last_panel_clearly_led():
    mem = stale_memory()
    assert p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'}) is True
    labels = ('たまごをつかう', 'きりふだ', 'たいきゃく')
    assert p._survival_menu(menu(labels), mem, mem['battle']) == [p.pad('b')]
    assert mem['battle']['survival']['stale_conserved'] == 1
    # The hold is bounded: after two returns the observed menu is used.
    mem['battle']['survival']['stale_conserved'] = 2
    assert p._survival_menu(menu(labels), mem, mem['battle']) != [p.pad('b')]


def test_stale_hold_needs_a_clear_healthy_margin():
    for over in ({'enemy_soldiers': 6, 'enemy_hp': 60},   # enemy can still out-force us
                 {'ally_hp': 40},                        # below 70% of max HP
                 {'enemy_hp': 71},                       # HP lead gone
                 {'ally_hp': 70, 'start_ally_hp': 60},   # impossible opening reading
                 {'egg_battle': True, 'card_summon_observed': True}):
        mem = stale_memory(**over)
        assert p._stale_army_melee_hold(mem, mem['battle']) is None
        assert p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'}) is False


def test_a_current_panel_does_not_use_the_stale_rule():
    mem = stale_memory(card_soldiers_current=True, card_hp_unread=False,
                       card_context_unclassified=False)
    assert p._stale_army_melee_hold(mem, mem['battle']) is None
