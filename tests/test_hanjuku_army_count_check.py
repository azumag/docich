"""Unknown army totals are inspected without paying; actual zero gets a refill."""
from copy import deepcopy
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows import month as monthly
from docich.hanjuku.strategy import economy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import month_canvas
from test_hanjuku_soldier_receipts import quantity

IDENTITY = {'game':'hanjuku-hero','runtime_id':'g745-18f84db7',
            'generation':745,'lease_id':'4abd000e-9826-4033-984b-ac6432655332'}


def memory(gold=72):
    return {'chapter':2,'month':'1-8','gold':gold,'_run_identity':deepcopy(IDENTITY),
            'shop':{'key':'1-8','items':[],'merchant_done':True,'gold_start':gold,
                    'soldiers':0,'soldiers_target':0,'soldiers_done':True,
                    'soldiers_from_gold':True,'egg':'done','reserve':0,
                    'recruit':'check','recruit_priority':True,'recruit_reserve':42,
                    'chikujou':'skipped'}}


def screen(gold=72, on='へいしほじゅう'):
    return parse(month_canvas(gold, month=8, on=on))


def observe_count(mem, count, gold=72):
    s=quantity(gold=gold);s.text+='へいしすうは'+str(count)+'めいです'
    p.observe_events(s,mem)
    return s


@pytest.mark.parametrize('gold',[0,30,72])
def test_zero_purchase_plan_still_opens_a_free_count_check(gold):
    mem=memory(gold)
    assert p.month_step(screen(gold),mem)==[p.pad('a')]
    assert mem['army_count_check']['stage']=='requested'
    assert not mem['shop'].get('soldier_receipt')
    # Same parent still present must never receive a second A confirmation.
    for _ in range(3):assert p.month_step(screen(gold),mem)==[]
    assert p.month_step(screen(gold),mem)==[p.pad('b')]
    assert mem['army_count_check']['result']=='unverified'
    assert mem.get('soldiers_seen') is None


def test_read_zero_cancels_inspection_then_replaces_adjusted_zero_with_affordable_army():
    mem=memory()
    p.month_step(screen(),mem)
    s=observe_count(mem,0)
    assert p.month_step(s,mem)==[p.pad('b')]
    assert mem['army_count_check']['result']=='measured'
    assert not mem['shop'].get('soldier_receipt')
    assert mem['shop']['soldiers']==42  # 72G minus full30G wage floor
    assert mem['shop']['recruit']=='deferred_army'
    assert mem['shop']['recruit_reserve']==0
    assert p.month_step(screen(),mem)==[p.pad('a')]
    assert mem['soldiers_seen']==0  # a new A is not a purchase receipt


def test_a_positive_total_is_not_replaced_by_zero_or_a_refill():
    mem=memory();p.month_step(screen(),mem)
    assert p.month_step(observe_count(mem,60),mem)==[p.pad('b')]
    assert mem['soldiers_seen']==60 and mem['shop']['soldiers']==0
    assert not mem['shop'].get('soldier_receipt')
    assert economy._soldier_count_current(mem,screen().header)


@pytest.mark.parametrize('count',[None,True,-1])
def test_unread_editor_never_confirms_its_displayed_quantity(count):
    mem=memory();p.month_step(screen(),mem)
    if count is not None:mem['soldiers_seen']=count
    for _ in range(3):assert p.quantity_step(quantity(),mem,soldiers=True)==[]
    mem=json.loads(json.dumps(mem))
    assert p.quantity_step(quantity(),mem,soldiers=True)==[p.pad('b')]
    assert mem['army_count_check']['result']=='unverified'
    assert not mem['shop'].get('soldier_receipt')
    assert mem.get('soldiers_seen') is count


@pytest.mark.parametrize('change',['month','chapter','lease','unscoped'])
def test_historical_even_same_month_count_does_not_skip_current_check(change):
    mem=memory();observe_count(mem,60)
    if change=='month':mem['soldiers_seen_key']='1-7'
    elif change=='chapter':mem['soldiers_seen_scope'][1]=1
    elif change=='lease':mem['soldiers_seen_scope'][0]['lease_id']='foreign'
    else:mem.pop('soldiers_seen_scope')
    assert p.month_step(screen(),mem)==[p.pad('a')]
    assert 'soldiers_seen_key' not in mem


