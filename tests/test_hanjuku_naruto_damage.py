"""Original g749 menus; policy replay is counterfactual, not a new live win."""
from dataclasses import replace
import json
from pathlib import Path
import pytest
from docich.hanjuku.flows.battle_menus import monster_menu_step
from docich.hanjuku.judgment.events import _monster_damage_first
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
EVIDENCE = json.loads((ROOT / 'g749-naruto-evidence.json').read_text())

def native(uid):
    row = next(r for r in EVIDENCE['evidence'] if r['decision']['decision_id'].endswith(':'+str(uid)))
    frame = read_png(ROOT / f'g749-naruto-{uid}.png')
    assert frame.digest() == row['rgb_sha256'] == row['decision']['frame_sha256']
    return parse(frame)

def memory():
    return {'chapter': 1, '_run_identity': EVIDENCE['active'], 'battle': {'ally': 'ヴィーナス', 'enemy': 'クイーン',
            'ally_hp': 23, 'enemy_hp': 70, 'side': 'attack', 'castle': 'けっかい',
            'summon_general_reading': {'identity': EVIDENCE['active'], 'ally':'ヴィーナス',
                                      'enemy':'クイーン','hp':23,'max_hp':82}}}

@pytest.mark.parametrize('uid,move', [(8549,'a'),(8550,'up'),(8562,'a'),(8563,'up')])
def test_native_owned_menu_chooses_damage_and_preserves_wounded_shield(uid,move):
    s=native(uid);m=memory()
    assert monster_menu_step(s,m)==[pad(move)]
    assert m['monster_menu_choice']=='skill1'
    assert m['_records'][-1]['observed_metric']['damage_first'] is True
    if uid in (8562,8563):
        assert m['_records'][-1]['observed_metric']['general_return_blocked'] is True

def test_hotload_saved_same_hp_old_swirl_choice_is_invalidated():
    s=native(8550);m=memory();monster_menu_step(s,m)
    m['monster_menu_choice']='skill2'
    m=json.loads(json.dumps(m))
    assert monster_menu_step(s,m)==[pad('up')]
    assert m['monster_menu_choice']=='skill1'

def test_healthy_general_can_still_return_low_hp_monster():
    s=native(8562);m=memory();m['battle']['ally_hp']=82
    m['battle']['summon_general_reading']['hp']=82
    # Counterfactual opponent small enough to remove summoner return risk.
    s.egg_rows=[replace(r,hp=160) if r.side=='enemy' else replace(r,hp=8) for r in s.egg_rows]
    assert monster_menu_step(s,m)==[pad('down')]
    assert m['monster_menu_choice']=='retreat'

def test_enemy_owned_native_menu_waits():
    s=native(8551);m=memory()
    assert monster_menu_step(s,m)==[]
    assert not m.get('monster_menu_choice')

@pytest.mark.parametrize('side,hp',[('ally',None),('enemy',None),('ally',0),('enemy',0),('ally',True),('enemy',True)])
def test_unread_dead_or_bool_hp_does_not_enable_rule(side,hp):
    s=native(8549);s.egg_rows=[replace(r,hp=hp) if r.side==side else r for r in s.egg_rows]
    panel={r.side:r for r in s.egg_rows}
    assert not _monster_damage_first('ally',panel['ally'],panel['enemy'],['ぎざぎざ','うずまき'])

@pytest.mark.parametrize('owner',['enemy',None])
def test_owner_must_be_known_ally(owner):
    panel={r.side:r for r in native(8549).egg_rows}
    assert not _monster_damage_first(owner,panel['ally'],panel['enemy'],['ぎざぎざ','うずまき'])

@pytest.mark.parametrize('skills',[['ぎざぎざ'],['うずまき','ぎざぎざ'],['ぎざぎざ','うずま'],['ぎざぎざ','うずまき','ふくらむ']])
def test_complete_ordered_pair_required(skills):
    panel={r.side:r for r in native(8549).egg_rows}
    assert not _monster_damage_first('ally',panel['ally'],panel['enemy'],skills)

@pytest.mark.parametrize('enemy',['クイーン','不明','ヴィーナス'])
def test_general_and_unknown_opponent_keep_existing_policy(enemy):
    panel={r.side:r for r in native(8549).egg_rows}
    assert not _monster_damage_first('ally',panel['ally'],replace(panel['enemy'],name=enemy),['ぎざぎざ','うずまき'])

def test_learned_status_skill_cannot_restore_repeat(monkeypatch):
    from docich.hanjuku.flows import battle_menus
    monkeypatch.setattr(battle_menus.experience,'preferred',lambda *a,**kw:'skill2')
    m=memory()
    assert monster_menu_step(native(8550),m)==[pad('up')]
    assert m['monster_menu_choice']=='skill1'

def test_unread_cursor_after_owner_change_waits_for_new_measurement():
    m=memory();monster_menu_step(native(8550),m)
    s=native(8550);s.menu_cursor=None
    s.egg_rows=[replace(r,name='ユニコーン') if r.side=='ally' else r for r in s.egg_rows]
    assert monster_menu_step(s,m)==[]
    assert m['monster_menu_cursor_recheck'] is True
