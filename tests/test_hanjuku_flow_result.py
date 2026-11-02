"""docich#3193 B stage 1: the departure-request procedure returns a contract.

The arbiter still receives bare actions; the procedure now also declares its
disposition, bounded retry use and permitted recovery, so a new hold needs no
arbiter-side mem key. Behavior (returned actions, state transitions) must
stay identical to the pre-contract version.
"""
from unittest.mock import patch

from docich import hanjuku_chart as chart, hanjuku_policy as p
from docich.hanjuku.flows.map import _departure_request_step
from docich.hanjuku.flows.result import FlowResult
from docich.hanjuku.os_layer.screen import Screen
from test_hanjuku_departure_request import TRACE, request as make_request, sent
from test_hanjuku_execution_binding import confirm, memory

TARGET = Screen([], None, '', kind='map_target')
FIELD = Screen([], None, '', kind='map')


def setup():
    mem = memory(); confirm(mem); req = make_request(mem)
    req['request_trace'] = dict(TRACE)
    order = p._order_for_step(mem, '1-A3')
    return mem, order, req


def test_off_picker_reports_commit_pending():
    mem, order, req = setup()
    r = _departure_request_step(FIELD, mem, order, req)
    assert isinstance(r, FlowResult) and r.owner == 'departure_request'
    assert r.disposition == 'commit_pending' and r.actions == []


def test_sent_receipt_reports_bounded_hold():
    mem, order, req = setup()
    sent(mem, 1)
    r = _departure_request_step(TARGET, mem, order, req)
    assert r.disposition == 'hold' and r.permitted_recovery == 'reobserve'
    assert r.retry == {'used': 1, 'limit': p.DEPARTURE_SENT_HOLD_LIMIT}


def test_proven_unsent_reports_bounded_resend_input():
    mem, order, req = setup()
    sent(mem, 0)
    r = _departure_request_step(TARGET, mem, order, req)
    assert r.disposition == 'input' and r.actions == [p.pad('a')]
    assert r.permitted_recovery == 'resend'
    assert r.retry == {'used': 1, 'limit': p.DEPARTURE_RESEND_LIMIT}


def test_unknown_receipt_reports_hold_without_resend():
    mem, order, req = setup()
    r = _departure_request_step(TARGET, mem, order, req)
    assert r.disposition == 'hold' and r.permitted_recovery == 'none'
    assert r.retry['limit'] == p.DEPARTURE_UNKNOWN_HOLD_LIMIT


def test_release_reports_reconfirm_or_end():
    mem, order, req = setup()
    sent(mem, 0)
    for _ in range(p.DEPARTURE_RESEND_LIMIT):
        _departure_request_step(TARGET, mem, order, req)
        req['request_trace'] = dict(TRACE)
    sent(mem, 0)
    r = _departure_request_step(TARGET, mem, order, req)
    assert r.disposition == 'release' and r.permitted_recovery == 'reconfirm'
    assert 'departure_request' not in mem
