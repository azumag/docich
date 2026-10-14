"""Offline contract tests, not a measured game driver or a gameplay replay."""
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json

import pytest

from docich import hanjuku_execution as e


RUN = e.RunIdentity('g-demo', 7, 'lease-demo')
ORDER = e.SortieOrder('attack-1', '将軍A', '本城', '城X', ('札A',))
PLAN = e.StrategyPlan(RUN, (ORDER,))


def frame_hash(sequence):
    return hashlib.sha256(f'frame-{sequence}'.encode()).hexdigest()


def world(sequence=1, **changes):
    state = e.WorldState(RUN, sequence, ('本城', '城X', '城Y'), ('本城',),
                         (e.GeneralState('将軍A', 'at_castle', '本城'),
                          e.GeneralState('将軍B', 'at_castle', '本城')), (('札A', 2),), frame_hash(sequence))
    return replace(state, **changes)


def selected(sequence=2, *, order=ORDER, run=RUN):
    return e.SortieEvidence(run, sequence, order, 'selection', frame_hash(sequence))


def departure(sequence=3, *, order=ORDER, run=RUN):
    return e.SortieEvidence(run, sequence, order, 'departure', frame_hash(sequence))


def accepted():
    session, result = e.begin(e.Session(PLAN), ORDER.order_id, world())
    assert result.status == 'ready'
    assert result.reason == 'ACCEPTED_NOT_SENT'
    return session, result.command_id


def final_intent():
    session, cid = accepted()
    session, intent = e.prepare_input(session, cid, world(2), (e.PadAction('a'),),
                                      final_confirmation=True, verified_selection=selected())
    return session, cid, intent


def departing():
    session, cid, intent = final_intent()
    return e.acknowledge(session, cid, intent.input_id, 'sent'), cid, intent


def raw_plan():
    return {'schema': 1, 'orders': [{**asdict(ORDER), 'cards': list(ORDER.cards)}]}


def test_plan_parser_roundtrip_and_trusted_identity():
    assert e.parse_plan(json.dumps(raw_plan()), RUN) == PLAN
    assert e.parse_plan('{"schema":1,"orders":[]}', RUN).orders == ()
    assert PLAN.revision == e.StrategyPlan(RUN, (ORDER,)).revision


@pytest.mark.parametrize('key', ['actions', 'code', 'run', 'revision', 'executor_version', 'reset'])
def test_model_cannot_add_authority(key):
    doc = raw_plan()
    doc[key] = 'anything'
    with pytest.raises(e.ContractError, match='INVALID_PLAN'):
        e.parse_plan(json.dumps(doc), RUN)


@pytest.mark.parametrize('key', ['actions', 'buttons', 'substitute', 'source_override', 'hold_ms'])
def test_model_cannot_smuggle_driver_actions(key):
    doc = raw_plan()
    doc['orders'][0][key] = True
    with pytest.raises(e.ContractError, match='INVALID_ORDER'):
        e.parse_plan(json.dumps(doc), RUN)


@pytest.mark.parametrize('text', [
    '{"schema":1,"schema":1,"orders":[]}',
    '{"schema":true,"orders":[]}', '{"schema":1.0,"orders":[]}',
    '{"schema":NaN,"orders":[]}', '{"schema":Infinity,"orders":[]}',
    '{"schema":1e999,"orders":[]}', 'null', '[]', '{}', 'not json',
    '{"schema":1,"orders":{}}', '{"schema":1,"orders":null}',
    '{"schema":1,"orders":[],"code":"print(1)"}',
])
def test_invalid_json_and_schema(text):
    with pytest.raises(e.ContractError):
        e.parse_plan(text, RUN)


def test_duplicate_nested_key_rejected():
    text = json.dumps(raw_plan()).replace('"order_id":', '"order_id":"other","order_id":')
    with pytest.raises(e.ContractError, match='DUPLICATE_KEY'):
        e.parse_plan(text, RUN)


def test_plan_size_and_depth_bounded():
    with pytest.raises(e.ContractError, match='PLAN_TOO_LARGE'):
        e.parse_plan(' ' * (e.MAX_PLAN_BYTES + 1), RUN)
    with pytest.raises(e.ContractError):
        e.parse_plan('[' * 1500 + ']' * 1500, RUN)


