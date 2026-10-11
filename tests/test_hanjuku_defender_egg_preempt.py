"""Observed eggless castle defense gets one bounded pre-summon last chance.

Synthetic screens and persisted state, not evidence of successful live inputs.
SFC rules: gcgx ai.html, battle.html and okunote.html. Castle attackers all
use thought 1; its HP trigger is not its earlier unmeasured position trigger.
"""
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, decide
from docich.hanjuku_egg_reference import general_max_hp
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Battle, Screen, parse
from test_hanjuku_chart_bot import Canvas


def memory(*, chapter=1, enemy='リーキ', enemy_hp=30, ally='トレビス', ally_hp=66):
    return {'chapter': chapter, 'battle': {
        'enemy': enemy, 'ally': ally, 'castle': p.chart.home_castle(chapter), 'side': 'defense',
        'start_enemy_hp': general_max_hp(enemy), 'enemy_hp': enemy_hp,
        'start_ally_hp': ally_hp, 'ally_hp': ally_hp, 'step': None,
        'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
        'card_evidence_version': 1, 'planned_cards': [],
        'okunote_only_observed': True,
        'survival': {'opens': 1, 'menu_ticks': 0, 'cards_checked': 0,
                     'cards_attempted': [], 'egg_attempted': False, 'exhausted': True}}}


def panel(mem, *, enemy_hp=None, ally_hp=None):
    cur = mem['battle']
    return Screen([], None, '', kind='battle', battle=Battle(
        cur['enemy'], cur['enemy_hp'] if enemy_hp is None else enemy_hp,
        cur['ally'], cur['ally_hp'] if ally_hp is None else ally_hp))


def menu(labels=('おくのて',), cursor=208, *, hidden=False):
    first_y = 208 - 16 * (len(labels) - 1)
    lines = [TextLine(first_y + 16*i, tuple((176+8*j,ch) for j,ch in enumerate(label)))
             for i,label in enumerate(labels)]
    return Screen(lines, None, ''.join(labels), kind='battle_menu', menu_cursor=cursor,
                  hidden_battle_commands=hidden)


def opening(mem):
    cur = mem['battle']
    assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    assert cur['okunote_egg_preempt']['stage'] == 'opening'


@pytest.mark.parametrize('chapter', range(1, 13))
def test_wall_hp_boundary_includes_one_hit_and_observation_latency(chapter):
    # Trigger: living enemy HP <= chapter+2 after a wall hit. The default
    # margin is one 4-HP hit plus another 4 HP for capture/menu latency.
    boundary = chapter + 2 + 8
    mem = memory(chapter=chapter, enemy_hp=boundary+1)
    window = p._defender_egg_window(mem, mem['battle'])
    assert window['wall_critical_hp'] == chapter + 2
    assert window['open_at_hp'] == boundary and not window['due']
    mem['battle']['enemy_hp'] = boundary
    opening(mem)
    record = mem['_records'][-1]
    assert record['decision'] == 'battle_okunote_egg_open'
    assert record['observed_metric']['reason'] == 'wall_hp_window'


@pytest.mark.parametrize('enemy', ['ミモザ', 'リーキ', 'キッシュ', 'クミン'])
def test_castle_defense_overrides_all_four_roster_thoughts(enemy):
    mem = memory(enemy=enemy, enemy_hp=12)
    cur = mem['battle']
    assert p._unarmed_clash_risk(cur)
    window = p._defender_egg_window(mem, cur)
    assert window['thought'] == 1
    assert window['thought_basis'] == 'player_castle_defense'
    assert window['clash_internal_x_threshold'] == 36
    assert not window['clash_position_measured'] and not window['due']
    # A raw thought-2/3 enemy's multiple-of-four rule is not used in defense.


def test_large_inter_observation_hp_drop_moves_the_deadline_earlier():
    mem = memory(enemy_hp=24)
    assert p.battle_step(panel(mem, enemy_hp=12), mem) == [p.pad('b')]
    window = mem['_records'][-1]['observed_metric']
    assert window['observed_hp_drop'] == 12
    assert window['input_margin_hp'] == 16 and window['open_at_hp'] == 19
    assert mem['battle']['defender_enemy_hp_drop'] == 12


