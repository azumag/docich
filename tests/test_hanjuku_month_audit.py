"""Synthetic month-boundary tests for the zero-sortie audit."""
from __future__ import annotations

import json
import time
from pathlib import Path

from unittest.mock import patch

import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import month_audit as audit

IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcd1234',
            'generation': 1, 'lease_id': 'synthetic-lease'}
STEP = 1.5


def state(chapter, month, tick, *, launches=0, scope=None, plan=None, world=None, flow=None):
    return {
        'policy': {
            'chapter': chapter,
            'month': month,
            'tick': tick,
            'month_start_tick': 0,
            'month_sorties': {'scope': scope if scope is not None else [chapter, month],
                              'launches': launches},
            'plan': plan if plan is not None else {'orders': {}, 'chart_plan': {}},
            'world': world if world is not None else {
                'garrison': {'S': ['G']}, 'sorties': {}, 'launched_orders': {}, 'gold': 10},
            'flow': flow if flow is not None else {},
            'stats': {},
        },
        'step': tick,
        'decision_trace': dict(IDENTITY),
        'screen_kind': 'map',
        'phase': 'field',
        'bot_version': 'synthetic-v1',
    }


def note(runtime, at, decision='situation_held', **fields):
    row = {'schema': 1, 'event': 'decision', 'at': at, 'decision': decision,
           **IDENTITY, **fields}
    with (runtime / 'hanjuku_decisions.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def run_month(runtime, chapter, month, start, count, *, launches=0, launch_at=None,
              launch_purpose='attack', **kwargs):
    last = None
    for index in range(count):
        at = start + index * STEP
        last = state(chapter, month, index, launches=launches, **kwargs)
        audit.observe(runtime, audit.capture(last), last, [], now=at)
        if launch_at == index:
            note(runtime, at, 'order_launched', chart_step='A:1:M1',
                 purpose=launch_purpose, general='G', target='T')
        else:
            note(runtime, at)
    return last


def boundary(runtime, old, new, at):
    audit.observe(runtime, audit.capture(old), new, [], now=at)


def prepare_zero(runtime):
    """One partial month, one full zero month and its boundary."""
    old = run_month(runtime, 1, '1-1', 1000, 100)
    new = state(1, '1-2', 200, launches=0)
    boundary(runtime, old, new, 1200)
    old = run_month(runtime, 1, '1-2', 1200, 100, launches=0)
    new = state(1, '1-3', 400, launches=0)
    boundary(runtime, old, new, 1380)
    return old, new


def sealed_zero(runtime):
    old, new = prepare_zero(runtime)
    later = state(1, '1-3', 500, launches=0)
    for index in range(5):
        audit.observe(runtime, audit.capture(new), later, [], now=1700 + index * STEP)
        note(runtime, 1700 + index * STEP)
    records = audit.load_incidents(runtime)
    assert len(records) == 1
    return records[0]


def test_full_zero_month_is_recorded_and_does_not_count_as_ok(runtime_dir):
    old, new = prepare_zero(runtime_dir)
    pending = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']
    record = pending['incident']
    assert record['kind'] == 'zero_sortie'
    assert record['confidence'] == 'high'
    assert record['chapter'] == 1 and record['month'] == '1-2'
    assert record['sorties']['confirmed'] == 0
    assert record['sorties']['departures'] == 0
    assert record['month_summary']['observations'] >= audit.MIN_OBSERVATIONS
    assert record['month_summary']['duration_s'] >= audit.MIN_MONTH_SECONDS
    assert record['auto_file'] is True


def test_month_with_a_departure_is_not_recorded(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=1, launch_at=40)
    boundary(runtime_dir, old, state(1, '1-3', 400), 1380)
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())
    assert saved['last_close']['kind'] == 'ok'
    assert saved['pending'] is None


def test_move_only_month_is_still_zero(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=0,
                    launch_at=40, launch_purpose='move')
    boundary(runtime_dir, old, state(1, '1-3', 400), 1380)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'zero_sortie'
    assert record['sorties']['departures'] == 0
    assert record['sorties']['moves'] == 1


def test_unconfirmed_departure_is_recorded_but_not_auto_filed(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=0, launch_at=40)
    boundary(runtime_dir, old, state(1, '1-3', 400), 1380)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'unconfirmed_launches'
    assert record['sorties']['confirmed'] == 0
    assert record['sorties']['departures'] == 1
    assert record['auto_file'] is False


def test_first_partial_month_is_skipped(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())
    assert saved['last_close']['kind'] == 'skipped'
    assert saved['pending'] is None


def test_stale_ledger_is_recorded_as_unverified(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200, scope=[1, '1-2']), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=0, scope=[1, '1-0'])
    boundary(runtime_dir, old, state(1, '1-3', 400), 1380)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'zero_unverified'
    assert record['confidence'] == 'low'
    assert 'ledger_scope_mismatch' in record['confidence_reasons']
    assert record['sorties']['confirmed'] is None
    assert record['auto_file'] is False


def test_chapter_change_is_skipped_and_next_month_is_partial(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=1, launch_at=10)
    boundary(runtime_dir, old, state(2, '1-1', 400), 1380)
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())
    assert saved['last_close']['kind'] == 'skipped'
    assert saved['last_close']['confidence'] == 'low'
    assert saved['observed']['started_clean'] is False
    assert saved['pending'] is None


def test_observation_gap_downgrades_a_zero_month(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=0)
    # A 100 s outage is longer than the accepted observation gap.
    boundary(runtime_dir, old, state(1, '1-3', 400), 1448.5)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'zero_unverified'
    assert 'observation_gap' in record['confidence_reasons']
    assert record['auto_file'] is False


def test_short_month_is_not_auto_filed(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 60, launches=0)
    boundary(runtime_dir, old, state(1, '1-3', 400), 1300)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'zero_unverified'
    assert 'short_month' in record['confidence_reasons']


def test_observe_reports_the_sealed_incident_to_the_caller(runtime_dir):
    old, new = prepare_zero(runtime_dir)
    later = state(1, '1-3', 500, launches=0)
    seen = []
    audit.observe(runtime_dir, audit.capture(new), later, [], now=1700,
                  on_sealed=seen.append)
    assert len(seen) == 1
    assert seen[0]['kind'] == 'zero_sortie'
    assert seen[0]['auto_file'] is True


def test_spawn_issue_filer_detaches_with_the_incident_id(runtime_dir, monkeypatch):
    monkeypatch.delenv('DOCICH_DISABLE_ISSUE_FILER', raising=False)
    record = {'auto_file': True, 'incident_id': 'abc123'}
    with patch.object(audit.subprocess, 'Popen') as popen:
        assert audit.spawn_issue_filer(runtime_dir, record) is True
    popen.assert_called_once()
    command = popen.call_args.args[0]
    assert command[:3] == [audit.sys.executable, '-m', 'docich.hanjuku.month_audit']
    assert '--incident' in command and 'abc123' in command
    assert popen.call_args.kwargs['start_new_session'] is True
    assert popen.call_args.kwargs['stdin'] == audit.subprocess.DEVNULL
    assert (runtime_dir / audit.ISSUE_LOG).exists()


def test_spawn_issue_filer_skips_non_auto_file_records(runtime_dir):
    with patch.object(audit.subprocess, 'Popen') as popen:
        assert audit.spawn_issue_filer(runtime_dir, {'auto_file': False,
                                                     'incident_id': 'x'}) is False
    popen.assert_not_called()


def test_spawn_issue_filer_survives_a_process_start_failure(runtime_dir):
    with patch.object(audit.subprocess, 'Popen', side_effect=OSError('no gh')):
        assert audit.spawn_issue_filer(runtime_dir, {'auto_file': True,
                                                     'incident_id': 'x'}) is False


def test_pending_is_sealed_once_and_has_after_window(runtime_dir):
    record = sealed_zero(runtime_dir)
    assert record['after']['complete'] is True
    assert record['after']['early'] is False
    before = len(audit.load_incidents(runtime_dir))
    later = state(1, '1-3', 600, launches=0)
    for index in range(3):
        audit.observe(runtime_dir, audit.capture(later), later, [], now=1800 + index * STEP)
    assert len(audit.load_incidents(runtime_dir)) == before


def test_new_boundary_seals_the_previous_pending_early(runtime_dir):
    prepare_zero(runtime_dir)
    # The 1-3 month closes before the 1-2 after window ends.
    last = state(1, '1-3', 500, launches=0)
    boundary(runtime_dir, last, state(1, '1-4', 600, launches=0), 1500)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['after']['early'] is True
    assert records[0]['month'] == '1-2'


def test_malformed_state_does_not_raise(runtime_dir):
    (runtime_dir / audit.AUDIT_FILE).write_text('{not json', encoding='utf-8')
    value = audit.observe(runtime_dir, None, {'policy': {}}, [], now=1.0)
    assert value is None
    value = audit.observe(runtime_dir, None, state(1, '1-1', 0), [], now=1.0)
    assert value is not None


def test_capture_is_bounded(runtime_dir):
    plan = {'orders': {f'S{i}': 'pending' for i in range(100)},
            'chart_plan': {'orders': [
                {'step': f'S{i}', 'note': 'x' * 400, 'general': 'g' * 100}
                for i in range(100)]}}
    world = {'garrison': {f'C{i}': [f'G{j}' for j in range(100)] for i in range(100)},
             'sorties': {f'S{i}': {'status': 'en_route', 'tick': 0} for i in range(100)},
             'launched_orders': {f'S{i}': {'target': 'T'} for i in range(100)},
             'captured': [f'C{i}' for i in range(100)]}
    value = audit.capture(state(1, '1-1', 10, plan=plan, world=world))
    assert len(value['plan']['orders']) <= 60
    assert len(value['plan']['chart_plan']['orders']) <= audit.MAX_LIST
    assert len(value['world']['garrison']) <= 12
    assert all(len(names) <= 12 for names in value['world']['garrison'].values())
    assert len(value['world']['sorties']) <= audit.MAX_LIST


def _boss_state(chapter=9, month='1-1', tick=0, captured=None):
    value = state(chapter, month, tick)
    value['policy']['captured'] = list(captured if captured is not None else chart.corridor(9))
    value['policy']['boss_cell'] = {'cell': [10, 10], 'chapter': chapter, 'tick': 0}
    return value


def test_boss_phase_is_the_last_boss_target_in_chapter_nine():
    phase = audit._boss_phase(_boss_state()['policy'])
    assert phase['chapter'] == 9
    assert phase['boss_name'] == 'ハデス'
    assert phase['bosses'] == ['ボス']
    missing = _boss_state(captured=list(chart.corridor(9))[:-1])['policy']
    assert audit._boss_phase(missing) is None


def test_boss_phase_generic_chapter_and_defeat():
    chapter = 7
    bosses = set(chart.boss_sites(chapter))
    normal = [name for name in chart.castles(chapter) if name not in bosses]
    value = state(chapter, '1-1', 0)
    value['policy']['captured'] = normal
    assert audit._boss_phase(value['policy'])['bosses'] == ['ボス']
    value['policy']['boss_defeated'] = chapter
    assert audit._boss_phase(value['policy']) is None


def test_boss_phase_ends_when_the_boss_is_owned():
    value = _boss_state()
    value['policy']['captured'] = list(chart.corridor(9)) + ['ボス']
    assert audit._boss_phase(value['policy']) is None


def test_boss_battle_evidence_is_not_only_battle_start():
    value = _boss_state()['policy']
    assert audit._boss_battle_seen(value, [], 9, 'ハデス') is False
    assert audit._boss_battle_seen(value, [{'decision': 'battle_start', 'enemy': 'ハデス'}],
                                   9, 'ハデス') is True
    value['boss_hp_seen'] = {'hp': 100, 'castle': 'ボス', 'tick': 1}
    assert audit._boss_battle_seen(value, [], 9, 'ハデス') is True
    value['boss_hp_seen'] = None
    value['boss_victories'] = {'ハデス': {'tick': 1}}
    assert audit._boss_battle_seen(value, [], 9, 'ハデス') is True