@pytest.mark.parametrize('changes', [
    {'order_id': ''}, {'order_id': 'bad id'}, {'general': ''}, {'general': '\ud800'},
    {'general': 'a\nb'}, {'source': '城X'}, {'cards': ('札A',) * 4}, {'cards': ['札A']},
])
def test_invalid_order(changes):
    with pytest.raises(e.ContractError):
        replace(ORDER, **changes)


def test_duplicate_order_and_oversized_plan():
    with pytest.raises(e.ContractError, match='DUPLICATE_ORDER'):
        e.StrategyPlan(RUN, (ORDER, ORDER))
    with pytest.raises(e.ContractError, match='INVALID_ORDERS'):
        e.StrategyPlan(RUN, tuple(replace(ORDER, order_id=f'o-{i}') for i in range(17)))


@pytest.mark.parametrize('changes,reason', [
    ({'castles': ('本城',), 'own_castles': ('本城',)}, 'UNSUPPORTED_CASTLE'),
    ({'own_castles': None}, 'OWNERSHIP_UNKNOWN'),
    ({'own_castles': ()}, 'SOURCE_NOT_OWNED'),
    ({'generals': ()}, 'GENERAL_STATE_UNKNOWN'),
    ({'generals': (e.GeneralState('将軍A', 'unknown'),)}, 'GENERAL_STATE_UNKNOWN'),
    ({'generals': (e.GeneralState('将軍A', 'unavailable'),)}, 'GENERAL_UNAVAILABLE'),
    ({'generals': (e.GeneralState('将軍A', 'marching'),)}, 'GENERAL_MARCHING'),
    ({'generals': (e.GeneralState('将軍A', 'at_castle', '城Y'),)}, 'GENERAL_ELSEWHERE'),
    ({'card_stock': ()}, 'STOCK_UNKNOWN'),
    ({'card_stock': (('札A', 0),)}, 'STOCK_INSUFFICIENT'),
])
def test_precondition_failure_does_not_issue_or_substitute(changes, reason):
    original = e.Session(PLAN)
    result_session, result = e.begin(original, ORDER.order_id, world(**changes))
    assert result_session is original
    assert result.reason == reason
    assert result.command_id is None
    assert not result.goal_completed


def test_unknown_can_be_reobserved_without_poisoning_command_id():
    session = e.Session(PLAN)
    session, result = e.begin(session, ORDER.order_id, world(card_stock=()))
    assert result.status == 'needs_observation'
    session, result = e.begin(session, ORDER.order_id, world(2))
    assert result.status == 'ready'


def test_known_unavailable_is_not_observation_unknown():
    session, result = e.begin(
        e.Session(PLAN), ORDER.order_id,
        world(generals=(e.GeneralState('将軍A', 'unavailable'),
                        e.GeneralState('将軍B', 'at_castle', '本城'))))
    assert session.executions == ()
    assert result.status == 'precondition_failed'
    assert result.reason == 'GENERAL_UNAVAILABLE'


@pytest.mark.parametrize('changes,reason', [
    ({'own_castles': ('城Y',)}, 'SOURCE_NOT_OWNED'),
    ({'generals': (e.GeneralState('将軍A', 'unavailable'),
                   e.GeneralState('将軍B', 'at_castle', '本城'))}, 'GENERAL_UNAVAILABLE'),
    ({'generals': (e.GeneralState('将軍A', 'marching'),
                   e.GeneralState('将軍B', 'at_castle', '本城'))}, 'GENERAL_MARCHING'),
    ({'generals': (e.GeneralState('将軍A', 'at_castle', '城Y'),
                   e.GeneralState('将軍B', 'at_castle', '本城'))}, 'GENERAL_ELSEWHERE'),
    ({'card_stock': (('札A', 0),)}, 'STOCK_INSUFFICIENT'),
])
def test_prepare_input_stops_when_fresh_world_explicitly_breaks_precondition(changes, reason):
    session, cid = accepted()
    with pytest.raises(e.ContractError, match=reason):
        e.prepare_input(session, cid, world(2, **changes), (e.PadAction('right'),))
    assert session.executions[0].phase == e.Phase.READY
    assert session.executions[0].input_count == 0