@pytest.mark.parametrize('changes', [
    {'side': 'attack'}, {'side': None}, {'castle': None}, {'castle': '未読'},
    {'enemy': '未読'}, {'enemy': 'クイーン'}, {'enemy': 'ミント'},
    {'ally': '未読'}, {'ally': 'だいじん'},
    {'ally_hp': 0}, {'enemy_hp': 0}, {'ally_hp': True}, {'enemy_hp': None},
    {'start_ally_hp': 999}, {'start_enemy_hp': 999},
    {'okunote_only_observed': False}, {'egg_battle': True},
    {'card_flow': {'stage': 'list', 'card': 'グリンボー'}},
])
def test_unknown_context_and_other_resources_do_not_invent_an_egg_deadline(changes):
    mem = memory(enemy_hp=10)
    mem['battle'].update(changes)
    assert p._defender_egg_window(mem, mem['battle']) is None


@pytest.mark.parametrize('changes', [{'melee_holds': p.MELEE_HOLD_LIMIT},
                                     {'melee_forced': True}, {'ally_hp': 60}])
def test_exhausted_position_guard_uses_okunote_before_forced_power_mashing(changes):
    mem = memory()
    mem['battle'].update(changes)
    opening(mem)
    assert mem['_records'][-1]['observed_metric']['reason'] == 'unmeasured_clash_guard_exhausted'
    assert not any(r['decision'] == 'battle_melee' for r in mem['_records'])


def test_confirmed_no_resource_menu_arms_deadline_without_immediate_lottery():
    mem = memory(ally='どうし', ally_hp=90)
    mem['battle'].pop('okunote_only_observed')
    assert p._defender_egg_window(mem, mem['battle']) is None
    # Even a healthy hero who survives the worst self-damage waits while the
    # observed deadline is still distant. The menu receipt is kept for later.
    assert p.battle_menu_step(menu(), mem) == [p.pad('b')]
    assert mem['battle']['okunote_only_observed']
    assert not mem['battle'].get('okunote_egg_preempt')
    assert mem['_records'][-1]['decision'] == 'battle_okunote_egg_wait'


@pytest.mark.parametrize('ally,hp', [('トレビス', 50), ('どうし', 50), ('しゅじんこう', 50)])
def test_low_hp_including_hero_can_take_one_imminent_summon_chance(ally, hp):
    mem = memory(ally=ally, ally_hp=hp, enemy_hp=10)
    opening(mem)
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    guard = mem['battle']['okunote_egg_preempt']
    assert guard['selected'] and guard['stage'] == 'selected'
    record = next(r for r in mem['_records'] if r['decision'] == 'battle_okunote_egg_preempt')
    assert record['observed_metric']['max_self_damage'] == 88
    assert record['resulting_event'] == 'irreversible_risk_accepted'
    # The first A is a plan, not a successful effect or a reason to retry the lottery.
    assert p.battle_menu_step(menu(), mem) == [p.pad('b')]
    assert not p._defender_last_resort(mem, mem['battle'])


def test_late_melee_and_json_round_trip_keep_preemption_ahead_of_survival():
    mem = memory(enemy_hp=10)
    opening(mem)
    mem = json.loads(json.dumps(mem))
    # An already exhausted ordinary rescue must not close the intended menu.
    assert mem['battle']['survival']['exhausted']
    assert p.defender_egg_pending_step(menu(cursor=192), mem) == [p.pad('down')]
    assert p.defender_egg_pending_step(menu(), mem) == [p.pad('a')]