def test_chapter_four_season_king_battle_counts_as_boss_evidence():
    from docich.hanjuku_map_sources import SEASON_KINGS
    king = sorted(SEASON_KINGS)[0]
    rows = [{'decision': 'battle_start', 'enemy': king}]
    assert audit._rows_boss_battle(rows, 4, '四季王') is True
    # Season kings are chapter 4 evidence only; the same name in another
    # chapter's window must not suppress its boss incident.
    assert audit._rows_boss_battle(rows, 9, 'ハデス') is False


def _run_boss(runtime, count=401, *, battle_at=None, hp=False,
              live_sortie=False, sortie_age=10):
    for index in range(count):
        at = 1000 + index * STEP
        value = _boss_state(month='1-1', tick=index)
        if hp:
            value['policy']['boss_hp_seen'] = {'hp': 50, 'castle': 'ボス', 'tick': index}
        if live_sortie:
            value['policy']['tick'] = index + sortie_age
            value['policy']['world']['sorties'] = {
                'boss-en-route': {
                    'status': 'en_route', 'general': 'G1', 'source': 'S',
                    'target': 'ボス', 'purpose': 'attack', 'tick': index,
                },
                'boss-unconfirmed': {
                    'status': 'launched_unconfirmed', 'general': 'G2', 'source': 'S',
                    'target': 'ボス', 'purpose': None, 'tick': index,
                },
            }
            value['policy']['world']['launched_orders'] = {
                'boss-unconfirmed': {
                    'target': 'ボス', 'source': 'S', 'general': 'G2',
                    'purpose': 'attack', 'tick': index,
                },
            }
        records = []
        if battle_at == index:
            records = [{'decision': 'battle_start', 'enemy': 'ハデス', 'at': at}]
        audit.observe(runtime, audit.capture(value), value, records, now=at)
    return value


def test_boss_phase_without_a_battle_is_recorded_after_the_grace(runtime_dir):
    _run_boss(runtime_dir)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'boss_phase_no_battle'
    assert record['confidence'] == 'high'
    assert record['auto_file'] is True
    assert record['boss_phase']['battle_count'] == 0
    assert record['boss_phase']['boss_name'] == 'ハデス'
    assert record['boss_phase']['observed_s'] >= audit.BOSS_NO_BATTLE_SECONDS
    assert 'ボスフェーズでボス戦闘ゼロ' in audit._issue_title(record)
    assert '### ボスフェーズ' in audit.render(record)


def test_boss_phase_with_a_battle_is_not_recorded(runtime_dir):
    _run_boss(runtime_dir, battle_at=100)
    assert audit.load_incidents(runtime_dir) == []


def test_boss_battle_in_the_decision_log_prevents_the_record(runtime_dir):
    note(runtime_dir, 1000, 'battle_start', enemy='ハデス')
    _run_boss(runtime_dir)
    assert audit.load_incidents(runtime_dir) == []


def test_boss_phase_with_hp_evidence_is_not_recorded(runtime_dir):
    _run_boss(runtime_dir, hp=True)
    assert audit.load_incidents(runtime_dir) == []


def test_boss_render_comment_has_the_boss_line(runtime_dir):
    _run_boss(runtime_dir)
    record = audit.load_incidents(runtime_dir)[0]
    text = audit.render_comment(record)
    assert 'ボス:' in text and 'ハデス' in text
    assert '翌5分' not in text


def test_file_creates_a_boss_issue(runtime_dir):
    _run_boss(runtime_dir)
    gh = FakeGh()
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True, now=time.time())
    assert result['status'] == 'created'
    create = [call for call in gh.calls if call[:2] == ['issue', 'create']]
    assert len(create) == 1


def test_boss_phase_leaving_clears_the_tracker(runtime_dir):
    _run_boss(runtime_dir, count=20)
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())
    assert saved['boss']['reported'] is False
    value = _boss_state(tick=20)
    value['policy']['captured'] = list(chart.corridor(9)) + ['ボス']
    audit.observe(runtime_dir, audit.capture(value), value, [], now=1030)
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())
    assert saved['boss'] is None
    assert saved['last_boss']['chapter'] == 9


def _camp_record(decision, *, reason='帰還できなかった', event=None, metric=None, general=None):
    return {'decision': decision, 'reason': reason, 'resulting_event': event,
            'observed_metric': metric or {}, 'general': general,
            'screen_kind': 'map', 'at': 1000.0}


def test_camp_terminal_decisions_are_classified():
    failed = audit._camp_event(_camp_record(
        'camp_recall_aborted', reason='野営の帰還指示が上限内に完了しない',
        metric={'stage': 'menu', 'steps': 91}))
    assert failed['kind'] == 'camp_recall_failed'
    assert failed['reason_code'] == 'camp_recall_limit'
    cancelled = audit._camp_event(_camp_record(
        'camp_recall_skipped', reason='可視範囲に自軍城の屋根がなく帰還先を選べない'))
    assert cancelled['kind'] == 'camp_recall_cancelled'
    assert cancelled['reason_code'] == 'camp_no_roof'
    unconfirmed = audit._camp_event(_camp_record(
        'camp_recall_unconfirmed', event='arrival_unconfirmed',
        metric={'castle': 'アルマムーン', 'input_sent': True}))
    assert unconfirmed['kind'] == 'camp_recall_unconfirmed'
    assert unconfirmed['reason_code'] == 'camp_arrival_unconfirmed'
    assert unconfirmed['input_sent'] is True
    dispatch = audit._camp_event(_camp_record(
        'camp_recall_unconfirmed', event='dispatch_unconfirmed',
        reason='帰還の本人・移動・到着を確認できず未確認として保持',
        metric={'castle': 'アルマムーン', 'camp': [134, 120],
                'stage': 'await_dispatch', 'steps': 6,
                'input_sent': False, 'picker_closed': False,
                'escape_move': False}))
    assert dispatch['reason_code'] == 'camp_dispatch_unconfirmed'
    assert dispatch['resulting_event'] == 'dispatch_unconfirmed'
    assert dispatch['stage'] == 'await_dispatch' and dispatch['steps'] == 6
    assert dispatch['camp'] == [134, 120]
    assert dispatch['picker_closed'] is False and dispatch['escape_move'] is False
    assert dispatch['input_receipt_confirmed'] is False
    assert audit._camp_event(_camp_record(
        'camp_recall_unconfirmed', event='dispatch_unconfirmed',
        metric={'escape_move': True})) is None
    escape = audit._camp_event(_camp_record(
        'camp_escape_move_unconfirmed', event='field_move_unconfirmed',
        metric={'escape_move': True, 'camp': [134, 120], 'target_cell': [135, 121],
                'stage': 'await_dispatch', 'steps': 6, 'input_sent': False}))
    assert escape['kind'] == 'camp_escape_move_unconfirmed'
    assert escape['reason_code'] == 'camp_escape_move_unconfirmed'
    assert escape['escape_move'] is True and escape['camp'] == [134, 120]
    assert escape['target_cell'] == [135, 121]
    assert audit._camp_event(_camp_record(
        'camp_escape_move_unconfirmed', event='field_move_unconfirmed',
        metric={'escape_move': False})) is None
    assert audit._camp_event(_camp_record(
        'camp_escape_move_dispatched', event='field_move_accepted',
        metric={'escape_move': True})) is None
    group = audit._camp_event(_camp_record(
        'camp_group_screen', event='unclassified_screen_closed', reason='未分類'))
    assert group['kind'] == 'camp_recall_cancelled'
    assert group['reason_code'] == 'camp_unclassified_screen'
    assert audit._camp_event(_camp_record('hero_recall_skipped')) is None
    assert audit._camp_event(_camp_record('retreat_recheck_invalidated')) is None


def test_camp_failure_is_recorded_and_titled(runtime_dir):
    value = state(9, '1-1', 0)
    seen = []
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_camp_record('camp_recall_aborted', reason='上限', metric={
                      'stage': 'fort_status', 'steps': 91, 'general': 'ヴィーナス',
                      'fort_status_reads': 0, 'fort_status_observations': 90,
                      'fort_list_observations': 2, 'fort_menu_observations': 1,
                      'screen_kind': 'text', 'screen_text': 'しろのなかにいます',
                      'observed_general': 'エピィ'})],
                  now=1000, on_sealed=seen.append)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'camp_recall_failed'
    assert record['auto_file'] is True
    assert record['camp_event']['reason_code'] == 'camp_recall_limit'
    assert record['camp_event']['general'] == 'ヴィーナス'
    assert record['camp_event']['fort_status_reads'] == 0
    assert record['camp_event']['fort_status_observations'] == 90
    assert record['camp_event']['screen'] == 'text'
    assert record['camp_event']['observed_general'] == 'エピィ'
    rendered = audit.render(record)
    assert 'キャンプ帰還失敗' in audit._issue_title(record)
    assert '### キャンプ帰還' in rendered
    assert '砦観測: menu 1 / list 2 / status 90 / status一致 0' in rendered
    assert 'しろのなかにいます' in rendered
    assert len(seen) == 1


def test_camp_cancel_and_unconfirmed_are_recorded(runtime_dir):
    value = state(9, '1-1', 0)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_camp_record('camp_recall_skipped',
                                reason='可視範囲に自軍城の屋根がなく帰還先を選べない')],
                  now=1000)
    value = state(9, '1-1', 1)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_camp_record('camp_recall_unconfirmed', event='arrival_unconfirmed')],
                  now=2000)
    kinds = [row['kind'] for row in audit.load_incidents(runtime_dir)]
    assert kinds == ['camp_recall_cancelled', 'camp_recall_unconfirmed']


def test_camp_dispatch_unconfirmed_issue_renders_stage_and_status(runtime_dir):
    value = state(9, '1-1', 0)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_camp_record(
                      'camp_recall_unconfirmed', event='dispatch_unconfirmed',
                      reason='帰還Aの送信receiptを確認できず、送信段階で未確認として保持',
                      metric={'camp': [134, 120], 'castle': None,
                              'stage': 'await_dispatch', 'steps': 6,
                              'input_sent': False, 'picker_closed': False,
                              'input_receipt_confirmed': False,
                              'escape_move': False, 'interrupted': False})],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    event = record['camp_event']
    assert event['reason_code'] == 'camp_dispatch_unconfirmed'
    assert event['resulting_event'] == 'dispatch_unconfirmed'
    assert event['stage'] == 'await_dispatch' and event['steps'] == 6
    assert event['input_receipt_confirmed'] is False
    body = audit.render(record)
    assert 'status: dispatch_unconfirmed' in body
    assert 'escape_move: False' in body
    assert 'input_receipt_confirmed: False' in body


def test_camp_escape_move_failure_has_a_separate_issue_kind(runtime_dir):
    value = state(9, '1-1', 0)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_camp_record(
                      'camp_escape_move_unconfirmed', event='field_move_unconfirmed',
                      reason='崖離脱のフィールド移動の送信・画面遷移を確認できず',
                      metric={'camp': [134, 120], 'castle': None,
                              'stage': 'await_dispatch', 'steps': 6,
                              'target_cell': [135, 121],
                              'input_sent': False, 'picker_closed': False,
                              'escape_move': True, 'interrupted': False})],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['kind'] == 'camp_escape_move_unconfirmed'
    assert '野営の崖離脱移動未確認' in audit._issue_title(record)
    assert '### 野営の崖離脱移動' in audit.render(record)
    assert 'target_cell: [135, 121]' in audit.render(record)
    assert '- 崖離脱: unconfirmed / camp_escape_move_unconfirmed' in audit.render_comment(record)


def test_camp_repeat_is_cooled_down(runtime_dir):
    value = state(9, '1-1', 0)
    record = _camp_record('camp_recall_skipped',
                          reason='可視範囲に自軍城の屋根がなく帰還先を選べない')
    for now in (1000, 1100):
        audit.observe(runtime_dir, audit.capture(value), value, [record], now=now)
    assert len(audit.load_incidents(runtime_dir)) == 1
    audit.observe(runtime_dir, audit.capture(value), value, [record],
                  now=1000 + audit.CAMP_ISSUE_COOLDOWN_SECONDS + 1)
    assert len(audit.load_incidents(runtime_dir)) == 2


