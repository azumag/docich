"""A released departure may fail only when this exact request owns the step.

Regression coverage for docich#5089: a previous arrived/overdue transit record
can share an order step with a new request, and a journaled A may be accepted
one observation after the last picker read. Neither is sufficient evidence to
write a durable failed order.
"""
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.flows.map import (
    DEPARTURE_REQUEST_LIMIT,
    DEPARTURE_SENT_MISS_LIMIT,
    _commit_departure,
    _release_departure_request,
)
from docich.hanjuku.state.regions import (
    flow as _flow,
    plan as _plan_region,
    sorties as _sorties,
    world as _world,
)

STEP = 'A:run:M1'
ORDER = {'step': STEP, 'target': 'タオ', 'source': 'ハヤク', 'purpose': 'move'}
REQUEST = {
    'step': STEP, 'target': 'タオ', 'source': 'ハヤク', 'general': 'ゼウス',
    'context': {'general': 'ゼウス'}, 'snapshot': {'purpose': 'move'},
    'status': 'requested',
}


def order_mem():
    mem = {'chapter': 9, 'tick': 500, '_records': []}
    _plan_region(mem)['orders'] = {STEP: 'pending'}
    _plan_region(mem)['active'] = STEP
    _flow(mem)['departure_miss'] = {STEP: DEPARTURE_REQUEST_LIMIT - 1}
    return mem


def release(mem, request=None):
    return _release_departure_request(mem, dict(ORDER), dict(request or REQUEST), 'test')


def decisions(mem):
    return [row['decision'] for row in mem['_records']]


def release_records(mem):
    return [row for row in mem['_records'] if row['decision'] == 'sortie_departure_not_accepted']


def bound_request(confirm_tick=450):
    return {
        **REQUEST,
        'snapshot': {
            **REQUEST['snapshot'],
            'departure_binding': {
                'confirm_tick': confirm_tick,
                'general': 'ゼウス',
                'source': 'ハヤク',
            },
        },
    }


def test_a_release_for_a_step_not_active_never_writes_failed():
    mem = order_mem()
    _plan_region(mem)['active'] = 'A:run:M2'
    result = release(mem)
    assert _plan_region(mem)['orders'][STEP] == 'pending'
    assert _flow(mem)['departure_miss'][STEP] == DEPARTURE_REQUEST_LIMIT - 1
    assert 'order_failed' not in decisions(mem)
    assert release_records(mem)[-1]['resulting_event'] == 'request_released_not_owned'
    assert result.permitted_recovery == 'reconfirm'


def test_a_late_release_does_not_fail_an_already_launched_order():
    mem = order_mem()
    _plan_region(mem)['orders'][STEP] = 'launched'
    result = release(mem)
    assert _plan_region(mem)['orders'][STEP] == 'launched'
    assert _flow(mem)['departure_miss'][STEP] == DEPARTURE_REQUEST_LIMIT - 1
    assert 'order_failed' not in decisions(mem)
    assert release_records(mem)[-1]['resulting_event'] == 'request_released_not_owned'


def test_an_untagged_live_sortie_keeps_the_release_fail_closed():
    mem = order_mem()
    _world(mem).setdefault('sorties', {})[STEP] = {'status': 'en_route'}
    result = release(mem)
    assert _plan_region(mem)['orders'][STEP] == 'pending'
    assert _flow(mem)['departure_miss'][STEP] == DEPARTURE_REQUEST_LIMIT - 1
    assert 'order_failed' not in decisions(mem)
    assert release_records(mem)[-1]['resulting_event'] == 'request_released_not_owned'
    assert result.permitted_recovery == 'reconfirm'


def test_a_previous_arrived_transit_sortie_does_not_block_a_new_request():
    mem = order_mem()
    _world(mem).setdefault('sorties', {})[STEP] = {
        'status': 'arrived', 'confirmation_tick': 100,
        'general': 'ゼウス', 'source': 'ハヤク',
    }
    result = release(mem, bound_request())
    assert _plan_region(mem)['orders'][STEP] == 'failed'
    assert 'order_failed' in decisions(mem)
    assert result.actions == [pad('b')]
    assert release_records(mem) == []


