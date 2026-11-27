"""docich#5770: unreadable card list + empty-kit guard must not form a loop."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart_adjust as chart_adjust
from docich import hanjuku_policy as policy
from docich import hanjuku_reference as reference
from docich.hanjuku.flows import card_census as card_census_flow
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.state.regions import plan as _plan_region, world as _world
from docich.hanjuku.strategy import (card_census as card_census_booking,
                                     orders as order_strategy, replan,
                                     unreadable_card_block)
from docich.hanjuku.strategy.kit import SORTIE_CONFIRM_LIMIT
from test_hanjuku_plan_failed_step_rearm import memory as failed_route_memory
from test_hanjuku_sortie_evidence import measured_card_select, measured_empty_sortie


def _ch8_mechanical_memory(*, unreadable_depart=True):
    step = 'A:rid12345:Mabcdef'
    order = {'step': step, 'general': 'ゼウス', 'source': 'ながはま',
             'target': 'しまばら', 'purpose': 'attack',
             'cards': ['イッテツーン', 'イッテツーン'], 'after': None,
             'note': '暫定: ゼウスでしまばらを攻撃'}
    mem = {'chapter': 8, 'month': '8-3', 'tick': 100, 'active': step,
           'variant': 'chart', '_records': [],
           'chart_plan': {'request_id': 'rid12345', 'source': 'mechanical',
                          'orders': [order]},
           'orders': {step: 'pending'}, 'picked': [],
           'captured': ['ながはま', 'しらかわ', 'まつやま'],
           'garrison': {'ながはま': ['どうし', 'ゼウス', 'キール']},
           'sorties': {}, 'launched_orders': {}}
    if unreadable_depart:
        mem['card_skip_departure'] = {step: 1}
    return mem, step, order


def _repeat_empty_confirmation(mem):
    screen = measured_empty_sortie()
    for _ in range(SORTIE_CONFIRM_LIMIT - 1):
        assert policy.deploy_step(screen, mem) == []
    return policy.deploy_step(screen, mem)


def test_unreadable_list_empty_cancel_blocks_the_route_and_books_census():
    mem, step, order = _ch8_mechanical_memory()
    assert _repeat_empty_confirmation(mem) == [pad('b'), pad('b')]

    assert _plan_region(mem)['orders'][step] == 'failed'
    assert _plan_region(mem)['active'] is None
    row = unreadable_card_block.entry(mem, order['purpose'], order['source'], order['target'])
    assert row is not None
    assert row['steps'] == [step]
    assert row['cards'] == sorted(order['cards'])
    due = card_census_booking.due(mem)
    assert due is not None and due['reason'] == 'unreadable_card_loop'
    assert any(record['decision'] == 'unreadable_card_loop_blocked' for record in mem['_records'])
    assert any(record['decision'] == 'sortie_kit_empty_cancel' for record in mem['_records'])
    assert any(record['decision'] == 'order_failed' for record in mem['_records'])


def test_readable_empty_kit_cancel_without_unreadable_list_keeps_guard_only():
    mem, step, order = _ch8_mechanical_memory(unreadable_depart=False)
    assert _repeat_empty_confirmation(mem) == [pad('b'), pad('b')]

    assert _plan_region(mem)['orders'][step] == 'failed'
    assert unreadable_card_block.entry(mem, order['purpose'], order['source'], order['target']) is None
    assert not mem.get('unreadable_card_block')
    assert card_census_booking.due(mem) is None
    assert any(record['decision'] == 'sortie_kit_empty_cancel' for record in mem['_records'])
    assert not any(record['decision'] == 'unreadable_card_loop_blocked' for record in mem['_records'])


def test_route_block_covers_wave_siblings_but_not_other_routes():
    mem = failed_route_memory()
    _world(mem)['garrison']['イーピヨル'] = ['ココット', 'ピオーネ', 'ゼウス']
    first = {'step': 'A:rid12345:Mone', 'general': 'ココット', 'source': 'イーピヨル',
             'target': 'ドンダム', 'purpose': 'retake', 'cards': ['イッテツーン'], 'after': None}
    sibling = {'step': 'A:rid12345:Mtwo', 'general': 'ピオーネ', 'source': 'イーピヨル',
               'target': 'ドンダム', 'purpose': 'retake', 'cards': ['イッテツーン'], 'after': None}
    other_target = {'step': 'A:rid12345:Mtarget', 'general': 'ゼウス', 'source': 'イーピヨル',
                    'target': 'メガパタ', 'purpose': 'attack', 'cards': ['イッテツーン'], 'after': None}
    other = {'step': 'A:rid12345:Mthree', 'general': 'ココット', 'source': 'パタゴーヌ',
             'target': 'ドンダム', 'purpose': 'retake', 'cards': ['イッテツーン'], 'after': None}
    assert order_strategy._ready(first, mem)
    assert order_strategy._ready(sibling, mem)

    unreadable_card_block.mark(mem, first, source=first['source'], cards=first['cards'], step=first['step'])
    assert unreadable_card_block.order_blocked(mem, first)
    assert unreadable_card_block.order_blocked(mem, sibling)
    assert unreadable_card_block.order_blocked(mem, other_target)
    assert not unreadable_card_block.order_blocked(mem, other)
    assert not order_strategy._ready(first, mem)
    assert not order_strategy._ready(sibling, mem)
    mem['month'] = '7-12'
    assert unreadable_card_block.order_blocked(mem, first)  # 月境界だけでは新しい証拠にならない


def test_blocked_route_is_not_replanned_with_nonempty_interim_cards():
    mem = failed_route_memory()
    _world(mem)['garrison']['イーピヨル'] = ['ココット', 'ピオーネ', 'ゼウス']
    before = list(replan.interim_candidates(mem).values())
    route = next(order for order in before
                 if order.get('purpose') == 'retake' and order.get('source') == 'イーピヨル'
                 and order.get('target') == 'ドンダム')
    # main 側で暫定出撃の携行札が増えたため、枚数ではなく「実在する札が入っている」ことだけ固定する。
    assert route['cards'] and all(isinstance(c, str) and c for c in route['cards'])

    unreadable_card_block.mark(mem, route, source=route['source'], cards=route['cards'],
                               step='A:rid12345:Mold')
    after = list(replan.interim_candidates(mem).values())
    assert not [order for order in after
                if order.get('purpose') == route['purpose']
                and order.get('source') == route['source']
                and order.get('target') == route['target']]


def test_blocked_failed_step_is_not_blindly_rearmed_and_recovers_after_release():
    mem = failed_route_memory()
    rid = chart_adjust.request_id(mem)
    local = 'Mblocked'
    step = chart_adjust.execution_step(rid, local)
    order = {'step': local, 'general': 'ココット', 'source': 'イーピヨル',
             'target': 'ドンダム', 'purpose': 'retake', 'cards': ['イッテツーン'], 'after': None}
    _plan_region(mem)['orders'][step] = 'failed'
    mem['failed_local_step'] = {local: mem['tick'] - replan.PLAN_STALE_TICKS - 1}
    unreadable_card_block.mark(mem, order, source=order['source'], cards=order['cards'], step=step)

    replan._release_stale_failures(mem, [order], rid)
    assert _plan_region(mem)['orders'][step] == 'failed'
    assert not any(record['decision'] == 'plan_step_rearmed' for record in mem['_records'])

    assert unreadable_card_block.clear(mem)
    replan._release_stale_failures(mem, [order], rid)
    assert _plan_region(mem)['orders'][step] == 'pending'
    assert any(record['decision'] == 'plan_step_rearmed' for record in mem['_records'])


def test_structured_sortie_card_list_read_releases_blocked_routes():
    mem = {'chapter': 1, 'month': '1-2', 'active': '1-A2', 'variant': 'chart',
           'orders': {'1-A2': 'pending'}, 'picked': [], '_records': []}
    order = {'step': 'A:rid12345:Mblocked', 'general': 'ゼウス', 'source': 'キカンドン',
             'target': 'ゴーメン', 'purpose': 'attack', 'cards': ['イッテツーン'], 'after': None}
    other = {'step': 'A:rid12345:Mother', 'general': 'ゼウス', 'source': 'アルマムーン',
             'target': 'ゴーメン', 'purpose': 'attack', 'cards': ['イッテツーン'], 'after': None}
    unreadable_card_block.mark(mem, order, source=order['source'], cards=order['cards'], step=order['step'])
    unreadable_card_block.mark(mem, other, source=other['source'], cards=other['cards'], step=other['step'])
    assert unreadable_card_block.order_blocked(mem, order)

    actions = policy.deploy_step(measured_card_select(), mem)
    assert isinstance(actions, list)
    assert unreadable_card_block.entry(mem, order['purpose'], order['source'], order['target']) is None
    assert unreadable_card_block.entry(mem, other['purpose'], other['source'], other['target']) is not None
    assert any(record['decision'] == 'unreadable_card_block_cleared' for record in mem['_records'])


def test_completed_empty_census_enables_cardless_replan_without_faking_readability():
    mem = {'chapter': 3, 'month': '7-11', 'tick': 10, '_records': []}
    order = {'step': 'A:rid12345:Mblocked', 'general': 'ココット', 'source': 'イーピヨル',
             'target': 'ドンダム', 'purpose': 'retake', 'cards': ['イッテツーン'], 'after': None}
    unreadable_card_block.mark(mem, order, source=order['source'], cards=order['cards'], step=order['step'])
    card_census_booking.book(mem, 'unreadable_card_loop')
    state = {'rows': [], 'observations': 1, 'started_at': time.time(), 'pages': []}

    assert card_census_flow._complete(mem, state) == [pad('b')]
    assert card_census_booking.due(mem) is None
    assert mem['card_stock']['イッテツーン'] == 0
    assert unreadable_card_block.order_blocked(mem, order)
    cardless = {**order, 'cards': []}
    assert not unreadable_card_block.order_blocked(mem, cardless)
    assert not any(record['decision'] == 'unreadable_card_block_cleared' for record in mem['_records'])


def test_safe_autocarry_makes_raw_cardless_order_part_of_the_blocked_loop():
    mem = {'chapter': 3, 'month': '7-11', '_records': []}
    source_order = {'step': 'A:rid12345:Mblocked', 'general': 'ココット', 'source': 'イーピヨル',
                    'target': 'ドンダム', 'purpose': 'retake', 'cards': ['イッテツーン'], 'after': None}
    unreadable_card_block.mark(mem, source_order, source=source_order['source'],
                               cards=source_order['cards'], step=source_order['step'])
    safe = next(name for name, card in sorted(reference.CARDS.items()) if card.get('egg_drop') == 0)
    mem['card_stock'] = {safe: 1}
    raw_cardless = {'step': 'A:rid12345:Mcardless', 'general': 'ココット', 'source': 'イーピヨル',
                    'target': 'ドンダム', 'purpose': 'retake', 'cards': [], 'after': None}

    assert unreadable_card_block.order_blocked(mem, raw_cardless)
    mem['card_stock'][safe] = 0
    assert not unreadable_card_block.order_blocked(mem, raw_cardless)