def _sortie_record(decision, *, reason=None, deviation=None, metric=None,
                   general=None, step='A:1:1', variant=None, event=None):
    return {'decision': decision, 'reason': reason, 'deviation_reason': deviation,
            'observed_metric': metric or {}, 'general': general,
            'chart_step': step, 'strategy_variant': variant,
            'resulting_event': event, 'screen_kind': 'map', 'at': 1000.0}


def test_sortie_terminal_decisions_are_classified():
    failed = audit._sortie_event(_sortie_record(
        'sortie_launch_failed', reason='出撃確定後に部隊が現れない',
        metric={'misses': 6}, general='キャンディー'))
    assert failed['kind'] == 'sortie_failed'
    assert failed['reason_code'] == 'sortie_launch_not_observed'
    assert failed['general'] == 'キャンディー'
    order = audit._sortie_event(_sortie_record(
        'order_failed', deviation='出撃元で決定しても城メニューが3回開かない'))
    assert order['kind'] == 'sortie_failed'
    assert order['reason_code'] == 'sortie_source_menu'
    cancelled = audit._sortie_event(_sortie_record('sortie_cancelled_observed'))
    assert cancelled['kind'] == 'sortie_cancelled'
    assert cancelled['reason_code'] == 'sortie_unit_stayed'
    retry = audit._sortie_event(_sortie_record(
        'situation_held', variant='attack_retry_exhausted'))
    assert retry['kind'] == 'sortie_failed'
    assert retry['reason_code'] == 'sortie_retry_exhausted'
    assert audit._sortie_event(_sortie_record('sortie_launch_unverified')) is None
    assert audit._sortie_event(_sortie_record('order_retry')) is None
    assert audit._sortie_event(_sortie_record('situation_held')) is None


def test_sortie_failure_is_recorded_and_titled(runtime_dir):
    value = state(9, '1-1', 0)
    seen = []
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('sortie_launch_failed',
                                  reason='出撃確定後に部隊が現れない',
                                  metric={'misses': 6}, general='キャンディー')],
                  now=1000, on_sealed=seen.append)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'sortie_failed'
    assert record['auto_file'] is True
    assert record['sortie_event']['general'] == 'キャンディー'
    assert '出撃失敗' in audit._issue_title(record)
    assert '### 出撃失敗 / キャンセル' in audit.render(record)
    assert len(seen) == 1


def test_cancel_after_a_launch_in_the_month_is_not_filed(runtime_dir):
    # docich#5136 (owner 2026-11-23): 同じ月に発射が成立しているキャンセルは
    # 再試行が効いた一過性の事象であり、停滞として起票しない。
    value = state(9, '1-1', 0, launches=2)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('sortie_cancelled_observed', general='ヘスティア')],
                  now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_cancel_without_any_launch_is_still_filed(runtime_dir):
    value = state(9, '1-1', 0, launches=0)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('sortie_cancelled_observed', general='ヘスティア')],
                  now=1000)
    assert audit.load_incidents(runtime_dir)[0]['kind'] == 'sortie_cancelled'


def test_failed_order_in_a_month_that_launched_is_not_high(runtime_dir):
    # docich#5162 (owner 2026-11-23): 同月に発射が成立している単発の失敗は
    # high で起票せず、台帳に medium として残す（0 発射の月は high のまま）。
    value = state(9, '1-1', 0, launches=2)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('order_failed', deviation='出撃要求が送信・受理されない')],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['sortie_event']['reason_code'] == 'sortie_order_unaccepted'
    assert record['confidence'] == 'medium'
    assert record['confidence_reasons'] == ['sortie_month_launched']


def test_failed_order_without_a_launch_stays_high(runtime_dir):
    value = state(9, '1-1', 0, launches=0)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('order_failed', deviation='出撃要求が送信・受理されない')],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['confidence'] == 'high'
    assert record['confidence_reasons'] == []


def test_unaccepted_order_with_stale_month_scope_stays_high(runtime_dir):
    value = state(9, '1-1', 0, launches=2, scope=[9, '1-0'])
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('order_failed', deviation='出撃要求が送信・受理されない')],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['confidence'] == 'high'
    assert record['confidence_reasons'] == []


def test_confirmed_launch_failure_stays_high_even_if_month_launched(runtime_dir):
    value = state(9, '1-1', 0, launches=2)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('sortie_launch_failed',
                                  reason='出撃確定後に部隊が現れない',
                                  metric={'misses': 6})],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['confidence'] == 'high'
    assert record['confidence_reasons'] == []


def test_exhausted_battle_retry_stays_high_even_if_month_launched(runtime_dir):
    value = state(9, '1-1', 0, launches=2)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('situation_held', variant='attack_retry_exhausted')],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['confidence'] == 'high'
    assert record['confidence_reasons'] == []


def test_other_order_failure_stays_high_even_if_month_launched(runtime_dir):
    value = state(9, '1-1', 0, launches=2)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('order_failed', deviation='出撃元で決定しても城メニューが3回開かない')],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['confidence'] == 'high'
    assert record['confidence_reasons'] == []


def test_unknown_order_failure_stays_high_even_if_month_launched(runtime_dir):
    value = state(9, '1-1', 0, launches=2)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('order_failed', deviation='unexpected order failure')],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['sortie_event']['reason_code'] == 'sortie_order_failed'
    assert record['confidence'] == 'high'
    assert record['confidence_reasons'] == []


def test_egg_drop_only_loss_is_not_high_confidence(runtime_dir):
    # docich#5165: 卵落とし型しか残っていない敗北は、署名未校正の検出限界として
    # medium に留める（実際に札を無駄にした証拠ではない）。
    value = state(9, '1-1', 0)
    record = _battle_record('battle_result', outcome='loss',
                            metric={'cards_selected': ['イッテツーン'], 'cards_used': [],
                                    'ally_hp': 0, 'enemy_hp': 40}, general='エピィ')
    audit.observe(runtime_dir, audit.capture(value), value, [record], now=1000)
    incident = audit.load_incidents(runtime_dir)[0]
    assert incident['confidence'] == 'medium'
    assert incident['confidence_reasons'] == ['cards_unconfirmed_signature']


def test_plain_card_loss_stays_high_confidence(runtime_dir):
    value = state(9, '1-1', 0)
    record = _battle_record('battle_result', outcome='loss',
                            metric={'cards_selected': ['クースカン'], 'cards_used': [],
                                    'ally_hp': 0, 'enemy_hp': 40}, general='エピィ')
    audit.observe(runtime_dir, audit.capture(value), value, [record], now=1000)
    incident = audit.load_incidents(runtime_dir)[0]
    assert incident['confidence'] == 'high'


def test_unclassified_only_card_loss_is_not_high_confidence(runtime_dir):
    # docich#5158: 選択したが使用告知の署名が未校正で確定できない札だけが残る敗北は、
    # 検出限界シグナルとして medium に留める。
    value = state(9, '1-1', 0)
    record = _battle_record('battle_result', outcome='loss',
                            metric={'cards_selected': ['ブラッキー'], 'cards_used': [],
                                    'cards_unclassified': ['ブラッキー'],
                                    'ally_hp': 0, 'enemy_hp': 40}, general='エピィ')
    audit.observe(runtime_dir, audit.capture(value), value, [record], now=1000)
    incident = audit.load_incidents(runtime_dir)[0]
    assert incident['confidence'] == 'medium'
    assert incident['confidence_reasons'] == ['cards_unconfirmed_signature']


def test_unclassified_card_beside_a_plain_card_stays_high(runtime_dir):
    value = state(9, '1-1', 0)
    record = _battle_record('battle_result', outcome='loss',
                            metric={'cards_selected': ['ブラッキー', 'クースカン'],
                                    'cards_used': [],
                                    'cards_unclassified': ['ブラッキー'],
                                    'ally_hp': 0, 'enemy_hp': 40}, general='エピィ')
    audit.observe(runtime_dir, audit.capture(value), value, [record], now=1000)
    incident = audit.load_incidents(runtime_dir)[0]
    assert incident['confidence'] == 'high'


def test_sortie_cancel_is_recorded(runtime_dir):
    value = state(9, '1-1', 0)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('sortie_cancelled_observed', general='カシス')],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['kind'] == 'sortie_cancelled'
    assert '出撃キャンセル' in audit._issue_title(record)


def test_sortie_repeat_is_cooled_down(runtime_dir):
    value = state(9, '1-1', 0)
    record = _sortie_record('order_failed', deviation='城メニューが開かない')
    for now in (1000, 1100):
        audit.observe(runtime_dir, audit.capture(value), value, [record], now=now)
    assert len(audit.load_incidents(runtime_dir)) == 1
    audit.observe(runtime_dir, audit.capture(value), value, [record],
                  now=1000 + audit.SORTIE_ISSUE_COOLDOWN_SECONDS + 1)
    assert len(audit.load_incidents(runtime_dir)) == 2