def test_prepare_input_does_not_treat_missing_facts_as_precondition_loss():
    session, cid = accepted()
    unknown = world(
        2,
        own_castles=None,
        generals=(e.GeneralState('将軍A', 'unknown'),
                  e.GeneralState('将軍B', 'unknown')),
        card_stock=(),
    )
    session, intent = e.prepare_input(session, cid, unknown, (e.PadAction('right'),))
    assert intent.actions == (e.PadAction('right'),)
    assert session.executions[0].phase == e.Phase.PENDING_INPUT


def test_duplicate_card_quantities_not_set_membership():
    order = replace(ORDER, cards=('札A', '札A'))
    session = e.Session(e.StrategyPlan(RUN, (order,)))
    _, denied = e.begin(session, order.order_id, world(card_stock=(('札A', 1),)))
    assert denied.reason == 'STOCK_INSUFFICIENT'
    _, allowed = e.begin(session, order.order_id, world(card_stock=(('札A', 2),)))
    assert allowed.status == 'ready'


def test_no_unrequested_resource_policy():
    # No hidden wage budget, reserve general count or automatic replacement.
    order = replace(ORDER, cards=())
    session = e.Session(e.StrategyPlan(RUN, (order,)))
    _, result = e.begin(session, order.order_id,
                        world(generals=(e.GeneralState('将軍A', 'at_castle', '本城'),), card_stock=()))
    assert result.status == 'ready'


@pytest.mark.parametrize('args', [
    ('将軍A', 'bad_status', None),
    ('将軍A', 'at_castle', None),
    ('将軍A', 'unknown', '本城'),
    ('将軍A', 'marching', '本城'),
    ('将軍A', 'unavailable', '本城'),
])
def test_general_state_requires_explicit_consistent_status(args):
    with pytest.raises(e.ContractError):
        e.GeneralState(*args)


@pytest.mark.parametrize('changes', [
    {'sequence': True}, {'sequence': -1}, {'sequence': 1.5},
    {'own_castles': ('missing',)}, {'castles': ('本城', '本城')},
    {'generals': (e.GeneralState('将軍A', 'at_castle', '本城'),) * 2},
    {'card_stock': (('札A', True),)}, {'card_stock': (('札A', -1),)},
    {'card_stock': (('札A', 1), ('札A', 2))}, {'card_stock': [['札A', 1]]},
])
def test_malformed_observations(changes):
    with pytest.raises(e.ContractError):
        world(**changes)


def test_identity_mismatch_at_admission_and_input():
    other = replace(RUN, generation=8)
    with pytest.raises(e.ContractError, match='RUN_MISMATCH'):
        e.begin(e.Session(PLAN), ORDER.order_id, world(run=other))
    session, cid = accepted()
    with pytest.raises(e.ContractError, match='RUN_MISMATCH'):
        e.prepare_input(session, cid, world(2, run=other), (e.PadAction('a'),))


def test_same_order_id_is_idempotent_even_after_plan_removed_it():
    session, cid = accepted()
    candidate = e.StrategyPlan(RUN, ())
    session = e.replan(session, candidate, expected_revision=PLAN.revision)
    replayed, result = e.begin(session, ORDER.order_id, world(50))
    assert replayed is session
    assert result.command_id == cid
    assert result.reason == 'ALREADY_ISSUED'
    assert len(session.executions) == 1


def test_replan_keeps_in_flight_command_and_cas():
    session, cid, intent = final_intent()
    journal = session.executions
    candidate = e.StrategyPlan(RUN, (replace(ORDER, order_id='attack-2', general='将軍B'),))
    changed = e.replan(session, candidate, expected_revision=PLAN.revision)
    assert changed.executions is journal
    assert changed.executions[0].pending == intent
    assert changed.executions[0].command.plan_revision == PLAN.revision
    with pytest.raises(e.ContractError, match='STALE_PLAN'):
        e.replan(changed, e.StrategyPlan(RUN, ()), expected_revision=PLAN.revision)
    with pytest.raises(e.ContractError, match='EXECUTOR_VERSION_MISMATCH'):
        replace(changed, executor_version='different-mid-run')
    with pytest.raises(FrozenInstanceError):
        changed.executions[0].command.order.target = '城Y'