def test_a_previous_overdue_sortie_does_not_block_a_new_request():
    mem = order_mem()
    _world(mem).setdefault('sorties', {})[STEP] = {
        'status': 'overdue', 'confirmation_tick': 100,
        'general': 'ゼウス', 'source': 'ハヤク',
    }
    result = release(mem, bound_request())
    assert _plan_region(mem)['orders'][STEP] == 'failed'
    assert 'order_failed' in decisions(mem)
    assert result.actions == [pad('b')]
    assert release_records(mem) == []


def test_a_sortie_matching_the_current_confirmation_is_preserved():
    mem = order_mem()
    _world(mem).setdefault('sorties', {})[STEP] = {
        'status': 'arrived', 'confirmation_tick': 450,
        'general': 'ゼウス', 'source': 'ハヤク',
    }
    result = release(mem, bound_request(450))
    assert _plan_region(mem)['orders'][STEP] == 'pending'
    assert 'order_failed' not in decisions(mem)
    assert release_records(mem)[-1]['resulting_event'] == 'request_released_not_owned'
    assert result.permitted_recovery == 'reconfirm'


def test_a_sortie_with_a_different_actor_or_source_does_not_protect_the_request():
    mem = order_mem()
    _world(mem).setdefault('sorties', {})[STEP] = {
        'status': 'arrived', 'confirmation_tick': 450,
        'general': 'ヴィーナス', 'source': 'コラダイン',
    }
    result = release(mem, bound_request(450))
    assert _plan_region(mem)['orders'][STEP] == 'failed'
    assert 'order_failed' in decisions(mem)
    assert result.actions == [pad('b')]


def test_a_request_for_another_step_never_settles_this_one():
    mem = order_mem()
    result = release(mem, {**REQUEST, 'step': 'A:run:M9'})
    assert _plan_region(mem)['orders'][STEP] == 'pending'
    assert _flow(mem)['departure_miss'][STEP] == DEPARTURE_REQUEST_LIMIT - 1
    assert 'order_failed' not in decisions(mem)
    assert release_records(mem)[-1]['resulting_event'] == 'request_released_not_owned'


def test_a_journaled_send_holds_the_miss_short_of_failed():
    mem = order_mem()
    result = release(mem, {**REQUEST, 'status': 'sent'})
    assert _plan_region(mem)['orders'][STEP] == 'pending'
    assert 'order_failed' not in decisions(mem)
    assert _flow(mem)['departure_miss'][STEP] == DEPARTURE_REQUEST_LIMIT - 1
    assert _flow(mem)['departure_sent_miss'][STEP] == 1
    assert release_records(mem)[-1]['resulting_event'] == 'sent_release_held'
    assert result.retry == {'used': 1, 'limit': DEPARTURE_SENT_MISS_LIMIT}
    assert result.permitted_recovery == 'reconfirm'


def test_enough_ignored_sends_still_fail_the_order():
    mem = order_mem()
    _flow(mem)['departure_sent_miss'] = {STEP: DEPARTURE_SENT_MISS_LIMIT - 1}
    result = release(mem, {**REQUEST, 'status': 'sent'})
    assert _plan_region(mem)['orders'][STEP] == 'failed'
    assert 'order_failed' in decisions(mem)
    assert STEP not in _flow(mem)['departure_miss']
    assert STEP not in _flow(mem)['departure_sent_miss']
    assert result.actions == [pad('b')]
    assert result.permitted_recovery == 'none'


def test_an_owned_unsent_release_still_fails_at_the_bound():
    mem = order_mem()
    result = release(mem)
    assert _plan_region(mem)['orders'][STEP] == 'failed'
    assert 'order_failed' in decisions(mem)
    assert result.actions == [pad('b')]
    assert result.permitted_recovery == 'none'


def test_a_committed_departure_drops_the_miss_counters():
    mem = order_mem()
    _flow(mem)['departure_sent_miss'] = {STEP: 3}
    _commit_departure(mem, dict(ORDER), {'general': 'ゼウス'},
                      {'source': 'ハヤク', 'purpose': 'move'})
    assert _flow(mem)['departure_miss'] == {}
    assert _flow(mem)['departure_sent_miss'] == {}
    assert _plan_region(mem)['orders'][STEP] == 'launched'
    assert _sorties(mem)[STEP]['status'] == 'en_route'