def test_sortie_order_failure_skips_move_orders(runtime_dir):
    value = state(9, '1-1', 0,
                  plan={'orders': {}, 'chart_plan': {'orders': [
                      {'step': 'A:1:1', 'purpose': 'move', 'target': 'X'}]}})
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_sortie_record('order_failed', deviation='城メニューが開かない')],
                  now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_interim_move_order_failure_is_skipped_from_record_purpose(runtime_dir):
    # Interim orders are absent from chart_plan; the terminal record must carry
    # the purpose so the existing non-sortie exclusion still applies.
    value = state(3, '6-2', 0,
                  plan={'orders': {}, 'chart_plan': {'orders': []}})
    record = _sortie_record(
        'order_failed', deviation='移動元に残す将軍がいない',
        step='I:743437:328a9db8')
    record.update(purpose='move', general='アテナ',
                  source='パタゴーヌ', target='アルマムーン')
    audit.observe(runtime_dir, audit.capture(value), value, [record], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_interim_attack_order_failure_is_still_filed(runtime_dir):
    value = state(3, '6-2', 0,
                  plan={'orders': {}, 'chart_plan': {'orders': []}})
    record = _sortie_record(
        'order_failed', deviation='城メニューが開かない',
        step='I:123456:abcdef')
    record.update(purpose='attack', target='敵城')
    audit.observe(runtime_dir, audit.capture(value), value, [record], now=1000)
    incident = audit.load_incidents(runtime_dir)[0]
    assert incident['sortie_event']['purpose'] == 'attack'

def _battle_record(decision, *, outcome=None, metric=None, general=None,
                   choice=None, card=None, step='A:1:1'):
    return {'decision': decision, 'outcome': outcome, 'observed_metric': metric or {},
            'general': general, 'choice': choice, 'card': card,
            'chart_step': step, 'reason': 'battle result', 'at': 1000.0}


def test_battle_resource_events_are_classified():
    loss_egg = audit._battle_resource_event(_battle_record(
        'battle_result', outcome='loss',
        metric={'egg_uses': 2, 'ally_hp': 0, 'enemy_hp': 40}, general='バゲット'))
    assert loss_egg['kind'] == 'battle_loss_with_resources'
    assert loss_egg['resources'] == ['egg']
    loss_card = audit._battle_resource_event(_battle_record(
        'battle_result', outcome='loss',
        metric={'cards_selected': ['A', 'B'], 'cards_used': ['A'],
                'ally_hp': 0, 'enemy_hp': 40}))
    assert loss_card['kind'] == 'battle_loss_with_resources'
    assert loss_card['resources'] == ['card']
    assert loss_card['cards_remaining'] == ['B']
    used = audit._battle_resource_event(_battle_record(
        'battle_result', outcome='loss',
        metric={'cards_selected': ['A'], 'cards_used': ['A'],
                'ally_hp': 0, 'enemy_hp': 40}))
    assert used is None
    win = audit._battle_resource_event(_battle_record(
        'battle_result', outcome='win', metric={'egg_uses': 2}))
    assert win is None
    retreat = audit._battle_resource_event(_battle_record(
        'battle_hero_retreat_select', choice='たいきゃく',
        metric={'egg_uses': 2, 'ally_hp': 5, 'enemy_hp': 50}, general='ブラウニー'))
    assert retreat['kind'] == 'battle_retreat_with_resources'
    assert retreat['resources'] == ['egg']
    preempted = audit._battle_resource_event(_battle_record(
        'battle_retreat_preempted_card', card='クースカン',
        metric={'ally_hp': 12, 'enemy_hp': 41}, general='マフィン'))
    assert preempted['resources'] == ['card']
    # docich#5101: the remaining-card alert keeps firing (the retreat with a
    # card left is the signal), but the record now says whether the card was
    # already tried, so triage can separate a failed spend attempt from a card
    # that was never used.
    attempted = audit._battle_resources(_battle_record(
        'battle_hero_retreat_select', choice='たいきゃく',
        metric={'ally_hp': 55, 'enemy_hp': 63,
                'cards_selected': ['イッテツーン'], 'cards_used': [],
                'cards_attempted': ['イッテツーン'],
                'cards_spend_retry': ['イッテツーン']}, general='アニス'))
    assert attempted['cards_remaining'] == ['イッテツーン']
    assert attempted['cards_attempted'] == ['イッテツーン']
    assert attempted['cards_spend_retry'] == ['イッテツーン']
    assert audit._battle_resource_event(_battle_record(
        'battle_hero_retreat_select', choice='たいきゃく',
        metric={'ally_hp': 55, 'enemy_hp': 63,
                'cards_selected': ['イッテツーン'], 'cards_used': []},
        general='アニス'))['resources'] == ['card']
    # docich#5130 (owner 2026-11-23): 即死帯では札を使わず退却するのが設計。
    # その退却を「資源を残した無駄」として起票しない。
    for floor_hp in (1, 2, 4):
        assert audit._battle_resource_event(_battle_record(
            'battle_hero_retreat_select', choice='たいきゃく',
            metric={'ally_hp': floor_hp, 'enemy_hp': 8,
                    'cards_selected': ['クースカン'], 'cards_used': []},
            general='リースリング')) is None
    # 即死帯の外では従来どおり起票する。
    assert audit._battle_resource_event(_battle_record(
        'battle_hero_retreat_select', choice='たいきゃく',
        metric={'ally_hp': 5, 'enemy_hp': 8,
                'cards_selected': ['クースカン'], 'cards_used': []},
        general='リースリング'))['resources'] == ['card']
    # docich#5165: 卵落とし型の残り札は「消費確定不能」として区別する。
    # v129 の安全則（敵HP低下では消費を確定しない）は変えない。
    egg_drop = audit._battle_resources(_battle_record(
        'battle_hero_retreat_select', choice='たいきゃく',
        metric={'ally_hp': 30, 'enemy_hp': 20,
                'cards_selected': ['イッテツーン'], 'cards_used': []}))
    assert egg_drop['cards_remaining'] == ['イッテツーン']
    assert egg_drop['cards_unconfirmed_egg_drop'] == ['イッテツーン']
    plain = audit._battle_resources(_battle_record(
        'battle_hero_retreat_select', choice='たいきゃく',
        metric={'ally_hp': 30, 'enemy_hp': 20,
                'cards_selected': ['クースカン'], 'cards_used': []}))
    assert plain['cards_remaining'] == ['クースカン']
    assert plain['cards_unconfirmed_egg_drop'] == []
    event = audit._battle_resource_event(_battle_record(
        'battle_hero_retreat_select', choice='たいきゃく',
        metric={'ally_hp': 30, 'enemy_hp': 20,
                'cards_selected': ['イッテツーン'], 'cards_used': []}))
    assert event['cards_unconfirmed_egg_drop'] == ['イッテツーン']
    # docich#5117: 実一覧で不在が確認できた札は、残り札として起票しない。
    assert audit._battle_resource_event(_battle_record(
        'battle_hero_retreat_select', choice='たいきゃく',
        metric={'ally_hp': 16, 'enemy_hp': 4,
                'cards_selected': ['クースカン'], 'cards_used': [],
                'cards_missing': ['クースカン']}, general='ライム')) is None
    mixed = audit._battle_resources(_battle_record(
        'battle_hero_retreat_select', choice='たいきゃく',
        metric={'ally_hp': 16, 'enemy_hp': 4,
                'cards_selected': ['クースカン', 'イッテツーン'],
                'cards_used': [], 'cards_missing': ['クースカン']}))
    assert mixed['cards_remaining'] == ['イッテツーン']
    assert mixed['cards_missing'] == ['クースカン']
    assert audit._battle_resource_event(_battle_record(
        'battle_egg_before_retreat', metric={'egg_uses': 2})) is None


def test_battle_loss_with_egg_is_recorded_and_titled(runtime_dir):
    value = state(10, '1-1', 0)
    seen = []
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_battle_record('battle_result', outcome='loss',
                                  metric={'egg_uses': 2, 'ally_hp': 0,
                                          'enemy_hp': 40}, general='バゲット')],
                  now=1000, on_sealed=seen.append)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'battle_loss_with_resources'
    assert record['auto_file'] is True
    assert '卵を残して敗北' in audit._issue_title(record)
    assert '### 戦闘結果' in audit.render(record)
    assert len(seen) == 1


def test_battle_retreat_with_card_is_recorded(runtime_dir):
    value = state(10, '1-1', 0)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_battle_record('battle_retreat_preempted_card',
                                  card='クースカン',
                                  metric={'ally_hp': 12, 'enemy_hp': 41},
                                  general='マフィン')], now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['kind'] == 'battle_retreat_with_resources'
    assert '切り札を残して退却' in audit._issue_title(record)


def test_battle_resource_repeat_is_cooled_down(runtime_dir):
    value = state(10, '1-1', 0)
    record = _battle_record('battle_result', outcome='loss',
                            metric={'egg_uses': 2, 'ally_hp': 0, 'enemy_hp': 40})
    for now in (1000, 1100):
        audit.observe(runtime_dir, audit.capture(value), value, [record], now=now)
    assert len(audit.load_incidents(runtime_dir)) == 1
    audit.observe(runtime_dir, audit.capture(value), value, [record],
                  now=1000 + audit.BATTLE_RESOURCE_ISSUE_COOLDOWN_SECONDS + 1)
    assert len(audit.load_incidents(runtime_dir)) == 2


def _camp_state(month, start, tick, *, general='カシュー', first_tick=None):
    value = state(10, month, tick)
    mem = value['policy']
    mem['month_start_tick'] = start
    mem['month_durations'] = [100, 100, 100]
    mem['general_statuses'] = {
        'complete': True,
        'statuses': {general: {
            'location': 'camp', 'location_observed': True,
            'hp': 10, 'max_hp': 10, 'status_text': 'キャンプしています',
            'camp_observed': True, 'broken': False, 'tick': tick,
        }},
    }
    mem['camp_status_seen'] = {
        'scope': {'chapter': 10},
        'actors': {general: {'tick': start if first_tick is None else first_tick}},
    }
    return value


def test_camp_age_counts_month_boundaries():
    value = _camp_state('1-1', 1000, 1100, first_tick=850)
    assert audit._camp_age_months(value['policy'], 'カシュー') == 3
    value = _camp_state('1-1', 1000, 1100, first_tick=950)
    assert audit._camp_age_months(value['policy'], 'カシュー') == 2


def test_camp_abandon_is_recorded_and_titled(runtime_dir):
    value = _camp_state('1-1', 1000, 1100, first_tick=850)
    seen = []
    audit.observe(runtime_dir, audit.capture(value), value, [], now=1100,
                  on_sealed=seen.append)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'camp_abandoned'
    assert record['camp_stay']['months'] == 3
    assert record['auto_file'] is True
    assert '野営放置' in audit._issue_title(record)
    assert '### 野営放置' in audit.render(record)
    assert len(seen) == 1


def test_camp_must_stay_three_consecutive_months(runtime_dir):
    first = _camp_state('1-1', 1000, 1100, first_tick=1000)
    audit.observe(runtime_dir, audit.capture(first), first, [], now=1100)
    assert audit.load_incidents(runtime_dir) == []
    second = _camp_state('1-2', 1100, 1200, first_tick=1000)
    audit.observe(runtime_dir, audit.capture(second), second, [], now=1200)
    assert audit.load_incidents(runtime_dir) == []
    third = _camp_state('1-3', 1200, 1300, first_tick=1000)
    audit.observe(runtime_dir, audit.capture(third), third, [], now=1300)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['camp_stay']['months'] == 3


def test_camp_return_resets_the_stay(runtime_dir):
    third = _camp_state('1-3', 1200, 1300, first_tick=1000)
    audit.observe(runtime_dir, audit.capture(third), third, [], now=1300)
    assert len(audit.load_incidents(runtime_dir)) == 1
    returned = _camp_state('1-3', 1200, 1300, first_tick=1000)
    returned['policy']['general_statuses'] = {'complete': True, 'statuses': {}}
    audit.observe(runtime_dir, audit.capture(returned), returned, [], now=1310)
    # A stale camp_status_seen tick must not resurrect the old stay.
    again = _camp_state('1-3', 1200, 1310, first_tick=1000)
    audit.observe(runtime_dir, audit.capture(again), again, [], now=1320)
    assert len(audit.load_incidents(runtime_dir)) == 1


def _chikujou_state(month='1-1', gold=1000, *, staffed=True, candidates=True):
    value = state(10, month, 0)
    mem = value['policy']
    home = chart.home_castle(10)
    other = next(c for c in chart.CASTLE_NAMES[10] if c != home)
    mem['captured'] = [other] if candidates else []
    mem['world']['garrison'] = ({home: ['A'], other: ['B']} if staffed else {})
    mem['general_statuses'] = {
        'complete': True,
        'statuses': ({home: {'location': 'castle', 'castle': home},
                      other: {'location': 'castle', 'castle': other}}
                     if staffed else {}),
    }
    mem['shop'] = {
        'key': month, 'chikujou': 'skipped', 'gold_start': gold,
        'hero_repair_reserve': 0, 'egg': 'done', 'soldiers_done': True,
        'soldiers': 0, 'recruit': 'done', 'recruit_reserve': 0,
        'merchant_done': True, 'items': [],
    }
    value['gold'] = gold
    return value


def test_chikujou_month_end_with_resources_is_recorded(runtime_dir):
    value = _chikujou_state()
    seen = []
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}],
                  now=1000, on_sealed=seen.append)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'chikujou_available_at_end'
    assert record['chikujou_event']['gold'] == 1000
    assert record['chikujou_event']['staffed_candidates']
    assert '築城可能なまま月一終了' in audit._issue_title(record)
    assert '### 築城' in audit.render(record)
    assert len(seen) == 1



def test_chikujou_month_end_uses_live_policy_gold_over_stale_outer_gold(runtime_dir):
    # docich#5465: the last verified upgrade left 30G (the 30G reserve),
    # while the outer state still carried the pre-payment 35G observation.
    # The audit must not report another affordable build from that stale copy.
    value = _chikujou_state(gold=35)
    mem = value['policy']
    home = chart.home_castle(10)
    mem['gold'] = 30
    mem['shop']['chikujou_tried'] = [home]
    mem['shop']['chikujou_upgrades'] = [
        {'castle': home, 'cost': 5, 'gold_before': 35, 'gold_after': 30},
    ]

    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}], now=1000)

    assert audit.load_incidents(runtime_dir) == []


def test_chikujou_month_end_uses_policy_gold_over_stale_transaction_metric(runtime_dir):
    # A gold_after value belongs to its transaction and can be older than the
    # current policy header after a later purchase.
    value = _chikujou_state(gold=35)
    mem = value['policy']
    home = chart.home_castle(10)
    mem['gold'] = 30
    mem['shop']['chikujou_tried'] = [home]
    mem['shop']['chikujou_upgrades'] = [
        {'castle': home, 'cost': 5, 'gold_before': 35, 'gold_after': 30},
    ]

    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1',
                    'observed_metric': {'gold_after': 35},
                    'reason': '月一の購入を終了', 'at': 1000}], now=1000)

    assert audit.load_incidents(runtime_dir) == []

