"""Cross-process serialisation of ``attempts.jsonl`` (PR4b review MED-3).

The bot and the detached archiver both rewrite the bounded history.  These
tests use real processes and a real ``fcntl.flock``; they never touch the
network.
"""
from __future__ import annotations

import fcntl
import multiprocessing
import os
from pathlib import Path
import time

import pytest

from docich.hanjuku_speedrun.rta import store


def _append_worker(runtime_value, prefix, count):
    runtime = Path(runtime_value)
    for index in range(count):
        store.append_attempt(runtime, {
            'schema': 1,
            'game': store.GAME,
            'runtime_id': runtime.name,
            'attempt_id': f'{prefix}-{index:03d}',
            'attempt_no': index,
            'status': 'finished',
            'result': 'finished',
        })


def _context():
    try:
        return multiprocessing.get_context('fork')
    except ValueError:  # pragma: no cover - non-POSIX
        return None


def test_two_processes_appending_interleaved_rows_lose_nothing(tmp_path):
    context = _context()
    if context is None:  # pragma: no cover
        pytest.skip('needs fork')
    runtime = tmp_path / 'run-soren-live' / 'runtimes' / 'g1-abc123'
    runtime.mkdir(parents=True)
    workers = [context.Process(target=_append_worker,
                               args=(str(runtime), f'p{index}', 25))
               for index in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=120)
        assert worker.exitcode == 0

    rows = store.load_attempts(runtime)
    ids = {row.get('attempt_id') for row in rows}
    expected = {f'p{index}-{number:03d}'
                for index in range(3) for number in range(25)}
    assert expected <= ids
    assert len(rows) == len(expected)


def test_a_held_lock_skips_the_update_without_raising_or_blocking(
        tmp_path, monkeypatch):
    runtime = tmp_path / 'run-soren-live' / 'runtimes' / 'g1-abc123'
    runtime.mkdir(parents=True)
    data = store.data_dir(runtime)
    data.mkdir(parents=True, exist_ok=True)
    lock_path = data / store.ATTEMPTS_LOCK_FILE
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(store, 'LOCK_WAIT_SECONDS', 0.1)
    try:
        started = time.monotonic()
        appended = store.append_attempt(runtime, {
            'schema': 1, 'game': store.GAME, 'attempt_id': 'a0001-g1-abc',
            'attempt_no': 1, 'status': 'finished'})
        elapsed = time.monotonic() - started
        updated = store.update_attempt_in(
            data, 'a0001-g1-abc', {'archived': True})
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert appended is False
    assert updated is False
    # The bounded wait is short; the bot's observation loop is not stalled
    # for the full five seconds when another process owns the file.
    assert elapsed < 2.0
    assert store.load_attempts(runtime) == []
