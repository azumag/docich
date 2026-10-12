"""Native Finch-at-cap/general-at-one must not repeat nonlethal Shout."""
from copy import deepcopy
from pathlib import Path

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import monster_menu_frame

F=Path(__file__).parent/'fixtures/hanjuku/g713-finch-full-general-one.png'
SHA='a7c4c2c35907ce9626d77959b92b2d9d972063b24ea592e1865607db531584e9'


def memory():
    return {'chapter':1,'battle':{'ally':'ココット','enemy':'クミン','ally_hp':24,
                                'enemy_hp':27,'side':'defense','castle':'スペンソニア'}}


def test_native_at_cap_returns_to_general_without_shout_or_battle_retreat():
    frame=read_png(F); assert frame.digest()==SHA
    s=parse(frame); assert [(r.name,r.hp,r.side) for r in s.egg_rows]==[
        ('バルーンフィンチ',9999,'ally'),('クミン',1,'enemy')]
    mem=memory(); before=deepcopy(mem['battle'])
    assert p.monster_menu_step(s,mem)==[p.pad('down')]
    assert mem['monster_menu_choice']=='retreat' and mem['monster_general_finish']
    assert mem['battle']['enemy_hp']==before['enemy_hp']  # not a human-panel receipt
    assert not mem['battle'].get('hero_retreat_selected')
    r=mem['_records'][-1]; assert r['observed_metric']['finish_general']
    assert '白兵' in r['reason'] and 'たまごに もどれ' in r['reason']


def test_hotloaded_same_hp_old_shout_choice_is_reconsidered():
    mem=memory(); s=parse(read_png(F))
    mem.update(monster_menu_key=tuple(''.join(l.known.split()) for l in s.menu_rows),
               monster_menu_choice='skill2',monster_menu_choice_hp=(9999,1))
    p.monster_menu_step(s,mem)
    assert mem['monster_menu_choice']=='retreat'


@pytest.mark.parametrize('bad',['not_full','enemy_two','enemy_unknown','enemy_monster','enemy_boss',
                               'actor_unknown','actor_hero','human_low','human_unknown','different_enemy',
                               'missing_return','wrong_skill','no_battle'])
def test_unverified_or_risky_finish_keeps_existing_monster_policy(bad):
    mem=memory(); frame=monster_menu_frame(['ふくらむ','シャウト'],
                          ally=('バルーンフィンチ',9999),enemy=('クミン',1),cursor=0)
    s=parse(frame)
    if bad=='not_full': s.egg_rows[0].hp=9998
    elif bad=='enemy_two': s.egg_rows[1].hp=2
    elif bad=='enemy_unknown': s.egg_rows[1].name='？？？'
    elif bad=='enemy_monster': s.egg_rows[1].name='エッグスライム'
    elif bad=='enemy_boss': s.egg_rows[1].name='クイーン';mem['battle']['enemy']='クイーン'
    elif bad=='actor_unknown': mem['battle']['ally']='？？？'
    elif bad=='actor_hero': mem['battle']['ally']=p.NAME
    elif bad=='human_low': mem['battle']['ally_hp']=9
    elif bad=='human_unknown': mem['battle']['ally_hp']=None
    elif bad=='different_enemy': mem['battle']['enemy']='コリアンダー'
    elif bad=='missing_return': s.menu_rows=s.menu_rows[:2]
    elif bad=='wrong_skill': s=parse(monster_menu_frame(['ふくらむ','マサムネる'],
                    ally=('バルーンフィンチ',9999),enemy=('クミン',1),cursor=0))
    else:mem.pop('battle')
    p.monster_menu_step(s,mem)
    assert not mem.get('monster_general_finish')


def test_changed_context_drops_cached_finish_without_a_stale_return():
    mem=memory();s=parse(read_png(F));p.monster_menu_step(s,mem)
    s.egg_rows[1].name='エッグスライム'
    p.monster_menu_step(s,mem)
    assert mem['monster_menu_choice']=='skill2' and not mem['monster_general_finish']


def test_return_follows_actual_cursor_and_confirms_only_return_row():
    mem=memory()
    for cursor,button in [(0,'down'),(1,'down'),(2,'a')]:
        frame=monster_menu_frame(['ふくらむ','シャウト'],
                    ally=('バルーンフィンチ',9999),enemy=('クミン',1),cursor=cursor)
        assert p.monster_menu_step(parse(frame),mem)==[p.pad(button)]
    assert not mem['battle'].get('hero_retreat_selected')
