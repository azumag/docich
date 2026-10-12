"""The live single-egg screen quotes the priest's flat fee, without Nこで."""
from copy import deepcopy
from pathlib import Path

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, MONTH_GRID
from test_hanjuku_recovery_payroll import ID

ROOT=Path(__file__).parent/'fixtures/hanjuku'


def native(which):
    frame=read_png(ROOT/f'g713-individual-priest-{which}.png')
    expected={'list':'425cbf5a56ebec36753bde0c564af163c843c9acd8d6a04ec00c9f37a2058881',
              'fee':'ef238bbc38aba42fbc01a68ee66eaac81afafc0fc6ccca782932f2671227cf81'}
    assert frame.digest()==expected[which]
    return parse(frame)


def memory():
    return {'chapter':1,'month':'3-12','tick':1200,'_run_identity':dict(ID),
            'egg_uses':{'ココット':0,'リーキ':2,'アルテミス':3,'ゼウス':3,p.NAME:3},
            'shop':{'key':'3-12','egg':'opened','reserve':250,'egg_cost':250,
                    'egg_priority':True,'soldiers':50,'soldiers_done':False},
            'month_sub':{'kind':'egg','key':'3-12','gold_before':215,'presses':0,'left_menu':True}}


def selected():
    m=memory(); assert p._egg_recovery_step(native('list'),m,m['month_sub'])==[p.pad('a')]
    assert m['month_sub']['individual_actor']=='ココット'
    return m


def fee(cost=50,marker='おはらいしを よぶのですな?',complete=True):
    c=Canvas();c.text(48,15,'3ねん 12のつき 215G')
    c.text(24,183,marker);c.text(24,199,f'{cost}Gかかりますが'+(' よろしいですかな' if complete else ''))
    c.text(184,183,'うむッ!');c.text(184,199,'いかんッ!');c.hand(162,177)
    return parse(c.frame())


def ready(gold=165):
    c=Canvas();c.text(48,15,f'3ねん 12のつき {gold}G')
    for label,(x,y) in MONTH_GRID.items():c.text(x,y,label)
    x,y=MONTH_GRID['しょうにん'];c.hand(x-22,y-6)
    return parse(c.frame())


def test_native_actor_zero_and_actual_50g_fee_confirm_without_inventing_a_screen_count():
    m=selected();before=deepcopy(m['egg_uses']);s=native('fee')
    assert 'こで' not in s.text
    assert p._egg_recovery_step(s,m,m['month_sub'])==[p.pad('a')]
    assert m['month_sub']['quoted_cost']==50 and m['month_sub']['stage']=='recovering'
    assert m['egg_uses']==before and p._own_egg_empty(m,{'ally':'ココット'})
    assert m['_records'][-1]['observed_metric']['count_basis']=='bound_actor_flat_fee'


def test_real_fee_payment_keeps_other_targets_and_zero_until_a_named_positive_reading():
    m=selected();p._egg_recovery_step(native('fee'),m,m['month_sub'])
    assert p._finish_month_sub(ready(),m,m['shop'])
    assert m['shop']['egg_paid_actors']==['ココット']
    assert m['egg_uses']=={'リーキ':2,'アルテミス':3,'ゼウス':3,p.NAME:3}
    assert m['shop']['reserve']==200 and m['shop']['soldiers']==0
    assert p._own_egg_empty(m,{'ally':'ココット'})


@pytest.mark.parametrize('bad',['selection','run','chapter','month','shop','header','gold_change',
                                'no_header_month','full_selected','other_kind','wrong_price',
                                'other_dialogue','partial_dialogue','duplicate_fee'])
def test_flat_fee_needs_the_bound_owner_epoch_and_exact_complete_single_fee(bad):
    m=selected();s=native('fee')
    if bad=='selection':m['month_sub']['individual_selected']=False
    elif bad=='run':m['_run_identity']['lease_id']='other'
    elif bad=='chapter':m['chapter']=2
    elif bad=='month':m['month']='4-1'
    elif bad=='shop':m['shop']['key']='4-1'
    elif bad=='header':s.header['month']=11
    elif bad=='gold_change':s.header['gold']=214
    elif bad=='no_header_month':s.header.pop('month')
    elif bad=='full_selected':m['month_sub']['full_selected']=True
    elif bad=='other_kind':m['month_sub']['kind']='chikujou'
    elif bad=='wrong_price':s=fee(100)
    elif bad=='other_dialogue':s=fee(marker='このしろを ぞうちくしますか?')
    elif bad=='partial_dialogue':s=fee(complete=False)
    else:
        from docich.hanjuku_font import TextLine
        s.lines.append(TextLine(207,tuple((24+8*i,ch) for i,ch in enumerate('50Gかかりますがよろしいですかな'))))
    assert p._egg_recovery_step(s,m,m['month_sub'])==[p.pad('b')]
    assert not m['month_sub'].get('quoted_cost') and m['egg_uses']['ココット']==0


