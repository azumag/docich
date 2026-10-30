"""#3063: a 四季王 fight is measured on the stacked summon panel.

g1075 2026-10-30: アルテミス entered 四季王城(南西) at 08:27:28 and ヴィーナス
entered 四季王城(北西) at 08:41:12. Both fights opened straight on the
stacked panel (general above, 四季王 below) beside こうげき/もうこうげき/
たまごをつかう and never drew the two-general panel, so neither left a
battle_start or a battle_result. The fixtures are those live frames.
"""

from copy import deepcopy
from pathlib import Path

import pytest

from docich.hanjuku import orchestrator
from docich.hanjuku.flows import battle as battle_flow
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku_map_sources import SEASON_KINGS

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'
NW = ('g1075-season-king-nw-82-425.png', 'g1075-season-king-nw-82-400.png',
      'g1075-season-king-nw-26-382.png')
TEXT = 'g1075-season-king-nw-text.png'


def native(name):
    frame = read_png(FIXTURES / name)
    return parse(frame, phase=classify(frame))


def entry(general='ヴィーナス', castle='四季王城(北西)', step='4-B4', **changes):
    return {'general': general, 'castle': castle, 'side': 'attack', 'step': step,
            'entry_evidence': 'measured_boss_entry', **changes}


def state(**policy):
    return {'policy': {'chapter': 4, 'attack': entry(), **policy}}


def feed(st, *names):
    seen = st.pop('seen', [])
    for name in names:
        _, st = orchestrator.decide(read_png(FIXTURES / name), st)
        seen += st.get('_records', [])    # each decide() returns only its own records
    st['seen'] = seen
    return st


def decisions(st, name):
    return [r for r in st.get('seen', []) if r['decision'] == name]


@pytest.mark.parametrize('name,rows', [
    ('g1075-season-king-sw-open.png', [('アルテミス', 49, 'ally'), ('サマカンテ', 614, 'enemy')]),
    (NW[0], [('ヴィーナス', 82, 'ally'), ('スプリミョーネ', 425, 'enemy')]),
    (NW[2], [('ヴィーナス', 26, 'ally'), ('スプリミョーネ', 382, 'enemy')]),
])
def test_native_king_fight_has_only_the_stacked_panel(name, rows):
    screen = native(name)
    assert screen.kind == 'egg_battle_menu'
    assert screen.battle is None           # battle_step never sees this fight
    assert [(r.name, r.hp, r.side) for r in screen.egg_rows] == rows


def test_the_game_spells_the_spring_king_as_drawn():
    assert 'スプリミョーネ' in SEASON_KINGS and 'スプリミリョーネ' not in SEASON_KINGS
    assert len(SEASON_KINGS) == 4


def test_measured_entry_opens_the_fight_after_two_equal_readings():
    st = feed(state(), NW[0])
    assert not st['policy'].get('battle') and not decisions(st, 'battle_start')
    st = feed(st, NW[0])
    cur = st['policy']['battle']
    assert {k: cur[k] for k in ('enemy', 'ally', 'start_enemy_hp', 'start_ally_hp', 'side',
                                'castle', 'step', 'entry_evidence', 'panel')} == {
        'enemy': 'スプリミョーネ', 'ally': 'ヴィーナス', 'start_enemy_hp': 425,
        'start_ally_hp': 82, 'side': 'attack', 'castle': '四季王城(北西)', 'step': '4-B4',
        'entry_evidence': 'measured_boss_entry', 'panel': 'season_king_stack'}
    record, = decisions(st, 'battle_start')
    assert record['observed_metric'] == {'enemy_hp': 425, 'ally_hp': 82,
                                         'panel': 'season_king_stack'}
    assert record['castle'] == '四季王城(北西)' and record['chart_step'] == '4-B4'


def test_the_southwest_entry_opens_on_its_own_live_frame():
    st = state(attack=entry('アルテミス', '四季王城(南西)', '4-B1'))
    st = feed(st, 'g1075-season-king-sw-open.png', 'g1075-season-king-sw-open.png')
    cur = st['policy']['battle']
    assert (cur['ally'], cur['ally_hp'], cur['enemy'], cur['enemy_hp']) == (
        'アルテミス', 49, 'サマカンテ', 614)


def test_hp_follows_the_stacked_rows_and_dialogue_is_kept_once():
    st = feed(state(), NW[0], *NW, TEXT, TEXT)
    cur = st['policy']['battle']
    assert (cur['ally_hp'], cur['enemy_hp']) == (26, 382)
    assert len(decisions(st, 'battle_start')) == 1
    record, = decisions(st, 'season_king_battle_text')
    assert record['observed_metric'] == {'text': 'かもんつくしたち!!', 'ally_hp': 26,
                                         'enemy_hp': 382}


def test_an_end_without_a_drawn_zero_stays_unclassified():
    st = feed(state(), NW[0], *NW, TEXT)
    mem = st['policy']
    mem['_records'] = []
    battle_flow.battle_end(mem, 'map')
    battle_flow.battle_end(mem, 'map')
    result, = [r for r in mem['_records'] if r['decision'] == 'battle_result']
    assert result['outcome'] == 'unclassified'
    assert result['observed_metric']['ally_hp'] == 26
    assert result['observed_metric']['enemy_hp'] == 382
    assert not mem.get('boss_victories') and not mem.get('battle')


def test_a_king_drawn_at_zero_is_a_measured_season_win():
    # Synthetic end: no live king fight has drawn its last panel yet.
    st = feed(state(), NW[0], *NW)
    mem = st['policy']
    mem['battle']['enemy_hp'] = 0
    battle_flow.battle_end(mem, 'map')
    battle_flow.battle_end(mem, 'map')
    assert mem['boss_victories']['スプリミョーネ']['site'] == '四季王城(北西)'
    assert mem['boss_victories']['スプリミョーネ']['evidence'] == 'measured_boss_entry_hp_win'


@pytest.mark.parametrize('change', [
    {'chapter': 3},
    {'attack': None},
    {'attack': entry(entry_evidence=None)},
    {'attack': entry(side='defense')},
    {'attack': entry('アポロン')},
])
def test_no_measured_entry_of_this_general_opens_nothing(change):
    st = state()
    st['policy'].update(change)
    st = feed(st, NW[0], NW[0], NW[1])
    assert not st['policy'].get('battle')
    assert not decisions(st, 'battle_start')


def test_an_open_two_panel_fight_is_left_alone():
    other = {'enemy': 'ロックフォール', 'ally': 'ヴィーナス', 'ally_hp': 40, 'enemy_hp': 57,
             'side': 'defense', 'castle': 'アルマムーン', 'cards_used': []}
    mem = {'chapter': 4, 'attack': entry(), 'battle': deepcopy(other)}
    for name in (NW[0], NW[0], NW[1]):
        battle_flow.observe_season_king_panel(native(name), mem)
    assert mem['battle'] == other
    assert not mem.get('_records')