def test_replan_cannot_reuse_issued_node_or_cross_run():
    session, _ = accepted()
    with pytest.raises(e.ContractError, match='ISSUED_ORDER_REUSED'):
        e.replan(session, e.StrategyPlan(RUN, (replace(ORDER, target='城Y'),)),
                 expected_revision=PLAN.revision)
    with pytest.raises(e.ContractError, match='RUN_MISMATCH'):
        e.replan(session, replace(PLAN, run=replace(RUN, lease_id='other')),
                 expected_revision=PLAN.revision)


def test_single_writer_gate_after_replan():
    session, _ = accepted()
    second = replace(ORDER, order_id='second', general='将軍B')
    session = e.replan(session, e.StrategyPlan(RUN, (second,)), expected_revision=PLAN.revision)
    unchanged, result = e.begin(session, second.order_id, world(10))
    assert unchanged is session
    assert result.reason == 'EXECUTOR_BUSY'


@pytest.mark.parametrize('button,ms', [('reset', 100), ('a', True), ('a', 0), ('a', 501), ('a', 1.5)])
def test_driver_action_bounds(button, ms):
    with pytest.raises(e.ContractError):
        e.PadAction(button, ms)


def test_prepare_input_requires_fresh_observation_and_bounded_tuple():
    session, cid = accepted()
    with pytest.raises(e.ContractError, match='STALE_OBSERVATION'):
        e.prepare_input(session, cid, world(), (e.PadAction('a'),))
    for actions in [(), [], (e.PadAction('a'),) * 5, ({'button': 'a'},)]:
        with pytest.raises(e.ContractError, match='INVALID_ACTIONS'):
            e.prepare_input(session, cid, world(2), actions)
    assert session.executions[0].phase == e.Phase.READY


@pytest.mark.parametrize('selection', [None, replace(ORDER, general='将軍B'),
                                      replace(ORDER, target='城Y'), replace(ORDER, cards=())])
def test_final_requires_exact_observed_selection(selection):
    session, cid = accepted()
    with pytest.raises(e.ContractError, match='SELECTION_NOT_VERIFIED'):
        e.prepare_input(session, cid, world(2), (e.PadAction('a'),),
                         final_confirmation=True, verified_selection=selection)


def test_final_only_single_confirm_and_boolean_flag():
    session, cid = accepted()
    for actions in [(e.PadAction('b'),), (e.PadAction('a'), e.PadAction('a'))]:
        with pytest.raises(e.ContractError, match='INVALID_FINAL_INPUT'):
            e.prepare_input(session, cid, world(2), actions,
                             final_confirmation=True, verified_selection=selected())
    with pytest.raises(e.ContractError, match='INVALID_FINAL_FLAG'):
        e.prepare_input(session, cid, world(2), (e.PadAction('a'),), final_confirmation=1)


def test_end_to_end_driver_contract_not_gameplay():
    session, cid = accepted()
    # A measured driver would select these steps from actual observations.
    session, move = e.prepare_input(session, cid, world(2), (e.PadAction('right'),))
    assert session.executions[0].pending == move  # durable-save point before send
    with pytest.raises(e.ContractError, match='INPUT_NOT_ALLOWED'):
        e.prepare_input(session, cid, world(3), (e.PadAction('right'),))
    session = e.acknowledge(session, cid, move.input_id, 'sent')
    session, confirm = e.prepare_input(session, cid, world(3), (e.PadAction('a'),),
                                       final_confirmation=True, verified_selection=selected(3))
    session = e.acknowledge(session, cid, confirm.input_id, 'sent')
    assert session.executions[0].phase == e.Phase.AWAITING_DEPARTURE
    session, result = e.observe_departure(session, cid, world(4),
                                          evidence=departure(4), input_id=confirm.input_id)
    assert result.status == 'departure_observed'
    assert result.reason == 'DEPARTURE_NOT_CAPTURE'
    assert result.goal_completed is False
    assert session.executions[0].command.order == ORDER


