"""Unclassified card selections are not proof that no rescue remains (g839).

g839 step 4214-4272: アマンディーヌ 59/59 vs デュオニソス 40 selected
イッテツーン, フットバース, イッテツーン; every announcement stayed
unclassified, the three-check budget ran out while きりふだ was still on the
menu, and "no observed rescue remains" retreated a full-HP general. The fix
reads the real list once more. Its existing rules decide: a usable card is
used, only unconfirmed copies keep ``cards_uncertain``, and an empty list
still retreats (the g486/g684 protection is unchanged).
"""
from docich import hanjuku_policy as p
from test_hanjuku_survival import menu
from test_hanjuku_card_damage_gate import cards, panel

PICKS = ['イッテツーン', 'フットバース', 'イッテツーン']


def g839(unclassified=PICKS, enemy_hp=40):
    mem = {'chapter': 2, 'tick': 40, 'battle': {
        'enemy': 'デュオニソス', 'ally': 'アマンディーヌ', 'enemy_hp': enemy_hp, 'ally_hp': 59,
        'start_enemy_hp': 54, 'start_ally_hp': 59, 'side': 'attack', 'step': None,
        'castle': 'ドミノーラ', 'cards_used': [], 'cards_selected': list(PICKS),
        'cards_unclassified': list(unclassified), 'card_evidence_version': 1,
        'planned_cards': [], 'card_flow': None,
        'survival': {'opens': 3, 'menu_ticks': 3, 'cards_checked': 3,
                     'cards_attempted': list(PICKS), 'egg_attempted': False,
                     'card_counts_at_selection': {'イッテツーン': 1, 'フットバース': 1}}}}
    p._card_battle_reading(panel(mem, soldiers=(None, None)), mem['battle'])
    return mem


def rescue(mem):
    return mem['battle']['survival']


def test_budget_end_after_unclassified_picks_reads_the_real_list_once_more():
    mem = g839()
    assert p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, mem['battle']) == [p.pad('a')]
    assert not rescue(mem).get('exhausted')
    assert rescue(mem)['final_card_read'] is True
    assert mem['battle']['card_flow']['stage'] == 'list'


def test_only_unconfirmed_copies_on_the_list_keep_the_general_fighting():
    mem = g839()
    p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, mem['battle'])
    for _ in range(3):
        actions = p.card_list_step(cards(['イッテツーン']), mem)
    assert actions == [p.pad('b')]
    assert rescue(mem)['cards_uncertain'] is True
    p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, mem['battle'])
    assert rescue(mem)['exhausted'] is True
    assert p._default_retreat_needed(mem['battle']) is False


def test_unconfirmed_copies_also_hold_outside_the_owner_hp_lead():
    mem = g839(enemy_hp=45)
    p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, mem['battle'])
    for _ in range(3):
        p.card_list_step(cards(['イッテツーン']), mem)
    p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, mem['battle'])
    assert rescue(mem)['cards_uncertain'] is True
    assert p._default_retreat_needed(mem['battle']) is False


def test_an_empty_list_still_retreats_before_the_summon():
    # 45 is outside the owner's pre-summon HP lead (enemy <= 70% of ours),
    # so only the resource reading decides here.
    mem = g839(enemy_hp=45)
    p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, mem['battle'])
    for _ in range(5):
        p.card_list_step(cards([]), mem)
    assert rescue(mem)['cards_exhausted'] is True and not rescue(mem)['cards_uncertain']
    p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, mem['battle'])
    assert rescue(mem)['exhausted'] is True
    assert p._default_retreat_needed(mem['battle']) is True


def test_the_final_read_happens_once_per_battle():
    mem = g839()
    p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, mem['battle'])
    mem['battle']['card_flow'] = None
    rescue(mem)['cards_checked'] = 4
    assert p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, mem['battle']) == [p.pad('b')]
    assert rescue(mem)['exhausted'] is True


def test_a_classified_last_pick_keeps_the_old_budget():
    mem = g839(unclassified=[])
    assert p._survival_menu(menu(('きりふだ', 'たいきゃく')), mem, mem['battle']) == [p.pad('b')]
    assert rescue(mem)['exhausted'] is True
    assert not rescue(mem).get('final_card_read')


def test_no_card_row_is_not_reopened():
    mem = g839()
    assert p._survival_menu(menu(('たいきゃく',)), mem, mem['battle']) == [p.pad('b')]
    assert rescue(mem)['exhausted'] is True
