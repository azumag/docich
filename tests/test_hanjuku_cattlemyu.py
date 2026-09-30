"""Event-only stock, bounded discovery and carried rare-card battle behavior."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as policy, hanjuku_chart as chart, hanjuku_chart_adjust as adjust
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen, Battle

RARE = 'キャトルミュー'


def inventory(names, selected=55, remaining=3):
    return {'rows': [{'card': name, 'stock': stock, 'y': 55 + 16*i}
                     for i, (name, stock) in enumerate(names)],
            'selected_y': selected, 'remaining': remaining}


def memory(boss=False, confirmed=True):
    step = '1-B1' if boss else '1-A1'
    order = next(o for o in chart.orders(1) if o['step'] == step)
    mem = {'chapter': 1, 'month': '1-10', 'active': step, 'picked': [],
           'rare_card_kit': {step: [RARE]}, 'order_context': {step: {
               'observed_metric': {'cards': [RARE] if confirmed else []}}}, '_records': []}
    return order, mem


def test_measured_inventory_accepts_five_event_cards():
    def line(y, x, text):
        return TextLine(y, tuple((x + i*8, ch) for i, ch in enumerate(text)))
    screen = Screen(lines=[line(31,136,'きりふだセレクト'),line(31,208,'あと3こ'),
                           line(55,160,RARE),line(55,232,'5'),
                           line(127,136,'バトルようのきりふだです')],
                    hand=(138,49,156,62), text='')
    screen.kind = 'card_select'
    assert policy._measured_card_select(screen)['rows'] == [{'y':55,'card':RARE,'stock':5}]
    assert RARE in policy.CARD_NAMES and RARE in adjust.CARD_NAMES


@pytest.mark.parametrize('month', ['1-10', None])
def test_actual_five_stock_changes_kit_to_one_rare_with_id_below_48(month):
    order, mem = memory(boss=True)
    mem.pop('rare_card_kit');mem['month']=month
    mem['retry_context']={order['step']:{'strategy_variant':'retry_chart_boss_kit'}}
    assert policy._rare_card_inventory(None,mem,order,inventory([(RARE,5)])) == []
    cards = policy._deploy_cards(order,mem)
    assert cards[0] == RARE and cards.count(RARE) == 1
    assert sum(policy.CARD_IDS[c] for c in cards) < 48 and len(cards) <= 3
    assert mem['_records'][-1]['strategy_variant'] == 'rare_cattlemyu'


@pytest.mark.parametrize('picked,stock,remaining', [(['イッテツーン'],5,2),([],0,3),([],5,0)])
def test_no_kit_change_after_pick_or_without_measured_available_stock(picked,stock,remaining):
    order, mem = memory()
    mem.pop('rare_card_kit');mem['picked'] = picked
    policy._rare_card_inventory(None,mem,order,inventory([(RARE,stock)],remaining=remaining))
    assert not mem.get('rare_card_kit')


def test_four_row_inventory_is_scanned_boundedly_and_rewound():
    order,mem=memory();mem.pop('rare_card_kit')
    rows=inventory([('イッテツーン',3),('ブラッキー',2),('ノリウツール',1),('クースカン',1)])
    for _ in range(policy.RARE_SCAN_LIMIT):
        assert policy._rare_card_inventory(None,mem,order,rows) == [policy.pad('down')]
    for _ in range(policy.RARE_SCAN_LIMIT):
        assert policy._rare_card_inventory(None,mem,order,rows) == [policy.pad('up')]
    assert policy._rare_card_inventory(None,mem,order,rows) is None
    assert policy._deploy_cards(order,mem) == list(order['cards'])


@pytest.mark.parametrize('month', ['1-10', None])
def test_hidden_rare_row_becomes_usable_during_inventory_sweep(month):
    order,mem=memory();mem.pop('rare_card_kit');mem['month']=month
    assert policy._rare_card_inventory(None,mem,order,inventory([
        ('イッテツーン',3),('ブラッキー',2),('ノリウツール',1),('クースカン',1)])) == [policy.pad('down')]
    assert policy._rare_card_inventory(None,mem,order,inventory([(RARE,5)])) == []
    assert mem['rare_card_kit'][order['step']] == [RARE]


def test_no_rare_tactic_without_actual_carry_or_after_selected_use():
    order,mem=memory(confirmed=False)
    assert not any(t['card']==RARE for t in policy._tactics(mem,order['step']))
    mem['order_context'][order['step']]['observed_metric']['cards']=[RARE]
    assert policy._tactics(mem,order['step'])[0]['card']==RARE
    mem['kit_spent']={order['step']:[RARE]}
    assert not any(t['card']==RARE for t in policy._tactics(mem,order['step']))


def test_normal_general_uses_carried_rare_before_clash():
    order,mem=memory()
    mem['battle']={'enemy':'ミント','ally':chart.HERO,'enemy_hp':70,'ally_hp':80,
        'start_enemy_hp':70,'start_ally_hp':80,'step':order['step'],'cards_used':[],
        'side':'attack','castle':'キカンドン','clashed':False,'planned_cards':[RARE]}
    mem['card_override']={order['step']:['イッテツーン']}
    screen=Screen(lines=[],hand=None,text='');screen.kind='battle'
    screen.battle=Battle(enemy='ミント',ally=chart.HERO,enemy_hp=70,ally_hp=80)
    assert policy.battle_step(screen,mem)==[policy.pad('b')]
    assert mem['battle']['card_flow']['card']==RARE
    assert not mem['battle']['clashed']


def test_boss_kit_rare_stays_reserved_until_measured_boss_entry():
    order,mem=memory(boss=True)
    cur={'step':order['step'],'enemy':'ソーピニヨン'}
    assert policy._available_rare_tactic(mem,cur) is None
    cur.update(enemy='クイーン',castle='けっかい',side='attack',entry_evidence='measured_boss_entry')
    assert policy._available_rare_tactic(mem,cur)['card']==RARE


@pytest.mark.parametrize('move', ['here', policy.pad('down'), policy.pad('up')])
def test_egg_menu_returns_once_to_use_rare_instead_of_summoning(monkeypatch, move):
    order,mem=memory();mem['battle']={'step':order['step'],'enemy':'ミント',
        'ally':chart.HERO,'enemy_hp':80,'ally_hp':80,'cards_used':[], 'cards_selected':[],
        'side':'attack'}
    screen=Screen(lines=[],hand=None,text='こうげきもうこうげきたまごをつかう');screen.kind='egg_battle_menu'
    assert policy.egg_battle_step(screen,mem)==[policy.pad('b')]
    assert mem['battle']['rare_egg_command_return'] is True
    screen.kind='battle_menu';screen.text='たまごをつかうきりふだたいきゃく'
    monkeypatch.setattr(policy,'_battle_menu_to',lambda s,label:move)
    assert policy.battle_menu_step(screen,mem)==([policy.pad('a')] if move == 'here' else [move])
    assert mem['battle']['card_flow']['card']==RARE
    if move != 'here':
        assert policy.battle_menu_step(screen,mem)==[policy.pad('a')]
    assert mem['battle']['card_flow']['stage']=='list'


def test_survival_prefers_visible_rare_and_event_card_cannot_be_purchased():
    assert policy._rescue_card(['ミックミー',RARE],{'enemy':'ミント'})==RARE
    with pytest.raises(ValueError,match='invalid purchase card'):
        adjust._purchases({'month':[1,10],'cards':[[RARE,5]]})


def test_deploy_discovers_hidden_rare_then_plans_only_measured_selection():
    from test_hanjuku_sortie_evidence import memory, measured_card_select
    mem=memory();mem.pop('rare_scan_month')
    assert policy.deploy_step(measured_card_select(),mem)==[policy.pad('down')]
    assert mem['_records'][-1]['decision']=='sortie_input'
    assert mem['picked']==[]
    screen=measured_card_select((RARE,),stock='5')
    assert policy.deploy_step(screen,mem)==[]
    assert mem['picked']==[]
    assert policy.deploy_step(screen,mem)==[policy.pad('a')]
    assert mem['picked']==[RARE]
    assert mem['_records'][-1]['observed_metric']['stock']==5


def test_zero_slots_never_trigger_discovery_input():
    order,mem=memory();mem.pop('rare_card_kit')
    assert policy._rare_card_inventory(None,mem,order,inventory([
        ('イッテツーン',3),('ブラッキー',2),('ノリウツール',1),('クースカン',1)],remaining=0)) is None
    assert not mem.get('rare_scan')
