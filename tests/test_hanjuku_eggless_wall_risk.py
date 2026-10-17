"""g684 eggless Trevis: wall summons need resources before healthy melee.

Native panel proves names/HP/armies; menu/list sequences are synthetic glyphs.
Neither opening nor selection proves card use, enemy egg loss, or victory.
"""
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_survival import memory, menu, battle
from test_hanjuku_card_damage_gate import cards, panel

FIXTURE = Path(__file__).parent / 'fixtures/hanjuku/g684-trevis-alfalfa-panel.png'


def trevis():
    mem = memory(66, 38)
    mem['battle'].update(ally='トレビス', enemy='アルファルファ', start_ally_hp=66,
                        start_enemy_hp=38, side='attack', castle='ゴーメン',
                        planned_cards=[], cards_selected=[], cards_unclassified=[])
    return mem


def test_native_eggless_general_checks_resources_before_first_wall_damage():
    frame = read_png(FIXTURE)
    assert frame.digest() == '26e8f7a51f8ec0c24cda67e8a4a96965ba0ff20c885b11fd2da4abc2b4018ded'
    screen = parse(frame)
    assert screen.field_soldiers == (6, 6)
    actions, state = decide(frame, {'policy': trevis()})
    assert actions == [p.pad('b')]
    cur = state['policy']['battle']
    assert cur['survival']['opens'] == 1
    assert cur['cards_used'] == [] and not cur.get('egg_battle')


def test_real_card_list_licenses_control_then_bounded_melee_without_fake_receipts():
    frame = read_png(FIXTURE)
    actions, state = decide(frame, {'policy': trevis()})
    assert actions == [p.pad('b')]
    mem = json.loads(json.dumps(state['policy']))
    assert p.battle_menu_step(menu(('きりふだ', 'たいきゃく')), mem) == [p.pad('a')]
    assert p.card_list_step(cards(['クースカン']), mem) == [p.pad('a')]
    cur = mem['battle']
    assert cur['cards_selected'] == ['クースカン'] and cur['cards_used'] == []
    assert cur['card_assessment']['reason'] == 'control_then_melee_not_a_kill'
    cur['enemy_hp'] = 19  # a subsequent synthetic complete panel observes it
    assert p.battle_step(panel(mem, soldiers=(6, 6)), mem) == p._power_mash(mem, cur)
    assert cur['card_melee_followup']['bursts'] == 1
    assert not cur.get('enemy_egg_dropped') and cur['cards_used'] == []


def test_confirmed_no_resources_keeps_clear_hp_lead_before_the_wall_summon():
    mem = trevis()
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('たいきゃく',)), mem) == [p.pad('b')]
    assert mem['battle']['survival']['exhausted']
    assert p.battle_step(battle(mem), mem) == p._power_mash(mem, mem['battle'])
    assert not p._hero_retreat_needed(mem['battle'])
    assert p._hero_retreat_menu(menu(('たいきゃく',)), mem, mem['battle']) is None
    assert not mem['battle'].get('hero_retreat')
    assert not mem['battle'].get('defeat_owner')


@pytest.mark.parametrize('own', ['トレビス', 'リースリング', 'マカデミア'])
def test_intrinsically_eggless_generals_share_the_wall_risk_rule(own):
    mem = trevis(); mem['battle']['ally'] = own
    assert p._unarmed_wall_risk(mem['battle'])


def test_observed_zero_uses_applies_to_egg_capable_general_and_is_rechecked():
    mem = trevis(); mem['battle'].update(ally='ヴィーナス', ally_hp=82, start_ally_hp=82)
    mem['egg_uses'] = {'ヴィーナス': 0}
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert mem['battle']['own_egg_zero_observed'] is True
    assert p._unarmed_wall_risk(mem['battle'])
    mem['egg_uses']['ヴィーナス'] = 4  # only a separate observed count can do this
    p.battle_menu_step(menu(), mem)
    assert mem['battle']['own_egg_zero_observed'] is False
    assert not p._unarmed_wall_risk(mem['battle'])


@pytest.mark.parametrize('changes', [
    {'ally': 'ヴィーナス'}, {'ally': 'どうし'}, {'ally': '未読'},
    {'enemy': 'ミント'}, {'enemy': '未読'}, {'enemy': 'クイーン'},
    {'side': None}, {'side': 'defense'}, {'planned_cards': ['イッテツーン']},
    {'egg_battle': True},
])
def test_wall_inspection_requires_known_eggless_attack_and_preserves_chart(changes):
    cur = trevis()['battle']; cur.update(changes)
    assert not p._unarmed_wall_risk(cur)


