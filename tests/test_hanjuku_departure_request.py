"""A decided departure A is a reservation, never a world-state fact.

Regression for docich#3193 A: the confirmed target A used to commit the
order, the garrison and the en-route sortie before the input was even sent.
Now the request holds the fixed actor/source/kit while the observed facts
stay untouched until the sender journal proves the A left and the picker is
seen closed. The sender journal itself is a boundary double here; the flow
and the state transitions under test are the real ones.
"""
from unittest.mock import patch

from docich import hanjuku_chart as chart, hanjuku_policy as p
from docich.hanjuku.os_layer.screen import Screen
from test_hanjuku_execution_binding import confirm, memory

TRACE = {'game': 'hanjuku-hero', 'runtime_id': 'test-runtime', 'generation': 741,
         'lease_id': 'test-lease', 'decision_id': 'test-runtime:741:20',
         'frame_sha256': 'f' * 64, 'planned_at': 1.0}
TARGET = Screen([], None, '', kind='map_target')
FIELD = Screen([], None, '', kind='map')


def request(mem):
    """Run the real confirm path and return the pending departure request."""
    mem['cursor'] = list(chart.castles(1)['スペンソニア'])
    with patch.object(p, 'nav_step', return_value='arrived'), \
         patch.object(p, '_target_roof_under_marker', return_value=True), \
         patch.object(p, '_want_y_jump', return_value=False):
        assert p.target_step(TARGET, mem, None) == [p.pad('a')]
    req = mem.get('departure_request')
    assert isinstance(req, dict) and req.get('step') == '1-A3'
    return req


def sent(mem, a_inputs, trace=None):
    """Inject the sender-journal receipt the orchestrator would supply."""
    trace = mem['departure_request'].get('request_trace') if trace is None else trace
    mem['_departure_input'] = {'request_trace': trace, 'a_inputs': a_inputs}


def assert_reserved(mem):
    assert mem['orders']['1-A3'] == 'pending'
    assert mem['garrison']['カストーラ'] == ['ゼウス', 'ヴィーナス']
    assert not mem.get('sorties') and not mem.get('launched_orders')


def test_unsent_departure_request_never_commits_world_state():
    mem = memory(); confirm(mem); request(mem)
    assert_reserved(mem)
    # The same picker observed again without a send: hold, never re-decide
    # the world and never fall back to the foreign-marker cancel.
    assert p.target_step(TARGET, mem, None) == []
    assert_reserved(mem)


def test_picker_closed_without_a_send_is_an_unconfirmed_departure():
    mem = memory(); confirm(mem); request(mem)
    p.observe_sortie_transition(FIELD, mem, 'map_target')
    assert mem['orders']['1-A3'] == 'launched_unconfirmed'
    assert mem['sorties']['1-A3']['status'] == 'launched_unconfirmed'
    assert 'departure_request' not in mem
    assert mem['garrison']['カストーラ'] == ['ゼウス', 'ヴィーナス']


def test_journaled_send_and_closed_picker_commit_the_fixed_departure():
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    sent(mem, 1)
    p.observe_sortie_transition(FIELD, mem, 'map_target')
    assert mem['orders']['1-A3'] == 'launched'
    assert mem['sorties']['1-A3']['status'] == 'en_route'
    assert mem['sorties']['1-A3']['target'] == 'スペンソニア'
    assert mem['launched_orders']['1-A3']['general'] == 'ゼウス'
    assert mem['garrison']['カストーラ'] == ['ヴィーナス']
    assert 'departure_request' not in mem


def test_journal_proven_unsent_resends_within_bounds():
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    for resends in (1, 2):
        sent(mem, 0)
        assert p.target_step(TARGET, mem, None) == [p.pad('a')]
        assert_reserved(mem)
        assert not mem['departure_request'].get('request_trace')
        mem['departure_request']['request_trace'] = dict(TRACE)
    sent(mem, 0)
    assert p.target_step(TARGET, mem, None) == []      # resend bound reached
    assert_reserved(mem)
    assert 'departure_request' not in mem
    assert mem['departure_miss']['1-A3'] == 1


