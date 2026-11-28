"""Segments/manifest tests for the speedrun RTA spool."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from docich.hanjuku_speedrun.rta import manifest, report_segments, spool, store, timer, trace

ROOT = Path(__file__).resolve().parents[1]
BASE_MS = 1_700_000_000_000


def make_runtime(root, runtime_id='g1-abc123'):
    runtime = Path(root) / 'run-soren-live' / 'runtimes' / runtime_id
    runtime.mkdir(parents=True)
    return runtime


def start_running(runtime, started_ms=BASE_MS):
    records = timer.default_records()
    current = timer.new_timer(runtime.name, records, started_ms)
    current, records = timer.start_attempt(current, records, started_ms)
    store.save_timer(runtime, current)
    return current


def set_splits(runtime, current):
    current = dict(current)
    current['splits'] = [
        {'index': 0, 'end_index': 0, 'segment_ms': 10_000,
         'cumulative_ms': 10_000, 'verdict': 'gold', 'source': 'chapter_end_save'},
        {'index': 1, 'end_index': 1, 'segment_ms': 15_000,
         'cumulative_ms': 25_000, 'verdict': 'blue', 'source': 'chapter_end_save'},
    ]
    current['status'] = 'dnf'
    current['total_ms'] = 25_000
    current['ended_at_ms'] = BASE_MS + 25_000
    current['dnf_reason'] = 'stalled'
    current['current_index'] = 2
    store.save_timer(runtime, current)
    return current


def write_run(runtime, **values):
    (runtime / 'hanjuku_run.json').write_text(json.dumps(values), encoding='utf-8')


def add_trace(runtime, current, elapsed, *, phase, month, bot, battles, decisions=()):
    state = {'policy': {'chapter': 1, 'month': month, 'chart_edition': 'speedrun'},
             'screen_kind': 'field_menu'}
    records = [{'decision': name} for name in decisions]
    return trace.append(runtime, current, records=records, state=state,
                        observed_ms=BASE_MS + elapsed)


def test_build_segments_summarizes_phases_battles_and_months(tmp_path):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    current = set_splits(runtime, current)
    attempt_id = 'a0001-' + runtime.name
    rows = [
        (2_000, 'field', '1月', 'v1', 1, ()),
        (6_000, 'battle', '1月', 'v1', 3, ('order_start',)),
        (9_000, 'field', '2月', 'v2', 4, ('battle_result',)),
        (15_000, 'field', '2月', 'v2', 5, ()),
        (24_000, 'battle', '3月', 'v2', 7, ('battle_result',)),
    ]
    for elapsed, phase, month, bot, battles, decisions in rows:
        write_run(runtime, phase=phase, bot_version=bot, battles_started=battles)
        add_trace(runtime, current, elapsed, phase=phase, month=month, bot=bot,
                  battles=battles, decisions=decisions)

    document = report_segments.build_segments(runtime, attempt_id)
    assert document['attempt_id'] == attempt_id
    assert document['trace_observations'] == 5
    assert document['segments'][0]['phase_ms'] == {'battle': 3_000, 'field': 5_000}
    assert document['segments'][1]['phase_ms'] == {'battle': 1_000, 'field': 9_000}
    assert document['segments'][0]['battles'] == 3
    assert document['segments'][1]['battles'] == 2
    assert document['segments'][0]['months'] == ['1月', '2月']
    assert document['segments'][1]['months'] == ['2月', '3月']
    assert document['segments'][0]['bot_versions'] == ['v1', 'v2']
    assert document['bot_versions'] == ['v1', 'v2']
    # Data the bot does not expose is null, not estimated.
    for segment in document['segments']:
        assert segment['sorties'] is None
        assert segment['hold_ms'] is None
        assert segment['stall_ms'] is None
        assert segment['llm_wait_ms'] is None
    written = json.loads(report_segments.segments_path(runtime, attempt_id).read_text())
    assert written['segments'][0]['segment_ms'] == 10_000
    assert written['segments'][1]['cumulative_ms'] == 25_000


def test_build_segments_without_trace_keeps_null_fields(tmp_path):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    current = set_splits(runtime, current)
    attempt_id = 'a0001-' + runtime.name
    document = report_segments.build_segments(runtime, attempt_id)
    assert document['trace_observations'] == 0
    assert document['segments'][0]['phase_ms'] == {}
    assert document['segments'][0]['battles'] is None
    assert document['segments'][0]['months'] == []


def test_build_segments_falls_back_to_the_attempt_row(tmp_path):
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    attempt_id = 'a0001-' + runtime.name
    row = {
        'schema': 1, 'game': 'hanjuku-speedrun', 'runtime_id': runtime.name,
        'attempt_id': attempt_id, 'attempt_no': 1, 'status': 'dnf',
        'result': 'dnf', 'started_at_ms': BASE_MS, 'ended_at_ms': BASE_MS + 500,
        'total_ms': 500, 'segments_ms': [500] + [None] * 11,
        'cumulative_ms': [500] + [None] * 11,
        'splits': [{'index': 0, 'end_index': 0, 'segment_ms': 500,
                    'cumulative_ms': 500, 'verdict': 'gold'}],
    }
    store.append_attempt(runtime, row)
    # The shared timer now belongs to a different attempt.
    other = timer.new_timer(runtime.name, timer.default_records(), BASE_MS)
    other['attempt_id'] = 'a0009-other'
    store.save_timer(runtime, other)
    document = report_segments.build_segments(runtime, attempt_id)
    assert document['attempt_no'] == 1
    assert document['segments'][0]['segment_ms'] == 500


def test_build_manifest_lists_the_spool_and_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, '_docich_commit', lambda _root: 'a' * 40)
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    current = set_splits(runtime, current)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    directory.mkdir(parents=True)
    (directory / 'decisions-0001.jsonl').write_bytes(b'{"a":1}\n')
    (directory / 'trace.jsonl').write_bytes(b'{"at":1}\n')
    report_segments.build_segments(runtime, attempt_id)

    document = manifest.build_manifest(runtime, attempt_id)
    config = ROOT / 'config' / 'games' / 'hanjuku-speedrun.toml'
    assert document['config_sha256'] == hashlib.sha256(config.read_bytes()).hexdigest()
    assert document['docich_commit'] == 'a' * 40
    assert document['archived'] is False
    assert document['inventory'] == 'pre-compression snapshot'
    assert document['attempt_id'] == attempt_id
    assert document['status'] == 'dnf'
    assert document['total_ms'] == 25_000
    assert document['spool_bytes_at_build'] == sum(
        item['bytes'] for item in document['spool_files_at_build'])
    assert [item['path'] for item in document['spool_files_at_build']] == [
        'decisions-0001.jsonl', 'trace.jsonl']
    written = json.loads(manifest.manifest_path(runtime, attempt_id).read_text())
    assert written == document


def test_build_manifest_uses_the_segment_bot_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, '_docich_commit', lambda _root: None)
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    current = set_splits(runtime, current)
    attempt_id = 'a0001-' + runtime.name
    write_run(runtime, phase='field', bot_version='v9', battles_started=1)
    add_trace(runtime, current, 1_000, phase='field', month='1月', bot='v9',
              battles=1)
    report_segments.build_segments(runtime, attempt_id)
    document = manifest.build_manifest(runtime, attempt_id)
    assert document['bot_versions'] == ['v9']


def test_build_manifest_tolerates_a_broken_git_and_missing_config(tmp_path, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired('git', 2)

    monkeypatch.setattr(manifest.subprocess, 'run', timeout)
    assert manifest._docich_commit(Path('.')) is None
    assert manifest._docich_commit(None) is None
    assert manifest._config_sha256(Path(tmp_path)) is None


def test_build_manifest_includes_dropped_counters(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, '_docich_commit', lambda _root: None)
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    frame = directory / 'frames' / 'frame-00001.png'
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b'x' * 100)
    spool.enforce_cap(runtime, attempt_id, max_bytes_value=1)
    document = manifest.build_manifest(runtime, attempt_id)
    assert document['dropped']['types']['frames'] == {'files': 1, 'bytes': 100}
    assert document['dropped']['over_cap'] is False