def test_unread_menu_cursor_never_selects_or_creates_resources():
    mem = trevis(); p.battle_step(battle(mem), mem)
    for _ in range(13):
        actions = p.battle_menu_step(menu(('きりふだ', 'たいきゃく'), selected=None), mem)
        assert actions != [p.pad('a')]
    assert mem['battle']['survival']['exhausted']
    assert mem['battle']['cards_used'] == [] and not mem['battle'].get('hero_retreat', {}).get('selected')


@pytest.mark.parametrize('own,hp', [('トレビス', 66), ('タルタル', 40), ('アマンディーヌ', 59)])
@pytest.mark.parametrize('uses', [1, 4, 5])
def test_acquired_live_egg_uses_override_initially_eggless_general(own, hp, uses):
    mem = trevis(); mem['battle'].update(ally=own, ally_hp=hp, start_ally_hp=hp)
    mem['egg_uses'] = {own: uses}
    actions = p.battle_step(battle(mem), mem)
    assert not p._unarmed_wall_risk(mem['battle'])
    assert actions != [p.pad('b')]
    assert not mem['battle'].get('survival')
    assert mem['egg_uses'][own] == uses


def test_acquired_egg_count_is_rechecked_after_json_and_new_observed_zero():
    mem = trevis(); mem['egg_uses'] = {'トレビス': 1}
    assert p.battle_step(battle(mem), mem) != [p.pad('b')]
    mem = json.loads(json.dumps(mem))
    mem['egg_uses']['トレビス'] = 0
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert p._unarmed_wall_risk(mem['battle'])


@pytest.mark.parametrize('uses', [None, True, '1', 1.0, 6, -1])
def test_other_general_or_invalid_count_cannot_claim_acquired_egg(uses):
    mem = trevis(); mem['egg_uses'] = {'ヴィーナス': 4, 'トレビス': uses}
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert mem['battle']['own_egg_positive_observed'] is False


def test_menu_rechecks_acquired_egg_before_stale_healthy_retreat_guard():
    mem = trevis()
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    mem['battle']['survival'].update(exhausted=True, cards_uncertain=False)
    mem['battle']['own_egg_zero_observed'] = True
    mem['egg_uses'] = {'トレビス': 1}
    p.battle_menu_step(menu(), mem)
    assert mem['battle']['own_egg_positive_observed'] is True
    assert mem['battle']['own_egg_zero_observed'] is False
    assert not p._hero_retreat_needed(mem['battle'])


@pytest.mark.parametrize('fresh', [None, 'other', 1, 0])
def test_selection_recheck_blocks_old_positive_until_named_observation(fresh):
    from test_hanjuku_chart_bot import sortie_canvas
    mem = trevis()
    # Actual observed-count producer, then actual menu selection producer.
    p.observe_events(sortie_canvas('トレビス', 1), mem)
    mem['battle']['ally_hp'] = 25
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('たまごをつかう', 'たいきゃく')), mem) == [p.pad('a')]
    assert mem['egg_recheck'] == ['トレビス']
    assert mem['egg_uses']['トレビス'] == 1  # selection does not prove a use
    p.battle_end(mem, 'map'); p.battle_end(mem, 'map')
    mem = json.loads(json.dumps(mem))
    if fresh == 'other': p.observe_events(sortie_canvas('ゼウス', 4), mem)
    elif type(fresh) is int: p.observe_events(sortie_canvas('トレビス', fresh), mem)
    # A subsequent synthetic complete attack panel; keep its named context,
    # but never manufacture a count decrement or consumption receipt.
    mem['battle'] = trevis()['battle']
    first = p.battle_step(battle(mem), mem)
    mem = json.loads(json.dumps(mem))
    second = p.battle_step(battle(mem), mem)
    if fresh in (None, 'other', 0):
        assert first == [p.pad('b')]
        assert not any(a.get('buttons') == ['a'] for a in second)
        assert mem['battle']['own_egg_positive_observed'] is False
    else:
        assert first != [p.pad('b')]
        assert mem['battle']['own_egg_positive_observed'] is True
    assert mem['egg_uses']['トレビス'] == (fresh if type(fresh) is int else 1)
    assert ('トレビス' in mem.get('egg_recheck', [])) == (fresh in (None, 'other'))


def bait_memory():
    from test_hanjuku_general_egg_recheck import IDENTITY
    from copy import deepcopy
    mem = trevis()
    mem.update(tick=12, _run_identity=deepcopy(IDENTITY), survey_run_identity=deepcopy(IDENTITY))
    mem['egg_uses'] = {'アルテミス': 0}
    mem['battle'].update(ally='アルテミス', enemy='ヘーゼル', ally_hp=49, enemy_hp=11,
                        start_ally_hp=49, start_enemy_hp=33,
                        survival={'exhausted': True, 'cards_uncertain': False})
    return mem


