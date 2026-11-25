"""Storage-bound, atomicity and corruption tests for the speedrun RTA files."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from docich.hanjuku_speedrun.rta import hook, store, timer

BASE = 1_700_000_000.0


def make_runtime(root, runtime_id='g1-abc'):
    runtime = Path(root) / 'run-soren-live' / 'runtimes' / runtime_id
    runtime.mkdir(parents=True)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b'frame')
    return runtime, shot


def start(runtime, shot):
    os.utime(shot, (BASE, BASE))
    return hook.observe(runtime, [], {},
                        {'hanjuku': {'name_entered': True,
                                     'phase': 'name',
                                     'gameplay_seen': False,
                                     'clear_ending_seen': False,
                                     'terminal_reason': None}},
                        screenshot=shot)


def test_interrupted_replace_leaves_the_previous_file_readable(tmp_path, monkeypatch):
    runtime, _shot = make_runtime(tmp_path)
    store.save_records(runtime, timer.default_records())
    before = store.records_path(runtime).read_bytes()

    def fail_replace(_src, _dst):
        raise OSError('simulated interruption')

    monkeypatch.setattr(store.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        store.save_records(runtime, timer.default_records(
            now_ms=1234))
    assert store.records_path(runtime).read_bytes() == before


def test_size_bound_refuses_without_damaging_the_old_timer(tmp_path, monkeypatch):
    runtime, shot = make_runtime(tmp_path)
    start(runtime, shot)
    path = store.timer_path(runtime)
    before = path.read_bytes()
    current = store.load_timer(runtime)
    monkeypatch.setattr(store, 'MAX_TIMER_BYTES', 10)
    with pytest.raises(store.StoreError):
        store.save_timer(runtime, current)
    assert path.read_bytes() == before
    monkeypatch.undo()
    assert store.load_timer(runtime)['status'] == 'running'


def test_attempt_line_is_bounded_and_still_valid_json(tmp_path):
    runtime, _shot = make_runtime(tmp_path)
    row = {
        'schema': 1, 'game': 'hanjuku-speedrun', 'runtime_id': 'g1-abc',
        'attempt_id': 'a0001-g1-abc', 'attempt_no': 1,
        'status': 'finished', 'result': 'finished',
        'started_at_ms': 1, 'ended_at_ms': 2, 'total_ms': 1,
        'segments_ms': [1] * 12, 'cumulative_ms': [1] * 12,
        'merged_ranges': [], 'splits': [],
        'junk': 'x' * (store.MAX_ATTEMPT_LINE_BYTES * 4),
    }
    store.append_attempt(runtime, row)
    lines = store.attempts_path(runtime).read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    assert len(lines[0].encode('utf-8')) <= store.MAX_ATTEMPT_LINE_BYTES
    parsed = json.loads(lines[0])
    assert parsed['attempt_id'] == 'a0001-g1-abc'
    assert parsed.get('truncated') is True


def test_attempts_history_is_bounded_by_rows(tmp_path):
    runtime, _shot = make_runtime(tmp_path)
    for index in range(5):
        store.append_attempt(runtime, {
            'schema': 1, 'game': 'hanjuku-speedrun',
            'attempt_id': f'a{index:04d}-g1-abc', 'attempt_no': index,
            'status': 'finished'})
    assert [row['attempt_id'] for row in store.load_attempts(runtime)] == [
        'a0000-g1-abc', 'a0001-g1-abc', 'a0002-g1-abc',
        'a0003-g1-abc', 'a0004-g1-abc']
    # A repeated id is a no-op, not a second line.
    store.append_attempt(runtime, {'schema': 1, 'game': 'hanjuku-speedrun',
                                   'attempt_id': 'a0004-g1-abc',
                                   'attempt_no': 4, 'status': 'finished'})
    assert len(store.load_attempts(runtime)) == 5


def test_corrupt_attempts_file_keeps_readable_rows_and_quarantines_bytes(tmp_path):
    runtime, _shot = make_runtime(tmp_path)
    path = store.attempts_path(runtime)
    path.parent.mkdir(parents=True)
    good = json.dumps({'attempt_id': 'a0001-g1-abc', 'attempt_no': 1,
                       'status': 'finished'})
    path.write_text(good + '\n{broken\n', encoding='utf-8')
    rows = store.load_attempts(runtime)
    assert [row['attempt_id'] for row in rows] == ['a0001-g1-abc']
    corrupt = list(path.parent.glob('attempts.jsonl.corrupt-*'))
    assert corrupt
    assert '{broken' in corrupt[0].read_text(encoding='utf-8')
    # The repaired file is a clean JSONL again.
    assert json.loads(path.read_text(encoding='utf-8').strip())['attempt_id'] == 'a0001-g1-abc'


def test_corrupt_timer_and_records_are_quarantined(tmp_path):
    runtime, _shot = make_runtime(tmp_path)
    directory = store.data_dir(runtime)
    directory.mkdir(parents=True)
    (directory / 'timer.json').write_text('null', encoding='utf-8')
    (directory / 'records.json').write_text('[]', encoding='utf-8')
    assert store.load_timer(runtime) is None
    assert store.load_records(runtime)['pb'] is None
    assert list(directory.glob('timer.json.corrupt-*'))
    assert list(directory.glob('records.json.corrupt-*'))


def test_interrupted_attempt_rewrite_keeps_the_previous_jsonl(tmp_path, monkeypatch):
    runtime, _shot = make_runtime(tmp_path)
    store.append_attempt(runtime, {'schema': 1, 'game': 'hanjuku-speedrun',
                                   'attempt_id': 'a0001-g1-abc', 'attempt_no': 1,
                                   'status': 'finished'})
    before = store.attempts_path(runtime).read_bytes()

    def fail_replace(_src, _dst):
        raise OSError('simulated interruption')

    monkeypatch.setattr(store.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        store.append_attempt(runtime, {'schema': 1, 'game': 'hanjuku-speedrun',
                                       'attempt_id': 'a0002-g1-abc',
                                       'attempt_no': 2, 'status': 'dnf'})
    assert store.attempts_path(runtime).read_bytes() == before


def test_finalize_is_idempotent_after_a_partial_write(tmp_path):
    runtime, shot = make_runtime(tmp_path)
    start(runtime, shot)
    for chapter in range(1, 11):
        os.utime(shot, (BASE + chapter, BASE + chapter))
        hook.observe(runtime, [{'decision': 'prompt',
                                'strategy_variant': 'chapter_end_save',
                                'chapter': chapter}], {},
                     {'hanjuku': {'name_entered': True,
                                  'clear_ending_seen': False,
                                  'terminal_reason': None}},
                     screenshot=shot)
    (runtime / 'hanjuku_run.json').write_text(
        '{"terminal_reason": "game_clear", "clear_ending_seen": true}',
        encoding='utf-8')
    current = store.load_timer(runtime)
    records = store.load_records(runtime)
    finished, updated = timer.finish(current, records, BASE * 1000 + 20_000)
    # Simulate a crash after the records write and before the JSONL/timer
    # writes: the retry must still produce exactly one row and a gold total.
    store.save_records(runtime, updated)
    first = store.finalize_attempt(runtime)
    second = store.finalize_attempt(runtime)
    assert first['status'] == 'finished'
    assert first['total_verdict'] == 'gold'
    assert second == first
    assert len(store.load_attempts(runtime)) == 1
    assert store.load_records(runtime)['pb']['attempt_id'] == current['attempt_id']


def test_finalize_does_not_touch_another_runtimes_timer(tmp_path):
    runtime_a, shot = make_runtime(tmp_path, 'g1-abc')
    start(runtime_a, shot)
    before = store.load_timer(runtime_a)
    runtime_b, _shot_b = make_runtime(tmp_path, 'g2-def')

    # ``timer.json`` is shared.  runtime B's teardown/retry must not DNF the
    # attempt that is still running in runtime A.
    result = store.finalize_attempt(runtime_b)
    assert result == before
    assert store.load_timer(runtime_a)['status'] == 'running'
    assert store.load_attempts(runtime_a) == []


def test_transient_read_error_is_not_quarantined_and_skips_the_observation(
        tmp_path, monkeypatch, capsys):
    runtime, shot = make_runtime(tmp_path)
    start(runtime, shot)
    store.save_records(runtime, timer.default_records())
    before = store.records_path(runtime).read_bytes()
    original = Path.read_text

    def failing_read(self, *args, **kwargs):
        if self.name == 'records.json':
            raise OSError(5, 'simulated EIO')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', failing_read)
    with pytest.raises(OSError):
        store.load_records(runtime)
    assert list(store.data_dir(runtime).glob('records.json.corrupt-*')) == []
    assert store.records_path(runtime).read_bytes() == before

    # The hook skips this observation with exactly one stderr line and does
    # not finalize or restart the attempt from partial state.
    observed = hook.observe(
        runtime, [], {},
        {'hanjuku': {'phase': 'name', 'gameplay_seen': True,
                     'name_entered': True, 'terminal_reason': None}},
        screenshot=shot)
    assert observed is None
    stderr = capsys.readouterr().err
    assert stderr.count('\n') == 1
    assert 'observation skipped' in stderr
    assert store.load_timer(runtime)['status'] == 'running'
    assert store.load_attempts(runtime) == []


def test_corrupt_records_are_rebuilt_from_attempts(tmp_path, capsys):
    runtime, _shot = make_runtime(tmp_path)
    path = store.attempts_path(runtime)
    path.parent.mkdir(parents=True)
    rows = [
        {'schema': 1, 'game': 'hanjuku-speedrun',
         'attempt_id': 'a0001-g1-abc', 'attempt_no': 1,
         'status': 'finished', 'result': 'finished', 'total_ms': 120_000,
         'segments_ms': [10_000] * 12, 'cumulative_ms': [10_000] * 12,
         'merged_ranges': [], 'at_ms': 1},
        {'schema': 1, 'game': 'hanjuku-speedrun',
         'attempt_id': 'a0002-g1-abc', 'attempt_no': 2,
         'status': 'dnf', 'result': 'dnf', 'total_ms': 50_000,
         'segments_ms': [9_000] + [None] * 11,
         'cumulative_ms': [9_000] + [None] * 11,
         'merged_ranges': [], 'at_ms': 2},
        {'schema': 1, 'game': 'hanjuku-speedrun',
         'attempt_id': 'a0003-g1-abc', 'attempt_no': 3,
         'status': 'finished', 'result': 'finished', 'total_ms': 110_000,
         'segments_ms': [11_000] + [None] * 11,
         'cumulative_ms': [11_000] + [None] * 11,
         'merged_ranges': [], 'at_ms': 3},
    ]
    path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n',
                    encoding='utf-8')
    store.records_path(runtime).write_text('{broken', encoding='utf-8')

    records = store.load_records(runtime)
    assert records['rebuilt'] is True
    assert records['next_attempt_no'] == 4
    assert records['pb']['attempt_id'] == 'a0003-g1-abc'
    assert records['pb']['total_ms'] == 110_000
    assert records['gold_segments_ms'][0] == 9_000
    assert records['gold_segments'][0]['attempt_id'] == 'a0002-g1-abc'
    assert records['gold_segments_ms'][1] == 10_000
    assert list(store.data_dir(runtime).glob('records.json.corrupt-*'))
    assert 'quarantined' in capsys.readouterr().err
    # The rebuilt document (including its marker) is what a later read sees.
    assert json.loads(store.records_path(runtime).read_text(
        encoding='utf-8'))['rebuilt'] is True
    assert store.load_records(runtime)['rebuilt'] is True


def test_bounded_rows_keeps_the_newest_that_fit(tmp_path, monkeypatch):
    rows = [{'attempt_id': f'a{index:04d}', 'attempt_no': index,
             'status': 'finished', 'pad': 'x' * 50} for index in range(6)]
    monkeypatch.setattr(store, 'MAX_ATTEMPTS_BYTES', 220)
    lines = store._bounded_rows(rows)
    assert 0 < len(lines) < len(rows)
    assert json.loads(lines[-1])['attempt_id'] == 'a0005'
    assert sum(len(line) + 1 for line in lines) <= 220