def test_chikujou_incident_names_the_refusal_window_it_skipped_in(runtime_dir):
    # docich#5151: 月を閉じた理由が「拒否スキップ枠」なのか別経路なのかを
    # issue 本文だけで判別できるよう、拒否の skips と対象城を記録に残す。
    value = _chikujou_state()
    home = chart.home_castle(10)
    value['policy']['chikujou_refused'] = {'castles': [home], 'skips': 1}
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}], now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['kind'] == 'chikujou_available_at_end'
    assert record['chikujou_refused_skips'] == 1
    assert record['chikujou_refused_castles'] == [home]


def test_chikujou_month_end_held_for_unresolved_egg_is_not_high_confidence(runtime_dir):
    """docich#5094 / #5045: a fail-closed hold is not a silent loss.

    The month ended with a buildable castle list only because the egg
    recovery payment was unverified, so the card/castle rows stayed held.
    The audit keeps the occurrence countable, but must not file it as a
    high-confidence missed build (the filer defaults to min_confidence=high).
    """
    value = _chikujou_state()
    value['policy']['shop'].update({'cards_held': 'recruit_pending',
                                    'chikujou_held': True,
                                    'recruit': 'deferred_egg',
                                    'egg': 'unverified'})
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_cards_held_for_recruit', 'month': '1-1', 'at': 999},
                   {'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}],
                  now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'chikujou_available_at_end'
    assert record['confidence'] == 'medium'
    assert record['confidence_reasons'] == ['chikujou_held_for_egg_unverified']
    assert record['chikujou_event']['held_for'] == 'egg_unverified'
    assert record['chikujou_held_for'] == 'egg_unverified'
    assert '保留' in audit.render(record)


def test_chikujou_month_end_held_for_unresolved_recruit_names_the_recruit(runtime_dir):
    value = _chikujou_state()
    value['policy']['shop'].update({'cards_held': 'recruit_pending',
                                    'chikujou_held': True,
                                    'recruit': 'pending', 'egg': 'done'})
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['confidence'] == 'medium'
    assert record['confidence_reasons'] == ['chikujou_held_for_recruit_pending']


def test_chikujou_month_end_after_released_hold_stays_high_confidence(runtime_dir):
    value = _chikujou_state()
    value['policy']['shop'].update({'chikujou': 'check', 'recruit': 'pending',
                                    'egg': 'done'})
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['confidence'] == 'high'
    assert record['chikujou_event']['held_for'] is None


def test_chikujou_month_end_after_reserve_skip_is_not_high_confidence(runtime_dir):
    """docich#5186: the flow's reserve math held the build money all month.

    The eval marker (`chikujou_last_eval` with affordable=False) is the
    recorded reason the month ended unbuilt, so the audit keeps the
    occurrence countable but must not file it as a high-confidence missed
    build (the filer defaults to min_confidence=high).
    """
    value = _chikujou_state()
    value['policy']['shop']['chikujou'] = 'check'
    value['policy']['shop']['chikujou_last_eval'] = {'month': '1-1', 'gold': 216,
                                                    'reserve': 250,
                                                    'affordable': False}
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}],
                  now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'chikujou_available_at_end'
    assert record['confidence'] == 'medium'
    assert record['confidence_reasons'] == ['chikujou_reserve_skipped']
    assert record['chikujou_reserve_skipped'] is True
    assert record['chikujou_event']['reserve_skipped'] is True


def test_chikujou_stale_reserve_skip_marker_still_files(runtime_dir):
    """A marker from an earlier month must not cover this month's end."""
    value = _chikujou_state()
    value['policy']['shop']['chikujou_last_eval'] = {'month': '0-12', 'gold': 10,
                                                    'reserve': 500,
                                                    'affordable': False}
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['confidence'] == 'high'
    assert record['chikujou_reserve_skipped'] is False
    assert record['chikujou_event']['reserve_skipped'] is False


def test_chikujou_month_end_without_resources_is_not_recorded(runtime_dir):
    value = _chikujou_state(gold=0)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1', 'at': 1000}], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_chikujou_month_end_without_staff_is_not_recorded(runtime_dir):
    value = _chikujou_state(staffed=False)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1', 'at': 1000}], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_chikujou_per_castle_decline_does_not_end_the_month(runtime_dir):
    value = _chikujou_state()
    value['policy']['shop']['chikujou'] = 'check'
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_declined',
                    'reason': '築城の実費が不明か必要資金を割るため、支払わず見送る',
                    'at': 1000}], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_chikujou_terminal_decline_with_resources_is_recorded(runtime_dir):
    value = _chikujou_state()
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_declined',
                    'reason': '当月未試行で増築候補となる所有城がなくなったため、築城を終える',
                    'at': 1000}], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['kind'] == 'chikujou_cancelled_with_resources'
    assert '築城可能なのにキャンセル' in audit._issue_title(records[0])


def test_chikujou_terminal_decline_reads_live_sub_rows_tried(runtime_dir):
    """The shop ledger only merges a chikujou sub's tried rows when the sub
    finishes, so a live sub's refusals used to be counted as still-available
    candidates (docich#5026)."""
    value = _chikujou_state()
    mem = value['policy']
    home = chart.home_castle(10)
    other = next(c for c in chart.CASTLE_NAMES[10] if c != home)
    mem['shop']['chikujou'] = 'check'
    mem['month_sub'] = {'kind': 'chikujou', 'key': '1-1',
                        'rows_tried': [home, other], 'presses': 4}
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_declined',
                    'reason': '当月未試行で増築候補となる所有城がなくなったため、築城を終える',
                    'at': 1000}], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_chikujou_terminal_decline_still_reports_untried_candidate(runtime_dir):
    value = _chikujou_state()
    mem = value['policy']
    home = chart.home_castle(10)
    mem['shop']['chikujou'] = 'check'
    mem['month_sub'] = {'kind': 'chikujou', 'key': '1-1',
                        'rows_tried': [home], 'presses': 2}
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_declined',
                    'reason': '当月未試行で増築候補となる所有城がなくなったため、築城を終える',
                    'at': 1000}], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['kind'] == 'chikujou_cancelled_with_resources'


def test_chikujou_terminal_decline_ignores_other_month_sub(runtime_dir):
    value = _chikujou_state()
    mem = value['policy']
    mem['month_sub'] = {'kind': 'chikujou', 'key': '1-0',
                        'rows_tried': [chart.home_castle(10)]}
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_declined',
                    'reason': '当月未試行で増築候補となる所有城がなくなったため、築城を終える',
                    'at': 1000}], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['kind'] == 'chikujou_cancelled_with_resources'


def _chikujou_candidates(mem):
    from docich.hanjuku.strategy import economy
    return sorted(economy._chikujou_candidates(mem))


def test_chikujou_skip_after_matching_refusal_is_not_recorded(runtime_dir):
    """month.py defers 築城 for up to two months after it read a refusal from
    the native list with the same castle set, so that skipped month is not a
    missed build (docich#5061)."""
    value = _chikujou_state()
    mem = value['policy']
    mem['chikujou_refused'] = {'castles': _chikujou_candidates(mem), 'skips': 1}
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_chikujou_skip_after_refusal_rearms_when_a_castle_is_added(runtime_dir):
    """A changed castle set no longer matches the refused set, so the month end
    is a real missed build again."""
    value = _chikujou_state()
    mem = value['policy']
    mem['chikujou_refused'] = {'castles': _chikujou_candidates(mem), 'skips': 1}
    home = chart.home_castle(10)
    extra = next(c for c in chart.CASTLE_NAMES[10]
                 if c != home and c not in mem['captured'])
    mem['captured'] = [*mem['captured'], extra]
    mem['world']['garrison'][extra] = ['C']
    mem['general_statuses']['statuses'][extra] = {'location': 'castle', 'castle': extra}
    assert _chikujou_candidates(mem) != mem['chikujou_refused']['castles']
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['kind'] == 'chikujou_available_at_end'


def test_chikujou_month_end_after_cost_decline_is_not_recorded(runtime_dir):
    """docich#5081: the native quote the bot declined is not visible to the
    gold>=reserve+spare heuristic, so that month end is not a missed build."""
    value = _chikujou_state()
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_declined', 'month': '1-1', 'at': 999,
                    'reason': '築城の実費が不明か必要資金を割るため、支払わず見送る',
                    'observed_metric': {'cost': None, 'gold': 32, 'reserve': 30}},
                   {'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_chikujou_decline_followed_by_a_later_quote_still_records(runtime_dir):
    """A reopened 築城 sub after the decline can still be a real missed build."""
    value = _chikujou_state()
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_declined', 'month': '1-1', 'at': 990,
                    'reason': '築城の実費が不明か必要資金を割るため、支払わず見送る'},
                   {'decision': 'chikujou_confirm', 'month': '1-1', 'at': 995},
                   {'decision': 'month_done', 'month': '1-1', 'at': 1000}], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['kind'] == 'chikujou_available_at_end'


def test_chikujou_cost_decline_from_another_month_still_records(runtime_dir):
    """The 120 s decision window spans month boundaries; only this month's
    decline may suppress the alert."""
    value = _chikujou_state()
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_declined', 'month': '1-0', 'at': 999,
                    'reason': '築城の実費が不明か必要資金を割るため、支払わず見送る'},
                   {'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['kind'] == 'chikujou_available_at_end'


def test_chikujou_terminal_decline_is_not_a_cost_decline(runtime_dir):
    value = _chikujou_state()
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_declined', 'month': '1-1', 'at': 999,
                    'reason': '当月未試行で増築候補となる所有城がなくなったため、築城を終える'},
                   {'decision': 'month_done', 'month': '1-1', 'at': 1000}], now=1000)
    assert audit.load_incidents(runtime_dir)[0]['kind'] == 'chikujou_available_at_end'


def test_chikujou_month_end_with_unreadable_menu_row_is_medium_confidence(runtime_dir):
    """docich#5097 / #5079: a bounded wait that still could not read ちくじょう
    is a named, medium-confidence occurrence rather than a reason-less alarm."""
    value = _chikujou_state()
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_menu_unreadable', 'month': '1-1', 'at': 999,
                    'observed_metric': {'skipped': True, 'waits': 4},
                    'reason': '有限回待ってもちくじょう行を読めないため、当月は築城せずに月を終える'},
                   {'decision': 'month_done', 'month': '1-1',
                    'reason': '月一の購入を終了', 'at': 1000}], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'chikujou_available_at_end'
    assert record['confidence'] == 'medium'
    assert record['confidence_reasons'] == ['chikujou_menu_unreadable']
    assert record['chikujou_menu_unreadable'] is True
    assert '築城行の読取り: 不可' in audit.render(record)


def test_chikujou_cost_decline_in_prior_observation_suppresses_alarm(runtime_dir):
    value = _chikujou_state()
    note(runtime_dir, 999, 'chikujou_declined', month='1-1',
         chapter=10, reason=audit._CHIKUJOU_COST_DECLINE)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1', 'at': 1000}],
                  now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_chikujou_prior_decline_followed_by_reopen_still_reports(runtime_dir):
    value = _chikujou_state()
    note(runtime_dir, 998, 'chikujou_declined', month='1-1',
         chapter=10, reason=audit._CHIKUJOU_COST_DECLINE)
    note(runtime_dir, 999, 'chikujou_confirm', month='1-1', chapter=10)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1', 'at': 1000}],
                  now=1000)
    assert audit.load_incidents(runtime_dir)[0]['kind'] == 'chikujou_available_at_end'


def test_chikujou_unreadable_in_prior_observation_downgrades(runtime_dir):
    value = _chikujou_state()
    note(runtime_dir, 999, 'chikujou_menu_unreadable', month='1-1',
         chapter=10, observed_metric={'skipped': True})
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1', 'at': 1000}],
                  now=1000)
    assert audit.load_incidents(runtime_dir)[0]['confidence'] == 'medium'


def test_chikujou_prior_other_month_does_not_suppress(runtime_dir):
    value = _chikujou_state()
    note(runtime_dir, 999, 'chikujou_declined', month='1-0',
         chapter=10, reason=audit._CHIKUJOU_COST_DECLINE)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'month_done', 'month': '1-1', 'at': 1000}],
                  now=1000)
    assert audit.load_incidents(runtime_dir)[0]['confidence'] == 'high'