def arm_bait(mem):
    # The pre-summon context is synthetic; the later g684 panel is native.
    assert p.battle_step(battle(mem), mem) != [p.pad('b')]
    assert mem['battle']['enemy_egg_bait']['phase'] == 'waiting'


def summon_panel():
    return parse(read_png(Path(__file__).parent / 'fixtures/hanjuku/g684-artemis-dark-elf.png'))


def test_continue_melee_then_native_enemy_summon_backs_out_and_selects_real_retreat():
    mem = bait_memory(); arm_bait(mem)
    for _ in range(3):
        assert p.battle_step(battle(mem), mem) != [p.pad('b')]
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g684-artemis-dark-elf.png')
    actions, state = decide(frame, {'policy': mem}, run_identity=mem['_run_identity'])
    assert actions == [p.pad('b')]
    mem = state['policy']; cur = mem['battle']
    assert cur['enemy_egg_bait']['phase'] == 'summoned'
    assert cur['enemy_hp'] == 11 and cur['ally_hp'] == 49
    p._card_battle_reading(battle(mem), cur, mem)
    assert not cur['egg_battle'] and not p._hero_retreat_needed(cur)
    assert p._hero_retreat_menu(menu(('こうげき', 'たいきゃく'), selected=0), mem, cur) == [p.pad('down')]
    assert p._hero_retreat_menu(menu(('こうげき', 'たいきゃく'), selected=1), mem, cur) == [p.pad('a')]
    assert cur['hero_retreat']['selected'] == 1
    evidence = next(r for r in state['_records'] if r['decision'] == 'enemy_egg_bait_summon_observed')
    assert evidence['observed_metric']['enemy_remaining_uses'] is None
    assert evidence['observed_metric']['quantity_change'] == 'unclassified'
    assert mem['egg_uses'] == {'アルテミス': 0}


def test_repeated_summon_and_json_restore_never_refill_return_budget_or_proof():
    mem = bait_memory(); arm_bait(mem)
    for n in range(12):
        mem = json.loads(json.dumps(mem)); mem['tick'] += 1
        actions = p.egg_battle_step(summon_panel(), mem)
        if n < 3:
            assert actions == [p.pad('b')]
        else:
            assert actions != [p.pad('b')]
    assert mem['battle']['egg_retreat_attempts'] == 3
    assert mem['battle']['enemy_egg_bait']['observed_tick'] == 13
    records = [r for r in mem['_records'] if r['decision'] == 'enemy_egg_bait_summon_observed']
    assert len(records) == 1


@pytest.mark.parametrize('change', [
    {'egg_uses': {}}, {'egg_uses': {'アルテミス': 1}}, {'egg_uses': {'アルテミス': True}},
    {'_run_identity': None},
])
def test_unknown_or_positive_own_egg_never_arms_bait(change):
    mem = bait_memory(); mem.update(change)
    p.battle_step(battle(mem), mem)
    assert not mem['battle'].get('enemy_egg_bait')


@pytest.mark.parametrize('change', [{'enemy': '未読'}, {'enemy': 'ミント'}, {'side': 'defense'},
                                 {'ally_hp': 12}, {'ally_hp': 15, 'enemy_hp': 14}])
def test_no_clear_advantage_or_enemy_capability_keeps_existing_protection(change):
    mem = bait_memory(); mem['battle'].update(change)
    p.battle_step(battle(mem), mem)
    assert not mem['battle'].get('enemy_egg_bait')


@pytest.mark.parametrize('change', ['hp_danger', 'hp_unread', 'positive_egg'])
def test_waiting_is_cancelled_on_current_danger_unread_hp_or_new_positive_egg(change):
    mem = bait_memory(); arm_bait(mem)
    if change == 'positive_egg': mem['egg_uses']['アルテミス'] = 2
    screen = battle(mem)
    if change == 'hp_danger': screen.battle.ally_hp = 12
    if change == 'hp_unread': screen.battle.ally_hp = None
    p.battle_step(screen, mem)
    assert mem['battle']['enemy_egg_bait']['phase'] == 'cancelled'
    if change == 'hp_danger': assert p._hero_retreat_needed(mem['battle'])


@pytest.mark.parametrize('change', ['flags_only', 'missing_enemy', 'own_summon', 'enemy_general',
                                 'wrong_ally', 'duplicate_enemy', 'unknown_monster', 'hp_bool'])
