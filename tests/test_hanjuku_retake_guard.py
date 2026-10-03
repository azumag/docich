"""Lost castles must not draw every general out of their remaining castles."""
from copy import deepcopy
import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen


def memory():
    o = {'step': 'I:retake:1', 'general': 'ゼウス', 'source': 'アルマムーン',
         'target': 'ジョンリギ', 'purpose': 'retake', 'cards': [], 'after': None, 'note': 't'}
    return {'chapter': 1, 'tick': 100, 'captured': ['カストーラ', 'キカンドン'],
            'lost': ['ジョンリギ'], 'active': o['step'], 'picked': [], '_records': [],
            'garrison': {'アルマムーン': ['ゼウス', 'どうし'], 'カストーラ': ['ヴィーナス'],
                         'キカンドン': ['ココット']}, 'orders': {},
            'launched_orders': {o['step']: o}}


def generals(names):
    lines = [TextLine(39+16*i, tuple((144+8*j,c) for j,c in enumerate(n)))
             for i,n in enumerate(names)]
    return Screen(lines=lines, hand=(122,33,139,45), text=''.join(names) if names else 'おりません', kind='general_list')


@pytest.mark.parametrize('status', ['en_route', 'launched_unconfirmed'])
def test_recent_retake_reserves_target_in_candidates_and_current_order(status):
    m=memory();m['sorties']={'earlier': {'general':'ヴィーナス','target':'ジョンリギ',
                                       'status':status,'tick':99}}
    assert not any(o['target']=='ジョンリギ' for o in p.interim_candidates(m).values())
    o=p._order(m);assert p._retake_reserved(o,m)
    assert p.deploy_step(Screen(kind='sortie_confirm',lines=[],hand=None,text=''),m)==[p.pad('b'),p.pad('b')]
    assert m['active'] is None and m['orders'][o['step']]=='failed'
    assert m['_records'][-1]['decision']=='sortie_held_target_reserved'


def test_interrupted_unknown_target_reserves_only_saved_intent_without_editing_sortie():
    m=memory();m['sorties']={'earlier':{'general':'ヴィーナス','target':None,
                                     'status':'launched_unconfirmed','tick':99}}
    m['launched_orders']['earlier']={'target':'ジョンリギ'}
    before=deepcopy(m['sorties']);assert p._retake_reserved(p._order(m),m)
    assert not any(o['target']=='ジョンリギ' for o in p.interim_candidates(m).values())
    assert m['sorties']==before


@pytest.mark.parametrize('status,tick', [('arrived',99),('failed',99),('en_route',None),
                                         ('en_route',100-p.SORTIE_BUSY_TICKS),('en_route',101)])
def test_finished_old_missing_or_future_receipts_do_not_reserve(status,tick):
    m=memory();m['sorties']={'earlier':{'general':'ヴィーナス','target':'ジョンリギ',
                                     'status':status,'tick':tick}}
    assert not p._retake_reserved(p._order(m),m)
    assert any(o['target']=='ジョンリギ' for o in p.interim_candidates(m).values())


def test_own_sortie_receipt_is_not_a_duplicate():
    m=memory();o=p._order(m);m['sorties']={o['step']:{'general':'ゼウス','target':'ジョンリギ',
                                                 'status':'launched_unconfirmed','tick':99}}
    assert not p._retake_reserved(o,m)


@pytest.mark.parametrize('names', [['ゼウス'], [], ['ゼウス','ゼウス']])
def test_live_retake_list_cannot_send_the_last_defender(names):
    m=memory();assert p.deploy_step(generals(names),m)==[p.pad('b'),p.pad('b')]
    assert m['_records'][-1]['decision']=='sortie_held_source_defender'
    assert m['orders']['I:retake:1']=='failed'


def test_live_spare_can_leave_and_then_no_further_attack_drains_source():
    m=memory();assert p.deploy_step(generals(['ゼウス','どうし']),m)==[p.pad('a')]
    p._garrison_move(m,'ゼウス',source='アルマムーン')
    m['sorties']={'I:retake:1':{'general':'ゼウス','target':'ジョンリギ','status':'en_route','tick':100}}
    assert not any(o['source']=='アルマムーン' for o in p.interim_candidates(m).values())
    assert m['garrison']['アルマムーン']==['どうし']


def test_busy_or_unclassified_general_is_not_a_remaining_defender():
    m=memory();m['sorties']={'busy':{'general':'どうし','target':'ゴーメン','status':'en_route','tick':99}}
    assert p.deploy_step(generals(['ゼウス','どうし']),m)==[p.pad('b'),p.pad('b')]


def test_unread_list_does_not_confirm_a_sortie():
    m=memory();actions=p.deploy_step(Screen(kind='general_list',lines=[],hand=None,text=''),m)
    assert not any('a' in a.get('buttons',()) for a in actions)
    assert m['active']=='I:retake:1'


def test_old_active_card_menu_is_rechecked_after_hotload():
    m=memory();m['garrison']['アルマムーン']=['ゼウス']
    assert p.deploy_step(Screen(kind='card_select',lines=[],hand=None,text=''),m)==[p.pad('b'),p.pad('b')]


def test_chart_recapture_stays_pending_and_does_not_block_staffing(monkeypatch):
    m=memory();o=m['launched_orders'].pop('I:retake:1');o['step']='1-A1'
    m['active']='1-A1';m['launched_orders']['1-A1']=o
    m['chart_plan']={'orders':[o]};m['garrison']['アルマムーン']=['ゼウス']
    monkeypatch.setattr(p,'_orders',lambda _m:(o,))
    monkeypatch.setattr(p.chart,'orders',lambda _chapter:(o,))
    assert not p._plan_pending(m)
    assert p.next_order(m) is None
    assert p.deploy_step(generals(['ゼウス']),m)==[p.pad('b'),p.pad('b')]
    assert m['orders']['1-A1']=='pending'
    m['garrison']['アルマムーン'].append('どうし')
    assert p.next_order(m) is o


def test_boss_waves_and_original_non_recapture_chart_remain_exempt():
    m=memory();o=p._order(m);o['target']='けっかい';o['purpose']='retake'
    m['lost'].append('けっかい');m['garrison']['アルマムーン']=['ゼウス']
    assert not p._reserve_source_guard(o,m) and not p._retake_reserved(o,m)
    o['target']='ゴーメン';o['purpose']='attack';o['step']='1-A2'
    assert not p._reserve_source_guard(o,m)