def test_chikujou_menu_unreadable_retry_without_skip_stays_high(runtime_dir):
    value = _chikujou_state()
    audit.observe(runtime_dir, audit.capture(value), value,
                  [{'decision': 'chikujou_menu_unreadable', 'month': '1-1', 'at': 999,
                    'observed_metric': {'skipped': False, 'waits': 1}},
                   {'decision': 'month_done', 'month': '1-1', 'at': 1000}], now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    assert record['confidence'] == 'high'
    assert record['chikujou_menu_unreadable'] is False


def test_chikujou_month_end_is_reported_once(runtime_dir):
    value = _chikujou_state()
    record = {'decision': 'month_done', 'month': '1-1', 'at': 1000}
    audit.observe(runtime_dir, audit.capture(value), value, [record], now=1000)
    audit.observe(runtime_dir, audit.capture(value), value, [record], now=1100)
    assert len(audit.load_incidents(runtime_dir)) == 1


def boss_state(chapter=9, month='1-1', tick=0, *, launches=0):
    value = state(chapter, month, tick)
    bosses = set(chart.boss_sites(chapter))
    normal = [name for name in chart.castles(chapter) if name not in bosses]
    value['policy']['captured'] = normal
    value['policy']['lost'] = []
    value['policy']['month_sorties'] = {'scope': [chapter, month], 'launches': launches}
    return value


def run_boss_month(runtime, chapter, month, start, count, *, launches=0, boss_at=None):
    last = None
    for index in range(count):
        at = start + index * STEP
        last = boss_state(chapter, month, index, launches=launches)
        audit.observe(runtime, audit.capture(last), last, [], now=at)
        if boss_at == index:
            note(runtime, at, 'order_launched', chart_step='A:1:M1',
                 purpose='boss', general='G', target='ボス')
        else:
            note(runtime, at)
    return last


def test_boss_phase_month_with_zero_boss_sorties_is_recorded(runtime_dir):
    old = run_boss_month(runtime_dir, 9, '1-1', 1000, 100)
    boundary(runtime_dir, old, boss_state(9, '1-2', 200), 1200)
    old = run_boss_month(runtime_dir, 9, '1-2', 1200, 100)
    boundary(runtime_dir, old, boss_state(9, '1-3', 400), 1380)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'boss_month_zero_sortie'
    assert record['confidence'] == 'high'
    assert record['auto_file'] is True
    assert record['boss_month']['boss_name'] == chart.BOSSES[9]
    assert record['boss_month']['departures'] == 0
    assert 'ボスフェーズで月内ボス出撃ゼロ' in audit._issue_title(record)
    assert '### ボスフェーズ月次' in audit.render(record)


def test_boss_phase_month_with_a_boss_sortie_is_not_flagged(runtime_dir):
    old = run_boss_month(runtime_dir, 9, '1-1', 1000, 100)
    boundary(runtime_dir, old, boss_state(9, '1-2', 200), 1200)
    old = run_boss_month(runtime_dir, 9, '1-2', 1200, 100,
                         launches=1, boss_at=40)
    boundary(runtime_dir, old, boss_state(9, '1-3', 400), 1380)
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())
    assert saved['last_close']['kind'] == 'ok'
    assert saved['pending'] is None


def _kikan_record(choice='きかん', move='here'):
    return {'decision': 'camp_recall_cursor',
            'observed_metric': {'current': [72, 31, choice],
                                'choice': choice, 'move': move},
            'reason': '野営の実手カーソルを帰還項目へ合わせる', 'at': 1000}


def test_recall_kikan_selection_is_classified():
    event = audit._recall_kikan_event(_kikan_record())
    assert event is not None
    assert event['choice'] == 'きかん'
    assert audit._recall_kikan_event(_kikan_record(choice='いどう')) is None
    assert audit._recall_kikan_event(_kikan_record(move='down')) is None


def test_recall_kikan_selection_is_recorded_and_titled(runtime_dir):
    value = state(10, '1-1', 0,
                  flow={'recall': {'stage': 'menu', 'target': [135, 125],
                                   'goal': 'アルマムーン', 'general': 'カシュー'}})
    value['policy']['y_jumps'] = {'RECALL:target': 1}
    seen = []
    audit.observe(runtime_dir, audit.capture(value), value, [_kikan_record()],
                  now=1000, on_sealed=seen.append)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'recall_kikan_selected'
    assert record['auto_file'] is True
    assert record['recall_event']['goal'] == 'アルマムーン'
    assert record['recall_event']['general'] == 'カシュー'
    assert '「きかん」を選択' in audit._issue_title(record)
    assert '### 帰還手段' in audit.render(record)
    assert len(seen) == 1


def test_recall_kikan_is_reported_once_per_episode(runtime_dir):
    value = state(10, '1-1', 0,
                  flow={'recall': {'stage': 'menu', 'target': [135, 125],
                                   'goal': 'アルマムーン', 'general': 'カシュー'}})
    record = _kikan_record()
    audit.observe(runtime_dir, audit.capture(value), value, [record], now=1000)
    audit.observe(runtime_dir, audit.capture(value), value, [record], now=1100)
    assert len(audit.load_incidents(runtime_dir)) == 1


def test_recall_with_idou_is_not_recorded(runtime_dir):
    value = state(10, '1-1', 0,
                  flow={'recall': {'stage': 'menu', 'target': [135, 125],
                                   'goal': 'アルマムーン'}})
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_kikan_record(choice='いどう')], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_the_idou_y_route_emits_no_kikan_incident(runtime_dir):
    """docich#5105: the real menu flow now confirms いどう for every recall
    (owner 2026-11-19: きかん禁止・いどう→Y), so its camp_recall_cursor
    records can never produce recall_kikan_selected."""
    from docich.hanjuku.flows import recall as recall_flow
    from test_hanjuku_chart_bot import camp_menu
    mem = {'chapter': 10, 'tick': 100, '_records': [],
           'recall': {'stage': 'menu', 'steps': 0, 'target': [135, 125],
                      'general': 'カシュー'}}
    for selected in (3, 2, 1, 0):
        assert recall_flow.camp_recall_step(camp_menu(selected), mem, None)
    assert not any((r.get('observed_metric') or {}).get('choice') == 'きかん'
                   for r in mem['_records'])
    value = state(10, '1-1', 0,
                  flow={'recall': {'stage': 'dest', 'target': [135, 125],
                                   'general': 'カシュー'}})
    audit.observe(runtime_dir, audit.capture(value), value,
                  [r for r in mem['_records']
                   if r['decision'] == 'camp_recall_cursor'], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_corner_stall_is_recorded_and_titled(runtime_dir):
    value = state(10, '1-1', 0)
    value['terminal_reason'] = 'screen_stalled'
    value['unchanged_seconds'] = 300
    value['actions_sent'] = 17
    value['phase'] = 'field'
    value['bot_version'] = 'synthetic-v1'
    value['terminal_evidence'] = {'terminal_reason': 'screen_stalled',
                                  'frame_sha256': 'a' * 64, 'tick': 99}
    record = audit.record_corner_stall(
        runtime_dir, reason='screen_stalled', run=value, identity=IDENTITY,
        rotation_request_id='req-1', now=1000, spawn=False)
    assert record['kind'] == 'corner_stalled'
    assert record['auto_file'] is True
    assert record['corner_stall']['unchanged_seconds'] == 300
    assert record['corner_stall']['rotation_request_id'] == 'req-1'
    assert '画面停滞でコーナー停止' in audit._issue_title(record)
    assert '### コーナー停止' in audit.render(record)
    stored = audit.load_incidents(runtime_dir)
    assert len(stored) == 1


def test_corner_stall_is_recorded_once(runtime_dir):
    value = state(10, '1-1', 0)
    value['terminal_reason'] = 'input_stalled'
    value['terminal_evidence'] = {'frame_sha256': 'b' * 64}
    first = audit.record_corner_stall(
        runtime_dir, reason='input_stalled', run=value, identity=IDENTITY,
        now=1000, spawn=False)
    second = audit.record_corner_stall(
        runtime_dir, reason='input_stalled', run=value, identity=IDENTITY,
        now=1100, spawn=False)
    assert first['incident_id'] == second['incident_id']
    assert len(audit.load_incidents(runtime_dir)) == 1


def test_non_stall_corner_reason_is_not_recorded(runtime_dir):
    value = state(10, '1-1', 0)
    assert audit.record_corner_stall(
        runtime_dir, reason='game_over', run=value, identity=IDENTITY,
        now=1000, spawn=False) is None
    assert audit.load_incidents(runtime_dir) == []


def test_retro_corner_records_screen_stall_before_teardown():
    text = (Path(__file__).resolve().parents[1] / 'src/docich/retro_corner.py').read_text(
        encoding='utf-8')
    assert 'record_corner_stall' in text
    assert "screen_stalled', 'input_stalled'" in text


def _menu_state(menu=True):
    value = state(10, '1-1', 0)
    value['screen_kind'] = 'battle_menu' if menu else 'battle'
    value['policy']['battle'] = {
        'general': 'カシュー', 'step': 'A:1:M1',
        'ally_hp': 30, 'enemy_hp': 40, 'side': 'attack',
    }
    return value


def test_battle_menu_closed_without_action_is_recorded(runtime_dir):
    before = _menu_state(False)
    opened = _menu_state(True)
    audit.observe(runtime_dir, audit.capture(before), opened, [], now=1000)
    closed = _menu_state(False)
    seen = []
    audit.observe(runtime_dir, audit.capture(opened), closed,
                  [{'decision': 'battle_egg_conserve', 'at': 1001,
                    'reason': '卵を温存して戻る'}], now=1001,
                  on_sealed=seen.append)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'battle_menu_wasted'
    assert record['battle_menu']['close_decision'] == 'battle_egg_conserve'
    assert record['auto_file'] is True
    assert '戦闘Bメニューを無駄に開閉' in audit._issue_title(record)
    assert '### 戦闘Bメニュー' in audit.render(record)
    assert len(seen) == 1


def test_battle_menu_with_action_is_not_recorded(runtime_dir):
    before = _menu_state(False)
    opened = _menu_state(True)
    audit.observe(runtime_dir, audit.capture(before), opened, [], now=1000)
    closed = _menu_state(False)
    audit.observe(runtime_dir, audit.capture(opened), closed,
                  [{'decision': 'battle_card_selected', 'at': 1001},
                   {'decision': 'battle_egg_conserve', 'at': 1001}], now=1001)
    assert audit.load_incidents(runtime_dir) == []


def test_battle_one_hit_close_is_not_recorded(runtime_dir):
    before = _menu_state(False)
    opened = _menu_state(True)
    audit.observe(runtime_dir, audit.capture(before), opened, [], now=1000)
    closed = _menu_state(False)
    audit.observe(runtime_dir, audit.capture(opened), closed,
                  [{'decision': 'battle_one_hit_menu_close', 'at': 1001}], now=1001)
    assert audit.load_incidents(runtime_dir) == []


def test_battle_menu_close_without_close_decision_is_not_recorded(runtime_dir):
    before = _menu_state(False)
    opened = _menu_state(True)
    audit.observe(runtime_dir, audit.capture(before), opened, [], now=1000)
    closed = _menu_state(False)
    audit.observe(runtime_dir, audit.capture(opened), closed, [], now=1001)
    assert audit.load_incidents(runtime_dir) == []


def test_battle_menu_waste_is_cooled_down(runtime_dir):
    for start in (1000, 1100):
        before = _menu_state(False)
        opened = _menu_state(True)
        audit.observe(runtime_dir, audit.capture(before), opened, [], now=start)
        closed = _menu_state(False)
        audit.observe(runtime_dir, audit.capture(opened), closed,
                      [{'decision': 'battle_egg_conserve', 'at': start + 1}],
                      now=start + 1)
    assert len(audit.load_incidents(runtime_dir)) == 1


def _cards_state(*, stock=None, purpose='attack', cards=None):
    value = state(10, '1-1', 0)
    value['policy']['card_stock'] = {'ノリウツール': 96} if stock is None else stock
    value['policy']['plan'] = {'orders': {}, 'chart_plan': {'orders': [{
        'step': 'A:1:M1', 'purpose': purpose, 'source': 'アルマムーン',
        'target': 'タタール', 'general': 'カシュー', 'cards': cards or [],
    }]}}
    return value


def _launch_record(purpose='attack'):
    return {'decision': 'order_launched', 'chart_step': 'A:1:M1',
            'general': 'カシュー', 'purpose': purpose, 'target': 'タタール',
            'reason': '出撃', 'at': 1000}


def _measured_empty_kit(value):
    value['policy']['plan']['order_context'] = {'A:1:M1': {
        'observed_metric': {'general': 'カシュー', 'cards': []}}}
    value['policy']['world']['launched_orders'] = {'A:1:M1': {
        'general': 'カシュー', 'source': 'アルマムーン', 'target': 'タタール',
        'cards': [], 'departure_binding': {'general': 'カシュー',
                                           'source': 'アルマムーン', 'cards': []}}}


def test_sortie_without_cards_despite_stock_is_recorded(runtime_dir):
    value = _cards_state()
    _measured_empty_kit(value)
    seen = []
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()],
                  now=1000, on_sealed=seen.append)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'sortie_without_cards'
    assert record['sortie_event']['inventory'] == {'ノリウツール': 96}
    assert record['sortie_event']['carried'] == []
    assert record['sortie_event']['carried_observed'] is True
    assert record['confidence'] == 'high'
    assert record['auto_file'] is True
    assert '切り札在庫があるのに非携行で出撃' in audit._issue_title(record)
    assert '### 切り札の非携行' in audit.render(record)
    assert '計画が空' in audit.render(record)
    assert len(seen) == 1


