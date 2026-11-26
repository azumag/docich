"""Observation-trace tests for the speedrun RTA logs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docich.hanjuku_speedrun.rta import hook, spool, store, timer, trace

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


def append(runtime, current, observed_ms, *, screenshot=None, records=None,
           state=None, meta=None):
    return trace.append(runtime, current, records=records, state=state,
                        meta=meta, screenshot=screenshot, observed_ms=observed_ms)


def trace_lines(runtime, attempt_id):
    path = trace.trace_path(runtime, attempt_id)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_trace_line_is_bounded_and_valid_json(tmp_path):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    records = [{'decision': 'x' * 200, 'chapter': 1,
                'deviation_reason': 'y' * 500} for _ in range(20)]
    state = {'policy': {'chapter': 3, 'month': 'm' * 100,
                        'chart_edition': 'c' * 100}}
    append(runtime, current, BASE_MS + 10, records=records, state=state)
    path = trace.trace_path(runtime, 'a0001-' + runtime.name)
    raw = path.read_bytes().splitlines()
    assert len(raw) == 1
    assert len(raw[0]) <= trace.MAX_LINE_BYTES
    parsed = json.loads(raw[0])
    assert parsed['at'] == BASE_MS + 10
    assert parsed['elapsed_ms'] == 10


def test_trace_records_only_observable_keys(tmp_path):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    (runtime / 'hanjuku_run.json').write_text(json.dumps({
        'phase': 'field_menu', 'battles_started': 7, 'bot_version': 'v42',
    }), encoding='utf-8')
    records = [
        {'decision': 'order_start', 'chapter': 5,
         'deviation_reason': 'waiting for the command menu'},
        {'decision': 'battle_result'},
    ]
    state = {'policy': {'chapter': 4, 'month': '8月',
                        'chart_edition': 'speedrun'},
             'screen_kind': 'command_menu'}
    row = append(runtime, current, BASE_MS + 12_345, records=records, state=state)
    assert row['elapsed_ms'] == 12_345
    assert row['chapter'] == 5
    assert row['month'] == '8月'
    assert row['phase'] == 'field_menu'
    assert row['screen_kind'] == 'command_menu'
    assert row['decisions'] == ['order_start', 'battle_result']
    assert row['hold_reason'] == 'waiting for the command menu'
    assert row['battles_started'] == 7
    assert row['bot_version'] == 'v42'
    assert row['chart_edition'] == 'speedrun'
    assert row['splits'] == 0
    assert row['interval_ms'] is None
    # A key the bot does not expose is absent, never guessed.
    assert 'input' not in row
    assert 'llm_wait_ms' not in row

    second = append(runtime, current, BASE_MS + 13_845, records=records, state=state)
    assert second['interval_ms'] == 1500


def test_trace_rotates_at_the_bound_and_compresses(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    directory.mkdir(parents=True)
    path = directory / 'trace.jsonl'
    path.write_bytes(b'x' * 20)
    monkeypatch.setattr(trace, 'MAX_TRACE_BYTES', 10)
    compressed = []
    monkeypatch.setattr(spool, 'compress_detached', lambda p: compressed.append(Path(p)) or True)
    append(runtime, current, BASE_MS + 1)
    rotated = directory / 'trace-0001.jsonl'
    assert rotated.read_bytes() == b'x' * 20
    assert compressed == [rotated]
    assert path.exists()
    assert len(path.read_text(encoding='utf-8').splitlines()) == 1


def test_trace_copies_a_periodic_frame_once_per_bucket(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir()
    shot.write_bytes(b'png')
    monkeypatch.setenv('DOCICH_SPEEDRUN_FRAME_INTERVAL_SEC', '1')
    append(runtime, current, BASE_MS + 1000, screenshot=shot)
    append(runtime, current, BASE_MS + 1500, screenshot=shot)
    append(runtime, current, BASE_MS + 2100, screenshot=shot)
    frames = sorted(p.name for p in spool.frames_dir(runtime, 'a0001-' + runtime.name).iterdir())
    assert frames == ['frame-00001.png', 'frame-00002.png']
    assert (spool.frames_dir(runtime, 'a0001-' + runtime.name) / 'frame-00001.png').read_bytes() == b'png'


def test_trace_skips_frames_over_the_size_limit(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir()
    shot.write_bytes(b'too many bytes')
    monkeypatch.setenv('DOCICH_SPEEDRUN_FRAME_INTERVAL_SEC', '1')
    monkeypatch.setattr(trace, 'MAX_FRAME_BYTES', 3)
    append(runtime, current, BASE_MS + 1000, screenshot=shot)
    assert not spool.frames_dir(runtime, 'a0001-' + runtime.name).exists()


def test_trace_copies_the_frame_at_a_confirmed_split(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir()
    shot.write_bytes(b'frame')
    monkeypatch.setenv('DOCICH_SPEEDRUN_FRAME_INTERVAL_SEC', '100000')
    append(runtime, current, BASE_MS + 500, screenshot=shot)
    current = store.load_timer(runtime)
    current['splits'] = [{'index': 0, 'end_index': 0, 'segment_ms': 900,
                          'cumulative_ms': 900, 'verdict': 'gold'}]
    store.save_timer(runtime, current)
    append(runtime, current, BASE_MS + 900, screenshot=shot)
    frames = [p.name for p in spool.frames_dir(runtime, 'a0001-' + runtime.name).iterdir()]
    assert any(name.startswith('split-00-') for name in frames)


def test_trace_does_not_copy_a_split_frame_after_rotation(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    attempt_id = 'a0001-' + runtime.name
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir()
    shot.write_bytes(b'frame')
    monkeypatch.setenv('DOCICH_SPEEDRUN_FRAME_INTERVAL_SEC', '100000')
    monkeypatch.setattr(spool, 'compress_detached', lambda _path: True)
    current['splits'] = [{'index': 0, 'end_index': 0, 'segment_ms': 900,
                          'cumulative_ms': 900, 'verdict': 'gold'}]
    store.save_timer(runtime, current)
    append(runtime, current, BASE_MS + 500, screenshot=shot)
    frames = spool.frames_dir(runtime, attempt_id)
    split_frames = [p.name for p in frames.iterdir() if p.name.startswith('split-')]
    assert len(split_frames) == 1

    # A rotation makes the active trace empty.  The split count must come
    # from the rotation-independent state, not from the empty file.
    monkeypatch.setattr(trace, 'MAX_TRACE_BYTES', 1)
    append(runtime, current, BASE_MS + 900, screenshot=shot)
    split_frames = [p.name for p in frames.iterdir() if p.name.startswith('split-')]
    assert len(split_frames) == 1
    state = json.loads((frames.parent / trace.STATE_FILE).read_text())
    assert state['splits'] == 1


def test_trace_marks_a_periodic_frame_bucket_once_when_the_cap_is_full(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    attempt_id = 'a0001-' + runtime.name
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir()
    shot.write_bytes(b'png')
    monkeypatch.setenv('DOCICH_SPEEDRUN_FRAME_INTERVAL_SEC', '1')
    monkeypatch.setenv('DOCICH_SPEEDRUN_SPOOL_MAX_BYTES', '10')
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(1)
        return 0

    monkeypatch.setattr(spool, 'enforce_cap', forbidden)
    for elapsed in (1000, 1100, 1200, 1300, 1400):
        append(runtime, current, BASE_MS + elapsed, screenshot=shot)
    frames = spool.frames_dir(runtime, attempt_id)
    names = sorted(path.name for path in frames.iterdir())
    assert names == ['.frame-00001.skip']
    # The copy was refused before it happened, so the per-observation cap
    # cleanup (a full scandir) did not run at all.
    assert calls == []


def test_trace_does_nothing_for_a_pending_attempt(tmp_path):
    runtime = make_runtime(tmp_path)
    records = timer.default_records()
    pending = timer.new_timer(runtime.name, records, BASE_MS)
    store.save_timer(runtime, pending)
    assert append(runtime, pending, BASE_MS + 10) is None
    assert not spool.spool_dir(runtime, 'a0001-' + runtime.name).exists()


def test_trace_does_not_follow_a_symlinked_file(tmp_path):
    runtime = make_runtime(tmp_path)
    current = start_running(runtime)
    directory = spool.spool_dir(runtime, 'a0001-' + runtime.name)
    directory.mkdir(parents=True)
    outside = tmp_path / 'outside.jsonl'
    outside.write_text('safe\n', encoding='utf-8')
    (directory / 'trace.jsonl').symlink_to(outside)
    assert append(runtime, current, BASE_MS + 10) is None
    assert outside.read_text(encoding='utf-8') == 'safe\n'


def test_hook_still_returns_the_timer_when_the_trace_fails(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir()
    shot.write_bytes(b'frame')

    def fail(*_args, **_kwargs):
        raise RuntimeError('trace down')

    monkeypatch.setattr(trace, 'append', fail)
    result = hook.observe(runtime, [], {},
                          {'hanjuku': {'name_entered': True, 'phase': 'name',
                                       'gameplay_seen': False,
                                       'clear_ending_seen': False,
                                       'terminal_reason': None}},
                          screenshot=shot)
    assert result is not None
    assert result['status'] == 'running'


def test_hook_does_not_trace_a_restart_of_a_closed_attempt(tmp_path, monkeypatch):
    """After a DNF, a state-restart keeps observing but must not touch its spool."""
    from docich.hanjuku_speedrun.rta import announce
    monkeypatch.setattr(announce, 'launch_pending', lambda *a, **k: True)
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    shot = runtime / 'screenshot.png'
    shot.write_bytes(b'png')
    run_path = runtime / 'hanjuku_run.json'
    run_path.write_text(json.dumps({
        'game': 'hanjuku-speedrun', 'runtime_id': runtime.name,
        'generation': 1, 'lease_id': 'lease', 'phase': 'field',
        'observed_at_ms': BASE_MS + 10_000, 'terminal_reason': 'game_over',
    }), encoding='utf-8')
    closed = hook.observe(runtime, [], {}, {}, screenshot=shot)
    assert closed['status'] == 'dnf'

    # The local driver can restart from .state; the attempt is not resumed, so
    # later frames belong to no attempt and must not grow this spool.
    run_path.write_text(json.dumps({
        'game': 'hanjuku-speedrun', 'runtime_id': runtime.name,
        'generation': 1, 'lease_id': 'lease', 'phase': 'field',
        'observed_at_ms': BASE_MS + 70_000,
    }), encoding='utf-8')
    again = hook.observe(runtime, [], {}, {}, screenshot=shot)
    assert again['status'] == 'dnf'
    rows = trace_lines(runtime, 'a0001-' + runtime.name)
    assert len(rows) == 1
    assert rows[0]['at'] == BASE_MS + 10_000
