"""Synthetic current panels and real-list gates; no ROM or input delivery claims."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_screen import Battle, Screen
from test_hanjuku_survival import menu


def memory(card='ゼンマイン', *, enemy='クミン', enemy_hp=27, ally='ココット',
           ally_hp=22, soldiers=(0, 6), side='attack'):
    mem = {'chapter': 1, 'tick': 12, 'battle': {
        'enemy': enemy, 'ally': ally, 'enemy_hp': enemy_hp, 'ally_hp': ally_hp,
        'start_enemy_hp': enemy_hp, 'start_ally_hp': ally_hp,
        'side': side, 'step': None, 'castle': None,
        'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
        'card_evidence_version': 1, 'planned_cards': [card],
        'tactics_done': ['test-card'],
        'card_flow': {'card': card, 'stage': 'list', 'tactic_id': 'test-card'}}}
    p._card_battle_reading(panel(mem, soldiers=soldiers), mem['battle'])
    return mem


def panel(mem, *, soldiers=(0, 6)):
    cur = mem['battle']
    return Screen([], None, '', kind='battle', battle=Battle(
        cur['enemy'], cur['enemy_hp'], cur['ally'], cur['ally_hp']),
        field_soldiers=soldiers)


def cards(names, selected=0):
    return menu(tuple(names), selected=selected, kind='text')


def assert_not_spent(mem):
    cur = mem['battle']
    assert cur['cards_selected'] == cur['cards_used'] == cur['cards_unclassified'] == []
    assert not mem.get('kit_spent')


def test_zenmain_soldier_absorption_blocks_final_a_without_consuming():
    mem = memory()
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert_not_spent(mem)
    assessment = mem['battle']['card_assessment']
    assert assessment['raw_damage_min'] == 32
    assert assessment['enemy_soldier_hp_upper'] == 60
    assert assessment['damage_lower_bound'] == 0
    assert assessment['remaining_hp_upper'] == 27
    assert assessment['lethal'] is assessment['allowed'] is False
    assert assessment['reason'] == 'nonlethal_egg_risk'
    assert mem['last_card_assessment'] == {**assessment, 'tick': 12}
    assert mem['battle']['card_flow'] is None
    assert mem['_records'][0]['resulting_event'] == 'card_not_selected'


@pytest.mark.parametrize('hp,enemy_soldiers,allowed,damage', [
    (32, 0, True, 32), (33, 0, False, 32),
    (22, 1, True, 22), (23, 1, False, 22),
])
def test_final_selection_uses_soldier_absorption_and_exact_current_hp_boundary(
        hp, enemy_soldiers, allowed, damage):
    mem = memory(enemy='ソーピニヨン', enemy_hp=hp, soldiers=(0, enemy_soldiers))
    expected = [p.pad('a' if allowed else 'b')]
    assert p.card_list_step(cards(['ゼンマイン']), mem) == expected
    current = mem['battle']['card_assessment']
    assert current['damage_lower_bound'] == damage
    assert current['allowed'] is allowed
    assert current['lethal'] is allowed
    assert current['enemy_hp'] == hp


def test_unknown_counts_do_not_borrow_previous_hotloaded_counts():
    mem = memory(soldiers=(6, 0))
    mem['battle'].pop('card_soldiers_current')
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    observed = mem['battle']['card_assessment']
    assert observed['ally_soldiers'] is observed['enemy_soldiers'] is None
    assert observed['enemy_soldier_hp_upper'] == 60
    assert_not_spent(mem)


@pytest.mark.parametrize('missing', [None, (None, None), (0, None)])
def test_latest_unread_count_cannot_reuse_an_old_zero_enemy_soldier_count(missing):
    mem = memory(soldiers=(0, 0))
    p._card_battle_reading(panel(mem, soldiers=missing), mem['battle'])
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert mem['battle']['card_assessment']['enemy_soldiers'] is None
    assert mem['battle']['card_assessment']['enemy_soldier_hp_upper'] == 60


def test_incomplete_battle_hp_cannot_authorize_from_a_previous_one_hp_reading():
    mem = memory(enemy_hp=1, soldiers=(0, 0))
    partial = panel(mem, soldiers=(0, 0))
    partial.battle.enemy_hp = None
    assert p.battle_step(partial, mem) == []
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert mem['battle']['card_assessment']['enemy_hp'] is None
    assert mem['battle']['card_assessment']['lethal'] is None


def test_a_live_panel_on_the_card_menu_replaces_the_old_damage_inputs():
    mem = memory(enemy_hp=1, soldiers=(0, 0))
    screen = cards(['ゼンマイン'])
    screen.battle = Battle('クミン', 27, 'ココット', 22)
    screen.field_soldiers = (0, 6)
    assert p.card_list_step(screen, mem) == [p.pad('b')]
    assert mem['battle']['card_assessment']['enemy_hp'] == 27
    assert mem['battle']['card_assessment']['damage_lower_bound'] == 0


@pytest.mark.parametrize('enemy,side,allowed,role', [
    ('ミント', 'attack', True, 'no_autonomous_egg_trigger'),
    ('ミモザ', 'attack', True, 'no_autonomous_egg_trigger'),
    ('ミモザ', 'defense', False, 'nonlethal_egg_risk'),
    ('ミモザ', None, False, 'nonlethal_egg_risk'),
    ('だれか', None, False, 'damage_or_egg_risk_unclassified'),
])
def test_known_eggless_thought_zero_defense_override_and_unknown(enemy, side, allowed, role):
    mem = memory(enemy=enemy, enemy_hp=30, side=side)
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('a' if allowed else 'b')]
    assert mem['battle']['card_assessment']['reason'] == role


def test_normal_egg_drop_does_not_need_to_penetrate_the_soldier_pool():
    mem = memory('イッテツーン')
    assert p.card_list_step(cards(['イッテツーン']), mem) == [p.pad('a')]
    cur = mem['battle']
    assert cur['card_assessment']['damage_lower_bound'] == 0
    assert cur['card_assessment']['lethal'] is False
    assert cur['card_assessment']['reason'] == 'egg_drop_candidate_not_a_kill'
    assert cur['cards_used'] == []
    assert cur['egg_drop_watch']['evidence_version'] == 2
    cur['enemy_hp'] = 26
    p._observe_egg_drop_candidate(mem, cur)
    assert cur['enemy_egg_drop_expected'] is True
    assert not cur.get('enemy_egg_dropped')


@pytest.mark.parametrize('enemy', ['クイーン', 'にせヒーロー', 'プリンス', 'せいめいたい'])
def test_general_boss_never_uses_normal_general_egg_drop_as_permission(enemy):
    mem = memory('イッテツーン', enemy=enemy, enemy_hp=70, ally='どうし', ally_hp=90)
    assert p._egg_drop_evidence(mem['battle'], 'イッテツーン') is None
    assert p.card_list_step(cards(['イッテツーン']), mem) == [p.pad('b')]
    assert mem['battle']['card_assessment']['target_kind'] == 'boss_general'
    assert mem['battle']['card_assessment']['egg_drop_fit'] is None


def test_control_chain_requires_a_visible_followup_not_a_chart_inventory():
    mem = memory('クースカン', enemy='プリンス', enemy_hp=100, ally='どうし', ally_hp=90)
    mem['battle']['planned_cards'] = ['クースカン', 'クースカン', 'ゼンマイン']
    assert p.card_list_step(cards(['クースカン']), mem) == [p.pad('b')]
    assert mem['battle']['card_assessment']['reason'] == 'control_has_no_observed_followup'
    assert_not_spent(mem)


def test_visible_control_chain_starts_without_claiming_a_kill_or_halved_soldier_hp():
    mem = memory('クースカン', enemy='プリンス', enemy_hp=100, ally='どうし', ally_hp=90)
    assert p.card_list_step(cards(['クースカン', 'クースカン', 'ゼンマイン']), mem) == [p.pad('a')]
    cur = mem['battle']
    assert cur['card_assessment']['reason'] == 'observed_control_chain_not_a_kill'
    assert cur['card_assessment']['remaining_hp_upper'] == 50
    assert cur['card_assessment']['enemy_soldier_hp_upper'] == 600
    assert cur['cards_selected'] == ['クースカン']
    assert cur['cards_used'] == []


def test_two_half_card_selections_do_not_authorize_zenmain_against_a_live_prince_soldier():
    mem = memory(enemy='プリンス', enemy_hp=25, ally='どうし', ally_hp=90, soldiers=(6, 1))
    mem['battle'].update(cards_selected=['クースカン', 'クースカン'],
                        cards_unclassified=['クースカン', 'クースカン'])
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    current = mem['battle']['card_assessment']
    assert current['enemy_soldier_hp_upper'] == 100
    assert current['damage_lower_bound'] == 0
    assert current['remaining_hp_upper'] == 25
    assert mem['battle']['cards_selected'] == ['クースカン', 'クースカン']


def test_extra_attack_card_is_an_alternative_not_permission_to_use_weak_attack():
    mem = memory()
    screen = cards(['ゼンマイン', 'キャトルミュー'])
    assert p.card_list_step(screen, mem) == [p.pad('down')]
    assert mem['battle']['cards_selected'] == []
    assert mem['battle']['card_flow']['card'] == 'キャトルミュー'
    assert p.card_list_step(cards(['ゼンマイン', 'キャトルミュー'], 1), mem) == [p.pad('a')]
    assert mem['battle']['cards_selected'] == ['キャトルミュー']
    assert mem['battle']['card_assessment']['reason'] == 'single_card_lethal'


@pytest.mark.parametrize('survival', [False, True])
def test_lethal_card_beats_nonlethal_egg_drop_in_both_live_selection_routes(survival):
    mem = memory('ブラッキー', enemy_hp=27, soldiers=(0, 0))
    cur = mem['battle']
    if survival:
        cur['planned_cards'] = []
        cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
        p._survival_state(mem, cur)
    names = ['ブラッキー', 'イッテツーン', 'ゼンマイン']
    assert p.card_list_step(cards(names), mem) == [p.pad('down')]
    assert p.card_list_step(cards(names, 1), mem) == [p.pad('down')]
    assert p.card_list_step(cards(names, 2), mem) == [p.pad('a')]
    assert cur['cards_selected'] == ['ゼンマイン']
    assert cur['card_assessment']['reason'] == 'single_card_lethal'


@pytest.mark.parametrize('hp,choice', [(22, 'エンジェリン'), (24, 'ゼンマイン')])
def test_health_need_decides_whether_healing_precedes_a_lethal_card(hp, choice):
    mem = memory('ブラッキー', ally_hp=hp, enemy_hp=27, soldiers=(0, 0))
    names = ['ブラッキー', 'エンジェリン', 'ゼンマイン']
    p.card_list_step(cards(names), mem)
    assert mem['battle']['card_flow']['card'] == choice


def test_control_without_attack_follows_a_visible_heal_instead():
    mem = memory('クースカン')
    assert p.card_list_step(cards(['クースカン', 'エンジェリン']), mem) == [p.pad('down')]
    assert p.card_list_step(cards(['クースカン', 'エンジェリン'], 1), mem) == [p.pad('a')]
    assert mem['battle']['cards_selected'] == ['エンジェリン']
    assert mem['battle']['card_assessment']['reason'] == 'healing_not_a_kill'


def test_safe_replan_never_adds_a_sacrificial_card_to_survival_choices():
    mem = memory()
    assert p.card_list_step(cards(['ゼンマイン', 'ファバード']), mem) == [p.pad('b')]
    assert_not_spent(mem)


def test_old_flow_permission_and_inferred_drop_flags_cannot_bypass_the_gate():
    mem = memory()
    mem['battle']['card_flow'].update(damage_allowed=True, lethal=True)
    mem['battle'].update(enemy_egg_dropped=True, enemy_egg_drop_expected=True)
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert_not_spent(mem)


@pytest.mark.parametrize('scope', ['outer', 'battle', 'both'])
def test_old_summon_flag_is_invalidated_by_current_complete_melee_panel(scope):
    mem = memory()
    if scope in ('outer', 'both'):
        mem['egg_battle'] = True
    if scope in ('battle', 'both'):
        mem['battle']['egg_battle'] = True
    screen = cards(['ゼンマイン'])
    screen.battle = Battle('クミン', 27, 'ココット', 22)
    screen.field_soldiers = (0, 6)
    assert p.card_list_step(screen, mem) == [p.pad('b')]
    assert mem['battle']['card_assessment']['target_kind'] == 'general'
    assert not mem['egg_battle']
    assert not mem['battle']['egg_battle']
    assert_not_spent(mem)


def test_old_summon_flag_without_a_current_summon_observation_cannot_authorize_a():
    mem = memory()
    mem['egg_battle'] = True
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert mem['battle']['card_assessment']['target_kind'] == 'unknown'
    assert mem['battle']['card_assessment']['reason'] == 'damage_or_egg_risk_unclassified'


@pytest.mark.parametrize('enemy,target', [('クミン', 'egg_monster'), ('クイーン', 'boss_monster'),
                                        ('ハードロボ', 'boss_monster')])
def test_measured_summon_is_separate_from_ordinary_enemy_hp_and_damage_gate(enemy, target):
    mem = memory(enemy=enemy)
    mem['battle']['card_summon_observed'] = True
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('a')]
    assessment = mem['battle']['card_assessment']
    assert assessment['target_kind'] == target
    assert assessment['enemy_hp'] is assessment['lethal'] is None
    assert assessment['reason'] == 'summon_already_observed'


@pytest.mark.parametrize('old_enemy,card', [('クミン', 'ゼンマイン'), ('ミント', 'ゼンマイン'),
                                         ('クミン', 'イッテツーン')])
@pytest.mark.parametrize('during_list', [False, True])
def test_different_combatant_panel_cannot_borrow_old_hp_or_egg_permissions(old_enemy, card, during_list):
    mem = memory(card, enemy=old_enemy, enemy_hp=1, soldiers=(0, 0))
    changed = Battle('ソーピニヨン', 40, 'ココット', 22)
    screen = cards([card])
    if during_list:
        screen.battle = changed
        screen.field_soldiers = (0, 6)
    else:
        assert p.battle_step(Screen([], None, '', kind='battle', battle=changed,
                                   field_soldiers=(0, 6)), mem) == []
    assert p.card_list_step(screen, mem) == [p.pad('b')]
    assert mem['battle']['card_assessment']['target_kind'] == 'unknown'
    assert mem['battle']['card_assessment']['allowed'] is False
    assert_not_spent(mem)


def test_an_old_already_selected_announcement_is_not_reclassified_as_never_used():
    mem = memory()
    cur = mem['battle']
    cur['cards_selected'] = ['ゼンマイン']
    cur['card_flow'].update(stage='announce', selection_planned=True)
    for _ in range(p.CARD_ANNOUNCE_LIMIT):
        assert p.card_list_step(cards(['ゼンマイン']), mem) == []
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert cur['cards_selected'] == cur['cards_unclassified'] == ['ゼンマイン']
    assert cur['cards_used'] == []


def test_survival_list_uses_the_same_gate_and_then_a_real_egg():
    mem = memory()
    cur = mem['battle']
    cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
    p._survival_state(mem, cur)
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert_not_spent(mem)
    assert p.battle_menu_step(menu(selected=0), mem) == [p.pad('a')]
    assert cur['survival']['egg_attempted'] is True
    assert not cur.get('okunote_only_observed')


def test_menu_hp_observation_keeps_the_defender_delay_margin():
    mem = memory(enemy='ソーピニヨン', enemy_hp=48, side='defense')
    screen = cards(['ゼンマイン'])
    screen.battle = Battle('ソーピニヨン', 37, 'ココット', 22)
    screen.field_soldiers = (0, 6)
    p.card_list_step(screen, mem)
    assert mem['battle']['defender_enemy_hp_drop'] == 11


def test_refusal_has_finite_defense_fallback_and_does_not_reopen_same_tactic(monkeypatch):
    tactic = {'card': 'ゼンマイン', 'enemy': 'クミン', 'open': True, 'note': 'test'}
    monkeypatch.setattr(p, '_tactics', lambda *args: [tactic])
    mem = memory(side='defense')
    cur = mem['battle']
    cur.update(card_flow=None, tactics_done=[])
    assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    assert cur['card_assessment']['reason'] == 'inspect_live_inventory_before_selection'
    assert p.battle_menu_step(menu(('きりふだ',)), mem) == [p.pad('a')]
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('きりふだ',)), mem) == [p.pad('b')]
    assert cur['survival']['exhausted'] is True
    assert cur.get('okunote_only_observed') is False
    results = [p.battle_step(panel(mem), mem) for _ in range(p.MELEE_HOLD_LIMIT + 2)]
    assert all(result != [p.pad('b')] for result in results)
    assert all(result == [p.pad('a', 3), {'type': 'wait', 'ms': 50}]
               for result in results[:p.MELEE_HOLD_LIMIT])
    assert results[-1] == p._power_mash(mem, cur)
    assert len([r for r in mem['_records'] if r['decision'] == 'battle_card']) == 1
    assert_not_spent(mem)


def test_refused_chart_does_not_retreat_while_healthy_but_does_at_probe_budget():
    mem = memory()
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('きりふだ',)), mem) == [p.pad('b')]
    for _ in range(p.MELEE_HOLD_LIMIT):
        assert p.battle_step(panel(mem), mem) == [p.pad('a', 3), {'type': 'wait', 'ms': 50}]
    assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(selected=2), mem) == [p.pad('a')]
    assert mem['battle']['hero_retreat']['selected'] == 1


@pytest.mark.parametrize('survival', [False, True])
@pytest.mark.parametrize('enemy,hp,remaining_hp,card,soldiers', [
    ('ソーピニヨン', 33, 33, 'ミックミー', 6),
    ('クイーン', 35, 30, 'ノリウツール', 4),
    ('にせヒーロー', 45, 16, 'イッテツーン', 4),
    ('プリンス', 25, 25, 'ゼンマイン', 1),
])
def test_healthy_post_control_observation_can_reenable_a_now_lethal_card(
        monkeypatch, survival, enemy, hp, remaining_hp, card, soldiers):
    tactic = {'card': card, 'enemy': enemy, 'open': True, 'note': 'post-control'}
    monkeypatch.setattr(p, '_tactics', lambda *args: [] if survival else [tactic])
    mem = memory(card, enemy=enemy, enemy_hp=hp, ally='どうし', ally_hp=90,
                 soldiers=(0, soldiers))
    cur = mem['battle']
    cur.update(cards_selected=['クースカン'], cards_unclassified=['クースカン'])
    if survival:
        cur['planned_cards'] = []
        cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
        p._survival_state(mem, cur)
    assert p.card_list_step(cards([card]), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('きりふだ', 'たいきゃく')), mem) == [p.pad('b')]
    assert not p._hero_retreat_needed(cur)
    assert p.battle_step(panel(mem, soldiers=(0, soldiers)), mem) == [
        p.pad('a', 3), {'type': 'wait', 'ms': 50}]
    # The next *actual* panel shows no remaining enemy soldier.  No card
    # selection count supplied the lower soldier HP or this zero count.
    cur['enemy_hp'] = remaining_hp
    assert p.battle_step(panel(mem, soldiers=(0, 0)), mem) == [p.pad('b')]
    assert cur['card_damage_probe_ticks'] == 1
    assert p.battle_menu_step(menu(('きりふだ', 'たいきゃく')), mem) == [p.pad('a')]
    assert p.card_list_step(cards([card]), mem) == [p.pad('a')]
    assert cur['cards_selected'] == ['クースカン', card]
    assert cur['card_assessment']['lethal'] is True
    assert cur['cards_used'] == []


def test_hp_budget_does_not_reset_when_refusal_signature_changes(monkeypatch):
    monkeypatch.setattr(p, '_tactics', lambda *args: [])
    mem = memory(ally='どうし', ally_hp=90)
    cur = mem['battle']
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('きりふだ',)), mem) == [p.pad('b')]
    assert p.battle_step(panel(mem), mem)[0] == p.pad('a', 3)
    cur['ally_hp'] = 81
    assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    assert cur['hero_retreat']['opens'] == 1
    assert cur['card_damage_probe_ticks'] == 1


@pytest.mark.parametrize('survival', [False, True])
def test_budget_boundary_allows_one_live_inventory_check_for_a_newly_lethal_card(monkeypatch, survival):
    tactic = {'card': 'ゼンマイン', 'enemy': 'クミン', 'open': True, 'note': 'test'}
    monkeypatch.setattr(p, '_tactics', lambda *args: [] if survival else [tactic])
    mem = memory(ally='どうし', ally_hp=90, soldiers=(0, 1))
    cur = mem['battle']
    if survival:
        cur['planned_cards'] = []
        cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
        p._survival_state(mem, cur)
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('きりふだ', 'たいきゃく')), mem) == [p.pad('b')]
    cur['card_damage_probe_ticks'] = p.MELEE_HOLD_LIMIT
    assert p.battle_step(panel(mem, soldiers=(0, 0)), mem) == [p.pad('b')]
    assert not cur.get('hero_retreat')
    assert cur['card_damage_final_recheck'] is True
    assert cur['card_damage_probe_ticks'] == p.MELEE_HOLD_LIMIT
    assert p.battle_menu_step(menu(('きりふだ', 'たいきゃく')), mem) == [p.pad('a')]
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('a')]
    assert cur['card_assessment']['lethal'] is True


def test_boundary_recheck_does_not_grant_a_second_probe_budget_after_another_refusal(monkeypatch):
    monkeypatch.setattr(p, '_tactics', lambda *args: [])
    mem = memory(ally='どうし', ally_hp=90, enemy='ソーピニヨン', enemy_hp=27,
                 soldiers=(0, 1))
    cur = mem['battle']
    p.card_list_step(cards(['ゼンマイン']), mem)
    p.battle_menu_step(menu(('きりふだ', 'たいきゃく')), mem)
    cur['card_damage_probe_ticks'] = p.MELEE_HOLD_LIMIT
    assert p.battle_step(panel(mem, soldiers=(0, 0)), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('きりふだ', 'たいきゃく')), mem) == [p.pad('a')]
    # The final current panel invalidates the optimistic earlier boundary
    # reading; closing it must not grant another eight short melee actions.
    screen = cards(['ゼンマイン'])
    screen.battle = Battle('ソーピニヨン', 33, 'どうし', 90)
    screen.field_soldiers = (0, 0)
    assert p.card_list_step(screen, mem) == [p.pad('b')]
    p.battle_menu_step(menu(('きりふだ', 'たいきゃく')), mem)
    assert p.battle_step(panel(mem, soldiers=(0, 0)), mem) == [p.pad('b')]
    assert cur['hero_retreat']['opens'] == 1
    assert cur['card_damage_probe_ticks'] == p.MELEE_HOLD_LIMIT


@pytest.mark.parametrize('survival', [False, True])
@pytest.mark.parametrize('side', ['attack', 'defense'])
def test_soldier_readability_oscillation_cannot_reopen_cards_forever(monkeypatch, survival, side):
    # Real 3-B1: both half-card intentions are already unclassified.  A
    # surviving Prince soldier is still HP100, whether the next frame can
    # count that soldier or temporarily cannot read either count.
    if survival:
        monkeypatch.setattr(p, '_tactics', lambda *args: [])
    mem = memory(enemy='プリンス', enemy_hp=25, ally='どうし', ally_hp=90,
                 soldiers=(6, 1), side=side)
    mem['chapter'] = 3
    cur = mem['battle']
    cur.update(step='3-B1', cards_selected=['クースカン', 'クースカン'],
               cards_unclassified=['クースカン', 'クースカン'],
               tactics_done=['c2:クースカン', 'c3:クースカン'])
    if survival:
        cur['planned_cards'] = []
        cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
        p._survival_state(mem, cur)
    command = menu(('きりふだ', 'たいきゃく') if side == 'attack' else ('きりふだ',))
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert p.battle_menu_step(command, mem) == [p.pad('b')]
    openings = 0
    for index in range(20):
        current_counts = (None, None) if index % 2 == 0 else (6, 1)
        actions = p.battle_step(panel(mem, soldiers=current_counts), mem)
        if cur.get('card_flow') or (actions == [p.pad('b')] and not cur.get('hero_retreat')):
            openings += 1
            assert p.battle_menu_step(command, mem) == [p.pad('a')]
            assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
            assert p.battle_menu_step(command, mem) == [p.pad('b')]
        elif actions == [p.pad('b')]:
            # The escape was requested; repeated missing transition frames
            # may exhaust its existing bounded menu probe, not reopen cards.
            assert cur.get('hero_retreat')
    assert openings == p.MELEE_HOLD_LIMIT
    assert cur['card_damage_rechecks'] == p.MELEE_HOLD_LIMIT
    assert cur.get('card_damage_probe_ticks', 0) == 0
    assert not cur.get('card_damage_final_recheck')
    assert cur['cards_selected'] == ['クースカン', 'クースカン']
    assert cur['cards_used'] == []
    if side == 'attack':
        assert cur['hero_retreat']['opens'] == 3
    else:
        assert cur['melee_forced'] is True
        assert not cur.get('okunote_egg_preempt')


@pytest.mark.parametrize('survival', [False, True])
def test_readability_budget_still_allows_one_newly_lethal_final_check(monkeypatch, survival):
    tactic = {'card': 'ゼンマイン', 'enemy': 'プリンス', 'open': True, 'note': 'test'}
    monkeypatch.setattr(p, '_tactics', lambda *args: [] if survival else [tactic])
    mem = memory(enemy='プリンス', enemy_hp=25, ally='どうし', ally_hp=90, soldiers=(6, 1))
    cur = mem['battle']
    if survival:
        cur['planned_cards'] = []
        cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
        p._survival_state(mem, cur)
    command = menu(('きりふだ', 'たいきゃく'))
    p.card_list_step(cards(['ゼンマイン']), mem)
    p.battle_menu_step(command, mem)
    for index in range(p.MELEE_HOLD_LIMIT):
        count = (None, None) if index % 2 == 0 else (6, 1)
        assert p.battle_step(panel(mem, soldiers=count), mem) == [p.pad('b')]
        assert p.battle_menu_step(command, mem) == [p.pad('a')]
        assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
        assert p.battle_menu_step(command, mem) == [p.pad('b')]
    assert cur['card_damage_rechecks'] == p.MELEE_HOLD_LIMIT
    assert p.battle_step(panel(mem, soldiers=(6, 0)), mem) == [p.pad('b')]
    assert not cur.get('hero_retreat')
    assert cur['card_damage_final_recheck'] is True
    assert p.battle_menu_step(command, mem) == [p.pad('a')]
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('a')]
    assert cur['card_assessment']['lethal'] is True
    assert cur['card_damage_rechecks'] == p.MELEE_HOLD_LIMIT
    assert cur.get('card_damage_probe_ticks', 0) == 0


def test_a_selected_control_alternative_can_resume_the_original_card_after_real_progress(monkeypatch):
    tactic = {'card': 'ゼンマイン', 'enemy': 'プリンス', 'open': True, 'note': 'test'}
    monkeypatch.setattr(p, '_tactics', lambda *args: [tactic])
    mem = memory(enemy='プリンス', enemy_hp=100, ally='どうし', ally_hp=90)
    cur = mem['battle']
    assert p.card_list_step(cards(['ゼンマイン', 'クースカン']), mem) == [p.pad('down')]
    assert p.card_list_step(cards(['ゼンマイン', 'クースカン'], 1), mem) == [p.pad('a')]
    p._card_use_unclassified(mem, cur, 'test receipt unavailable')
    assert not cur['card_damage_rescue_needed']
    cur['enemy_hp'] = 25
    assert p.battle_step(panel(mem, soldiers=(6, 0)), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('きりふだ', 'たいきゃく')), mem) == [p.pad('a')]
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('a')]
    assert cur['cards_selected'] == ['クースカン', 'ゼンマイン']
    assert cur['cards_used'] == []


def test_mixed_inventory_rechecks_and_melee_probes_are_counted_exactly_once(monkeypatch):
    tactic = {'card': 'ゼンマイン', 'enemy': 'プリンス', 'open': True, 'note': 'test'}
    monkeypatch.setattr(p, '_tactics', lambda *args: [tactic])
    mem = memory(enemy='プリンス', enemy_hp=25, ally='どうし', ally_hp=90, soldiers=(6, 1))
    cur = mem['battle']
    command = menu(('きりふだ', 'たいきゃく'))
    p.card_list_step(cards(['ゼンマイン']), mem)
    p.battle_menu_step(command, mem)
    for count in ((None, None), (6, 1), (None, None)):
        assert p.battle_step(panel(mem, soldiers=count), mem) == [p.pad('b')]
        assert p.battle_menu_step(command, mem) == [p.pad('a')]
        assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
        assert p.battle_menu_step(command, mem) == [p.pad('b')]
    for probe_count in range(1, p.MELEE_HOLD_LIMIT - 3 + 1):
        assert p.battle_step(panel(mem, soldiers=(None, None)), mem) == [
            p.pad('a', 3), {'type': 'wait', 'ms': 50}]
        assert cur['card_damage_probe_ticks'] == probe_count
        assert cur['card_damage_rechecks'] == 3
        assert cur['melee_holds'] == 0
        assert not cur.get('hero_retreat')
    assert p.battle_step(panel(mem, soldiers=(None, None)), mem) == [p.pad('b')]
    assert cur['hero_retreat']['opens'] == 1


@pytest.mark.parametrize('card,enemy', [('ゼンマイン', 'ミント'), ('イッテツーン', 'クミン'),
                                     ('エンジェリン', 'クミン')])
@pytest.mark.parametrize('dead', ['ally_hp', 'enemy_hp'])
def test_zero_hp_panel_cannot_authorize_a_card_through_non_damage_permission(card, enemy, dead):
    mem = memory(card, enemy=enemy)
    mem['battle'][dead] = 0
    assert p.card_list_step(cards([card]), mem) == [p.pad('b')]
    assert_not_spent(mem)


def test_selected_or_unclassified_copy_is_not_automatically_reselected_as_alternative():
    mem = memory()
    mem['battle'].update(cards_selected=['キャトルミュー'], cards_unclassified=['キャトルミュー'])
    assert p.card_list_step(cards(['ゼンマイン', 'キャトルミュー']), mem) == [p.pad('b')]
    assert mem['battle']['cards_selected'] == ['キャトルミュー']


def test_current_assessment_is_bounded_and_history_is_independent_of_battle_lifetime():
    mem = memory()
    p.card_list_step(cards(['ゼンマイン']), mem)
    assessment = deepcopy(mem['last_card_assessment'])
    mem.pop('battle')
    assert mem['last_card_assessment'] == assessment
    assert set(assessment) == {'card', 'enemy_hp', 'ally_soldiers', 'enemy_soldiers',
                               'target_kind', 'raw_damage_min', 'enemy_soldier_hp_upper',
                               'damage_lower_bound', 'remaining_hp_upper', 'lethal',
                               'egg_drop_fit', 'allowed', 'reason', 'tick'}