def test_non_boss_sortie_ignores_boss_only_stock(runtime_dir):
    # #5510: メガパタ is an ordinary chapter-3 castle. バルムンク is
    # boss-only, so its field count cannot make this an unarmed-sortie defect.
    value = _cards_state(stock={'バルムンク': 1}, purpose='attack',
                         cards=['ミックミー', 'ミックミー'])
    value['policy']['chapter'] = 3
    value['policy']['plan']['chart_plan']['orders'][0]['target'] = 'メガパタ'
    _measured_empty_kit(value)
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_boss_sortie_still_counts_boss_only_stock(runtime_dir):
    value = _cards_state(stock={'バルムンク': 1}, purpose='boss',
                         cards=['バルムンク'])
    value['policy']['chapter'] = 3
    value['policy']['plan']['chart_plan']['orders'][0]['target'] = 'ボス'
    _measured_empty_kit(value)
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record('boss')], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['kind'] == 'sortie_without_cards'
    assert records[0]['sortie_event']['inventory'] == {'バルムンク': 1}


def test_sortie_without_cards_names_a_planned_kit(runtime_dir):
    # docich#5353: 計画に札があるのに携行できていない型は、計画が空の型と区別する。
    value = _cards_state(cards=['クースカン'])
    _measured_empty_kit(value)
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert records and '計画はあるが実測kitが空' in audit.render(records[0])


def test_sortie_with_carried_cards_is_not_recorded(runtime_dir):
    value = _cards_state()
    value['policy']['rare_card_kit'] = {'A:1:M1': ['ノリウツール']}
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_move_without_cards_is_not_recorded(runtime_dir):
    value = _cards_state(purpose='move')
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_launch_record(purpose='move')], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_no_card_stock_is_not_recorded(runtime_dir):
    value = _cards_state(stock={})
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_sortie_without_cards_is_reported_once(runtime_dir):
    value = _cards_state()
    _measured_empty_kit(value)
    for now in (1000, 1100):
        audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=now)
    assert len(audit.load_incidents(runtime_dir)) == 1


def test_sortie_with_measured_kit_in_order_context_is_not_recorded(runtime_dir):
    value = _cards_state(cards=['クースカン', 'ノリウツール'])
    value['policy']['plan']['order_context'] = {'A:1:M1': {
        'observed_metric': {'general': 'カシュー',
                            'cards': ['クースカン', 'ノリウツール']}}}
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_sortie_with_bound_kit_in_launched_orders_is_not_recorded(runtime_dir):
    value = _cards_state(cards=['クースカン', 'ノリウツール'])
    value['policy']['world']['launched_orders'] = {'A:1:M1': {
        'general': 'カシュー', 'source': 'アルマムーン', 'target': 'タタール',
        'cards': ['クースカン', 'ノリウツール'],
        'departure_binding': {'general': 'カシュー', 'source': 'アルマムーン',
                              'cards': ['クースカン', 'ノリウツール']}}}
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_sortie_with_sortie_confirm_cards_is_not_recorded(runtime_dir):
    value = _cards_state(cards=['クースカン', 'ノリウツール'])
    confirm = {'decision': 'sortie_confirm', 'chart_step': 'A:1:M1',
               'general': 'カシュー', 'cards': ['クースカン', 'ノリウツール'],
               'at': 999}
    audit.observe(runtime_dir, audit.capture(value), value,
                  [confirm, _launch_record()], now=1000)
    assert audit.load_incidents(runtime_dir) == []


def test_sortie_with_empty_measured_kit_is_recorded(runtime_dir):
    value = _cards_state(cards=['クースカン'])
    value['policy']['plan']['order_context'] = {'A:1:M1': {
        'observed_metric': {'general': 'カシュー', 'cards': []}}}
    value['policy']['world']['launched_orders'] = {'A:1:M1': {
        'general': 'カシュー', 'cards': [], 'departure_binding': {'cards': []}}}
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['kind'] == 'sortie_without_cards'


def test_empty_card_strings_do_not_count_as_measured_kit(runtime_dir):
    value = _cards_state(cards=['クースカン'])
    value['policy']['rare_card_kit'] = {'A:1:M1': ''}
    value['policy']['strong_card_kit'] = {'A:1:M1': '  '}
    value['policy']['plan']['order_context'] = {'A:1:M1': {
        'observed_metric': {'general': 'カシュー', 'cards': ['']}}}
    value['policy']['world']['launched_orders'] = {'A:1:M1': {
        'departure_binding': {'cards': ['  ']}}}
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['kind'] == 'sortie_without_cards'


def test_sortie_with_unbound_snapshot_cards_is_unconfirmed(runtime_dir):
    # A snapshot without departure_binding may copy planned cards; absence of
    # a measured receipt must not be represented as an empty carried kit.
    value = _cards_state(cards=['クースカン'])
    value['policy']['world']['launched_orders'] = {'A:1:M1': {
        'general': 'カシュー', 'source': 'アルマムーン', 'target': 'タタール',
        'cards': ['クースカン']}}
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1000)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    record = records[0]
    assert record['kind'] == 'sortie_kit_unconfirmed'
    assert record['confidence'] == 'low'
    assert record['confidence_reasons'] == ['carried_kit_unobserved']
    assert record['auto_file'] is False
    assert record['sortie_event']['carried'] is None
    assert record['sortie_event']['carried_observed'] is False
    assert '携行: 未確認' in audit.render(record)


def test_unconfirmed_sortie_does_not_cool_down_later_measured_empty_kit(runtime_dir):
    value = _cards_state(cards=['クースカン'])
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1000)
    _measured_empty_kit(value)
    audit.observe(runtime_dir, audit.capture(value), value, [_launch_record()], now=1100)
    records = audit.load_incidents(runtime_dir)
    assert [record['kind'] for record in records] == [
        'sortie_kit_unconfirmed', 'sortie_without_cards']
    assert records[0]['auto_file'] is False
    assert records[1]['auto_file'] is True


def test_capture_reads_unmigrated_world_fields():
    state_value = state(1, '1-1', 10)
    state_value['policy']['world']['gold'] = None
    state_value['policy']['gold'] = 321
    state_value['policy']['captured'] = ['A']
    state_value['policy']['lost'] = ['B']
    state_value['policy']['castle_ownership'] = {'A': 'own'}
    state_value['policy']['sortie_history'] = [
        {'outcome': 'won', 'general': 'G', 'target': 'A', 'tick': 5}]
    value = audit.capture(state_value)
    assert value['world']['gold'] == 321
    assert value['world']['captured'] == ['A']
    assert value['world']['lost'] == ['B']
    assert value['world']['castle_ownership'] == {'A': 'own'}
    assert value['world']['sortie_history'][0]['outcome'] == 'won'


def test_render_puts_the_noise_note_before_the_table(runtime_dir):
    record = sealed_zero(runtime_dir)
    text = audit.render(record)
    assert text.index('定常的な保留') < text.index('| 時刻 |')


def test_render_contains_markers_and_evidence(runtime_dir):
    record = sealed_zero(runtime_dir)
    text = audit.render(record)
    assert 'hanjuku-month-audit fingerprint=' in text
    assert f'occurrence={record["incident_id"]}' in text
    assert audit.TITLE_PREFIX in text
    assert '月内の決定' in text
    assert record['incident_id'] in text


def test_issue_title_uses_the_primary_signal():
    assert audit._issue_title({'chapter': 1, 'signature': 'unread'}) == (
        '[半熟英雄] 月内出撃ゼロ: ch1 未読画面で保留')
    assert audit._issue_title({'chapter': 2, 'signature': 'departure_failure'}) == (
        '[半熟英雄] 月内出撃ゼロ: ch2 出撃開始後に発射されない')


def test_render_comment_is_compact(runtime_dir):
    record = sealed_zero(runtime_dir)
    text = audit.render_comment(record)
    assert '### 再発' in text
    assert f'occurrence={record["incident_id"]}' in text
    assert len(text) < 4000


def test_record_size_is_bounded(runtime_dir):
    record = sealed_zero(runtime_dir)
    assert len(json.dumps(record, ensure_ascii=False).encode('utf-8')) <= audit.MAX_RECORD_BYTES


class FakeGh:
    def __init__(self, issues=None, comments=None):
        self.issues = issues if issues is not None else []
        self.comments = comments if comments is not None else []
        self.calls = []
        self.comment_body = None

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ['auth', 'status']:
            return 0, '', ''
        if args[:2] == ['issue', 'list']:
            return 0, json.dumps(self.issues), ''
        if args[:2] == ['issue', 'view']:
            return 0, json.dumps({'comments': self.comments}), ''
        if args[:2] == ['issue', 'create']:
            return 0, 'https://github.com/azumag/docich/issues/1', ''
        if args[:2] == ['issue', 'comment']:
            path = args[args.index('--body-file') + 1]
            self.comment_body = Path(path).read_text(encoding='utf-8')
            return 0, 'https://github.com/azumag/docich/issues/1#c', ''
        return 1, '', 'unexpected'


def test_file_creates_one_issue_with_labels(runtime_dir):
    record = sealed_zero(runtime_dir)
    gh = FakeGh()
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True, now=time.time())
    assert result['status'] == 'created'
    create = [call for call in gh.calls if call[:2] == ['issue', 'create']]
    assert len(create) == 1
    assert '--label' in create[0] and 'auto-generated' in create[0]
    assert record['fingerprint'] in result['fingerprint']


def test_file_comments_on_the_matching_open_issue(runtime_dir):
    record = sealed_zero(runtime_dir)
    issue = {'number': 7, 'body': f"<!-- hanjuku-month-audit fingerprint={record['fingerprint']} -->"}
    gh = FakeGh(issues=[issue], comments=[])
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True, now=time.time())
    assert result['status'] == 'commented'
    assert result['issue'] == 7
    assert '### 再発' in gh.comment_body


def test_file_does_not_repeat_the_same_occurrence(runtime_dir):
    record = sealed_zero(runtime_dir)
    issue = {'number': 7, 'body': f"<!-- hanjuku-month-audit fingerprint={record['fingerprint']} -->"}
    comments = [{'body': f"<!-- hanjuku-month-audit occurrence={record['incident_id']} -->",
                 'createdAt': '2026-11-20T00:00:00Z'}]
    gh = FakeGh(issues=[issue], comments=comments)
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True, now=time.time())
    assert result['status'] == 'already_commented'