def test_unverifiable_receipt_never_blind_resends():
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    for _ in range(p.DEPARTURE_UNKNOWN_HOLD_LIMIT - 1):
        assert p.target_step(TARGET, mem, None) == []  # no receipt: unknown
        assert_reserved(mem)
    # A receipt from a different decision never counts as this request's.
    sent(mem, 1, trace={**TRACE, 'decision_id': 'other'})
    assert p.target_step(TARGET, mem, None) == []
    assert 'departure_request' not in mem
    assert_reserved(mem)


def test_sent_picker_staying_open_holds_then_releases():
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    for _ in range(p.DEPARTURE_SENT_HOLD_LIMIT):
        sent(mem, 1)
        p.target_step(TARGET, mem, None)
    # A sent A whose picker never closed is released, not committed; the
    # request cycle is bounded and the order may re-confirm.
    assert 'departure_request' not in mem
    assert mem['departure_miss']['1-A3'] == 1
    assert_reserved(mem)


def test_released_request_cycles_are_bounded_then_fail():
    mem = memory(); confirm(mem)
    for _ in range(p.DEPARTURE_REQUEST_LIMIT):
        req = request(mem)
        req['request_trace'] = dict(TRACE)
        sent(mem, 0)
        for _ in range(p.DEPARTURE_RESEND_LIMIT):
            assert p.target_step(TARGET, mem, None) == [p.pad('a')]
            mem['departure_request']['request_trace'] = dict(TRACE)
        sent(mem, 0)
        assert p.target_step(TARGET, mem, None) in ([], [p.pad('b')])
    assert mem['orders']['1-A3'] == 'failed'
    assert mem['garrison']['カストーラ'] == ['ゼウス', 'ヴィーナス']


def test_defense_interruption_releases_reservation_without_committing():
    # A defense battle closing the picker is not a departure observation,
    # even when the journal proves the A was sent: the reservation is
    # released into the unconfirmed record and the garrison fact stays.
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    sent(mem, 1)
    battle = Screen([], None, '', kind='defense_started')
    p.observe_sortie_transition(battle, mem, 'map_target')
    assert mem['orders']['1-A3'] == 'launched_unconfirmed'
    assert mem['sorties']['1-A3']['status'] == 'launched_unconfirmed'
    assert 'departure_request' not in mem
    assert mem['garrison']['カストーラ'] == ['ゼウス', 'ヴィーナス']


def test_finished_order_releases_stale_reservation():
    # The order the reservation belonged to is gone (it arrived, failed or
    # was cancelled): the reservation owns nothing and must not shadow the
    # foreign-marker cancel of the next target screen.
    mem = memory(); confirm(mem); request(mem)
    mem['orders'].pop('1-A3')
    mem.pop('active', None)
    assert p.target_step(TARGET, mem, None) == [p.pad('b')]
    assert 'departure_request' not in mem


def test_reservation_of_another_order_owns_nothing():
    # A reservation whose step no longer matches the active order (e.g. an
    # earlier order finished while the picker result was pending) is dropped
    # before the current order's normal handling continues.
    mem = memory(); confirm(mem); req = request(mem)
    req['step'] = '9-Z9'
    p.target_step(TARGET, mem, None)
    assert 'departure_request' not in mem
    assert_reserved(mem)


def test_chapter_reset_drops_pending_reservation_and_misses():
    # The chapter/run reset is the durable boundary for a restart: a pending
    # reservation and its retry budget die with the old run and never leak
    # reservations into the next one.
    from docich.hanjuku.strategy import campaign
    mem = memory(); confirm(mem); request(mem)
    mem['departure_miss'] = {'1-A3': 1}
    campaign._enter_chapter(mem, 2, reason='test')
    assert 'departure_request' not in mem
    assert 'departure_miss' not in mem
    assert 'sortie_attempt' not in mem
    assert 'orders' not in mem


def test_recall_transition_neither_commits_nor_steals_the_reservation():
    # While a recall destination picker owns the screen, an observed
    # transition is the recall's, not the sortie's: the pending reservation
    # is neither committed nor misattributed, and is released later by the
    # stale-request guards once the order is gone.
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    sent(mem, 1)
    mem['recall'] = {'stage': 'dest'}
    p.observe_sortie_transition(FIELD, mem, 'map_target')
    assert_reserved(mem)
    assert mem.get('departure_request', {}).get('step') == '1-A3'
    mem.pop('recall', None)
    mem['orders'].pop('1-A3')
    mem.pop('active', None)
    assert p.target_step(TARGET, mem, None) == [p.pad('b')]
    assert 'departure_request' not in mem
