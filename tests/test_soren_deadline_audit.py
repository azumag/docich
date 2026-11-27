"""Soren deadline misplacements become audit evidence only when a safe alternative existed."""
from __future__ import annotations

import json
from pathlib import Path

from docich import soren_deadline_audit as audit
from docich.hanjuku import month_audit


def piece(pid, ptype, x, y, r=None):
    row = {'id': pid, 'type': ptype, 'x': x, 'y': y}
    if r is not None:
        row['r'] = r
    return row


def high_support(x=0.0):
    return piece(f'high-{x}', 8, x, 2.8, 0.8)


def crossing_transition(supports=None, decision_x=0.0):
    supports = supports if supports is not None else [high_support()]
    support = min(supports, key=lambda row: abs(row['x'] - decision_x))
    radius = 0.207
    contact = radius + support['r']
    dx = abs(decision_x - support['x'])
    y = support['y'] + (contact * contact - dx * dx) ** 0.5
    placed = piece('new-piece', 1, decision_x, y, radius)
    before = [dict(row) for row in supports]
    after = [*before, placed]
    prev = {
        'state_snapshot': {'pieces': before},
        'decision_x': decision_x,
        'next_type': 1,
        'turn': 41,
        'score': 123,
        'piece_count': len(before),
        'decision_reason': 'synthetic high-stack choice',
    }
    curr = {'state_snapshot': {'pieces': after}, 'turn': 42}
    return prev, curr


def monitor_event(prev, curr, ts=1700000000):
    event = audit.evaluate_transition(prev, curr)
    assert event is not None
    return {'ts': ts, 'detector': 'actual_snapshot_geometry', **event}


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows),
                    encoding='utf-8')


def test_transition_flags_crossing_only_because_a_safe_floor_alternative_exists():
    prev, curr = crossing_transition()
    event = audit.evaluate_transition(prev, curr)
    assert event is not None
    assert event['status'] == 'inappropriate'
    assert event['actual_new_piece']['top_y'] > audit.DEADLINE_Y
    assert event['best_lower_alternative']['top_y'] <= audit.DEADLINE_Y
    assert event['lower_candidate_count'] > 0


def test_transition_with_no_safe_alternative_is_appropriate_and_not_an_incident():
    supports = [high_support(x) for x in (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0)]
    for index, row in enumerate(supports):
        row['id'] = f'wall-{index}'
    prev, curr = crossing_transition(supports=supports)
    event = audit.evaluate_transition(prev, curr)
    assert event is not None
    assert event['status'] == 'appropriate'
    assert event['best_lower_alternative'] is None
    assert event['merge_alternative_improves_top'] is False


def test_an_in_air_snapshot_is_not_a_landed_misplacement():
    prev, curr = crossing_transition()
    after = curr['state_snapshot']['pieces']
    after[-1]['y'] = 6.0
    assert audit.evaluate_transition(prev, curr) is None


def test_scan_records_confirmed_event_once_and_rendering_is_soren_specific(tmp_path):
    prev, curr = crossing_transition()
    good = monitor_event(prev, curr)
    wall = [high_support(x) for x in (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0)]
    for index, row in enumerate(wall):
        row['id'] = f'wall-{index}'
    no_alt_prev, no_alt_curr = crossing_transition(supports=wall)
    ignored = monitor_event(no_alt_prev, no_alt_curr, ts=1700000001)
    log = tmp_path / 'logs' / 'deadline_misplacement_monitor.jsonl'
    write_jsonl(log, [good, ignored])
    with log.open('a', encoding='utf-8') as stream:
        stream.write('{not json\n')

    runtime = tmp_path / 'audit'
    result = audit.scan_log(log, runtime, now=2000)
    assert result['status'] == 'ok'
    assert result['malformed'] == 1
    assert result['confirmed'] == 1
    assert result['ignored'] == 1
    assert result['recorded'] == 1

    incidents = month_audit.load_incidents(runtime)
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident['kind'] == 'soren_deadline_misplacement'
    assert incident['auto_file'] is True
    assert incident['fingerprint'] == audit.FINGERPRINT
    assert incident['soren_event']['verification'] == 'independent_geometry'
    assert incident['soren_event']['best_lower_alternative']['top_y'] <= audit.DEADLINE_Y
    assert incident['soren_event']['prev_pieces']
    assert incident['soren_event']['curr_pieces']

    title = month_audit._issue_title(incident)
    body = month_audit.render(incident)
    comment = month_audit.render_comment(incident)
    assert title.startswith('[ソ連ゲーム]')
    assert '安全な配置先がある' in title
    assert '### 安全だった代替' in body
    assert '半熟英雄' not in title
    assert '再発: 安全な配置先がある' in comment

    second = audit.scan_log(log, runtime, now=2100)
    assert second['confirmed'] == 0
    assert second['duplicates'] == 2
    assert len(month_audit.load_incidents(runtime)) == 1


