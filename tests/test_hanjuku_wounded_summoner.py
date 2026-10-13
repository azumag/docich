"""Native return/exposure evidence; alternate skill outcomes are unconfirmed."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows import battle_menus as menus
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
EVIDENCE = json.loads((ROOT / 'g745-emergency-egg-evidence.json').read_text())


def native(uid):
    e = next(e for e in EVIDENCE['evidence']
             if e['decision']['decision_id'].endswith(':' + str(uid)))
    frame = read_png(ROOT / e['fixture'])
    assert frame.digest() == e['rgb_sha256'] == e['decision']['frame_sha256']
    return parse(frame)


def memory():
    mem = {'chapter': 2, '_run_identity': deepcopy(EVIDENCE['identity']),
           'battle': {'ally': 'ゼウス', 'enemy': 'ヴィーナス', 'side': 'attack',
                      'step': '2-Z1', 'ally_hp': 34, 'enemy_hp': 19,
                      'start_ally_hp': 85, 'start_enemy_hp': 82, 'planned_cards': []}}
    p.egg_battle_step(native(763), mem)
    return mem


def test_original_return_a_and_exposed_general_panel_have_exact_receipts():
    e = next(e for e in EVIDENCE['evidence'] if e['decision']['decision_id'].endswith(':790'))
    assert any(r['decision_id'] == e['decision']['decision_id']
               and r['buttons'] == ['a']
               and r['decision_frame_sha256'] == r['frame_sha256'] == e['rgb_sha256']
               for r in e['input_receipts'])
    s = native(790)
    assert s.menu_cursor == s.menu_rows[-1].y
    s = native(834)
    assert [(r.name, r.hp) for r in s.egg_rows if r.side == 'ally'] == [('ゼウス', 13)]
    # These actual gray menus must not be claimed as usable eggs conserved.
    for uid in (834, 1955):
        assert 'たまごをつかう' not in {word for _, _, word in p._options(native(uid))}


def test_native_summon_keeps_a_skill_instead_of_exposing_wounded_general():
    mem = memory()
    p.monster_menu_step(native(788), mem)
    assert mem['monster_menu_choice'] == 'skill2'
    assert mem['_records'][-1]['observed_metric']['wounded_general_return_blocked']
    assert mem['battle']['ally_hp'] == 34  # never replace with Baby Moth97
    assert mem['battle']['summon_general_reading']['max_hp'] == 85
    # Counterfactual input from the old return cursor, not a natural new action.
    assert p.monster_menu_step(native(790), mem) == [p.pad('up')]


def test_saved_or_learned_return_cannot_bypass_wounded_general(monkeypatch):
    mem = memory()
    p.monster_menu_step(native(788), mem)
    mem['monster_menu_choice'] = 'retreat'
    mem = json.loads(json.dumps(mem))
    monkeypatch.setattr(p.experience, 'preferred', lambda *a, **k: 'retreat')
    assert p.monster_menu_step(native(790), mem) == [p.pad('up')]
    assert mem['monster_menu_choice'] == 'skill2'


@pytest.mark.parametrize('change', ['identity', 'actor', 'enemy', 'hp', 'missing', 'malformed'])
def test_foreign_or_unbound_reading_does_not_create_general_protection(change):
    mem = memory(); cur = mem['battle']
    if change == 'identity': mem['_run_identity']['lease_id'] = 'foreign'
    elif change == 'actor': cur['ally'] = 'ココット'
    elif change == 'enemy': cur['enemy'] = 'クイーン'
    elif change == 'hp': cur['ally_hp'] = None
    elif change == 'missing': cur.pop('summon_general_reading')
    else: cur['summon_general_reading'] = True
    p.monster_menu_step(native(788), mem)
    assert mem['monster_menu_choice'] == 'retreat'


@pytest.mark.parametrize('hp,full,expected', [(12,20,True),(13,20,False),(42,85,True),
                                           (43,85,False),(0,85,False),(True,85,False),
                                           (34,None,False),(90,85,False)])
def test_only_measured_positive_wounded_general_is_protected(hp, full, expected):
    mem = memory(); cur = mem['battle']; cur['ally_hp'] = hp
    cur['summon_general_reading'].update(hp=hp, max_hp=full)
    s = native(788); panel = {r.side:r for r in s.egg_rows}
    assert menus._wounded_summoner(mem, 'ally', panel['ally'], panel['enemy']) is expected
    assert not menus._wounded_summoner(mem, 'enemy', panel['ally'], panel['enemy'])
    assert not menus._wounded_summoner(mem, 'unknown', panel['ally'], panel['enemy'])


def test_unknown_identity_or_monster_hp_does_not_manufacture_general_reading():
    mem = memory(); mem['battle'].pop('summon_general_reading'); mem.pop('_run_identity')
    assert menus._egg_general_reading(native(788), mem, mem['battle']) is None
    assert 'summon_general_reading' not in mem['battle']
    menus._egg_general_reading(native(763), mem, mem['battle'])
    assert 'summon_general_reading' not in mem['battle']


def test_useful_summon_protects_hero_too_but_powerless_exception_stays():
    from test_hanjuku_chart_bot import monster_menu_frame
    mem = memory(); cur = mem['battle']
    cur['ally'] = p.NAME; cur['ally_hp'] = 12
    cur['summon_general_reading'].update(ally=p.NAME, hp=12, max_hp=90)
    p.monster_menu_step(native(788), mem)
    assert mem['monster_menu_choice'] == 'skill2'
    mem = memory()
    frame = monster_menu_frame(['なぐれっ!', 'かきむしれ!'],
                               ally=('ウゴカザル', 120), enemy=('なると', 226), cursor=0)
    p.monster_menu_step(parse(frame), mem)
    assert mem['monster_menu_choice'] == 'retreat'
