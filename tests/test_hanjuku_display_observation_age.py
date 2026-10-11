"""Display evidence expires independently of policy decisions and estimates."""
from types import SimpleNamespace
from docich import hanjuku_policy as policy


def test_reobserving_the_same_garrison_refreshes_only_display_evidence(monkeypatch):
    monkeypatch.setattr(policy.time, 'time', lambda: 1000.0)
    monkeypatch.setattr(policy, '_present_generals', lambda screen: ['ゼウス'])
    monkeypatch.setattr(policy, '_source', lambda order, mem: 'アルマムーン')
    mem={'garrison':{'アルマムーン':['ゼウス']}, 'garrison_observed_at':{'アルマムーン':990.0}}
    policy._observe_garrison(SimpleNamespace(), mem, {})
    assert mem['garrison_observed_at']['アルマムーン']==1000.0
    assert mem['garrison']['アルマムーン']==['ゼウス']
    policy._garrison_move(mem,'ゼウス',source='アルマムーン',target='カストーラ')
    assert 'アルマムーン' not in mem['garrison_observed_at']
    assert 'カストーラ' not in mem['garrison_observed_at']
    assert mem['garrison']['カストーラ']==['ゼウス']


def test_partial_hp_panel_does_not_make_old_hp_fresh(monkeypatch):
    monkeypatch.setattr(policy.time, 'time', lambda: 1000.0)
    battle=SimpleNamespace(enemy='敵',ally='ゼウス',enemy_hp=50,ally_hp=80)
    cur={'enemy':'敵','ally':'ゼウス'}
    screen=SimpleNamespace(kind='battle',battle=battle,field_soldiers=None)
    policy._card_battle_reading(screen,cur)
    assert cur['hp_observed_at']==1000.0
    battle.enemy_hp=None
    monkeypatch.setattr(policy.time, 'time', lambda: 2000.0)
    policy._card_battle_reading(screen,cur)
    assert cur['hp_observed_at']==1000.0
    assert cur['card_hp_unread'] is True
