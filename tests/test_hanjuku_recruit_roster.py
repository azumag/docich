from docich import hanjuku_policy as p
from docich import hanjuku_house as house
from docich.hanjuku_screen import Screen
import pytest


def memory():
    return {'chapter':1, 'tick':100, '_records':[], 'garrison':{'ほんじょう':['one']},
            'recruit_roster_floor':{'chapter':1,'tick':95,'count':8}}


def test_fresh_roster_stops_old_shortage_and_surplus_recruitment(monkeypatch):
    mem=memory()
    shop={'key':'2-8','recruit':'check','recruit_priority':True,'recruit_budget_version':1,
          'recruit_reserve':50, 'soldiers':99, 'soldiers_done':True}
    assert p._recruit_shortage(mem) is None
    p._prioritise_recruit(mem,shop,500)
    assert shop['recruit']=='not_needed' and not shop['recruit_priority'] and shop['recruit_reserve']==0
    called=[]
    monkeypatch.setattr(p,'menu_to',lambda screen,name:called.append(name))
    p._month_extra(Screen(lines=[],hand=None,kind='month_menu',text='',header={'gold':500}),mem,shop)
    assert 'しょうぐんぼしゅう' not in called


@pytest.mark.parametrize('field,value',[('tick',-1000),('tick',101),('count',5),('count',True),('chapter',2)])
def test_stale_future_incomplete_or_other_chapter_cannot_prove_enough(field,value):
    mem=memory();mem['recruit_roster_floor'][field]=value
    assert not p._recruit_sufficient(mem)
    assert p._recruit_shortage(mem)['count_lower_bound']==1


def test_paid_or_started_recruit_not_cancelled_or_recounted():
    for status in ['opened','done','unverified']:
        shop={'recruit':status,'recruit_reserve':50}
        p._prioritise_recruit(memory(),shop,500)
        assert shop=={'recruit':status,'recruit_reserve':50}


def test_roster_observation_uses_unique_visible_names_not_old_house_cache(monkeypatch):
    mem={'chapter':1,'tick':100,'_records':[]}
    screen=Screen(lines=[],hand=None,kind='text',text='',header=None)
    monkeypatch.setattr(house,'roster',lambda screen:['a','b','c','d','e','f'])
    p.observe_events(screen,mem)
    assert p._recruit_sufficient(mem)
    assert mem['recruit_roster_floor']=={'chapter':1,'tick':101,'count':6}
    # A smaller later page cannot establish that the whole roster shrank.
    monkeypatch.setattr(house,'roster',lambda screen:['a','b'])
    p.observe_events(screen,mem)
    assert mem['recruit_roster_floor']['tick']==101
    assert p._recruit_sufficient(mem)
