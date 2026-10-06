"""Synthetic regressions for evidence/learning boundaries, not a live run replay."""
import copy
import json

import pytest

from docich import hanjuku_experience as experience
from docich import hanjuku_review_evidence as evidence
from docich import hanjuku_chart_review as review


def memory(**battle):
    return {'chapter': 1, 'hero_max_hp': 90, 'egg_uses': {'どうし': 4},
            'battle': {'step': '1-A1', 'ally': 'どうし', 'enemy': 'ガルバンゾー',
                       'side': 'attack', 'ally_hp': 90, 'enemy_hp': 40,
                       'ally_soldiers': 6, 'enemy_soldiers': 6, **battle}}


def learned(key, actions):
    return {'schema': 1, 'situations': {key: {'actions': actions}}}


def decision(kind, **fields):
    return {'event': 'decision', 'decision': kind, 'chapter': 1, **fields}


def win(step='I:first', castle='ゴーメン', **fields):
    return decision('battle_result', chart_step=step, castle=castle,
                    side='attack', outcome='win', resulting_event=f'captured:{castle}', **fields)


def owner(value='own', castle='ゴーメン', chapter=1):
    return {'event': 'decision', 'decision': 'world_map_owners', 'chapter': chapter,
            'observed_metric': {castle: value}}


def proposal(step='I:first', target='ゴーメン'):
    return {'type': 'promote_interim_attack', 'step': step, 'target': target}


def gated(records, step='I:first', target='ゴーメン'):
    result = evidence.audit(records)
    return evidence.gate_proposals([proposal(step, target)], result,
                                   {step: {'chapter': 1, 'kind': 'interim'}})[0]


@pytest.mark.parametrize('kind', ['battle_menu', 'egg_summon', 'monster_menu'])
def test_attack_and_defense_do_not_share_learning(kind):
    attack = memory()
    defense = memory(side='defense')
    assert experience.situation_key(kind, attack) != experience.situation_key(kind, defense)


@pytest.mark.parametrize('change', [
    {'ally_hp': 10}, {'enemy_hp': 100}, {'ally_soldiers': 0},
    {'enemy_soldiers': 0}, {'side': None}])
def test_enemy_summon_key_includes_risk_and_resources(change):
    assert experience.situation_key('egg_summon', memory()) != experience.situation_key(
        'egg_summon', memory(**change))


def test_old_key_is_not_silently_migrated_to_new_context():
    mem = memory()
    key = experience.situation_key('egg_summon', mem)
    old_key = 'egg_summon|1|ガルバンゾー|どうし|1-A1'
    exp = learned(old_key, {'use_egg': {'losses': 5}, 'attack': {'wins': 20}})
    assert old_key != key
    assert experience.preferred(exp, key, default='use_egg', kind='egg_summon') == 'use_egg'
    assert old_key in experience._clean(exp)['situations']


def test_pending_egg_recheck_is_unknown_not_last_known_available():
    mem = memory()
    known = experience.situation_key('egg_summon', mem)
    mem['egg_recheck'] = ['どうし']
    unknown = experience.situation_key('egg_summon', mem)
    mem['egg_recheck'] = []
    mem['egg_uses']['どうし'] = 0
    empty = experience.situation_key('egg_summon', mem)
    assert len({known, unknown, empty}) == 3


@pytest.mark.parametrize('value', [None, True, -1, '0', 1000])
def test_resource_unknown_is_not_empty(value):
    assert experience._resource_band(value) == 'unknown'
    assert experience._resource_band(0) == 'empty'


def test_critical_hero_keeps_rescue_default_even_with_biased_history():
    key = experience.situation_key('egg_summon', memory(ally_hp=8))
    exp = learned(key, {'use_egg': {'losses': 5}, 'attack': {'wins': 20}})
    assert experience.preferred(exp, key, default='use_egg', kind='egg_summon') == 'use_egg'


@pytest.mark.parametrize('side,hp', [('defense', 90), (None, 90), ('attack', None)])
def test_no_untried_exploration_in_defense_or_unknown_context(side, hp):
    key = experience.situation_key('egg_summon', memory(side=side, ally_hp=hp))
    exp = learned(key, {'use_egg': {'losses': 1}})
    assert experience.preferred(exp, key, default='use_egg', kind='egg_summon') == 'use_egg'


def test_healthy_attack_retains_existing_exploration():
    key = experience.situation_key('egg_summon', memory())
    exp = learned(key, {'use_egg': {'losses': 1}})
    assert experience.preferred(exp, key, default='use_egg', kind='egg_summon') == 'attack'


