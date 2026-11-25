"""Local data directory / environment labels and archive dry-run tests."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from docich.hanjuku_speedrun.rta import archive, hook, manifest, store, timer

BASE_MS = 1_700_000_000_000
RUNTIME_ID = 'g1300-abcdef12'


def _runtime(tmp_path, *, data_dir=None):
    runtime = tmp_path / 'run-local' / 'runtimes' / RUNTIME_ID
    (runtime / 'screenshots').mkdir(parents=True)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.write_bytes(b'frame')
    data = Path(data_dir) if data_dir else (
        tmp_path / 'run-local' / 'speedrun' / 'hanjuku-speedrun')
    data.mkdir(parents=True, exist_ok=True)
    return runtime, shot, data


def _start(runtime, shot, *, observed_at_ms=BASE_MS):
    return hook.observe(
        runtime, [], {},
        {'observed_at_ms': observed_at_ms,
         'hanjuku': {'name_entered': True, 'phase': 'name',
                     'gameplay_seen': False, 'clear_ending_seen': False,
                     'terminal_reason': None}},
        screenshot=shot)


def test_data_dir_env_override_and_default(monkeypatch, tmp_path):
    runtime = tmp_path / 'run-soren-live' / 'runtimes' / RUNTIME_ID
    runtime.mkdir(parents=True)
    monkeypatch.delenv(store.DATA_DIR_ENV, raising=False)
    assert store.data_dir(runtime) == (
        tmp_path / 'run-soren-live' / 'speedrun' / 'hanjuku-speedrun')
    custom = tmp_path / 'custom-speedrun'
    monkeypatch.setenv(store.DATA_DIR_ENV, str(custom))
    assert store.data_dir(runtime) == custom


def test_local_env_is_recorded_in_timer_attempt_and_manifest(monkeypatch, tmp_path):
    runtime, shot, data = _runtime(tmp_path)
    monkeypatch.setenv(store.DATA_DIR_ENV, str(data))
    monkeypatch.setenv(store.ENVIRONMENT_ENV, 'local')

    started = _start(runtime, shot)
    assert started['status'] == 'running'
    assert started['env'] == 'local'
    assert store.timer_path(runtime).parent == data
    persisted = store.load_timer(runtime)
    assert persisted['env'] == 'local'

    stopped = store.finalize_attempt(runtime, terminal_reason='game_over',
                                     now_ms=BASE_MS + 120_000,
                                     reason='operator_stop')
    assert stopped['status'] == 'dnf'
    rows = store.load_attempts_in(data)
    assert len(rows) == 1
    assert rows[0]['env'] == 'local'
    assert rows[0]['dnf_reason'] == 'game_over'
    assert rows[0]['attempt_id'] == started['attempt_id']

    document = manifest.build_manifest_at(data, started['attempt_id'])
    assert document['env'] == 'local'
    assert (data / 'attempts' / started['attempt_id'] / 'manifest.json').is_file()


def test_production_default_label(monkeypatch, tmp_path):
    runtime, shot, data = _runtime(tmp_path)
    monkeypatch.delenv(store.DATA_DIR_ENV, raising=False)
    monkeypatch.delenv(store.ENVIRONMENT_ENV, raising=False)
    started = _start(runtime, shot)
    assert started['env'] == 'soren'
    # Without the env override the production path is used again.
    assert store.timer_path(runtime).parent == (
        tmp_path / 'run-local' / 'speedrun' / 'hanjuku-speedrun')


def test_observed_at_ms_wins_over_the_screenshot_mtime(monkeypatch, tmp_path):
    runtime, shot, data = _runtime(tmp_path)
    monkeypatch.setenv(store.DATA_DIR_ENV, str(data))
    monkeypatch.setenv(store.ENVIRONMENT_ENV, 'local')
    os.utime(shot, (BASE_MS / 1000 + 500, BASE_MS / 1000 + 500))
    frame_ms = BASE_MS - 60_000
    started = _start(runtime, shot, observed_at_ms=frame_ms)
    assert started['started_at_ms'] == frame_ms
    assert started['heartbeat_at_ms'] == frame_ms


def _attempt_tree(data, attempt_id, *, with_manifest=True):
    attempt = data / 'attempts' / attempt_id
    spool = attempt / 'spool'
    (spool / 'frames').mkdir(parents=True)
    (spool / 'decisions.previous.jsonl.gz').write_bytes(b'log')
    (spool / 'frames' / 'frame-001.png').write_bytes(b'png')
    (attempt / 'segments.json').write_text('{"segments":[]}', encoding='utf-8')
    if with_manifest:
        (attempt / 'manifest.json').write_text('{"attempt_id":"%s"}' % attempt_id,
                                               encoding='utf-8')
    (data / 'attempts.jsonl').write_text(json.dumps({
        'schema': 1, 'game': 'hanjuku-speedrun', 'runtime_id': RUNTIME_ID,
        'attempt_id': attempt_id, 'attempt_no': 1, 'status': 'finished',
        'result': 'finished', 'started_at_ms': 1, 'ended_at_ms': 2,
        'env': 'local', 'archived': False,
    }) + '\n', encoding='utf-8')
    return attempt, spool


def test_archive_dry_run_writes_plan_and_sends_nothing(monkeypatch, tmp_path,
                                                      capsys):
    data = tmp_path / 'speedrun-data'
    data.mkdir()
    attempt_id = f'a0001-{RUNTIME_ID}'
    attempt, spool = _attempt_tree(data, attempt_id)
    monkeypatch.setenv(store.DATA_DIR_ENV, str(data))
    monkeypatch.setenv(store.ENVIRONMENT_ENV, 'local')
    monkeypatch.delenv(archive.ENV_FILE_ENV, raising=False)
    before = {path: path.read_bytes() for path in data.rglob('*')
              if path.is_file()}

    document = archive.plan_attempt(data, attempt_id)
    plan_path = attempt / 'archive_plan.json'
    assert plan_path.is_file()
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    assert plan == document
    assert plan['dry_run'] is True
    assert plan['env'] == 'local'
    keys = {item['key'] for item in plan['files']}
    assert f'hanjuku-speedrun/attempts/0001-{attempt_id}/spool/decisions.previous.jsonl.gz' in keys
    assert f'hanjuku-speedrun/attempts/0001-{attempt_id}/spool/frames.tar' in keys
    assert f'hanjuku-speedrun/attempts/0001-{attempt_id}/segments.json' in keys
    assert f'hanjuku-speedrun/attempts/0001-{attempt_id}/manifest.json' in keys
    assert plan['keep'] == []
    # Nothing was deleted or changed except the new plan file.
    for path, content in before.items():
        assert path.read_bytes() == content
    assert (spool / 'decisions.previous.jsonl.gz').read_bytes() == b'log'
    assert (spool / 'frames' / 'frame-001.png').read_bytes() == b'png'

    code = archive.main(['--data-dir', str(data), 'upload', '--attempt',
                         attempt_id, '--dry-run'])
    assert code == 0
    captured = capsys.readouterr()
    assert 'archive: dry_run' in captured.out
    assert captured.err == ''


def test_archive_dry_run_without_manifest_lists_generated_key(tmp_path):
    data = tmp_path / 'speedrun-data'
    data.mkdir()
    attempt_id = f'a0001-{RUNTIME_ID}'
    attempt, _spool = _attempt_tree(data, attempt_id, with_manifest=False)
    plan = archive.plan_attempt(data, attempt_id)
    manifest_entry = [item for item in plan['files']
                      if item['kind'] == 'manifest'][0]
    assert manifest_entry['bytes'] is None
    assert manifest_entry['generated_at_upload'] is True
    assert 'manifest_will_be_generated' in plan['notes']
    assert not (attempt / 'manifest.json').exists()
    assert (attempt / 'archive_plan.json').is_file()


def test_archive_dry_run_rejects_pending(tmp_path, capsys):
    data = tmp_path / 'speedrun-data'
    data.mkdir()
    code = archive.main(['--data-dir', str(data), 'upload', '--pending',
                         '--dry-run'])
    assert code == 2
    assert 'invalid_arguments' in capsys.readouterr().err


def test_archive_dry_run_missing_attempt_is_a_fixed_code(tmp_path, capsys):
    data = tmp_path / 'speedrun-data'
    data.mkdir()
    code = archive.main(['--data-dir', str(data), 'upload', '--attempt',
                         f'a0001-{RUNTIME_ID}', '--dry-run'])
    assert code == 1
    assert 'archive: missing_attempt' in capsys.readouterr().out
