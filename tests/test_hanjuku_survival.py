"""Survival actions must open a real menu and select only observed resources."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_screen import Battle, Screen
from docich.hanjuku_font import TextLine
from docich.hanjuku_bot import decide
from test_hanjuku_chart_bot import Canvas


def memory(hp=30, enemy=50):
    return {'chapter': 1, 'battle': {
        'enemy': 'ミント', 'ally': 'どうし', 'start_enemy_hp': 60, 'start_ally_hp': 90,
        'enemy_hp': enemy, 'ally_hp': hp, 'step': None, 'cards_used': [],
        'card_evidence_version': 1}}


def battle(mem):
    cur = mem['battle']
    return Screen([], None, '', kind='battle', battle=Battle(
        cur['enemy'], cur['enemy_hp'], cur['ally'], cur['ally_hp']))


def menu(labels=('たまごをつかう', 'きりふだ', 'たいきゃく'), selected=0, kind='battle_menu'):
    lines = [TextLine(175 + 16*i, tuple((160+8*j, ch) for j, ch in enumerate(label)))
             for i, label in enumerate(labels)]
    hand = None if selected is None else (138, 169+16*selected, 156, 181+16*selected)
    return Screen(lines, hand, ''.join(labels), kind=kind)


def test_danger_opens_menu_then_selects_visible_heal_before_damage():
    mem = memory()
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(), mem) == [p.pad('down')]
    assert p.battle_menu_step(menu(selected=1), mem) == [p.pad('a')]
    cards = ('デッドガン', 'グリンボー', 'エンジェリン')
    assert p.card_list_step(menu(cards, kind='text'), mem) == [p.pad('down')]
    assert p.card_list_step(menu(cards, selected=1, kind='text'), mem) == [p.pad('down')]
    assert p.card_list_step(menu(cards, selected=2, kind='text'), mem) == [p.pad('a')]
    assert mem['battle']['cards_selected'] == ['エンジェリン']
    assert mem['battle']['cards_used'] == []
    assert mem['battle']['card_consumption_complete'] is False
    # Selection is not a receipt; two clear melee readings retain that distinction.
    p.battle_step(battle(mem), mem)
    p.battle_step(battle(mem), mem)
    assert mem['battle']['cards_unclassified'] == ['エンジェリン']
    assert not mem['battle']['card_flow']


@pytest.mark.parametrize('cards', [('デッドガン', 'ファバード'), ()])
def test_no_safe_card_returns_to_parent_menu_and_uses_egg(cards):
    mem = memory()
    p.battle_step(battle(mem), mem)
    p.battle_menu_step(menu(selected=1), mem)
    for _ in range(4 if not cards else 1):
        actions = p.card_list_step(menu(cards, kind='text'), mem)
    assert actions == [p.pad('b')]
    assert mem['battle']['card_flow'] is None
    assert p.battle_menu_step(menu(selected=1), mem) == [p.pad('up')]
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert mem['battle']['survival']['egg_attempted']
    # Allow the menu to fade after selection; a failed summon is bounded.
    for _ in range(3):
        assert p.battle_menu_step(menu(), mem) == []
    assert p.battle_menu_step(menu(), mem) == [p.pad('b')]


def test_no_card_row_uses_readable_egg_despite_learned_pass():
    mem = memory()
    mem['indep_menu_action'] = 'pass'
    p.battle_step(battle(mem), mem)
    assert p.battle_menu_step(menu(('たまごをつかう',)), mem) == [p.pad('a')]


def test_greyed_egg_and_unsafe_cards_are_not_selected():
    mem = memory()
    p.battle_step(battle(mem), mem)
    p.battle_menu_step(menu(('きりふだ',)), mem)
    assert p.card_list_step(menu(('ファバード',), kind='text'), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('きりふだ',)), mem) == [p.pad('b')]
    assert mem['battle']['survival']['exhausted']
    assert p.battle_step(battle(mem), mem)[0] == p.pad('a', 3)


@pytest.mark.parametrize('hp,enemy,needed', [(36,50,True),(37,50,False),(13,10,False),
                                         (12,10,True),(0,50,False),(30,0,False),
                                         (None,50,False),(90,90,False)])
def test_thresholds(hp, enemy, needed):
    assert p._survival_needed(memory(hp, enemy)['battle']) is needed


def test_tutorial_does_not_attempt_unavailable_menu():
    mem = memory(1, 8)
    mem['battle'].update(enemy='だいじん', start_enemy_hp=90)
    assert not p._survival_needed(mem['battle'])
    assert p.battle_step(battle(mem), mem)[0] == p.pad('a', 3)


def test_unreadable_cursor_and_missing_menu_are_bounded():
    mem = memory()
    for _ in range(3):
        assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert p.battle_step(battle(mem), mem)[0] == p.pad('a', 3)
    for _ in range(12):
        assert p.battle_menu_step(menu(selected=None), mem) == []
    assert p.battle_menu_step(menu(selected=None), mem) == [p.pad('b')]


def test_card_cursor_is_required_and_navigation_bounded():
    mem = memory()
    p.battle_step(battle(mem), mem)
    p.battle_menu_step(menu(selected=1), mem)
    for _ in range(10):
        assert p.card_list_step(menu(('グリンボー',), selected=None, kind='text'), mem) == []
    assert p.card_list_step(menu(('グリンボー',), selected=None, kind='text'), mem) == [p.pad('b')]
    assert not mem['battle'].get('cards_selected')


def test_empty_list_is_routed_without_blind_a_and_eventually_falls_back():
    mem = memory()
    p.battle_step(battle(mem), mem)
    p.battle_menu_step(menu(selected=1), mem)
    state = {'policy': mem}
    c = Canvas((20,20,20))
    c.text(160,175,'きりふだなし')
    for i in range(4):
        actions, state = decide(c.frame(), state)
        assert actions == ([] if i < 3 else [p.pad('b')])
    assert state['policy']['battle']['survival']['cards_exhausted']


def test_chart_tactic_still_has_priority_and_healed_battle_resumes_melee(monkeypatch):
    monkeypatch.setattr(p, '_tactics', lambda *args: [
        {'enemy': 'ミント', 'card': 'グリンボー', 'open': True, 'note': '開幕'}])
    mem = memory()
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'グリンボー'
    assert 'survival' not in mem['battle']
    mem['battle']['card_flow'] = None
    mem['battle']['ally_hp'] = 90
    assert p.battle_step(battle(mem), mem)[0] == p.pad('a', 3)


def command_frame(enabled=(0,1,2), cursor=0):
    c = Canvas((20,20,20))
    for index in enabled:
        c.text(176,176+16*index,p.BATTLE_MENU[index])
    for y in range(168+16*cursor,180+16*cursor):
        for x in range(152,164):
            c.put(x,y,(230,105,74))
    return c.frame()


def test_all_disabled_commands_scroll_to_hidden_okunote_without_selecting_grey_rows():
    from docich.hanjuku_screen import parse
    c = Canvas()
    for i,label in enumerate(p.BATTLE_MENU):
        c.text(176,176+16*i,label,color=(106,105,106))
    assert parse(c.frame()).kind != 'battle_menu'  # no knight: do not infer a menu
    for y in range(168,180):
        for x in range(152,164): c.put(x,y,(230,105,74))
    actions,state=decide(c.frame(),{'policy':memory(6,30)})
    assert state['screen_kind']=='battle_menu' and actions==[p.pad('down')]
    assert parse(c.frame()).text==''  # disabled rows are never selectable labels
    c=Canvas()
    c.text(176,176,'きりふだ',color=(106,105,106))
    c.text(176,192,'たいきゃく',color=(106,105,106))
    c.text(176,208,'おくのて')
    for y in range(200,212):
        for x in range(152,164): c.put(x,y,(230,105,74))
    actions,state=decide(c.frame(),state)
    assert actions==[p.pad('a')]
    assert any(r['decision']=='battle_okunote_select' for r in state['_records'])


@pytest.mark.parametrize('labels,winner', [
    (('ヤケクソ','せっとく','ウェイブもどき'),'ウェイブもどき'),
    (('あやまる','うそなき','しんだフリ'),'しんだフリ'),
    (('だいじんアタック','ファバードもどき','いあつする'),'だいじんアタック'),
])
def test_okunote_chooses_the_best_visible_candidate(labels,winner):
    c=Canvas()
    for i,label in enumerate(labels): c.text(176,176+16*i,label)
    target=labels.index(winner)
    for y in range(168+16*target,180+16*target):
        for x in range(152,164): c.put(x,y,(230,105,74))
    actions,state=decide(c.frame(),{'policy':memory(6,30)})
    assert state['screen_kind']=='okunote_menu' and actions==[p.pad('a')]
    assert any(r.get('choice')==winner for r in state['_records'])


def test_okunote_scroll_is_bounded_and_a_new_melee_reading_resets_the_attempt():
    screen=Screen([],None,'',kind='battle_menu',menu_cursor=176,hidden_battle_commands=True)
    mem=memory(6,30)
    for _ in range(16): assert p.okunote_step(screen,mem)==[p.pad('down')]
    assert p.okunote_step(screen,mem)==[p.pad('b')]
    p.battle_step(battle(mem),mem)
    assert 'okunote_flow' not in mem['battle']


def test_defense_egg_only_command_box_is_not_dialogue():
    mem = memory()
    p.battle_step(battle(mem), mem)
    actions, state = decide(command_frame(enabled=(0,)), {'policy': mem})
    assert state['screen_kind'] == 'battle_menu'
    assert actions == [p.pad('a')]
    assert state['policy']['battle']['survival']['egg_attempted']


def test_knight_on_disabled_top_row_moves_to_readable_cards():
    mem = memory()
    p.battle_step(battle(mem), mem)
    actions, state = decide(command_frame(enabled=(1,2)), {'policy': mem})
    assert state['screen_kind'] == 'battle_menu'
    assert actions == [p.pad('down')]
    actions, state = decide(command_frame(enabled=(1,2),cursor=1), state)
    assert actions == [p.pad('a')]
    assert state['policy']['battle']['card_flow']['stage'] == 'list'


def test_actual_card_row_geometry_uses_knight_cursor_and_bounds_receipt_wait():
    mem = memory()
    p.battle_step(battle(mem), mem)
    p.battle_menu_step(menu(selected=1), mem)
    state = {'policy': mem}
    def cards_frame(cursor):
        c = Canvas((20,20,20))
        for i, card in enumerate(('デッドガン','グリンボー')):
            c.text(176,176+16*i,card)
        for y in range(168+16*cursor,180+16*cursor):
            for x in range(152,164): c.put(x,y,(230,105,74))
        return c.frame()
    actions, state = decide(cards_frame(0), state)
    assert actions == [p.pad('down')]
    actions, state = decide(cards_frame(1), state)
    assert actions == [p.pad('a')]
    assert state['policy']['battle']['cards_selected'] == ['グリンボー']
    for _ in range(6):
        actions, state = decide(cards_frame(1), state)
        assert actions == []
    actions, state = decide(cards_frame(1), state)
    assert actions == [p.pad('b')]
    assert state['policy']['battle']['cards_used'] == []
    assert state['policy']['battle']['cards_unclassified'] == ['グリンボー']


def test_observed_menu_resets_open_retries_for_following_resource():
    mem = memory()
    for _ in range(3): p.battle_step(battle(mem), mem)
    p.battle_menu_step(menu(selected=1), mem)
    p.card_list_step(menu(('グリンボー',),kind='text'), mem)
    p.battle_step(battle(mem), mem)
    p.battle_step(battle(mem), mem)
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