@pytest.mark.parametrize('illegal', ['skill2', 'retreat', 'shutdown', ''])
def test_other_menu_actions_cannot_be_selected_from_history(illegal):
    key = experience.situation_key('egg_summon', memory())
    exp = learned(key, {'use_egg': {'wins': 1, 'losses': 1}, illegal: {'wins': 20}})
    assert experience.preferred(exp, key, default='use_egg', kind='egg_summon') == 'use_egg'


def test_generic_legacy_action_learning_contract_is_preserved():
    mem = {'_experience': experience.empty()}
    assert experience.record(mem, 'k', 'use_egg', 'loss')
    assert experience.record(mem, 'k', 'attack', 'win')
    assert experience.preferred(mem['_experience'], 'k', default='use_egg', kind='egg_summon') == 'attack'
    assert not experience.record(mem, 'k', 'attack', 'unclassified')


def test_key_and_preference_do_not_mutate_live_memory():
    mem = memory(ally_hp=8)
    original = copy.deepcopy(mem)
    key = experience.situation_key('egg_summon', mem)
    experience.preferred(experience.empty(), key, default='use_egg', kind='egg_summon')
    assert mem == original


def test_hp_win_and_inferred_captured_event_are_not_ownership():
    assert gated([win()])['type'] == 'review_unverified_capture'


def test_later_same_castle_owner_receipt_unlocks_promotion():
    result = gated([win(), owner()])
    assert result['type'] == 'promote_interim_attack'
    assert result['capture_evidence'] == 'observed_ownership_after_attack'


@pytest.mark.parametrize('records', [
    [owner(), win()], [win(), owner('enemy')], [win(), owner('unknown')],
    [win(), owner(chapter=2)], [win(), owner(castle='カストーラ')],
    [win(), owner(), owner('enemy')],
    [win(), win('I:other'), owner()], [win(), win(None), owner()],
])
def test_missing_stale_lost_or_ambiguous_receipts_never_promote(records):
    assert gated(records)['type'] == 'review_unverified_capture'


def test_repeated_wins_of_same_plan_are_not_competing_plans():
    assert gated([win(), win(), owner()])['type'] == 'promote_interim_attack'


def test_ownership_after_new_plan_is_not_credited_to_old_plan():
    assert gated([win(), owner(), win('I:new'), owner()])['type'] == 'review_unverified_capture'


def test_unknown_owner_does_not_invent_loss_after_confirmed_capture():
    assert gated([win(), owner(), owner('unknown')])['type'] == 'promote_interim_attack'


def test_direct_ownership_receipt_is_supported():
    receipt = decision('castle_owned_observed', castle='ゴーメン',
                       observed_metric={'world_map': 'own'})
    assert gated([win(), receipt])['type'] == 'promote_interim_attack'


def test_unassigned_defense_loss_is_visible_without_invented_death():
    result = evidence.audit([decision('battle_result', side='defense', outcome='loss'),
                             decision('battle_result', outcome='unclassified')])
    assert result['battle_outcomes'] == {'loss': 1, 'unclassified': 1}
    assert result['unattributed_battles'] == result['battle_outcomes']
    assert 'generals_lost' not in result
    assert evidence.gate_proposals([], result, {})[0]['type'] == 'review_unattributed_battles'


def test_defense_win_is_not_a_capture_candidate():
    record = win()
    record['side'] = 'defense'
    assert gated([record, owner()])['type'] == 'review_unverified_capture'


def test_malformed_records_are_not_proof():
    invalid = win()
    invalid['chapter'] = True
    result = evidence.audit([None, [], {'event': 'input_sent'}, invalid, owner(chapter=True)])
    assert result['verified_captures'] == {}


def test_failures_of_adjusted_charts_are_not_dropped():
    audit = evidence.audit([])
    result = evidence.gate_proposals([], audit, {
        'A:deadbeef:one': {'kind': 'adjusted', 'failed': 1, 'losses': 2, 'retries': 2}})
    assert result == [{'type': 'review_adjusted_failure', 'step': 'A:deadbeef:one',
                       'target': None, 'failed': 1, 'losses': 2, 'retries': 2}]


def test_audit_limits_fail_closed_for_promotions(monkeypatch):
    monkeypatch.setattr(evidence, 'MAX_TRACKED_STEPS', 1)
    result = evidence.audit([win(), owner(), win('I:other'), owner()])
    assert result['capture_evidence_overflow'] is True
    assert result['verified_captures'] == {}


