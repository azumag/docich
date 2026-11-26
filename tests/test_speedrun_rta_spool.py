"""Spool collection, compression and cap tests for the speedrun RTA logs."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import pytest

from docich import game_switch
from docich.hanjuku_speedrun.rta import hook, manifest, report_segments, spool, store, timer

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


@pytest.fixture
def no_real_compression(monkeypatch):
    """Keep gzip processes out of the spool tests unless one asks for them."""
    calls = []

    def fake(path):
        calls.append(Path(path))
        return True

    monkeypatch.setattr(spool, 'compress_detached', fake)
    return calls


def test_before_rotate_moves_the_previous_log_and_numbers_it(tmp_path, no_real_compression):
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    previous = runtime / 'hanjuku_decisions.previous.jsonl'
    previous.write_text('{"old":1}\n', encoding='utf-8')
    assert spool.before_rotate(runtime, 'hanjuku_decisions', previous) is True
    assert not previous.exists()
    target = spool.spool_dir(runtime, 'a0001-' + runtime.name) / 'decisions-0001.jsonl'
    assert target.read_text(encoding='utf-8') == '{"old":1}\n'

    previous.write_text('{"old":2}\n', encoding='utf-8')
    assert spool.before_rotate(runtime, 'hanjuku_decisions', previous) is True
    target2 = target.with_name('decisions-0002.jsonl')
    assert target2.read_text(encoding='utf-8') == '{"old":2}\n'


def test_before_rotate_accepts_a_minimal_running_timer(tmp_path, no_real_compression):
    runtime = make_runtime(tmp_path)
    timer_path = store.timer_path(runtime)
    timer_path.parent.mkdir(parents=True)
    timer_path.write_text(json.dumps({
        'status': 'running', 'runtime_id': runtime.name,
        'attempt_id': 'a0001-' + runtime.name,
    }), encoding='utf-8')
    previous = runtime / 'hanjuku_events.previous.jsonl'
    previous.write_text('old\n', encoding='utf-8')
    assert spool.before_rotate(runtime, 'hanjuku_events', previous) is True
    target = spool.spool_dir(runtime, 'a0001-' + runtime.name) / 'events-0001.jsonl'
    assert target.read_text(encoding='utf-8') == 'old\n'


def test_before_rotate_does_nothing_outside_a_running_attempt(tmp_path, no_real_compression):
    runtime = make_runtime(tmp_path)
    previous = runtime / 'hanjuku_events.previous.jsonl'
    previous.write_text('old\n', encoding='utf-8')
    # No timer at all.
    assert spool.before_rotate(runtime, 'hanjuku_events', previous) is False
    assert previous.exists()
    # Pending attempt.
    records = timer.default_records()
    store.save_timer(runtime, timer.new_timer(runtime.name, records, BASE_MS))
    assert spool.before_rotate(runtime, 'hanjuku_events', previous) is False
    assert previous.exists()
    # A running attempt of another runtime generation.
    other = runtime.parent / 'g2-def456'
    other.mkdir()
    start_running(other)
    assert spool.before_rotate(runtime, 'hanjuku_events', previous) is False
    assert previous.exists()
    # A missing previous file is not an error.
    assert spool.before_rotate(other, 'hanjuku_events',
                               other / 'hanjuku_events.previous.jsonl') is False


def test_before_rotate_does_not_follow_a_symlinked_previous(tmp_path, no_real_compression):
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    outside = tmp_path / 'outside.jsonl'
    outside.write_text('do not touch\n', encoding='utf-8')
    previous = runtime / 'hanjuku_decisions.previous.jsonl'
    previous.symlink_to(outside)
    assert spool.before_rotate(runtime, 'hanjuku_decisions', previous) is False
    assert previous.is_symlink()
    assert outside.read_text(encoding='utf-8') == 'do not touch\n'
    assert not spool.spool_dir(runtime, 'a0001-' + runtime.name).exists()


def test_attempt_and_spool_paths_reject_traversal(tmp_path):
    runtime = make_runtime(tmp_path)
    for bad in ('..', '../x', '/abs', 'a/b', '', 'a\\b'):
        with pytest.raises(spool.SpoolError):
            spool.attempt_dir(runtime, bad)


def test_compress_detached_is_a_noop_without_nice_or_gzip(tmp_path, monkeypatch):
    path = tmp_path / 'decisions-0001.jsonl'
    path.write_text('{}\n', encoding='utf-8')
    monkeypatch.setattr(spool.shutil, 'which', lambda name: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('must not start a process')

    monkeypatch.setattr(spool.subprocess, 'Popen', forbidden)
    assert spool.compress_detached(path) is False
    assert path.exists()
    assert not path.with_name(path.name + '.gz').exists()


def test_compress_detached_skips_a_live_lock(tmp_path, monkeypatch):
    path = tmp_path / 'decisions-0001.jsonl'
    path.write_text('{}\n', encoding='utf-8')
    lock = path.with_name(path.name + spool.GZIP_LOCK_SUFFIX)
    lock.write_text(str(os.getpid()), encoding='utf-8')
    monkeypatch.setattr(spool.shutil, 'which', lambda name: '/usr/bin/' + name)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('must not start a second gzip')

    monkeypatch.setattr(spool.subprocess, 'Popen', forbidden)
    assert spool.compress_detached(path) is False
    assert path.exists()


@pytest.mark.skipif(shutil.which('gzip') is None,
                    reason='gzip is not installed; the detached compression cannot be exercised')
def test_compress_detached_starts_gzip_without_waiting(tmp_path):
    path = tmp_path / 'decisions-0001.jsonl'
    path.write_text('{"a":1}\n', encoding='utf-8')
    started = time.monotonic()
    assert spool.compress_detached(path) is True
    assert time.monotonic() - started < 5
    compressed = path.with_name(path.name + '.gz')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not compressed.exists():
        time.sleep(0.05)
    assert compressed.exists()
    assert gzip.decompress(compressed.read_bytes()) == b'{"a":1}\n'
    assert not path.exists()


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def test_enforce_cap_drops_periodic_images_first(tmp_path):
    runtime = make_runtime(tmp_path)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    frame = directory / 'frames' / 'frame-00001.png'
    trace = directory / 'trace-0001.jsonl'
    decisions = directory / 'decisions-0001.jsonl'
    sizes = (_write(frame, b'f' * 100) + _write(trace, b't' * 100)
             + _write(decisions, b'd' * 100))
    dropped = spool.enforce_cap(runtime, attempt_id, max_bytes_value=sizes - 1)
    assert dropped == 100
    assert not frame.exists()
    assert trace.exists() and decisions.exists()
    document = json.loads(spool.dropped_path(runtime, attempt_id).read_text())
    assert document['types']['frames'] == {'files': 1, 'bytes': 100}
    assert document['over_cap'] is False


def test_enforce_cap_drops_trace_then_the_oldest_log(tmp_path):
    runtime = make_runtime(tmp_path)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    trace = directory / 'trace-0001.jsonl'
    decisions = directory / 'decisions-0001.jsonl'
    events = directory / 'events-0001.jsonl'
    _write(trace, b't' * 100)
    _write(decisions, b'd' * 100)
    _write(events, b'e' * 100)
    # The event log is the oldest; the decisions log must survive this pass.
    os.utime(events, (1_600_000_000, 1_600_000_000))
    os.utime(decisions, (1_700_000_000, 1_700_000_000))
    spool.enforce_cap(runtime, attempt_id, max_bytes_value=150)
    assert not trace.exists()
    assert not events.exists()
    assert decisions.exists()
    document = json.loads(spool.dropped_path(runtime, attempt_id).read_text())
    assert document['types']['trace']['files'] == 1
    assert document['types']['logs']['files'] == 1


def test_enforce_cap_orders_logs_across_types_by_mtime_and_numeric_sequence(tmp_path):
    runtime = make_runtime(tmp_path)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    oldest = directory / 'events-0001.jsonl'
    lower = directory / 'decisions-0009.jsonl'
    higher = directory / 'decisions-00010.jsonl'
    _write(oldest, b'e' * 100)
    _write(lower, b'l' * 100)
    _write(higher, b'h' * 100)
    os.utime(oldest, (1_600_000_000, 1_600_000_000))
    # 0009 and 00010 share an mtime: the numeric sequence decides, not the
    # lexicographic order (which would put 00010 before 0009).
    os.utime(lower, (1_700_000_000, 1_700_000_000))
    os.utime(higher, (1_700_000_000, 1_700_000_000))
    spool.enforce_cap(runtime, attempt_id, max_bytes_value=100)
    assert not oldest.exists()
    assert not lower.exists()
    assert higher.exists()
    document = json.loads(spool.dropped_path(runtime, attempt_id).read_text())
    assert document['max_sequence']['decisions'] == 9
    assert document['max_sequence']['events'] == 1


def test_enforce_cap_drops_unclassified_leftovers(tmp_path):
    runtime = make_runtime(tmp_path)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    unknown = directory / 'notes.txt'
    malformed = directory / 'decisions-abc.jsonl'
    active = directory / 'trace.jsonl'
    _write(unknown, b'n' * 100)
    _write(malformed, b'm' * 100)
    _write(active, b'a' * 100)
    spool.enforce_cap(runtime, attempt_id, max_bytes_value=100)
    assert not unknown.exists()
    assert not malformed.exists()
    # The active trace is owned by the spool and is never part of the
    # unclassified fallback.
    assert active.exists()


def test_enforce_cap_skips_a_log_whose_gzip_is_still_running(tmp_path):
    runtime = make_runtime(tmp_path)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    source = directory / 'decisions-0001.jsonl'
    _write(source, b'd' * 100)
    lock = source.with_name(source.name + spool.GZIP_LOCK_SUFFIX)
    lock.write_text('4242', encoding='ascii')
    spool.enforce_cap(runtime, attempt_id, max_bytes_value=1)
    assert source.exists()
    assert lock.exists()
    document = json.loads(spool.dropped_path(runtime, attempt_id).read_text())
    assert document['types']['logs'] == {'files': 0, 'bytes': 0}


def test_compress_detached_records_the_child_pid_in_the_lock(tmp_path, monkeypatch):
    path = tmp_path / 'decisions-0001.jsonl'
    path.write_text('{}\n', encoding='utf-8')
    monkeypatch.setattr(spool.shutil, 'which', lambda name: '/usr/bin/' + name)

    class FakeProcess:
        pid = 4321

    monkeypatch.setattr(spool.subprocess, 'Popen', lambda *a, **k: FakeProcess())
    assert spool.compress_detached(path) is True
    lock = path.with_name(path.name + spool.GZIP_LOCK_SUFFIX)
    assert lock.read_text(encoding='ascii') == '4321'


def test_compress_pending_removes_a_stale_lock_of_a_finished_compression(tmp_path, no_real_compression):
    runtime = make_runtime(tmp_path)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    directory.mkdir(parents=True)
    source = directory / 'decisions-0001.jsonl'
    source.with_name(source.name + '.gz').write_bytes(b'compressed')
    lock = source.with_name(source.name + spool.GZIP_LOCK_SUFFIX)
    lock.write_text('999999', encoding='ascii')
    spool.compress_pending(runtime, attempt_id)
    assert not lock.exists()


def test_enforce_cap_removes_a_stale_lock_of_a_finished_compression(tmp_path):
    runtime = make_runtime(tmp_path)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    directory.mkdir(parents=True)
    source = directory / 'decisions-0001.jsonl'
    source.with_name(source.name + '.gz').write_bytes(b'compressed')
    lock = source.with_name(source.name + spool.GZIP_LOCK_SUFFIX)
    lock.write_text('999999', encoding='ascii')
    spool.enforce_cap(runtime, attempt_id, max_bytes_value=1)
    assert not lock.exists()


def test_sequence_continues_after_every_log_was_dropped(tmp_path, no_real_compression):
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    dropped = directory / 'decisions-0007.jsonl'
    _write(dropped, b'd' * 100)
    spool.enforce_cap(runtime, attempt_id, max_bytes_value=1)
    assert not dropped.exists()
    document = json.loads(spool.dropped_path(runtime, attempt_id).read_text())
    assert document['max_sequence']['decisions'] == 7
    previous = runtime / 'hanjuku_decisions.previous.jsonl'
    previous.write_text('{"next":1}\n', encoding='utf-8')
    assert spool.before_rotate(runtime, 'hanjuku_decisions', previous) is True
    assert (directory / 'decisions-0008.jsonl').exists()
    assert not (directory / 'decisions-0001.jsonl').exists()


def test_before_rotate_ignores_other_rotated_logs(tmp_path, no_real_compression):
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    previous = runtime / 'hanjuku_narration.previous.jsonl'
    previous.write_text('narration\n', encoding='utf-8')
    assert spool.before_rotate(runtime, 'hanjuku_narration', previous) is False
    # The caller keeps its original overwrite behaviour: the file itself is
    # not moved into a spool that does not exist yet.
    assert previous.exists()
    assert not spool.spool_dir(runtime, 'a0001-' + runtime.name).exists()


def test_ensure_dir_rejects_a_symlinked_attempts_directory(tmp_path):
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    data = store.data_dir(runtime)
    data.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (data / 'attempts').symlink_to(outside, target_is_directory=True)
    previous = runtime / 'hanjuku_decisions.previous.jsonl'
    previous.write_text('{"old":1}\n', encoding='utf-8')
    assert spool.before_rotate(runtime, 'hanjuku_decisions', previous) is False
    assert previous.exists()
    assert list(outside.iterdir()) == []


def test_ensure_dir_rejects_a_symlinked_attempt_directory(tmp_path):
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    attempt_id = 'a0001-' + runtime.name
    data = store.data_dir(runtime)
    attempts = data / 'attempts'
    attempts.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (attempts / attempt_id).symlink_to(outside, target_is_directory=True)
    with pytest.raises(spool.SpoolError):
        spool._ensure_dir(spool.spool_dir(runtime, attempt_id))
    assert list(outside.iterdir()) == []


def test_teardown_warns_when_spool_finalize_returns_false(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    (runtime / 'hanjuku_decisions.jsonl').write_text('{"e":1}\n', encoding='utf-8')
    monkeypatch.setattr(spool, 'finalize_logs', lambda *_args, **_kwargs: False)
    entry = {'game': 'hanjuku-speedrun', 'runtime_id': runtime.name,
             'generation': 1, 'adapter': 'retroarch'}
    warnings = []
    assert game_switch.finalize_speedrun_attempts(
        tmp_path / 'run-soren-live', [entry], warnings) == 1
    assert any('spool finalize returned False' in warning for warning in warnings)


def test_teardown_collects_ended_attempts_without_a_manifest(tmp_path, monkeypatch, no_real_compression):
    monkeypatch.setattr(manifest, '_docich_commit', lambda _root: None)
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    old_id = 'a0000-old'
    store.append_attempt(runtime, {
        'schema': 1, 'game': 'hanjuku-speedrun', 'runtime_id': runtime.name,
        'attempt_id': old_id, 'attempt_no': 0, 'status': 'dnf',
        'result': 'dnf', 'started_at_ms': BASE_MS - 1000,
        'ended_at_ms': BASE_MS - 500, 'total_ms': 500,
    })
    other_id = 'a0001-other'
    store.append_attempt(runtime, {
        'schema': 1, 'game': 'hanjuku-speedrun', 'runtime_id': 'g2-other123',
        'attempt_id': other_id, 'attempt_no': 1, 'status': 'dnf',
        'result': 'dnf', 'started_at_ms': BASE_MS - 1000,
        'ended_at_ms': BASE_MS - 500, 'total_ms': 500,
    })
    entry = {'game': 'hanjuku-speedrun', 'runtime_id': runtime.name,
             'generation': 1, 'adapter': 'retroarch'}
    warnings = []
    assert game_switch.finalize_speedrun_attempts(
        tmp_path / 'run-soren-live', [entry], warnings) == 1
    assert warnings == []
    assert (spool.attempt_dir(runtime, old_id) / 'manifest.json').exists()
    assert not (spool.attempt_dir(runtime, other_id) / 'manifest.json').exists()



def test_enforce_cap_keeps_the_active_trace_and_reports_over_cap(tmp_path):
    runtime = make_runtime(tmp_path)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    active = directory / 'trace.jsonl'
    _write(active, b'x' * 500)
    assert spool.enforce_cap(runtime, attempt_id, max_bytes_value=10) == 0
    assert active.exists()
    document = json.loads(spool.dropped_path(runtime, attempt_id).read_text())
    assert document['over_cap'] is True
    assert document['types']['frames'] == {'files': 0, 'bytes': 0}


def test_enforce_cap_deletes_nothing_below_the_bound(tmp_path):
    runtime = make_runtime(tmp_path)
    attempt_id = 'a0001-' + runtime.name
    directory = spool.spool_dir(runtime, attempt_id)
    frame = directory / 'frames' / 'frame-00001.png'
    _write(frame, b'f' * 50)
    assert spool.enforce_cap(runtime, attempt_id, max_bytes_value=1000) == 0
    assert frame.exists()
    assert not spool.dropped_path(runtime, attempt_id).exists()


def test_finalize_logs_collects_logs_frames_and_writes_summary(tmp_path, monkeypatch, no_real_compression):
    monkeypatch.setattr(manifest, '_docich_commit', lambda _root: None)
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    attempt_id = 'a0001-' + runtime.name
    decisions = runtime / 'hanjuku_decisions.jsonl'
    decisions.write_text('{"event":"decision"}\n', encoding='utf-8')
    previous = runtime / 'hanjuku_events.previous.jsonl'
    previous.write_text('{"event":"old"}\n', encoding='utf-8')
    ring = runtime / 'hanjuku_frames'
    ring.mkdir()
    (ring / 'frame-000.png').write_bytes(b'png')
    (ring / 'frame-001.png').write_bytes(b'png2')

    assert spool.finalize_logs(runtime, attempt_id) is True
    directory = spool.spool_dir(runtime, attempt_id)
    assert not decisions.exists()
    assert not previous.exists()
    assert (directory / 'decisions-0001.jsonl').exists()
    assert (directory / 'events-0001.jsonl').exists()
    assert (directory / 'frames' / 'ring-frame-000.png').exists()
    segments = json.loads((spool.attempt_dir(runtime, attempt_id) / 'segments.json').read_text())
    assert segments['attempt_id'] == attempt_id
    assert segments['segments'] == []
    manifest_doc = json.loads((spool.attempt_dir(runtime, attempt_id) / 'manifest.json').read_text())
    assert manifest_doc['archived'] is False
    assert manifest_doc['attempt_id'] == attempt_id
    assert manifest_doc['status'] == 'running'
    assert manifest_doc['spool_bytes_at_build'] > 0
    assert any(item['path'].endswith('decisions-0001.jsonl')
               for item in manifest_doc['spool_files_at_build'])
    assert manifest_doc['inventory'] == 'pre-compression snapshot'
    config = Path(__file__).resolve().parents[1] / 'config' / 'games' / 'hanjuku-speedrun.toml'
    assert manifest_doc['config_sha256'] == hashlib.sha256(config.read_bytes()).hexdigest()


def test_finalize_logs_never_raises(tmp_path, monkeypatch, no_real_compression):
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    attempt_id = 'a0001-' + runtime.name

    def fail(*_args, **_kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(report_segments, 'build_segments', fail)
    assert spool.finalize_logs(runtime, attempt_id) is False
    assert spool.finalize_logs(runtime, '../bad') is False


def test_generated_append_log_rotates_into_the_spool(tmp_path, monkeypatch, no_real_compression):
    from docich.hanjuku_speedrun import hanjuku_run as generated_run

    runtime = make_runtime(tmp_path)
    start_running(runtime)
    attempt_id = 'a0001-' + runtime.name
    monkeypatch.setattr(generated_run, 'MAX_LOG_BYTES', 5)
    generated_run.append_log(runtime, 'hanjuku_decisions', {'n': 1})
    generated_run.append_log(runtime, 'hanjuku_decisions', {'n': 2})
    generated_run.append_log(runtime, 'hanjuku_decisions', {'n': 3})
    target = spool.spool_dir(runtime, attempt_id) / 'decisions-0001.jsonl'
    assert target.exists()
    assert json.loads(target.read_text(encoding='utf-8')) == {'n': 1}
    assert json.loads((runtime / 'hanjuku_decisions.previous.jsonl').read_text()) == {'n': 2}
    assert json.loads((runtime / 'hanjuku_decisions.jsonl').read_text()) == {'n': 3}


def test_generated_append_log_keeps_other_rotated_logs_in_the_runtime(tmp_path, monkeypatch, no_real_compression):
    from docich.hanjuku_speedrun import hanjuku_run as generated_run

    runtime = make_runtime(tmp_path)
    start_running(runtime)
    attempt_id = 'a0001-' + runtime.name
    monkeypatch.setattr(generated_run, 'MAX_LOG_BYTES', 5)
    generated_run.append_log(runtime, 'hanjuku_narration', {'n': 1})
    generated_run.append_log(runtime, 'hanjuku_narration', {'n': 2})
    # The original overwrite behaviour is kept: the older generation is in
    # the runtime's .previous file, not in the attempt spool.
    assert json.loads((runtime / 'hanjuku_narration.previous.jsonl').read_text()) == {'n': 1}
    assert json.loads((runtime / 'hanjuku_narration.jsonl').read_text()) == {'n': 2}
    directory = spool.spool_dir(runtime, attempt_id)
    assert not directory.exists() or not list(directory.glob('narration-*'))


def test_original_append_log_has_no_rta_call():
    source = Path(__file__).resolve().parents[1] / 'src' / 'docich' / 'hanjuku_run.py'
    text = source.read_text(encoding='utf-8')
    assert 'before_rotate' not in text
    assert 'hanjuku_speedrun.rta' not in text


def test_teardown_does_not_collect_into_another_runtimes_attempt(tmp_path, monkeypatch, no_real_compression):
    monkeypatch.setattr(manifest, '_docich_commit', lambda _root: None)
    state_dir = tmp_path / 'run-soren-live'
    first = make_runtime(tmp_path)
    start_running(first)
    second = first.parent / 'g2-def456'
    second.mkdir()
    log = second / 'hanjuku_decisions.jsonl'
    log.write_text('{"e":2}\n', encoding='utf-8')
    warnings = []
    entry = {'game': 'hanjuku-speedrun', 'runtime_id': second.name,
             'generation': 2, 'adapter': 'retroarch'}
    game_switch.finalize_speedrun_attempts(state_dir, [entry], warnings)
    # The shared timer still names the first runtime, so the second runtime's
    # log must not be moved into the first attempt's spool.
    assert log.exists()
    assert not spool.spool_dir(first, 'a0001-' + first.name).exists()


def test_common_teardown_finalizes_the_spool(tmp_path, monkeypatch, no_real_compression):
    monkeypatch.setattr(manifest, '_docich_commit', lambda _root: None)
    state_dir = tmp_path / 'run-soren-live'
    runtime = make_runtime(tmp_path)
    start_running(runtime)
    (runtime / 'hanjuku_decisions.jsonl').write_text('{"e":1}\n', encoding='utf-8')
    entry = {'game': 'hanjuku-speedrun', 'runtime_id': runtime.name,
             'generation': 1, 'adapter': 'retroarch'}
    warnings = []
    assert game_switch.finalize_speedrun_attempts(state_dir, [entry], warnings) == 1
    assert warnings == []
    attempt_id = 'a0001-' + runtime.name
    assert store.load_timer(runtime)['status'] == 'dnf'
    assert (spool.attempt_dir(runtime, attempt_id) / 'manifest.json').exists()
    assert (spool.spool_dir(runtime, attempt_id) / 'decisions-0001.jsonl').exists()