def test_unknown_dispatch_retains_intent_and_blocks_resend_and_cancel():
    session, cid, intent = final_intent()
    session = e.acknowledge(session, cid, intent.input_id, 'unknown')
    assert session.executions[0].pending == intent
    assert session.executions[0].phase == e.Phase.UNCERTAIN
    with pytest.raises(e.ContractError, match='INPUT_NOT_ALLOWED'):
        e.prepare_input(session, cid, world(10), (e.PadAction('a'),))
    with pytest.raises(e.ContractError, match='CANCEL_NOT_SAFE'):
        e.cancel_before_confirmation(session, cid)
    session, result = e.observe_departure(session, cid, world(11),
                                          evidence=departure(11), input_id=intent.input_id)
    assert result.status == 'departure_observed'


def test_definitely_not_sent_is_different_from_unknown():
    session, cid, intent = final_intent()
    session = e.acknowledge(session, cid, intent.input_id, 'not_sent')
    assert session.executions[0].phase == e.Phase.RUNNING
    assert session.executions[0].final_input_id is None
    with pytest.raises(e.ContractError, match='DEPARTURE_NOT_EXPECTED'):
        e.observe_departure(session, cid, world(3), evidence=departure(), input_id=intent.input_id)
    session, second = e.prepare_input(session, cid, world(3), (e.PadAction('a'),),
                                      final_confirmation=True, verified_selection=selected(3))
    assert second.input_id != intent.input_id


def test_wrong_duplicate_and_late_receipts():
    session, cid, intent = final_intent()
    with pytest.raises(e.ContractError, match='INPUT_MISMATCH'):
        e.acknowledge(session, cid, 'wrong', 'sent')
    with pytest.raises(e.ContractError, match='INVALID_DISPOSITION'):
        e.acknowledge(session, cid, intent.input_id, 'timeout')
    session = e.acknowledge(session, cid, intent.input_id, 'sent')
    with pytest.raises(e.ContractError, match='NO_PENDING_INPUT'):
        e.acknowledge(session, cid, intent.input_id, 'not_sent')


@pytest.mark.parametrize('changes', [
    {'world': world(2)}, {'world': world(3, run=replace(RUN, lease_id='wrong'))},
    {'evidence': departure(order=replace(ORDER, general='将軍B'))}, {'evidence': departure(order=replace(ORDER, target='城Y'))},
    {'input_id': 'wrong'},
])
def test_departure_provenance(changes):
    session, cid, intent = departing()
    args = {'world': world(3), 'evidence': departure(), 'input_id': intent.input_id, **changes}
    with pytest.raises(e.ContractError):
        e.observe_departure(session, cid, **args)
    assert session.executions[0].phase == e.Phase.AWAITING_DEPARTURE


def test_nonfinal_input_cannot_become_departure():
    session, cid = accepted()
    session, intent = e.prepare_input(session, cid, world(2), (e.PadAction('right'),))
    session = e.acknowledge(session, cid, intent.input_id, 'unknown')
    with pytest.raises(e.ContractError, match='INPUT_MISMATCH'):
        e.observe_departure(session, cid, world(3), evidence=departure(), input_id=intent.input_id)


def test_stall_preserves_ambiguous_command_and_blocks_new_work():
    session, cid, intent = final_intent()
    session = e.mark_uncertain(session, cid)
    assert session.executions[0].pending == intent
    assert session.executions[0].command.order == ORDER
    session = e.replan(session, e.StrategyPlan(RUN, (replace(ORDER, order_id='retry'),)),
                       expected_revision=PLAN.revision)
    _, result = e.begin(session, 'retry', world(99))
    assert result.reason == 'EXECUTOR_BUSY'


def test_cancellation_only_before_any_driver_input():
    session, cid = accepted()
    session = e.cancel_before_confirmation(session, cid)
    assert session.executions[0].phase == e.Phase.CANCELLED
    with pytest.raises(e.ContractError, match='COMMAND_TERMINAL'):
        e.mark_uncertain(session, cid)
    session, cid, intent = final_intent()
    with pytest.raises(e.ContractError, match='CANCEL_NOT_SAFE'):
        e.cancel_before_confirmation(session, cid)