def test_no_runtime_identity_or_header_cannot_bind_a_count_inspection():
    mem=memory();mem.pop('_run_identity')
    assert monthly._month_army_count_check(screen(),mem,mem['shop']) is None
    s=screen();s.header=None
    assert monthly._month_army_count_check(s,memory(),{}) is None


@pytest.mark.parametrize('gold,reserve,wages,expected',[(45,15,30,0),(100,50,30,20),
                                                    (100,0,62,38),(500,50,30,99)])
def test_zero_refill_keeps_egg_repair_and_complete_wages(monkeypatch,gold,reserve,wages,expected):
    mem=memory(gold);observe_count(mem,0,gold)
    shop=mem['shop'];shop['reserve']=reserve
    monkeypatch.setattr(economy,'_egg_recovery_wage_floor',lambda m:wages)
    economy._zero_army_refill(mem,shop,screen(gold).header)
    assert shop['soldiers']==expected
    assert not shop.get('soldier_receipt')


@pytest.mark.parametrize('block',['payment','ritual','unverified_recruit','unknown_price'])
def test_live_or_unverified_transaction_is_never_rewritten(block):
    mem=memory();observe_count(mem,0);shop=mem['shop']
    if block=='payment':shop['soldier_receipt']={'status':'pending'}
    elif block=='ritual':mem['month_sub']={'kind':'egg'}
    elif block=='unverified_recruit':shop['recruit']='unverified'
    else:shop.update(merchant_done=False,items=[['未測定',1]])
    before=deepcopy(shop)
    economy._zero_army_refill(mem,shop,screen().header)
    assert shop==before


def test_inspection_exit_must_reach_quiet_parent_before_any_refill_confirmation():
    mem=memory();mem['shop'].update(soldiers=24,soldiers_done=False)
    p.month_step(screen(),mem)
    q=observe_count(mem,0)
    assert p.month_step(q,mem)==[p.pad('b')]
    for _ in range(3):assert p.quantity_step(q,mem,soldiers=True)==[p.pad('b')]
    for _ in range(5):assert p.quantity_step(q,mem,soldiers=True)==[]
    assert not mem['shop'].get('soldier_receipt')
    assert p.month_step(screen(),mem)==[p.pad('a')]
    assert mem['army_count_check']['stage']=='measured'


@pytest.mark.parametrize('value',[None,True,-1,'bad',10000])
def test_corrupt_saved_read_budget_cannot_refill_or_confirm(value):
    mem=memory();p.month_step(screen(),mem)
    mem['army_count_check']['quantity_reads']=value
    assert p.quantity_step(quantity(),mem,soldiers=True)==[p.pad('b')]
    assert mem['army_count_check']['result']=='unverified'
    assert not mem['shop'].get('soldier_receipt')


def test_measured_empty_army_gets_a_plan_even_before_a_future_chart_purchase(monkeypatch):
    mem=memory();observe_count(mem,0);mem.pop('shop')
    monkeypatch.setattr(economy,'_charted_purchase_ahead',lambda *a:True)
    monkeypatch.setattr(p.chart,'purchase_for',lambda *a:None)
    shop=economy._plan(mem,screen().header)
    assert shop is not None
    economy._zero_army_refill(mem,shop,screen().header)
    assert shop['soldiers']==42 and shop['soldiers_done'] is False


def test_real_policy_snapshot_distinguishes_unread_stock_from_zero_purchase_plan():
    from pathlib import Path
    d=json.loads((Path(__file__).parent/'fixtures/hanjuku/g745-unread-army-plan.json').read_text())
    assert d['soldiers_seen'] is None and d['soldiers_seen_key'] is None
    assert d['shop']['soldiers']==0 and d['shop']['soldiers_done'] is True
    assert d['kind']=='read_only_policy_snapshot_not_an_observed_army_total'


