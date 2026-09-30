"""Demand, complete global scans and cash/payroll constraints for recruitment."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as p, hanjuku_house as house, hanjuku_roster as r
from docich.hanjuku_screen import Screen
import pytest

NAMES = ['どうし', 'ゼウス', 'ヴィーナス', 'ココット', 'バジル', 'ミント',
         'クミン', 'チコリ', 'ビーツ', 'ヘーゼル', 'ラズベリー', 'カシュー',
         'ピスタチオ', 'クランベリー', 'タピオカ']


def memory(names=None, complete=True, castles=1, income=100):
    names = NAMES[:2] if names is None else names
    captured = ['キカンドン', 'ジョンリギ', 'ゴーメン', 'スペンソニア', 'カストーラ'][:castles-1]
    owned = ['アルマムーン', *captured]
    return {'chapter': 1, 'month': '1-7', 'tick': 100, '_records': [],
            'garrison': {'ほんじょう': NAMES[:2]}, 'captured': captured,
            'recruit_roster': {'chapter': 1, 'month': '1-7', 'tick': 95,
                               'names': list(names), 'complete': complete,
                               'wages': {n:r.fixed_wage(n) for n in names}},
            'castle_income': {c:{'chapter':1, 'month':'1-7', 'tick':95, 'income':income} for c in owned}}


def shop():
    return {'key':'1-7', 'recruit':'check', 'recruit_priority':True,
            'recruit_reserve':50, 'reserve':50, 'hero_repair_reserve':50,
            'items':[], 'soldiers':99, 'soldiers_done':False}


def test_six_castles_need_fifteen_and_a_six_member_page_is_not_a_cap():
    mem=memory(NAMES[:6], False, 6)
    assert p._recruit_target(mem)==15
    assert not p._recruit_sufficient(mem) and p._recruit_shortage(mem) is None
    s=shop(); p._prioritise_recruit(mem,s,500)
    assert s['recruit']=='deferred_roster' and s['recruit_reserve']==50
    assert s['reserve']==50 and s['hero_repair_reserve']==50
    assert mem['recruit_roster_recheck']
    mem['recruit_roster']['complete']=True
    assert p._recruit_shortage(mem)['total_roster']==6


def test_partial_lower_bound_at_demand_proves_no_recruit_even_without_wages():
    mem=memory(NAMES, False, 6); mem['recruit_roster'].pop('wages')
    s=shop();p._prioritise_recruit(mem,s,500)
    assert s['recruit']=='not_needed' and s['recruit_reserve']==0
    assert not s['recruit_priority']


@pytest.mark.parametrize('field,value', [('tick',-1000),('tick',101),('tick',True),
                                         ('chapter',2),('month','1-8'),('names',['ゼウス','ゼウス'])])
def test_stale_other_scope_or_duplicate_receipt_is_not_a_shortage(field,value):
    mem=memory();mem['recruit_roster'][field]=value
    assert not p._recruit_sufficient(mem) and p._recruit_shortage(mem) is None
    s=shop();p._prioritise_recruit(mem,s,500)
    assert s['recruit']=='deferred_roster'
    p._prioritise_recruit(mem,s,500)
    assert len(mem['_records'])==1


def test_old_house_cache_paid_headcount_and_garrisons_are_not_global_membership():
    mem=memory(); mem.pop('recruit_roster')
    mem['house_eggs']={n:{} for n in NAMES}
    mem['recruit_roster_floor']={'chapter':1,'tick':100,'count':32}
    mem['recruited']=20
    assert not p._recruit_sufficient(mem) and p._recruit_shortage(mem) is None


def test_started_and_paid_conversation_keeps_reserves_and_finishes():
    for status in ['opened','done','unverified']:
        s={'recruit':status,'recruit_reserve':50}
        p._prioritise_recruit(memory(),s,500)
        assert s=={'recruit':status,'recruit_reserve':50}


def test_roles_can_be_overridden_by_chapter_and_adopted_chart(monkeypatch):
    mem=memory(castles=6)
    monkeypatch.setitem(p.chart.RECRUITMENT_BY_CHAPTER,1,{'per_castle':1,'attack':2})
    assert p._recruit_target(mem)==8
    mem['chart_plan']={'recruitment':{'per_castle':2,'attack':4}}
    assert p._recruit_target(mem)==16
    mem['chart_plan']['recruitment']={'per_castle':True,'attack':3}
    assert p._recruit_target(mem) is None
    mem['captured'].append('unknown castle')
    assert p._recruit_target(mem) is None


def test_receipt_union_requires_one_scan_and_wrap_not_repeated_page():
    mem=memory();state={'chapter':1,'phase':'roster','seen':[]}
    mem['house']=state;r.begin(mem,state)
    r.page(mem,NAMES[:6]);r.page(mem,NAMES[:6]);r.page(mem,NAMES[6:])
    assert mem['recruit_roster']['names']==NAMES
    state['seen']=NAMES[:6]
    assert not r.complete(mem,state)
    state.update(seen=NAMES,roster_wrapped=True)
    assert r.complete(mem,state)
    assert mem['recruit_roster']['complete']
    # A separate one-page visit never inherits the earlier complete union.
    mem.pop('house');r.page(mem,NAMES[:2])
    assert mem['recruit_roster']['names']==NAMES[:2]
    assert not mem['recruit_roster']['complete']


def test_partial_duplicate_page_and_month_join_or_defeat_invalidate():
    mem=memory();state={'chapter':1,'phase':'roster','seen':[]};mem['house']=state;r.begin(mem,state)
    r.page(mem,['ゼウス','ゼウス']);r.page(mem,None)
    assert mem['recruit_roster']['names']==[]
    mem['month']='1-8';r.page(mem,NAMES)
    assert mem['recruit_roster']['names']==[]
    r.invalidate(mem)
    assert r.fresh(mem) is None and state['roster_invalidated']


@pytest.mark.parametrize('income,allowed', [(18,False),(19,True),(100,True)])
def test_normal_income_covers_actual_wages_and_additional_max15(income,allowed):
    mem=memory(income=income) # hero0 + Zeus4 + candidate max15
    assert r.economics(mem,{'アルマムーン'})['wages']==4
    assert p._stop_uneconomic_recruit(mem,shop()) is (not allowed)


def test_unknown_member_or_unobserved_castle_income_is_not_zero():
    for mutate in [lambda m:m['recruit_roster']['wages'].update({'ゼウス':None}),
                   lambda m:m['castle_income'].clear(),
                   lambda m:m['castle_income']['アルマムーン'].update(month='1-6')]:
        mem=memory();mutate(mem);s=shop()
        assert p._stop_uneconomic_recruit(mem,s)
        assert s['recruit']=='deferred_economy' and s['recruit_reserve']==50
        assert mem['recruit_hold']['reason']=='income_or_payroll_unknown'
    assert r.fixed_wage('ゼウ') is None and r.fixed_wage('unknown') is None


def test_pending_employed_generals_after_chapter_change_prevent_understated_payroll():
    mem=memory(NAMES[:6]);p._enter_chapter(mem,2,reason='observed next chapter')
    assert mem['recruit_payroll_pending']==NAMES[:6]
    mem.update(month='1-8',recruit_roster={'chapter':2,'month':'1-8','tick':100,
        'names':['どうし','ゼウス'],'wages':{'どうし':0,'ゼウス':9},'complete':True})
    assert r.economics(mem,{'アルマムーン'}) is None
    s=shop();assert p._stop_uneconomic_recruit(mem,s)
    assert mem['recruit_hold']['reason']=='pending_employees'
    state={'chapter':2,'phase':'roster','seen':NAMES[:6]};mem['house']=state;r.begin(mem,state)
    r.page(mem,NAMES[:6]);state['roster_wrapped']=True
    assert r.complete(mem,state) and not mem.get('recruit_payroll_pending')


def test_global_scan_counts_field_generals_without_inventing_castle_placements():
    mem=memory();state={'chapter':1,'phase':'status','selected':'バジル','seen':[]}
    mem['house']=state;r.begin(mem,state);r.page(mem,['どうし','バジル'])
    r.wage(mem,{'general':'バジル','wage':7,'location':'field'})
    assert mem['recruit_roster']['wages']=={'バジル':7}
    assert 'バジル' not in mem['garrison']['ほんじょう']


def test_fixed_wages_match_pinned_sfc_data_and_maximum_when_submodule_present():
    import csv
    path=Path(__file__).resolve().parents[1]/'games/hanjuku-sfc-speedrun/data/char.csv'
    if not path.is_file():
        pytest.skip('pinned knowledge submodule not expanded locally')
    rows=[row for row in csv.DictReader(path.open()) if row['ID'].isdigit() and 0<=int(row['ID'])<=127]
    assert len(rows)==128
    assert r.GENERAL_WAGES=={row['名前']:int(row['賃金']) for row in rows}
    assert max(r.GENERAL_WAGES.values())==r.MAX_RECRUIT_WAGE==15


def test_measured_global_roster_scrolls_across_pages_then_wraps_to_first():
    from test_hanjuku_house import memory as house_memory, roster, status, feed
    from docich.hanjuku_screen import parse
    names=NAMES[:8]
    mem=house_memory('roster')
    r.begin(mem,mem['house'])
    for index,name in enumerate(names):
        page=names[:6] if index<6 else names[6:]
        offset=index if index<6 else index-6
        assert feed(mem,roster(page,offset))==[p.pad('a')]
        assert feed(mem,status(name,egg='エラベルエッグ4'))==[p.pad('b')]
        assert feed(mem,roster(page,offset))==[p.pad('down')]
        next_index=(index+1)%len(names)
        next_page=names[:6] if next_index<6 else names[6:]
        next_offset=next_index if next_index<6 else next_index-6
        assert feed(mem,roster(next_page,next_offset))==[]
    assert feed(mem,roster(names[:6],0))==[p.pad('b')]
    assert house.step(Screen([],None,'',kind='map'),mem,None)==[]
    got=r.fresh(mem)
    assert got['complete'] and got['names']==names
    assert got['wages']=={n:r.fixed_wage(n) for n in names}


def test_unchanged_page_cursor_does_not_turn_partial_scan_into_total():
    from test_hanjuku_house import memory as house_memory, roster, feed
    mem=house_memory('roster_advance', selected='どうし',seen=['どうし'])
    r.begin(mem,mem['house']);r.page(mem,NAMES[:6])
    for _ in range(9):feed(mem,roster(NAMES[:6],0))
    assert not mem['house']['roster_wrapped']
    assert house.step(Screen([],None,'',kind='map'),mem,None)==[]
    assert not mem['recruit_roster']['complete']
    assert mem['recruit_roster_recheck']


def test_failed_scan_retries_once_then_releases_and_next_month_can_retry():
    map_screen=Screen([],None,'',kind='map')
    mem={'chapter':1,'month':'1-7','tick':500}
    assert house.step(map_screen,mem,None)==[p.pad('x')]
    for _ in range(25):house.step(Screen([],None,'unreadable',kind='text'),mem,None)
    assert mem['house']['phase']=='close'
    assert mem['recruit_hold']['status']=='observation_failed'
    house.step(map_screen,mem,None)
    mem['tick']+=200
    assert house.step(map_screen,mem,None)==[p.pad('x')]
    house._finish(mem);mem['tick']+=200
    assert house.step(map_screen,mem,None) is None
    assert mem['recruit_roster_attempts']['count']==2
    mem['month']='1-8'
    assert house.step(map_screen,mem,None)==[p.pad('x')]
    assert mem['recruit_roster_attempts']['count']==1


def test_real_castle_status_records_income_without_guessing_level(monkeypatch):
    from test_hanjuku_castle_panel import status
    mem=memory();mem['castle_income'].clear()
    p.observe_events(status('アルマムーン'),mem)
    assert mem['castle_income']['アルマムーン']['income']==22
    assert r.economics(mem,{'アルマムーン'})['income']==22
    p.observe_events(status('スペンソニア'),mem) # not owned; cannot add its income
    assert set(mem['castle_income'])=={'アルマムーン'}


def test_missing_income_survey_only_reads_status_and_returns_without_sortie(monkeypatch):
    from test_hanjuku_castle_panel import status
    mem=memory();mem['castle_income'].clear()
    mem['house']={'phase':'income_next','chapter':1,'age':0,'total':0,
                  'income_castles':['ほんじょう'],'income_failures':[], 'pending':[]}
    map_screen=Screen([],None,'',kind='map')
    assert house.step(map_screen,mem,None)==[p.pad('y')]
    assert mem['house']['phase']=='income_view'
    # Existing measured view/roof servos have their own pixel regressions.
    monkeypatch.setattr(house,'_view_move',lambda screen,mem,frame,target,next_phase,tolerance:
                        (house._phase(mem['house'],next_phase) or [p.pad('a')]))
    assert house.step(Screen([],None,'',kind='world_map'),mem,None)==[p.pad('a')]
    monkeypatch.setattr(p,'_align_on_roof',lambda *args:'on')
    assert house.step(map_screen,mem,None)==[p.pad('a')]
    observed=status('アルマムーン');p.observe_events(observed,mem)
    assert house.step(observed,mem,None)==[p.pad('b')]
    assert house.step(map_screen,mem,None)==[]
    assert house.step(map_screen,mem,None)==[]
    assert r.economics(mem,{'アルマムーン'})['income']==22
    assert not mem.get('sorties') and not mem.get('month_sub')


def test_income_survey_is_aborted_at_month_boundary_without_spending():
    mem=memory();mem['house']={'phase':'income_view','chapter':1,'age':0,'total':0,
                              'roster_month':'1-6','source':'ほんじょう'}
    assert house.step(Screen([],None,'',kind='world_map'),mem,None)==[]
    assert mem['house']['phase']=='close'
    assert mem['recruit_hold']['status']=='observation_failed'
    assert not mem.get('month_sub')


def test_partial_ocr_global_page_is_not_added_to_scan():
    from test_hanjuku_house import roster
    from docich.hanjuku_screen import parse
    from docich.hanjuku_font import TextLine, UNKNOWN
    mem=memory();mem['house']={'chapter':1,'phase':'roster','seen':[]};r.begin(mem,mem['house'])
    screen=parse(roster(NAMES[:3]))
    row=next(l for l in screen.lines if l.y==55)
    screen.lines[screen.lines.index(row)]=TextLine(row.y,tuple((x,UNKNOWN if x==176 else c) for x,c in row.cells))
    assert house.roster(screen) is None
    r.page(mem,house.roster(screen))
    assert mem['recruit_roster']['names']==[] and r.fresh(mem) is None


def test_adopted_chart_role_spec_is_validated_and_preserved():
    from test_hanjuku_chart_adjust import adjusted_doc
    from docich import hanjuku_chart_adjust as adjust
    doc=adjust.validate(adjusted_doc('0000000000000000',recruitment={'per_castle':1,'attack':4}))
    mem=memory(castles=6)
    p._adopt_plan(mem,doc,doc['request_id'])
    assert p._recruit_target(mem)==10
    for invalid in [{}, {'per_castle':True,'attack':3}, {'per_castle':2,'attack':0},
                    {'per_castle':2,'attack':3,'fixed_cap':6}]:
        with pytest.raises(ValueError):adjust.validate(adjusted_doc('0000000000000000',recruitment=invalid))


def test_paid_receipt_invalidates_membership_without_adding_a_general():
    from test_hanjuku_chart_bot import month_canvas
    from docich.hanjuku_screen import parse
    mem=memory();mem['month_sub']={'kind':'recruit','gold_before':100,
                                  'recruit_paid_gold':50,'left_menu':True,'key':'1-7'}
    s=shop();s['recruit']='opened'
    assert p._finish_month_sub(parse(month_canvas(50)),mem,s)
    assert r.fresh(mem) is None and s['recruit']=='done'
    assert mem['recruit_roster_recheck']
    assert {n for gs in mem['garrison'].values() for n in gs} <= set(NAMES[:2])


def test_hp_defeat_invalidates_scan_without_inferring_death():
    mem=memory();mem['battle']={'ally':'ゼウス','enemy':'ミント','ally_hp':0,
        'enemy_hp':1,'start_ally_hp':85,'start_enemy_hp':32,'side':'attack',
        'cards_used':[],'card_evidence_version':1,'castle':'キカンドン'}
    p.battle_end(mem,'map');p.battle_end(mem,'map')
    assert r.fresh(mem) is None
    result=next(x for x in mem['_records'] if x['decision']=='battle_result')
    assert result['observed_metric']['general_loss']=='unclassified'


def _survey_main(month):
    from test_hanjuku_house import Canvas
    c=Canvas();c.text(56,7,f'1わ1ねん{month}のつき250G')
    c.text(48,47,'しょうぐん');c.text(48,63,'ステータス');c.text(144,63,'システム');c.hand(26,41)
    return c.frame()


def _survey_header(frame, month):
    from test_hanjuku_house import Canvas
    c=Canvas();c.rgb=bytearray(frame.rgb)
    for y in range(7,15):
        for x in range(256):c.put(x,y,(16,72,57))
    c.text(56,7,f'1わ1ねん{month}のつき250G')
    return c.frame()


def _drive_survey(state, names, month):
    """Native pixels through decide, no hand-edited phases or receipts."""
    from docich.hanjuku_bot import decide
    from test_hanjuku_house import roster,status
    actions,state=decide(_survey_main(month),state)
    assert actions==[p.pad('a')]
    for i,name in enumerate(names):
        frame=_survey_header(roster(names,i),month)
        actions,state=decide(frame,state);assert actions==[p.pad('a')]
        actions,state=decide(_survey_header(status(name,'エラベルエッグ4'),month),state)
        assert actions==[p.pad('b')]
        actions,state=decide(frame,state);assert actions==[p.pad('down')]
        if len(names)>1:
            actions,state=decide(_survey_header(roster(names,(i+1)%len(names)),month),state)
            assert actions==[]
        else:
            for _ in range(6):actions,state=decide(frame,state)
            assert actions==[p.pad('b')]
    if len(names)>1:
        actions,state=decide(_survey_header(roster(names,0),month),state)
        assert actions==[p.pad('b')]
    actions,state=decide(_survey_main(month),state)
    assert actions==[p.pad('b')]
    return state


@pytest.mark.parametrize('names',[['どうし'],['どうし','ゼウス']])
def test_field_scan_then_month_boundary_rescans_and_reaches_paid_recruitment(names):
    from docich.hanjuku_bot import decide
    from test_hanjuku_chart_bot import month_canvas
    def field(x,y):
        from test_hanjuku_house import Canvas
        c=Canvas((16,120,57))
        for dy in (1,14):
            for dx in (2,3,4,11,12,13):c.put(x+dx,y+dy,(255,255,255))
        for dy in (2,3,4):c.put(x+1,y+dy,(255,255,255))
        return c.frame()
    mem=memory(names=names,income=22);mem.update(month='1-6',tick=500)
    p._apply_world_flags(mem,{'ほんじょう':'own'})
    mem['recruit_roster']['month']='1-6'
    mem['castle_income']['アルマムーン'].update(month='1-6',tick=10)
    state={'policy':mem}
    actions,state=decide(field(100,100),state);assert actions==[p.pad('x')]
    state=_drive_survey(state,names,6)
    actions,state=decide(field(100,100),state);assert actions==[]
    # The field scan is complete in the old month, not silently re-dated.
    assert r.fresh(state['policy'])['complete']
    # Finish the free field survey before the next monthly menu.
    actions,state=decide(field(100,100),state)
    actions,state=decide(field(100,100),state)
    old_tick=state['policy']['recruit_roster']['tick']
    actions,state=decide(month_canvas(250,on='メインメニュー',month=7),state)
    assert actions==[p.pad('a')]
    assert state['policy']['house']['month_scan']
    assert state['policy']['recruit_roster']['tick']>old_tick
    state=_drive_survey(state,names,7)
    actions,state=decide(month_canvas(250,on='メインメニュー',month=7),state)
    assert actions==[] and not state['policy'].get('house')
    assert r.fresh(state['policy'])['complete']
    # Native monthly frames navigate to, and actually open, recruitment.
    actions,state=decide(month_canvas(250,on='しょうぐんぼしゅう',month=7),state)
    assert actions==[p.pad('a')]
    assert state['policy']['month_sub']['kind']=='recruit'
    assert state['policy']['shop']['recruit']=='opened'
    assert state['policy']['castle_income']['アルマムーン']['tick']==10


def test_single_readable_name_with_nonblank_unknown_slot_is_never_complete():
    from test_hanjuku_house import Canvas,roster,feed,status
    from docich.hanjuku_screen import parse
    frame=roster(['どうし']);c=Canvas();c.rgb=bytearray(frame.rgb)
    c.tile(168,55,0x0123456789ABCDEF)
    assert not house._singleton_page(parse(c.frame()),c.frame())
    mem=memory();mem['house']={'chapter':1,'phase':'open_roster','age':0,'total':0,'seen':[], 'pending':[]}
    r.begin(mem,mem['house']);feed(mem,_survey_main(7))
    feed(mem,c.frame());feed(mem,status('どうし','エラベルエッグ4'));feed(mem,c.frame())
    for _ in range(9):feed(mem,c.frame())
    assert not mem['house'].get('roster_wrapped')


def test_month_information_open_failure_is_bounded_and_ordinary_month_policy_resumes():
    from docich.hanjuku_bot import decide
    from test_hanjuku_chart_bot import month_canvas
    state={'policy':memory()};state['policy'].pop('recruit_roster')
    frame=month_canvas(250,on='メインメニュー')
    actions,state=decide(frame,state);assert actions==[p.pad('a')]
    for _ in range(house.STEP_LIMIT):actions,state=decide(frame,state)
    assert state['policy']['house']['phase']=='close'
    actions,state=decide(frame,state);assert not state['policy'].get('house')
    actions,state=decide(frame,state);assert actions==[p.pad('a')]
    for _ in range(house.STEP_LIMIT+1):actions,state=decide(frame,state)
    actions,state=decide(frame,state)
    assert not state['policy'].get('house')
    assert state['policy']['recruit_month_scan_attempts']['count']==2
    assert state['policy']['recruit_hold']['status']=='deferred_roster'



def test_known_castle_property_reuses_original_receipt_only_with_fresh_current_owner():
    mem=memory(income=22);mem['tick']=500;mem['recruit_roster']['tick']=500
    row=mem['castle_income']['アルマムーン'];row.update(month='1-1',tick=10)
    original=dict(row)
    assert r.economics(mem,{'アルマムーン'}) is None
    p._apply_world_flags(mem,{'ほんじょう':'own'})
    assert r.economics(mem,{'アルマムーン'})['income']==22
    assert row==original
    for proof in ({'chapter':1,'tick':100,'owner':'own'},
                  {'chapter':2,'tick':499,'owner':'own'},
                  {'chapter':1,'tick':501,'owner':'own'},
                  {'chapter':1,'tick':True,'owner':'own'},
                  {'chapter':1,'tick':499,'owner':'enemy'}):
        mem['castle_ownership']['アルマムーン']=proof
        assert r.economics(mem,{'アルマムーン'}) is None
    r.owner(mem,'アルマムーン','own')
    row['income']=100  # mismatch with stage's fixed base is not a static property
    assert r.economics(mem,{'アルマムーン'}) is None
    row.update(income=22,chapter=2)
    assert r.economics(mem,{'アルマムーン'}) is None


def test_static_income_never_supplies_an_unobserved_or_future_receipt():
    mem=memory(income=22);r.owner(mem,'アルマムーン','own')
    mem['castle_income'].clear()
    assert r.economics(mem,{'アルマムーン'}) is None
    for tick in (True,101,-1):
        mem['castle_income']['アルマムーン']={'chapter':1,'month':'1-1','tick':tick,'income':22}
        assert r.economics(mem,{'アルマムーン'}) is None


def test_actual_ownership_updates_do_not_change_base_income_on_unchanged_flags():
    mem=memory(income=22);old=dict(mem['castle_income']['アルマムーン'])
    p._apply_world_flags(mem,{'ほんじょう':'own'})
    assert r.owned_fresh(mem,'アルマムーン')
    assert mem['castle_income']['アルマムーン']==old
    p._apply_world_flags(mem,{'ほんじょう':'enemy'})
    assert not r.owned_fresh(mem,'アルマムーン')
    assert 'ほんじょう' not in p._owned(mem)
