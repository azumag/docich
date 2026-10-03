"""Card inputs follow observed cursors and cannot freeze their transaction."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_policy as policy
from test_hanjuku_survival import battle, memory, menu


CARDS = ('イッテツーン', 'ブラッキー', 'グリンボー')


def chart_card(card='ブラッキー'):
    mem = memory(hp=40, enemy=26)
    mem['battle'].update(
        ally='バジル', enemy='クミン', start_ally_hp=40, start_enemy_hp=27,
        side='attack', cards_selected=[], cards_unclassified=[],
        card_flow={'card': card, 'stage': 'list', 'tactic_id': 'test-card'},
        tactics_done=['test-card'])
    return mem


def test_chart_card_does_not_move_past_an_already_selected_target():
    mem = chart_card()
    assert policy.card_list_step(menu(CARDS, selected=1, kind='text'), mem) == [policy.pad('a')]
    assert mem['battle']['cards_selected'] == ['ブラッキー']
    assert mem['battle']['cards_used'] == []


def test_chart_card_observes_each_movement_before_confirming():
    mem = chart_card('グリンボー')
    for row in (0, 1):
        assert policy.card_list_step(menu(CARDS, selected=row, kind='text'), mem) == [policy.pad('down')]
        assert mem['battle']['card_flow']['stage'] == 'list'
        assert mem['battle']['cards_selected'] == []
    assert policy.card_list_step(menu(CARDS, selected=2, kind='text'), mem) == [policy.pad('a')]
    assert mem['battle']['cards_selected'] == ['グリンボー']


def test_chart_card_recovers_a_cursor_below_its_target():
    mem = chart_card()
    assert policy.card_list_step(menu(CARDS, selected=2, kind='text'), mem) == [policy.pad('up')]
    assert mem['battle']['card_flow']['stage'] == 'list'
    assert policy.card_list_step(menu(CARDS, selected=1, kind='text'), mem) == [policy.pad('a')]


def test_duplicate_card_uses_the_current_copy_without_wrapping():
    mem = chart_card('イッテツーン')
    cards = ('イッテツーン', 'イッテツーン', 'ブラッキー')
    assert policy.card_list_step(menu(cards, selected=1, kind='text'), mem) == [policy.pad('a')]


def test_unreadable_card_cursor_exits_without_selecting_or_spending():
    mem = chart_card()
    screen = menu(CARDS, selected=None, kind='text')
    for _ in range(policy.CARD_LIST_CURSOR_LIMIT):
        assert policy.card_list_step(screen, mem) == []
    assert policy.card_list_step(screen, mem) == [policy.pad('b')]
    cur = mem['battle']
    assert cur['card_flow'] is None
    assert cur['cards_selected'] == cur['cards_used'] == cur['cards_unclassified'] == []
    assert not mem.get('kit_spent')
    assert cur['card_open_failures'] == {'test-card': 1}


def test_failed_direction_input_cannot_confirm_the_wrong_card_or_wait_forever():
    mem = chart_card()
    screen = menu(CARDS, selected=0, kind='text')
    for _ in range(policy.CARD_LIST_CURSOR_LIMIT):
        assert policy.card_list_step(screen, mem) == [policy.pad('down')]
    assert policy.card_list_step(screen, mem) == [policy.pad('b')]
    assert mem['battle']['cards_selected'] == []


def test_chart_card_announcement_times_out_without_claiming_consumption():
    mem = chart_card()
    screen = menu(CARDS, selected=1, kind='text')
    assert policy.card_list_step(screen, mem) == [policy.pad('a')]
    for _ in range(policy.CARD_ANNOUNCE_LIMIT):
        assert policy.card_list_step(screen, mem) == []
    assert policy.card_list_step(screen, mem) == [policy.pad('b')]
    cur = mem['battle']
    assert cur['card_flow'] is None
    assert cur['cards_used'] == []
    assert cur['cards_unclassified'] == ['ブラッキー']
    assert cur['card_consumption_complete'] is False


def test_hotloaded_announcement_without_a_counter_also_exits():
    mem = chart_card()
    mem['battle']['cards_selected'] = ['ブラッキー']
    mem['battle']['card_flow'].update(stage='announce', selection_planned=True)
    for _ in range(policy.CARD_ANNOUNCE_LIMIT):
        assert policy.card_list_step(menu(CARDS, selected=1, kind='text'), mem) == []
    assert policy.card_list_step(menu(CARDS, selected=1, kind='text'), mem) == [policy.pad('b')]


def test_preselection_melee_damage_does_not_disarm_the_enemy():
    mem = chart_card('イッテツーン')
    cur = mem['battle']
    # An older flow opened B at HP27, but the enemy reached HP26 before A.
    cur['egg_drop_watch'] = {'card': 'イッテツーン', 'hp': 27, 'value': 8,
                             'threshold': 4, 'max_hp_sum': 67}
    assert policy.card_list_step(menu(CARDS, selected=0, kind='text'), mem) == [policy.pad('a')]
    assert cur['egg_drop_watch']['hp'] == 26
    policy.battle_step(battle(mem), mem)
    assert not cur.get('enemy_egg_dropped')
    assert not cur.get('enemy_egg_drop_expected')


def test_postselection_damage_is_only_a_candidate_even_if_the_input_was_lost():
    mem = chart_card('イッテツーン')
    cur = mem['battle']
    assert policy.card_list_step(menu(CARDS, selected=0, kind='text'), mem) == [policy.pad('a')]
    # No input_sent/consumption receipt exists. An ordinary hit still lowers HP.
    cur['enemy_hp'] = 25
    policy.battle_step(battle(mem), mem)
    assert cur['enemy_egg_drop_expected'] is True
    assert not cur.get('enemy_egg_dropped')
    assert cur['cards_used'] == []
    assert policy._unarmed_clash_risk(cur) is True
    assert policy._melee_step(mem, cur) == []
    assert any(r['decision'] == 'battle_egg_drop_unconfirmed' for r in mem['_records'])


def test_old_inferred_egg_drop_is_invalidated_once_without_an_endless_hold():
    mem = chart_card()
    cur = mem['battle']
    cur['card_flow'] = None
    cur['enemy_egg_dropped'] = True
    # The flags cannot authorize a mash even before the migration is observed.
    assert policy._unarmed_clash_risk(cur) is True
    assert policy._melee_step(mem, cur) == []
    policy._observe_egg_drop_candidate(mem, cur)
    policy._observe_egg_drop_candidate(mem, cur)
    assert not cur.get('enemy_egg_dropped')
    assert len([r for r in mem['_records'] if r['decision'] == 'battle_egg_drop_invalidated']) == 1
    for _ in range(policy.MELEE_HOLD_LIMIT):
        actions = policy._melee_step(mem, cur)
    assert actions and actions[0]['buttons'] == ['a']


def test_old_preselection_watch_cannot_borrow_an_earlier_same_named_selection():
    mem = chart_card('イッテツーン')
    cur = mem['battle']
    cur['cards_selected'] = ['イッテツーン']
    cur['egg_drop_watch'] = {'card': 'イッテツーン', 'hp': 27, 'value': 8,
                             'threshold': 4, 'max_hp_sum': 67}
    policy._observe_egg_drop_candidate(mem, cur)
    assert not cur.get('enemy_egg_dropped')
    assert not cur.get('enemy_egg_drop_expected')
    assert 'egg_drop_watch' not in cur