def test_real_command_geometry_routes_hero_preemption_through_decide():
    mem = memory(ally='どうし', ally_hp=50, enemy_hp=10)
    opening(mem)
    c = Canvas()
    c.text(176, 176, 'たまごをつかう', color=(106, 105, 106))
    c.text(176, 192, 'きりふだ', color=(106, 105, 106))
    c.text(176, 208, 'おくのて')
    for y in range(200, 212):
        for x in range(152, 164):
            c.put(x, y, (230, 105, 74))
    frame = c.frame()
    screen = parse(frame)
    assert screen.kind == 'battle_menu' and screen.menu_cursor == 208
    assert screen.text == 'おくのて'
    actions, state = decide(frame, {'policy': mem})
    assert actions == [p.pad('a')]
    assert state['policy']['battle']['okunote_egg_preempt']['selected']


@pytest.mark.parametrize('label', ['たまごをつかう', 'きりふだ'])
def test_live_resource_revokes_old_disabled_receipt_and_pending_lottery(label):
    mem = memory(enemy_hp=10)
    opening(mem)
    assert p.defender_egg_pending_step(menu((label,)), mem) == [p.pad('a')]
    assert not mem['battle']['okunote_only_observed']
    assert 'okunote_egg_preempt' not in mem['battle']
    assert not any(r['decision'] == 'battle_okunote_egg_preempt' for r in mem['_records'])


def test_existing_due_card_keeps_priority_over_the_new_deadline(monkeypatch):
    mem = memory(enemy_hp=10)
    monkeypatch.setattr(p, '_tactics', lambda mem, step: [
        {'enemy': 'リーキ', 'card': 'グリンボー', 'open': True, 'note': 'existing tactic'}])
    assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'グリンボー'
    assert 'okunote_egg_preempt' not in mem['battle']


@pytest.mark.parametrize('enemy_hp,taps', [(19, 1), (20, 4)])
def test_approaching_deadline_shortens_a_charted_burst_without_changing_card_gates(enemy_hp, taps, monkeypatch):
    mem = memory(enemy_hp=enemy_hp)
    monkeypatch.setattr(p, '_charted_melee', lambda mem, cur: True)
    actions = p._melee_step(mem, mem['battle'])
    assert actions == [a for _ in range(taps) for a in (p.pad('a', 3), {'type': 'wait', 'ms': 50})]
    assert mem['_records'][-1]['a_frames_sent'] == taps * 3


def test_failed_opening_is_bounded_and_does_not_restore_an_infinite_hold():
    mem = memory()
    mem['battle']['melee_holds'] = p.MELEE_HOLD_LIMIT
    for _ in range(p.DEFENDER_EGG_OPEN_LIMIT):
        assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    for _ in range(3):
        assert p.battle_step(panel(mem), mem)[0] == p.pad('a', 3)
    assert mem['battle']['okunote_egg_preempt']['exhausted']
    assert sum(r['decision'] == 'battle_okunote_egg_open_failed' for r in mem['_records']) == 1


def test_unknown_cursor_never_confirms_and_menu_navigation_is_bounded():
    mem = memory(enemy_hp=10)
    opening(mem)
    for _ in range(16):
        assert p.defender_egg_pending_step(menu(cursor=None), mem) == [p.pad('down')]
    assert p.defender_egg_pending_step(menu(cursor=None), mem) == [p.pad('b')]
    assert not mem['battle']['okunote_egg_preempt'].get('selected')
    assert mem['battle']['okunote_egg_preempt']['exhausted']


def test_parent_navigation_budget_cannot_block_the_irreversible_candidate_choice():
    mem = memory(enemy_hp=10)
    opening(mem)
    for _ in range(15):
        assert p.defender_egg_pending_step(menu(cursor=None), mem) == [p.pad('down')]
    assert p.defender_egg_pending_step(menu(), mem) == [p.pad('a')]
    choices = menu(('ヤケクソ', 'あやまる', 'しんだフリ'))
    choices.kind = 'okunote_menu'
    assert p.defender_egg_pending_step(choices, mem) == [p.pad('a')]
    assert mem['_records'][-1]['choice'] == 'しんだフリ'