def test_departure_releases_ui_not_general_reservation():
    session, cid, intent = departing()
    session, _ = e.observe_departure(session, cid, world(3), evidence=departure(), input_id=intent.input_id)
    next_order = replace(ORDER, order_id='second')
    session = e.replan(session, e.StrategyPlan(RUN, (next_order,)), expected_revision=PLAN.revision)
    with pytest.raises(e.ContractError, match='STALE_OBSERVATION'):
        e.begin(session, 'second', world(3))
    _, result = e.begin(session, 'second', world(4))
    assert result.reason == 'GENERAL_RESERVED'  # arrival reconciliation not implemented yet
    next_order = replace(next_order, order_id='third', general='将軍B')
    session = e.replan(session, e.StrategyPlan(RUN, (next_order,)), expected_revision=session.plan.revision)
    session, result = e.begin(session, 'third', world(4))
    assert result.status == 'ready'
    assert len(session.executions) == 2


def test_full_journal_never_evicts_evidence():
    entries = []
    for i in range(e.MAX_EXECUTIONS):
        order = replace(ORDER, order_id=f'old-{i}')
        command = e.SortieCommand(RUN, PLAN.revision, order, 1)
        entries.append(e.Execution(command, e.Phase.CANCELLED, 1))
    session = e.Session(PLAN, tuple(entries))
    with pytest.raises(e.ContractError, match='JOURNAL_FULL'):
        e.begin(session, ORDER.order_id, world(2))
    assert len(session.executions) == e.MAX_EXECUTIONS


def test_new_run_starts_clean_and_command_ids_change():
    session, cid = accepted()
    newer = replace(RUN, generation=8)
    new_session, result = e.begin(e.Session(replace(PLAN, run=newer)), ORDER.order_id,
                                  world(run=newer))
    assert result.command_id != cid
    assert len(new_session.executions) == len(session.executions) == 1


@pytest.mark.parametrize('proof', [
    selected(1), selected(run=replace(RUN, generation=8)), departure(2),
    selected(order=replace(ORDER, general='将軍B')),
    selected(order=replace(ORDER, cards=())),
])
def test_selection_evidence_must_be_current_and_of_right_kind(proof):
    session, cid = accepted()
    with pytest.raises(e.ContractError, match='SELECTION_NOT_VERIFIED'):
        e.prepare_input(session, cid, world(2), (e.PadAction('a'),),
                         final_confirmation=True, verified_selection=proof)


@pytest.mark.parametrize('proof', [
    departure(2), selected(3), departure(run=replace(RUN, lease_id='other')),
])
def test_departure_cannot_use_stale_selection_or_other_lease(proof):
    session, cid, intent = departing()
    with pytest.raises(e.ContractError, match='DEPARTURE_MISMATCH'):
        e.observe_departure(session, cid, world(3), evidence=proof, input_id=intent.input_id)


@pytest.mark.parametrize('value', [None, [], {}, True, 1])
def test_invalid_receipt_types_are_fixed_errors(value):
    session, cid, intent = final_intent()
    with pytest.raises(e.ContractError, match='INVALID_DISPOSITION'):
        e.acknowledge(session, cid, intent.input_id, value)


def test_goal_completion_is_not_a_sortie_result_input():
    with pytest.raises(TypeError):
        e.CommandResult(None, 'departure_observed', 'test', goal_completed=True)


@pytest.mark.parametrize('proof', [replace(selected(), frame_sha256='0' * 64)])
def test_selection_frame_hash_must_match_world(proof):
    session, cid = accepted()
    with pytest.raises(e.ContractError, match='SELECTION_NOT_VERIFIED'):
        e.prepare_input(session, cid, world(2), (e.PadAction('a'),),
                         final_confirmation=True, verified_selection=proof)


def test_departure_evidence_is_kept_in_journal_after_replan():
    session, cid, intent = departing()
    with pytest.raises(e.ContractError, match='DEPARTURE_MISMATCH'):
        e.observe_departure(session, cid, world(3),
                             evidence=replace(departure(), frame_sha256='0' * 64),
                             input_id=intent.input_id)
    session, _ = e.observe_departure(session, cid, world(3),
                                      evidence=departure(), input_id=intent.input_id)
    session = e.replan(session, e.StrategyPlan(RUN, ()), expected_revision=PLAN.revision)
    entry = session.executions[0]
    assert entry.selection_evidence == selected()
    assert entry.departure_evidence == departure()
    assert entry.final_input_id == intent.input_id