def test_snapshot_backed_monitor_false_positive_is_rejected_by_independent_geometry(tmp_path):
    prev, curr = crossing_transition()
    event = monitor_event(prev, curr)
    event['curr_pieces'][-1]['y'] = 6.0
    log = tmp_path / 'deadline_misplacement_monitor.jsonl'
    write_jsonl(log, [event])
    runtime = tmp_path / 'audit'
    result = audit.scan_log(log, runtime, now=3000)
    assert result['confirmed'] == 0
    assert result['ignored'] == 1
    assert month_audit.load_incidents(runtime) == []


def test_legacy_event_without_snapshots_uses_monitor_status_only_when_safe_alternative_is_present(tmp_path):
    legacy = {
        'ts': 1700000100,
        'kind': 'deadline_misplacement_check',
        'status': 'inappropriate',
        'turn': 9,
        'next_turn': 10,
        'decision_x': 0.0,
        'deadline_y': audit.DEADLINE_Y,
        'actual_new_piece': {'id': 'p', 'type': 1, 'x': 0.0, 'y': 3.5, 'r': 0.207, 'top_y': 3.707},
        'best_lower_alternative': {'x': -2.0, 'top_y': -4.7, 'support_id': None},
    }
    assert audit.assess_event(legacy)['verification'] == 'monitor_status'
    no_alternative = dict(legacy, ts=1700000101, best_lower_alternative=None,
                          merge_alternative_improves_top=False, best_merge_alternative=None)
    assert audit.assess_event(no_alternative) is None

    log = tmp_path / 'deadline_misplacement_monitor.jsonl'
    write_jsonl(log, [legacy, no_alternative])
    runtime = tmp_path / 'audit'
    result = audit.scan_log(log, runtime, now=4000)
    assert result['confirmed'] == 1
    assert result['ignored'] == 1
    incidents = month_audit.load_incidents(runtime)
    assert len(incidents) == 1
    assert incidents[0]['soren_event']['verification'] == 'monitor_status'


def test_spawn_reuses_existing_detached_issue_filer(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(audit.month_audit, 'spawn_issue_filer',
                        lambda path, record: calls.append((path, record)) or True)
    prev, curr = crossing_transition()
    log = tmp_path / 'deadline_misplacement_monitor.jsonl'
    write_jsonl(log, [monitor_event(prev, curr)])
    runtime = tmp_path / 'audit'
    result = audit.scan_log(log, runtime, spawn=True, now=5000)
    assert result['reported'] == 1
    assert len(calls) == 1
    assert calls[0][0] == runtime
    assert calls[0][1]['kind'] == 'soren_deadline_misplacement'


def test_dry_run_reports_without_writing_incident_or_state(tmp_path):
    prev, curr = crossing_transition()
    log = tmp_path / 'deadline_misplacement_monitor.jsonl'
    write_jsonl(log, [monitor_event(prev, curr)])
    runtime = tmp_path / 'audit'
    result = audit.scan_log(log, runtime, dry_run=True, now=6000)
    assert result['confirmed'] == 1
    assert result['incidents'][0]['incident_id'].startswith('soren-deadline-')
    assert not (runtime / audit.STATE_FILE).exists()
    assert month_audit.load_incidents(runtime) == []
