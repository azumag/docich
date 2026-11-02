"""g749 original panels: another card must not erase a measured melee option."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_card_damage_gate import cards, memory


@pytest.fixture(autouse=True)
def refused_card(monkeypatch):
    """These tests pin what follows a refusal, so the hit itself is not the reason to allow.

    The rule for hits that do not finish the enemy is in test_hanjuku_card_wear_down.py.
    """
    monkeypatch.setattr('docich.hanjuku.judgment.battle._card_wears_down_enemy', lambda estimate: False)

FIXTURES = Path(__file__).parent / 'fixtures/hanjuku'


def native(label):
    return parse(read_png(FIXTURES / f'g749-roquefort-control-{label}.png'))


def selected():
    mem = memory('クースカン', enemy='アルテミス', enemy_hp=49,
                 ally='ロックフォール', ally_hp=57, soldiers=(5, 3))
    mem['battle']['planned_cards'] = []
    p._card_battle_reading(native('opening'), mem['battle'], mem)
    assert p.card_list_step(native('card-list'), mem) == [p.pad('a')]
    return mem


def test_original_list_keeps_both_options_without_claiming_card_use():
    mem = selected()
    cur = mem['battle']
    assert cur['card_assessment']['reason'] == 'observed_control_chain_not_a_kill'
    assert cur['card_melee_followup']['enemy_hp_before'] == 49
    assert cur['card_melee_followup']['enemy_hp_after_upper'] == 25
    assert cur['cards_selected'] == ['クースカン']
    assert cur['cards_used'] == [] and cur['enemy_hp'] == 49
    assert cur['enemy_soldiers'] == 3


def test_original_post_control_panel_pursues_without_reopening_menu():
    mem = selected()
    # Preserve the real nine-observation delay, including an unclassified
    # selection timeout. This never fabricates a use announcement.
    mem['battle']['card_flow']['announce_retried'] = True  # post-retry exit
    for _ in range(p.CARD_ANNOUNCE_LIMIT + 1):
        mem['tick'] += 1
        p.card_list_step(native('card-list'), mem)
    s = native('followup')
    assert (s.battle.ally_hp, s.battle.enemy_hp, s.field_soldiers) == (57, 25, (4, 3))
    actions = p.battle_step(s, mem)
    assert any(a.get('buttons') == ['a'] for a in actions)
    assert all(a.get('buttons') == ['a'] or a.get('type') == 'wait' for a in actions)
    assert mem['battle']['card_melee_followup']['bursts'] == 1
    assert mem['battle']['cards_used'] == []
    assert mem['battle']['cards_unclassified'] == ['クースカン']
    assert not mem['battle'].get('enemy_egg_dropped')


@pytest.mark.parametrize('change', [
    {'enemy_hp': 49}, {'ally_hp': 12}, {'ally_hp': 40},
    {'ally_soldiers': 2}, {'ally_soldiers': None}, {'enemy_soldiers': None},
    {'enemy': 'クミン'}, {'ally': 'どうし'}, {'egg_battle': True},
    {'card_hp_unread': True}, {'card_soldiers_current': False},
    {'tick_expired': True}, {'budget_spent': True},
])
def test_future_melee_still_needs_fresh_progress_army_identity_and_budget(change):
    mem = json.loads(json.dumps(selected()))
    cur = mem['battle']
    p._card_battle_reading(native('followup'), cur, mem)
    change = deepcopy(change)
    if change.pop('tick_expired', False):
        mem['tick'] += p.CARD_MELEE_OBSERVE_LIMIT + 1
    if change.pop('budget_spent', False):
        cur['card_melee_followup']['bursts'] = p.MELEE_HOLD_LIMIT
    cur.update(change)
    assert not p._card_melee_ready(mem, cur)


@pytest.mark.parametrize('change', [
    {'enemy': 'クイーン', 'enemy_hp': 70}, {'ally_hp': 12},
    {'ally_soldiers': 2}, {'enemy_soldiers': None}, {'card_hp_unread': True},
])
def test_control_chain_without_melee_evidence_keeps_existing_route(change):
    mem = memory('クースカン', enemy='アルテミス', enemy_hp=49,
                 ally='ロックフォール', ally_hp=57, soldiers=(5, 3))
    mem['battle'].update(change)
    p.card_list_step(cards(['クースカン', 'フットバース']), mem)
    assert not mem['battle'].get('card_melee_followup')