def test_fading_single_fee_after_confirmation_does_not_repeat_a_or_abort():
    m=selected();p._egg_recovery_step(native('fee'),m,m['month_sub'])
    for _ in range(3):assert p._egg_recovery_step(native('fee'),m,m['month_sub'])==[]
    assert m['month_sub']['stage']=='recovering' and not m['month_sub'].get('aborted')


def hero_selected():
    m=memory();m['egg_uses'][p.NAME]=0
    c=Canvas();c.text(48,15,'3ねん 12のつき 215G');c.text(32,47,'ぜんかいふく')
    c.text(32,71,p.NAME);c.text(104,71,'0');c.hand(10,65)
    assert p._egg_recovery_step(parse(c.frame()),m,m['month_sub'])==[p.pad('a')]
    assert m['month_sub']['hero_selected'] and m['month_sub']['hero_identity']==ID
    return m


def test_named_hero_selection_binds_the_same_real_flat_fee_and_fade():
    m=hero_selected();assert p._egg_recovery_step(fee(),m,m['month_sub'])==[p.pad('a')]
    assert p._egg_recovery_step(fee(),m,m['month_sub'])==[]
    assert m['month_sub']['quoted_cost']==50


def test_saved_unbound_hero_flag_cannot_authorize_a_new_flat_fee():
    m=hero_selected();m['month_sub'].pop('hero_identity')
    assert p._egg_recovery_step(fee(),m,m['month_sub'])==[p.pad('b')]


def test_flat_fee_still_preserves_a_known_full_payroll_above_30g():
    m=selected();m['recruit_roster']={'chapter':1,'month':'3-12','tick':1200,'identity':dict(ID),
        'complete':True,'names':['ココット','リーキ','アルテミス','ゼウス'],
        'wages':{'ココット':15,'リーキ':15,'アルテミス':15,'ゼウス':15}}
    m['month_sub']['gold_before']=109;s=fee();s.header['gold']=109
    assert p._egg_recovery_step(s,m,m['month_sub'])==[p.pad('b')]
    assert not m['month_sub'].get('quoted_cost')


def test_reselecting_an_unread_hero_row_drops_the_prior_flat_fee_binding():
    m=hero_selected()
    c=Canvas();c.text(48,15,'3ねん 12のつき 215G');c.text(32,47,'ぜんかいふく')
    c.text(32,71,p.NAME);c.hand(10,65)
    assert p._egg_recovery_step(parse(c.frame()),m,m['month_sub'])==[p.pad('a')]
    assert 'hero_identity' not in m['month_sub']
    assert p._egg_recovery_step(fee(),m,m['month_sub'])==[p.pad('b')]


def select_hero_after_general(m, quantity):
    m['egg_uses'][p.NAME]=0
    c=Canvas();c.text(48,15,'3ねん 12のつき 215G');c.text(32,47,'ぜんかいふく')
    c.text(32,71,p.NAME);c.hand(10,65)
    if quantity=='duplicate':
        c.text(104,71,'0');c.text(32,95,p.NAME);c.text(104,95,'0')
    elif quantity is not None:
        c.text(104,71,quantity)
    assert p._egg_recovery_step(parse(c.frame()),m,m['month_sub'])==[p.pad('a')]


@pytest.mark.parametrize('quantity',[None,'duplicate'])
def test_unread_or_duplicate_hero_cannot_borrow_the_previous_generals_binding(quantity):
    m=selected();select_hero_after_general(m,quantity)
    assert m['month_sub']['hero_selected']
    assert not any(k in m['month_sub'] for k in
                   ('individual_selected','individual_actor','individual_identity','individual_chapter'))
    assert 'hero_identity' not in m['month_sub']
    assert p._egg_recovery_step(fee(),m,m['month_sub'])==[p.pad('b')]
    assert m['egg_uses']['ココット']==0 and not m['month_sub'].get('quoted_cost')


def test_read_hero_replaces_general_binding_and_payment_belongs_only_to_hero():
    m=selected();select_hero_after_general(m,'0')
    assert m['month_sub']['hero_identity']==ID
    assert not p._individual_egg_selected(m,m['month_sub'])
    assert p._egg_recovery_step(fee(),m,m['month_sub'])==[p.pad('a')]
    assert m['_records'][-1]['choice']==p.NAME
    assert p._finish_month_sub(ready(),m,m['shop'])
    assert m['shop']['egg_paid_actors']==[p.NAME]
    assert m['egg_uses']['ココット']==0 and p._own_egg_empty(m,{'ally':p.NAME})


def test_saved_mixed_flags_cannot_authorize_hero_fee_with_only_a_general_binding():
    m=selected();m['month_sub']['hero_selected']=True
    assert p._individual_egg_selected(m,m['month_sub'])
    assert p._egg_recovery_step(fee(),m,m['month_sub'])==[p.pad('b')]
    assert not m['month_sub'].get('quoted_cost')