def test_file_respects_comment_cooldown(runtime_dir):
    record = sealed_zero(runtime_dir)
    issue = {'number': 7, 'body': f"<!-- hanjuku-month-audit fingerprint={record['fingerprint']} -->"}
    now = time.time()
    comments = [{'body': 'earlier', 'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now - 60))}]
    gh = FakeGh(issues=[issue], comments=comments)
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True, now=now)
    assert result['status'] == 'cooldown'


def test_battle_loss_comments_before_the_generic_hour(runtime_dir):
    value = state(10, '1-1', 0)
    audit.observe(runtime_dir, audit.capture(value), value,
                  [_battle_record('battle_result', outcome='loss',
                                  metric={'egg_uses': 2, 'ally_hp': 0,
                                          'enemy_hp': 40}, general='バゲット')],
                  now=1000)
    record = audit.load_incidents(runtime_dir)[0]
    issue = {'number': 7,
             'body': f"<!-- hanjuku-month-audit fingerprint={record['fingerprint']} -->"}
    now = time.time()
    comments = [{'body': 'earlier',
                 'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                            time.gmtime(now - 1200))}]
    gh = FakeGh(issues=[issue], comments=comments)
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True, now=now)
    assert result['status'] == 'commented'


def test_file_refuses_when_secret_scan_detects(runtime_dir):
    sealed_zero(runtime_dir)
    gh = FakeGh()
    with pytest.raises(audit.AuditError):
        audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: False, now=time.time())
    assert not [call for call in gh.calls if call[:2] == ['issue', 'create']]


def test_file_dry_run_never_calls_gh(runtime_dir):
    sealed_zero(runtime_dir)

    def explode(args):
        raise AssertionError('gh must not run in dry-run')

    result = audit.file_issue(runtime_dir, dry_run=True, gh=explode, scanner=lambda path: True)
    assert result['status'] == 'dry_run'
    assert result['body']


def test_file_min_confidence_filters(runtime_dir):
    sealed_zero(runtime_dir)
    log = runtime_dir / f'{audit.LOG_NAME}.jsonl'
    rows = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines() if line]
    rows[-1]['confidence'] = 'medium'
    rows[-1]['auto_file'] = True
    log.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n',
                   encoding='utf-8')
    assert audit.file_issue(runtime_dir, gh=FakeGh(), scanner=lambda path: True)['status'] == 'no_incident'
    assert audit.file_issue(runtime_dir, gh=FakeGh(), scanner=lambda path: True,
                            min_confidence='medium')['status'] == 'created'


def test_watch_once_files_new_and_skips_unchanged(runtime_dir):
    sealed_zero(runtime_dir)
    gh = FakeGh()
    seen = {}
    state_dir = runtime_dir.parent.parent
    results = audit.watch_once(state_dir, gh=gh, scanner=lambda path: True,
                               now=time.time(), seen=seen)
    assert [row['status'] for row in results] == ['created']
    assert audit.watch_once(state_dir, gh=gh, scanner=lambda path: True,
                            now=time.time(), seen=seen) == []


def test_load_incidents_skips_malformed_lines(runtime_dir):
    path = runtime_dir / f'{audit.LOG_NAME}.jsonl'
    path.write_text('{bad\n' + json.dumps({'schema': 1, 'incident_id': 'x'}) + '\n',
                    encoding='utf-8')
    rows = audit.load_incidents(runtime_dir)
    assert [row['incident_id'] for row in rows] == ['x']


@pytest.fixture
def runtime_dir(tmp_path):
    directory = tmp_path / 'runtimes' / 'g1-abcd1234'
    directory.mkdir(parents=True)
    return directory


def test_shrink_singleton_and_after_rows():
    record = {'decisions': {'before': ['x' * audit.MAX_RECORD_BYTES]},
              'after': {'rows': ['y' * audit.MAX_RECORD_BYTES]}}
    result = audit._shrink(record)
    assert result['decisions']['before'] == []
    assert result['after']['rows'] == []
    assert result['truncated'] is True
    assert len(json.dumps(result, ensure_ascii=False).encode('utf-8')) <= audit.MAX_RECORD_BYTES


def test_shrink_oversize_required_payload_fails_closed():
    with pytest.raises(audit.AuditError):
        audit._shrink({'required': 'x' * audit.MAX_RECORD_BYTES})


def test_file_deduplicates_occurrence_in_issue_body(runtime_dir):
    record = sealed_zero(runtime_dir)
    gh = FakeGh(issues=[{'number': 7, 'body': audit.render(record)}])
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True)
    assert result['status'] == 'already_commented'
    assert not any(call[:2] == ['issue', 'comment'] for call in gh.calls)


def test_watch_retries_unchanged_log_after_cooldown(runtime_dir):
    record = sealed_zero(runtime_dir)
    now = time.time()
    issue = {'number': 7, 'body': f"<!-- hanjuku-month-audit fingerprint={record['fingerprint']} -->"}
    comments = [{'body': 'earlier', 'createdAt': time.strftime(
        '%Y-%m-%dT%H:%M:%SZ', time.gmtime(now - 60))}]
    gh = FakeGh(issues=[issue], comments=comments)
    seen = {}
    state_dir = runtime_dir.parent.parent
    assert audit.watch_once(state_dir, gh=gh, scanner=lambda path: True,
                            now=now, seen=seen)[0]['status'] == 'cooldown'
    assert not seen
    assert audit.watch_once(state_dir, gh=gh, scanner=lambda path: True,
                            now=now + audit.COMMENT_COOLDOWN_SECONDS + 1,
                            seen=seen)[0]['status'] == 'commented'


def test_card_miss_decision_seals_an_auto_file_incident(runtime_dir):
    first = state(1, '1-1', 0)
    audit.observe(runtime_dir, audit.capture(first), first, [], now=1000)
    second = state(1, '1-1', 1)
    sealed = []
    miss = {'schema': 1, 'event': 'decision', 'at': 1001,
            'decision': 'battle_boss_card_missed', 'card': 'ノリウツール',
            'chapter': 1, 'ally': 'ヘルメス', 'enemy': 'ニンフ', 'castle': 'ながはま',
            'screen_kind': 'egg_battle_menu', 'side': 'attack',
            'reason': '携行切り札を開けず攻撃',
            'observed_metric': {
                'cards_left': ['ノリウツール'], 'scrolls': 3, 'cursor': 192,
                'text': 'こうげきたいきゃく', 'empty_egg': False,
                'summon_egg_unavailable': False, 'enemy_summon_observed': True,
            }, **IDENTITY}
    audit.observe(runtime_dir, audit.capture(first), second, [miss], now=1001,
                  on_sealed=sealed.append)
    assert len(sealed) == 1
    incident = sealed[0]
    assert incident['kind'] == 'card_miss'
    assert incident['auto_file'] is True
    assert incident['card'] == 'ノリウツール'
    battle = incident['battle_event']
    assert battle['side'] == 'attack'
    assert battle['scrolls'] == 3 and battle['cursor'] == 192
    assert battle['empty_egg'] is False
    assert battle['summon_egg_unavailable'] is False
    assert battle['enemy_summon_observed'] is True
    assert battle['screen_text'] == 'こうげきたいきゃく'
    assert battle['cards_left'] == ['ノリウツール']
    assert '切り札未使用' in audit._issue_title(incident)
    body = audit.render(incident)
    assert 'ノリウツール' in body and 'card_miss' in body
    assert 'scrolls: 3' in body and 'cursorY: 192' in body
    assert 'こうげきたいきゃく' in body and 'side: attack' in body
    comment = audit.render_comment(incident)
    assert '再発' in comment
    # The same decision observed again does not seal a second incident.
    audit.observe(runtime_dir, audit.capture(second), state(1, '1-1', 2), [miss],
                  now=1002, on_sealed=sealed.append)
    assert len(sealed) == 1


def test_card_miss_projection_bounds_malformed_diagnostics(runtime_dir):
    first = state(1, '1-1', 0)
    audit.observe(runtime_dir, audit.capture(first), first, [], now=1000)
    second = state(1, '1-1', 1)
    miss = {
        'schema': 1, 'event': 'decision', 'at': 1001,
        'decision': 'battle_boss_card_missed', 'card': 'ノリウツール',
        'chapter': 1, 'ally': 'ヘルメス', 'enemy': 'ニンフ', 'castle': 'ながはま',
        'side': {'unexpected': 'value'}, 'screen_kind': 'egg_battle_menu',
        'reason': '携行切り札を開けず攻撃',
        'observed_metric': {
            'cards_left': 'not-a-list', 'scrolls': True, 'cursor': 225,
            'text': 'x' * 200, 'empty_egg': 'false',
            'summon_egg_unavailable': 1, 'enemy_summon_observed': None,
        },
        **IDENTITY,
    }
    sealed = []
    audit.observe(runtime_dir, audit.capture(first), second, [miss], now=1001,
                  on_sealed=sealed.append)
    battle = sealed[0]['battle_event']
    assert battle['side'] is None
    assert battle['cards_left'] is None
    assert battle['scrolls'] is None and battle['cursor'] is None
    assert battle['empty_egg'] is None
    assert battle['summon_egg_unavailable'] is None
    assert battle['enemy_summon_observed'] is None
    assert battle['screen_text'] == 'x' * 96


def test_boss_battle_starts_are_preserved_in_complete_decision_counts(tmp_path):
    # Issue #5525: a low-frequency decision must survive alongside more than
    # 30 other decision names instead of disappearing from the audit summary.
    current = {
        'chapter': 9, 'boss_name': 'ハデス', 'bosses': ['ボス'],
        'started_at': 1000.0, 'first_tick': 0, 'last_at': 1700.0,
        'last_tick': 400, 'observations': 200, 'max_gap_s': 1.0,
        'bot_version': 'synthetic-v1',
    }
    rows = [{'at': 1000.0 + i, 'decision': f'filler_{i:02d}'}
            for i in range(30) for _ in range(2)]
    rows.append({'at': 1700.0, 'decision': 'battle_start', 'enemy': '別の敵'})
    state_value = _boss_state()
    record = audit._build_boss_incident(str(tmp_path), state_value, state_value,
                                        [], 1700.0, current, rows=rows)
    counts = record['decisions']['counts']
    assert len(counts) == 31
    assert counts['battle_start'] == 1
    assert all(counts[f'filler_{i:02d}'] == 2 for i in range(30))
    assert record['boss_phase']['battle_starts'] == 1
    assert record['sorties']['battle_starts'] == 1
    assert record['battles']['decisions'] == 1


def test_order_start_signal_is_preserved_after_more_than_30_decision_types():
    # Issue #5525: the rare order_start must reach _signals, not just the
    # human-readable count map.
    rows = [{'decision': f'filler_{i:02d}'} for i in range(31) for _ in range(2)]
    rows.append({'decision': 'order_start'})
    counts = audit._decision_counts(rows)
    assert counts['order_start'] == 1
    assert 'order_start_without_launch' in audit._signals({}, counts, 0, 0, 0)


def test_boss_phase_with_live_boss_attack_sorties_is_deferred_until_they_settle(runtime_dir):
    _run_boss(runtime_dir, live_sortie=True)
    assert audit.load_incidents(runtime_dir) == []
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())
    assert saved['boss']['reported'] is False
    assert saved['boss']['live_boss_sortie_deferred'] is True

    # Once the boss-targeted orders settle, the already-expired phase is
    # evaluated on the next observation instead of being suppressed forever.
    value = _boss_state(tick=401)
    value['policy']['world']['sorties'] = {
        'boss-en-route': {'status': 'arrived', 'target': 'ボス',
                          'purpose': 'attack', 'tick': 390},
        'boss-unconfirmed': {'status': 'arrived', 'target': 'ボス',
                             'purpose': 'attack', 'tick': 390},
    }
    audit.observe(runtime_dir, audit.capture(value), value, [],
                  now=1000 + 401 * STEP)
    assert len(audit.load_incidents(runtime_dir)) == 1


def test_chapter_four_season_king_battle_counts_as_boss_evidence():
    from docich.hanjuku_map_sources import SEASON_KINGS
    king = sorted(SEASON_KINGS)[0]
    rows = [{'decision': 'battle_start', 'enemy': king}]
    assert audit._rows_boss_battle(rows, 4, '四季王') is True
    # Season kings are chapter 4 evidence only; the same name in another
    # chapter's window must not suppress its boss incident.
    assert audit._rows_boss_battle(rows, 9, 'ハデス') is False
