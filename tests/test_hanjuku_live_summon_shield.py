"""Real return/exposure panels; the new response and outcome are counterfactual."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows import battle, battle_menus
from docich.hanjuku.judgment.battle import _summoner_return_risk
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
EVIDENCE = json.loads((ROOT / 'g747-live-shield-evidence.json').read_text())


def native(uid):
    item = next(e for e in EVIDENCE['evidence']
                if e['decision']['decision_id'].endswith(':' + str(uid)))
    frame = read_png(ROOT / item['fixture'])
    assert frame.digest() == item['rgb_sha256'] == item['decision']['frame_sha256']
    return parse(frame)


def memory():
    mem = {'chapter': 1, '_run_identity': deepcopy(EVIDENCE['identity']),
           'attack': {'castle': 'ナキューメラ', 'side': 'defense'}}
    p.battle_step(native(6757), mem)
    p.battle_step(native(6758), mem)
    return mem


def test_native_return_has_exact_inputs_and_later_exposes_general():
    for uid, buttons in [(6855, ['down']), (6856, ['down']), (6857, ['a'])]:
        e = next(e for e in EVIDENCE['evidence']
                 if e['decision']['decision_id'].endswith(':' + str(uid)))
        assert any(r['decision_id'] == e['decision']['decision_id']
                   and r['buttons'] == buttons
                   and r['decision_frame_sha256'] == r['frame_sha256'] == e['rgb_sha256']
                   for r in e['input_receipts'])
    s = native(6857)
    assert s.menu_cursor == s.menu_rows[-1].y
    assert [(r.name, r.hp) for r in native(6866).egg_rows if r.side == 'ally'] == [('ガルバンゾー', 30)]
    assert native(6924).battle.ally_hp == 0
    # The final panel has no input receipt. Do not infer a death or new win.
    zero = next(e for e in EVIDENCE['evidence'] if e['decision']['decision_id'].endswith(':6924'))
    assert not zero['input_receipts']


def test_ordinary_opening_binds_healthy_general_before_either_summon():
    mem = memory()
    r = mem['battle']['summon_general_reading']
    assert (r['ally'], r['enemy'], r['hp'], r['max_hp']) == ('ガルバンゾー', 'ビシソワーズ', 30, 30)
    assert not p._wounded_summoner(mem, 'ally', *native(6855).egg_rows)
    p.monster_menu_step(native(6855), mem)
    assert mem['monster_menu_choice'] == 'skill2'
    metric = mem['_records'][-1]['observed_metric']
    assert metric['general_return_risk'] is not None
    assert not metric['general_return_blocked']
    assert not metric['wounded_general_return_blocked']
    assert metric['general_return_risk']['rule'] == 'general_vs_monster_hp'
    assert mem['battle']['ally_hp'] == 30  # never replace by Final Jelly76
    assert p.monster_menu_step(native(6857), mem) == [p.pad('up')]


@pytest.mark.parametrize('mode', ['cached', 'learned', 'saved'])
def test_old_return_cannot_override_current_exposure_risk(mode, monkeypatch):
    mem = memory()
    p.monster_menu_step(native(6855), mem)
    mem['monster_menu_choice'] = 'retreat'
    if mode == 'learned':
        monkeypatch.setattr(p.experience, 'preferred', lambda *a, **k: 'retreat')
    if mode == 'saved':
        mem = json.loads(json.dumps(mem))
    assert p.monster_menu_step(native(6857), mem) == [p.pad('up')]
    assert mem['monster_menu_choice'] == 'skill2'


@pytest.mark.parametrize('change', ['missing', 'foreign_lease', 'foreign_generation', 'actor',
                                  'opponent', 'hp', 'bool_hp', 'max', 'bad_identity'])
def test_unbound_or_corrupt_general_cannot_authorize_new_protection(change):
    mem = memory(); cur = mem['battle']; r = cur['summon_general_reading']
    if change == 'missing': cur.pop('summon_general_reading')
    elif change == 'foreign_lease': r['identity']['lease_id'] = 'different'
    elif change == 'foreign_generation': r['identity']['generation'] += 1
    elif change == 'actor': r['ally'] = 'ココット'
    elif change == 'opponent': r['enemy'] = 'タピオカ'
    elif change == 'hp': cur['ally_hp'] = None
    elif change == 'bool_hp': r['hp'] = cur['ally_hp'] = True
    elif change == 'max': r['max_hp'] = 29
    else: r['identity'] = {'runtime_id': 'x', 'generation': True, 'lease_id': 'y'}
    p.monster_menu_step(native(6855), mem)
    assert mem['monster_menu_choice'] == 'skill2'


@pytest.mark.parametrize('own_hp,enemy_hp,expected', [(30,59,False), (30,60,True),
                                                   (30,193,True), (100,193,False)])
def test_exposure_boundary_uses_general_and_enemy_hp_pools(own_hp, enemy_hp, expected):
    mem = memory(); cur = mem['battle']; cur['ally_hp'] = own_hp
    cur['summon_general_reading'].update(hp=own_hp, max_hp=own_hp)
    s = native(6855); ally, enemy = s.egg_rows
    enemy = type(enemy)(enemy.name, enemy_hp, enemy.side, enemy.y)
    assert bool(_summoner_return_risk(mem, 'ally', ally, enemy)) is expected


@pytest.mark.parametrize('owner', ['enemy', 'unknown', None])
def test_other_menu_owner_cannot_protect_from_our_reading(owner):
    mem = memory()
    assert _summoner_return_risk(mem, owner, *native(6855).egg_rows) is None


@pytest.mark.parametrize('side,hp', [('ally', 0), ('enemy', 0), ('ally', None), ('enemy', True)])
def test_both_monsters_must_be_live_and_read(side, hp):
    rows = native(6855).egg_rows
    rows = [type(r)(r.name, hp, r.side, r.y) if r.side == side else r for r in rows]
    assert _summoner_return_risk(memory(), 'ally', *rows) is None


def test_current_native_general_panel_refreshes_binding_but_old_memory_is_not_backfilled():
    mem = memory(); cur = mem['battle']; cur.pop('summon_general_reading')
    p.monster_menu_step(native(6855), mem)
    assert mem['monster_menu_choice'] == 'skill2'
    assert 'summon_general_reading' not in cur
    assert battle_menus._egg_general_reading(native(6866), mem, cur) == (30, 30)
    p.monster_menu_step(native(6855), mem)
    assert mem['monster_menu_choice'] == 'skill2'
    # This reordered replay is a counterfactual, not the original chronology.


def test_later_complete_general_hp_replaces_reading_and_zero_clears_it():
    mem = memory(); cur = mem['battle']
    s = native(6758); s.battle = type(s.battle)('ビシソワーズ', 27, 'ガルバンゾー', 14)
    battle._card_battle_reading(s, cur, mem)
    assert cur['summon_general_reading']['hp'] == cur['ally_hp'] == 14
    battle._card_battle_reading(native(6924), cur, mem)
    assert 'summon_general_reading' not in cur


def test_powerless_and_balloon_rules_remain_and_zero_egg_is_not_enabled():
    from test_hanjuku_chart_bot import monster_menu_frame
    mem = memory()
    screen = parse(monster_menu_frame(['なぐれっ!', 'かきむしれ!'],
                   ally=('ウゴカザル', 76), enemy=('コマイヌ', 193), cursor=0))
    p.monster_menu_step(screen, mem)
    assert mem['monster_menu_choice'] == 'retreat'
    mem = memory()
    screen = parse(monster_menu_frame(['ふくらむ', 'シャウト'],
                   ally=('バルーンフィンチ', 1), enemy=('コマイヌ', 193), cursor=0))
    p.monster_menu_step(screen, mem)
    assert mem['monster_menu_choice'] == 'skill1'
    mem['egg_uses'] = {'ガルバンゾー': 0}
    assert p._own_egg_empty(mem, mem['battle'])
