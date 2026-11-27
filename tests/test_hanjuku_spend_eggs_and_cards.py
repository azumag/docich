"""Regressions for spending remaining eggs and duplicate/retried cards."""
from unittest.mock import patch

from docich import hanjuku_policy as p
from docich.hanjuku.flows import battle_menus as menus
from docich.hanjuku.judgment import battle as rules
from test_hanjuku_card_damage_gate import cards, memory as gate_memory
from test_hanjuku_survival import menu


def _survival_mem(ally='ビシソワーズ', enemy='クミン', ally_hp=10, enemy_hp=50):
    return {
        'chapter': 6,
        'tick': 100,
        'egg_uses': {ally: 2},
        '_records': [],
        'battle': {
            'ally': ally,
            'enemy': enemy,
            'ally_hp': ally_hp,
            'enemy_hp': enemy_hp,
            'start_ally_hp': 60,
            'start_enemy_hp': 60,
            'side': 'attack',
            'step': '6-1',
            'castle': 'ミルフィーユ',
            'cards_used': [],
            'cards_selected': [],
            'cards_unclassified': [],
            'planned_cards': [],
            'card_evidence_version': 1,
        },
    }


def test_survival_menu_allows_bounded_egg_retry_after_first_unconfirmed_pick():
    mem = _survival_mem(ally_hp=10)
    cur = mem['battle']
    cur['survival'] = {
        'opens': 1, 'menu_ticks': 0, 'cards_checked': 0, 'cards_attempted': [],
        'egg_attempted': True, 'egg_attempts': 1, 'egg_attempt_hp': 20,
    }
    assert p._survival_menu(menu(('たまごをつかう', 'たいきゃく')), mem, cur) == [p.pad('a')]
    assert cur['survival']['egg_attempts'] == 2

    # Dropping further HP allows retrying up to EGG_ATTEMPT_HARD_LIMIT.
    cur['ally_hp'] = 6
    cur['survival']['egg_pending'] = False
    assert p._survival_menu(menu(('たまごをつかう', 'たいきゃく')), mem, cur) == [p.pad('a')]
    assert cur['survival']['egg_attempts'] == 3

    # Legacy egg_attempted=True with egg_attempts=0 remains spent.
    legacy_mem = _survival_mem(ally_hp=10)
    legacy_cur = legacy_mem['battle']
    legacy_cur['survival'] = {
        'opens': 1, 'menu_ticks': 0, 'cards_checked': 0, 'cards_attempted': [],
        'egg_attempted': True, 'egg_attempts': 0,
    }
    assert rules._egg_attempt_spent(legacy_cur['survival'], legacy_cur.get('ally_hp')) is True
    assert p._survival_menu(menu(('たまごをつかう', 'たいきゃく')), legacy_mem, legacy_cur) == [p.pad('b')]


def test_white_egg_observation_rearms_exhausted_survival_once():
    mem = _survival_mem(ally_hp=10)
    cur = mem['battle']
    cur['survival'] = {
        'opens': 3, 'menu_ticks': 15, 'cards_checked': 3, 'cards_attempted': [],
        'cards_exhausted': True, 'exhausted': True, 'exhausted_closed': True,
        'egg_attempted': True, 'egg_attempts': 1, 'egg_attempt_hp': 20,
    }
    with patch.object(menus, '_white_egg_available', return_value=True):
        actions = p.battle_menu_step(menu(('たまごをつかう', 'たいきゃく')), mem)
    assert actions == [p.pad('a')]
    assert cur['survival']['white_egg_rearmed'] is True
    assert cur['survival']['egg_attempts'] == 2


def test_critical_ordinary_battle_allows_nonlethal_card_without_summon():
    with (patch.object(rules, '_card_wears_down_enemy', return_value=False),
          patch.object(rules, '_score_pressure', return_value=False),
          patch.object(rules, '_egg_race_pressure', return_value=False)):
        mem = gate_memory('ゼンマイン', enemy='クミン', enemy_hp=60, ally_hp=6)
        gate = rules._card_damage_gate(mem, mem['battle'], 'ゼンマイン', listed_cards=['ゼンマイン'])
        assert gate['allowed'] is True and gate['role'] == 'critical_card_spend'

        safe_mem = gate_memory('ゼンマイン', enemy='クミン', enemy_hp=60, ally_hp=60, soldiers=(6, 0))
        safe_gate = rules._card_damage_gate(safe_mem, safe_mem['battle'], 'ゼンマイン', listed_cards=['ゼンマイン'])
        assert safe_gate['allowed'] is False


def test_duplicate_carried_card_survives_first_use_in_remaining_helpers():
    mem = _survival_mem()
    cur = mem['battle']
    cur['planned_cards'] = ['ノリウツール', 'ノリウツール', 'クースカン']
    cur['cards_used'] = ['ノリウツール']
    cur['cards_selected'] = ['ノリウツール']
    assert menus._remaining_carried_cards(mem, cur) == ['ノリウツール', 'クースカン']
    assert menus._carried_card_left(mem, cur) == 'ノリウツール'


def test_critical_survival_retries_unconfirmed_card_and_allows_extra_checks():
    mem = _survival_mem(ally_hp=8)
    cur = mem['battle']
    cur['cards_selected'] = ['イッテツーン']
    cur['cards_unclassified'] = ['イッテツーン']
    cur['survival'] = {
        'opens': 4, 'menu_ticks': 0, 'cards_checked': 4,
        'cards_attempted': ['イッテツーン'],
        'card_counts_at_selection': {'イッテツーン': 1},
        'egg_attempted': True, 'egg_attempts': rules.EGG_ATTEMPT_HARD_LIMIT,
    }
    # Critical HP still opens きりふだ past cards_checked=3 (up to 6).
    assert p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, cur) == [p.pad('a')]
    assert cur['survival']['cards_checked'] == 5

    two = cards(['イッテツーン'])
    assert p.card_list_step(two, mem) == []
    assert p.card_list_step(two, mem) == [p.pad('a')]
    assert cur['cards_spend_attempts']['イッテツーン'] == 1