def test_unconfirmed_foreign_or_own_summons_never_prove_enemy_use(change):
    from copy import deepcopy
    mem = bait_memory(); arm_bait(mem); s = summon_panel()
    if change == 'flags_only':
        mem['egg_battle'] = True; mem['battle']['card_summon_observed'] = True; s.egg_rows = []
    elif change == 'missing_enemy': s.egg_rows = s.egg_rows[:1]
    elif change == 'own_summon': s.egg_rows[0].name = 'ヒュドラ'; s.egg_rows[1].name = 'ヘーゼル'
    elif change == 'enemy_general': s.egg_rows[1].name = 'ヘーゼル'
    elif change == 'wrong_ally': s.egg_rows[0].name = 'ヴィーナス'
    elif change == 'duplicate_enemy': s.egg_rows.append(deepcopy(s.egg_rows[1]))
    elif change == 'unknown_monster': s.egg_rows[1].name = '未確認'
    else: s.egg_rows[1].hp = True
    p.egg_battle_step(s, mem)
    assert mem['battle']['enemy_egg_bait']['phase'] == 'waiting'
    assert not any(r['decision'] == 'enemy_egg_bait_summon_observed' for r in mem['_records'])


@pytest.mark.parametrize('change', ['run', 'chapter', 'actor', 'enemy', 'battle_end'])
def test_old_intention_never_survives_a_different_battle_or_scope(change):
    from docich.hanjuku.judgment import egg_bait
    mem = bait_memory(); arm_bait(mem)
    assert p.egg_battle_step(summon_panel(), mem) == [p.pad('b')]
    if change == 'run': mem['_run_identity']['generation'] += 1
    elif change == 'chapter': mem['chapter'] += 1
    elif change == 'actor': mem['battle']['ally'] = 'ヴィーナス'
    elif change == 'enemy': mem['battle']['enemy'] = 'カシュー'
    else:
        p.battle_end(mem, 'map'); p.battle_end(mem, 'map')
        assert not mem.get('battle')
        return
    assert not egg_bait.retreat_due(mem, mem['battle'])
    assert not mem['battle'].get('enemy_egg_bait')


def test_json_restore_and_repeated_normal_menus_never_refill_retreat_a_budget():
    mem = bait_memory(); arm_bait(mem)
    p.egg_battle_step(summon_panel(), mem)
    total_a = 0
    for _ in range(35):
        mem = json.loads(json.dumps(mem))
        actions = p._hero_retreat_menu(menu(('たいきゃく',)), mem, mem['battle'])
        total_a += sum(action.get('buttons') == ['a'] for action in actions or [])
    assert total_a == 2
    assert mem['battle']['hero_retreat']['selected'] == 2
    assert mem['battle']['hero_retreat']['exhausted'] is True


def test_native_enemy_general_command_is_not_the_opponents_egg_use():
    from test_hanjuku_enemy_general_command import native
    mem = bait_memory(); arm_bait(mem)
    assert p.egg_battle_step(native(17492), mem) == []
    assert mem['battle']['enemy_egg_bait']['phase'] == 'waiting'


@pytest.mark.parametrize('change', ['age', 'future', 'bool_tick', 'bad_monster', 'bad_hp', 'bool_scope'])
def test_stale_or_malformed_saved_summon_proof_does_not_authorize_retreat(change):
    from docich.hanjuku.judgment import egg_bait
    mem = bait_memory(); arm_bait(mem); p.egg_battle_step(summon_panel(), mem)
    proof = mem['battle']['enemy_egg_bait']
    if change == 'age': mem['tick'] += egg_bait.RETURN_PROOF_TICKS + 1
    elif change == 'future': proof['observed_tick'] = mem['tick'] + 1
    elif change == 'bool_tick': proof['observed_tick'] = True
    elif change == 'bad_monster': proof['enemy_monster'] = []
    elif change == 'bad_hp': proof['general_hp'] = True
    else: proof['scope']['chapter'] = True
    assert not egg_bait.retreat_due(mem, mem['battle'])


def test_positive_egg_observed_at_summon_invalidates_the_old_wait_permanently():
    mem = bait_memory(); arm_bait(mem)
    mem['egg_uses']['アルテミス'] = 1
    p.egg_battle_step(summon_panel(), mem)
    assert mem['battle']['enemy_egg_bait']['phase'] == 'cancelled'
    mem['egg_uses']['アルテミス'] = 0
    p.egg_battle_step(summon_panel(), mem)
    assert mem['battle']['enemy_egg_bait']['phase'] == 'cancelled'