def test_reopened_unread_editor_and_corrupt_stage_cannot_pay_old_plan():
    for stage in ('unverified','unexpected'):
        mem=memory();p.month_step(screen(),mem)
        mem['shop'].update(soldiers=24,soldiers_done=False)
        mem['army_count_check']['stage']=stage
        assert p.quantity_step(quantity(),mem,soldiers=True)==[p.pad('b')]
        assert not mem['shop'].get('soldier_receipt')


def test_unknown_digit_cannot_collapse_to_a_fresh_zero_count():
    from docich.hanjuku_font import TextLine, UNKNOWN
    mem=memory();s=quantity()
    text='へいしすうは0'+UNKNOWN+'めいです'
    s.lines.append(TextLine(135,tuple((24+i*8,c) for i,c in enumerate(text))))
    s.text+='へいしすうは0めいです'  # known-only text hides the unread digit
    p.observe_events(s,mem)
    assert mem.get('soldiers_seen') is None
    assert not economy._soldier_count_current(mem,s.header)
    assert mem['soldiers_seen_basis']=='unread_total'


def test_conflicting_total_rows_invalidate_freshness_without_claiming_zero():
    mem=memory();observe_count(mem,60)
    s=quantity();s.text+='へいしすうは0めいですへいしすうは60めいです'
    p.observe_events(s,mem)
    assert mem['soldiers_seen']==60  # history only
    assert not economy._soldier_count_current(mem,s.header)


@pytest.mark.parametrize('old_state',[None,True,{'scope':['foreign'],'stage':'measured'}])
def test_hotload_inside_an_unknown_editor_starts_an_inspection_without_payment(old_state):
    mem=memory();mem['shop'].update(soldiers=24,soldiers_done=False)
    mem['army_count_check']=old_state
    for _ in range(3):assert p.quantity_step(quantity(),mem,soldiers=True)==[]
    assert p.quantity_step(quantity(),mem,soldiers=True)==[p.pad('b')]
    assert not mem['shop'].get('soldier_receipt')


def test_corrupt_closing_result_cannot_become_a_measured_total():
    mem=memory();p.month_step(screen(),mem)
    mem['army_count_check'].update(stage='closing');mem['army_count_check'].pop('result',None)
    monthly._month_army_count_check(screen(),mem,mem['shop'])
    assert mem['army_count_check']['stage']=='unverified'
    assert mem.get('soldiers_seen') is None


def test_native_count_intro_a_only_opens_editor_then_inspection_uses_b():
    from pathlib import Path
    from docich.hanjuku_pixels import read_png
    root=Path(__file__).parent/'fixtures/hanjuku'
    d=json.loads((root/'g741-army-count-evidence.json').read_text())
    frames={}
    for e in d['evidence']:
        frame=read_png(root/e['fixture'])
        assert frame.digest()==e['rgb_sha256']==e['decision']['frame_sha256']
        uid=int(e['decision']['decision_id'].rsplit(':',1)[1]);frames[uid]=parse(frame)
    # Original A at the count question did not pay; editor still shows131G.
    e=next(e for e in d['evidence'] if e['decision']['decision_id'].endswith(':7458'))
    assert any(r['buttons']==['a'] and r['decision_id']==e['decision']['decision_id']
               and r['decision_frame_sha256']==r['frame_sha256']==e['rgb_sha256']
               for r in e['input_receipts'])
    assert frames[7458].header['gold']==frames[7460].header['gold']==131
    mem=memory(131);mem.update(month='2-2',_run_identity={k:e['decision'][k]
                             for k in ('game','runtime_id','generation','lease_id')})
    mem['shop']['key']='2-2'
    p.observe_events(frames[7458],mem)
    assert mem['soldiers_seen']==0
    assert monthly._month_army_count_check(frames[7458],mem,mem['shop'])==[p.pad('a')]
    for _ in range(3):assert monthly._month_army_count_check(frames[7458],mem,mem['shop'])==[]
    assert p.quantity_step(frames[7460],mem,soldiers=True)==[p.pad('b')]
    assert not mem['shop'].get('soldier_receipt')
    assert mem['army_count_check']['result']=='measured'
    # Original7460 used A and paid1G; the new B above is counterfactual,
    # not a naturally accepted cancellation or purchase receipt.