def test_collate_uses_rotated_receipts_and_reports_unassigned_battles(tmp_path):
    initial = [decision('order_start', chart_step='I:first', target='ゴーメン', general='どうし'), win()]
    tail = [owner(), decision('battle_result', side='defense', outcome='loss')]
    for suffix, records in [('previous.', initial), ('', tail)]:
        path = tmp_path / f'hanjuku_decisions.{suffix}jsonl'
        path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
    result = review.collate(tmp_path)
    assert result['evidence']['battle_outcomes'] == {'win': 1, 'loss': 1}
    assert result['evidence']['unattributed_battles'] == {'loss': 1}
    kinds = [p['type'] for p in result['proposals']]
    assert 'promote_interim_attack' in kinds
    assert 'review_unattributed_battles' in kinds


def test_menu_and_selection_observations_are_not_execution_receipts():
    result = evidence.audit([decision('menu_nav_stuck'), decision('sortie_kit_mismatch'),
                             decision('battle_card_selected'), decision('battle_card_candidate')])
    assert result['counts']['battle_card_selected'] == 1
    assert result['counts']['menu_nav_stuck'] == 1
    assert result['verified_captures'] == {}
    assert result['battle_outcomes'] == {}


def test_lost_castle_is_not_a_successful_step_in_next_intro():
    rows = {'I:first': {'chapter': 1, 'target': 'ゴーメン', 'kind': 'interim', 'captured_target': True}}
    evidence.gate_proposals([proposal()], evidence.audit([win(), owner(), owner('enemy')]), rows)
    assert rows['I:first']['battle_win_at_target'] is True
    assert rows['I:first']['captured_target'] is False


def test_unconfirmed_win_does_not_hide_base_step_failure():
    rows = {'1-A1': {'chapter': 1, 'target': 'ゴーメン', 'kind': 'base',
                     'captured_target': True, 'losses': 1}}
    result = evidence.gate_proposals([], evidence.audit([win('1-A1')]), rows)
    assert result[0]['type'] == 'review_base_step'


def test_intro_uses_all_observed_battles_not_only_assigned_steps(tmp_path, monkeypatch):
    from docich import hanjuku_history as history
    report = {'steps': {'1-A1': {'wins': 2, 'losses': 0}}, 'adjusted_orders': {}, 'proposals': [],
              'evidence': {'schema': 1, 'scope': 'retained_decision_logs',
                           'battle_outcomes': {'win': 2, 'loss': 3, 'unclassified': 4}}}
    monkeypatch.setattr(history, 'latest_completed_review', lambda path: report)
    intro = history.describe_latest_run(tmp_path)
    assert '2勝3敗' in intro
    assert '勝敗未分類が4件' in intro


def test_legacy_intro_and_malformed_new_counts_keep_safe_fallback(tmp_path, monkeypatch):
    from docich import hanjuku_history as history
    report = {'steps': {'1-A1': {'wins': 2, 'losses': 1}}, 'adjusted_orders': {}, 'proposals': []}
    monkeypatch.setattr(history, 'latest_completed_review', lambda path: report)
    assert '2勝1敗' in history.describe_latest_run(tmp_path)
    report['evidence'] = {'schema': 1, 'scope': 'retained_decision_logs',
                          'battle_outcomes': {'win': True, 'loss': -4}}
    assert '2勝1敗' in history.describe_latest_run(tmp_path)


def test_empty_new_summary_cannot_erase_existing_battle_evidence(tmp_path, monkeypatch):
    from docich import hanjuku_history as history
    report = {'steps': {'1-A1': {'wins': 2, 'losses': 1}}, 'adjusted_orders': {}, 'proposals': [],
              'evidence': {'schema': 1, 'scope': 'retained_decision_logs', 'battle_outcomes': {}}}
    monkeypatch.setattr(history, 'latest_completed_review', lambda path: report)
    assert '2勝1敗' in history.describe_latest_run(tmp_path)


def test_malformed_cached_actor_does_not_crash_key_generation():
    mem = memory(ally=[])
    mem['egg_uses'] = {}
    assert 'egg=unknown' in experience.situation_key('egg_summon', mem)


def test_unknown_full_hp_does_not_enable_untried_exploration():
    mem = memory()
    mem['hero_max_hp'] = None
    key = experience.situation_key('egg_summon', mem)
    exp = learned(key, {'use_egg': {'losses': 1}})
    assert experience.preferred(exp, key, default='use_egg', kind='egg_summon') == 'use_egg'