def test_pending_unknown_frames_do_not_escape_to_generic_a_or_old_month_transaction(monkeypatch):
    mem = memory(enemy_hp=10)
    opening(mem)
    mem['month_sub'] = {'kind': 'soldiers'}  # a stale transaction is not the active battle
    monkeypatch.setattr(p, 'month_sub_step', lambda *args: (_ for _ in ()).throw(AssertionError('old transaction ran')))
    c = Canvas((120, 120, 120))
    c.text(24, 80, 'ひょうじちゅう')
    frame = c.frame()
    assert parse(frame).kind == 'text'
    state = {'policy': mem, 'no_input_streak': NO_INPUT_HOLD_MAX,
             'no_input_frames': [frame.digest()]}
    for _ in range(8):
        actions, state = decide(frame, state)
        assert actions == [{'type': 'wait', 'ms': 50}]
    actions, state = decide(frame, state)
    assert actions == [p.pad('b')]
    assert state['policy']['battle']['okunote_egg_preempt']['exhausted']
    # A B plan is not proof that the menu closed. A late frame must still
    # receive B rather than escaping to the ordinary event/text A fallback.
    actions, state = decide(frame, state)
    assert actions == [p.pad('b')]
    mem = state['policy']
    assert p.defender_egg_pending_step(panel(mem), mem) is None
    assert not mem['battle']['okunote_egg_preempt'].get('close_pending')


def test_visible_candidate_list_chooses_the_least_self_damage_if_all_three_are_harmful():
    mem = memory(ally='どうし', ally_hp=50, enemy_hp=10)
    opening(mem)
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    choices = menu(('ヤケクソ', 'あやまる', 'しんだフリ'))
    choices.kind = 'okunote_menu'
    assert p.defender_egg_pending_step(choices, mem) == [p.pad('a')]
    assert mem['_records'][-1]['choice'] == 'しんだフリ'


def test_melee_return_ends_pending_episode_but_keeps_the_one_lottery_guard():
    mem = memory(ally='どうし', ally_hp=50, enemy_hp=10)
    opening(mem)
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    choices = menu(('ヤケクソ', 'あやまる', 'しんだフリ'))
    choices.kind = 'okunote_menu'
    assert p.defender_egg_pending_step(choices, mem) == [p.pad('a')]
    p.battle_step(panel(mem), mem)
    guard = mem['battle']['okunote_egg_preempt']
    assert guard['stage'] == 'completed' and guard['selected']
    assert p.defender_egg_pending_step(Screen([], None, 'そのあと', kind='text'), mem) is None
    assert p.battle_menu_step(menu(), mem) == [p.pad('b')]


def test_partial_battle_hp_cannot_create_a_damage_margin_or_preemption():
    mem = memory(enemy_hp=20)
    incomplete = Screen([], None, '', kind='battle', battle=Battle('リーキ', None, 'トレビス', 66))
    assert p.battle_step(incomplete, mem) == []
    assert mem['battle']['enemy_hp'] == 20
    assert 'defender_enemy_hp_drop' not in mem['battle']
    assert 'okunote_egg_preempt' not in mem['battle']


def test_global_egg_battle_state_never_opens_human_defender_preemption():
    mem = memory(enemy_hp=10)
    mem['egg_battle'] = True
    assert p._defender_egg_window(mem, mem['battle']) is None


def test_healing_above_opening_hp_does_not_disable_a_current_enemy_deadline():
    mem = memory(ally='どうし', ally_hp=20, enemy_hp=10)
    mem['battle']['ally_hp'] = 90
    assert p._defender_egg_window(mem, mem['battle'])['due']


@pytest.mark.parametrize('changes', [{'ally_hp': 0}, {'enemy_hp': 0},
                                     {'egg_battle': True}, {'card_flow': {'stage': 'list'}}])
def test_pending_close_does_not_override_resolved_or_other_battle_modes(changes):
    mem = memory(enemy_hp=10)
    opening(mem)
    mem['battle']['okunote_egg_preempt'].update(exhausted=True, close_pending=True)
    mem['battle'].update(changes)
    assert p.defender_egg_pending_step(Screen([], None, '', kind='unknown'), mem) is None
