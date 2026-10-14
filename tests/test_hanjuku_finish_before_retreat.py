"""Low-HP resources are evaluated by damage, never by an HP-ratio win claim."""
import pytest
from docich.hanjuku.flows.battle import _hero_retreat_open
from docich.hanjuku.flows.battle_menus import monster_menu_step, _hero_retreat_menu
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import monster_menu_frame
from test_hanjuku_card_damage_gate import memory, cards
from test_hanjuku_survival import menu


def armed(*,hp=1,enemy_hp=11,soldiers=0):
    mem=memory('ゼンマイン',enemy='ソーピニヨン',enemy_hp=enemy_hp,
               ally='どうし',ally_hp=hp,soldiers=(0,soldiers))
    cur=mem['battle'];cur['card_flow']=None
    from docich.hanjuku.judgment.battle import _survival_state
    _survival_state(mem,cur)['listed_cards']=['ゼンマイン']
    return mem


def test_hp_one_vs_eleven_keeps_an_observed_lethal_card_instead_of_retreat():
    mem=armed();cur=mem['battle']
    assert _hero_retreat_open(mem,cur) is None
    assert _hero_retreat_menu(menu(('きりふだ','たまごをつかう','たいきゃく')),mem,cur)[0]['buttons']==['a']
    assert cur['card_flow']['stage']=='list'
    assert not cur.get('hero_retreat')
    from docich import hanjuku_policy as p
    assert p.card_list_step(cards(['ゼンマイン']),mem)==[p.pad('a')]
    assert cur['card_assessment']['damage_lower_bound']>=11
    assert cur['cards_selected']==['ゼンマイン']  # selection, not a live kill


@pytest.mark.parametrize('change',['unknown_list','planned_only','already_selected','used','troops','nonlethal'])
def test_no_observed_finishing_blow_does_not_invent_a_win(change):
    mem=armed();cur=mem['battle']
    if change in ('unknown_list','planned_only'):cur['survival']['listed_cards']=[]
    elif change=='already_selected':cur['cards_selected']=['ゼンマイン']
    elif change=='used':cur['survival']['cards_attempted']=['ゼンマイン']
    elif change=='troops':cur['enemy_soldiers']=6
    else:cur['enemy_hp']=33
    assert _hero_retreat_open(mem,cur) is not None


@pytest.mark.parametrize('budget',[{'cards_checked':3},{'cards_exhausted':True},
    {'exhausted':True,'critical_menu_recheck':True}])
def test_old_lethal_inventory_does_not_disable_retreat_after_bounded_failure(budget):
    mem=armed();cur=mem['battle'];cur['survival'].update(budget)
    assert _hero_retreat_open(mem,cur) is not None


@pytest.mark.parametrize('saved',['cached','roundtrip','learned'])
def test_old_hp_only_monster_retreat_is_invalidated_on_hotload(saved,monkeypatch):
    import json
    from docich import hanjuku_experience as experience
    s=parse(monster_menu_frame(['ダッシュプレス','メガトンプレス'],
              ally=('ローラーキラー',40),enemy=('クイーン',100),cursor=0))
    mem={'chapter':1}
    monster_menu_step(s,mem)
    mem['monster_menu_choice']='retreat'
    if saved=='roundtrip':mem=json.loads(json.dumps(mem))
    if saved=='learned':monkeypatch.setattr(experience,'preferred',lambda *a,**k:'retreat')
    monster_menu_step(s,mem)
    assert mem['monster_menu_choice']=='skill2'
