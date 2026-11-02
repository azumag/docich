"""Synthetic logs: ordinary opening plans must not include egg/cancel B."""
import pytest

from docich.hanjuku_menu_audit import REQUEST, audit


def plan(n, kind='battle', *, opening=False, buttons=None, **extra):
    return dict(event='action_plan', runtime_id='synthetic-run', generation=1,
                lease_id='synthetic-lease', bot_version='synthetic-v271',
                decision_id=f'synthetic-{n}', at=float(n), screen_kind=kind,
                reason_decisions=[REQUEST] if opening else [],
                planned_actions=[{'type': 'pad', 'buttons': buttons or ['b']}] if opening or buttons else [],
                **extra)


def test_only_explicit_human_request_counts_with_observed_menu():
    rows = [plan(0, opening=True), plan(1, 'battle_menu_pending'), plan(2, 'battle_menu'),
            plan(3, 'egg_battle_menu', buttons=['b']), plan(4, 'monster_menu', buttons=['b']),
            plan(5, buttons=['b'])]
    result = audit(rows)
    assert result['planned_openings'] == result['menu_observed'] == 1
    assert result['unconfirmed'] == 0
    assert result['excluded_unlabelled_b'] == 3
    assert result['observed_latency_seconds'] == [2.0]
    assert result['input_delivery'] == 'unconfirmed'


@pytest.mark.parametrize('change', [dict(runtime_id='next-run'), dict(generation=2),
                                    dict(lease_id='next-lease'), dict(bot_version='next-version')])
def test_identity_change_does_not_supply_receipt(change):
    receipt = plan(1, 'battle_menu')
    receipt.update(change)
    result = audit([plan(0, opening=True), receipt])
    assert result['menu_observed'] == 0
    assert result['unconfirmed'] == 1


@pytest.mark.parametrize('middle', [plan(1, 'egg_battle_menu'), plan(1, 'map'),
                                    {**plan(1), 'reason_decisions': ['battle_start']},
                                    plan(1, buttons=['b'])])
def test_boundary_or_unlabelled_input_cannot_be_bridged(middle):
    result = audit([plan(0, opening=True), middle, plan(2, 'battle_menu')])
    assert result['unconfirmed'] == 1
    assert result['menu_observed'] == 0


def test_retry_and_log_end_remain_unconfirmed_not_failed():
    result = audit([plan(0, opening=True), plan(1, opening=True)])
    assert result['planned_openings'] == result['unconfirmed'] == 2
    assert result['menu_observed_per_plan'] == 0
    assert 'failures' not in result


def test_duplicates_and_policy_records_do_not_double_count():
    start = plan(0, opening=True)
    result = audit([start, start, {'event': 'decision', 'decision': REQUEST}, plan(1, 'battle_menu')])
    assert result['planned_openings'] == result['menu_observed'] == 1


@pytest.mark.parametrize('at', [9, -1, 0, float('nan')])
def test_gap_or_invalid_time_does_not_prove_opening(at):
    receipt = plan(1, 'battle_menu')
    receipt['at'] = at
    assert audit([plan(0, opening=True), receipt])['unconfirmed'] == 1


def test_old_logs_have_no_success_rate():
    result = audit([plan(0, buttons=['b']), plan(1, 'battle_menu')])
    assert result['planned_openings'] == 0
    assert result['menu_observed_per_plan'] is None


def test_mixed_input_batch_is_not_a_single_opening():
    row = plan(0, opening=True)
    row['planned_actions'].append({'type': 'pad', 'buttons': ['a']})
    assert audit([row, plan(1, 'battle_menu')])['planned_openings'] == 0


def test_malformed_plan_breaks_receipt_chain():
    row = plan(1)
    row['planned_actions'] = 'invalid'
    result = audit([plan(0, opening=True), row, plan(2, 'battle_menu')])
    assert result['invalid_plans'] == result['unconfirmed'] == 1
    assert result['menu_observed'] == 0
