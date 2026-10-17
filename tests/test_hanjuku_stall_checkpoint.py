"""A natural stasis stop is never acknowledged without its durable checkpoint."""
import os
import time
from unittest.mock import Mock

import pytest
from test_retroarch_safe_boundary import adapter
from test_hanjuku_saved_stop import emulator
from docich import game_switch, hanjuku_run
from docich.adapters.base import AdapterError
from docich.adapters.retroarch import ManualSaveTimeout
from docich.retroarch_boundary import (BOUNDARY_FILE, MANUAL_SAVE_FILE, identity,
                                      read_record, require_input_open)


def terminal(adapter, reason='screen_stalled'):
    adapter.game.raw['hanjuku'] = {'script_bot': True}
    game_switch.atomic_write_json(adapter.spec.runtime_dir / hanjuku_run.RUN_FILE, {
        **hanjuku_run.runtime_identity(adapter.spec), 'schema': 1,
        'terminal_reason': reason, 'frame_sha256': 'a' * 64,
        'unchanged_seconds': 300, 'observed_monotonic': 1000,
        'last_input_monotonic': 1, 'observations': 1000,
        'last_input_observations': 0, 'name_entered': True,
        'gameplay_seen': True, 'phase': 'title', 'title_count': 3, 'title_since': 0,
    })


def setup(adapter, monkeypatch, **kwargs):
    calls = emulator(adapter, monkeypatch, **kwargs)
    (adapter.spec.runtime_dir / MANUAL_SAVE_FILE).unlink()
    terminal(adapter)
    return calls


@pytest.mark.parametrize('reason', ['screen_stalled', 'input_stalled'])
@pytest.mark.parametrize('paused', [False, True])
def test_stasis_saves_before_boundary_and_does_not_need_manual_marker(adapter, monkeypatch, reason, paused):
    calls = setup(adapter, monkeypatch, paused=paused)
    terminal(adapter, reason)
    adapter.request_round_boundary('natural-stop', time.monotonic() + 2, None)
    r = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    assert r['status'] == 'reached' and r['outcome'] == reason
    assert r['checkpoint'] == 'hanjuku.state' and len(r['sha256']) == 64
    assert r['frame_sha256'] == 'a' * 64
    assert calls.count('SAVE_STATE') == 1
    assert calls.count('PAUSE_TOGGLE') == (0 if paused else 1)
    with pytest.raises(AdapterError, match='holds input'):
        require_input_open(adapter.spec.runtime_dir)
    adapter.request_round_boundary('natural-stop', time.monotonic() + 1, None)
    assert calls.count('SAVE_STATE') == 1
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)


@pytest.mark.parametrize('paused', [False, True])
@pytest.mark.parametrize('bad', ['missing', 'empty', 'old'])
def test_save_failure_never_acknowledges_or_forces_natural_stop(adapter, monkeypatch, paused, bad):
    calls = setup(adapter, monkeypatch, paused=paused, save=False)
    path = adapter.spec.runtime_dir / 'states/hanjuku.state'
    if bad != 'missing':
        path.write_bytes(b'' if bad == 'empty' else b'old checkpoint')
        if bad == 'old': os.utime(path, ns=(1, 1))
    # A separate operator request's force policy must never authorize this stop.
    game_switch.atomic_write_json(adapter.spec.runtime_dir / MANUAL_SAVE_FILE,
        dict(identity(adapter.spec, 'different-request'), allow_unsaved_stop=True))
    monkeypatch.setattr('docich.adapters.retroarch.MANUAL_SAVE_TIMEOUT_S', .05)
    with pytest.raises(ManualSaveTimeout):
        adapter.request_round_boundary('natural-stop', time.monotonic() + 1, None)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'
    assert calls.count('PAUSE_TOGGLE') == (0 if paused else 2)
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)
    require_input_open(adapter.spec.runtime_dir)


def test_game_over_still_ends_without_saving_or_loading(adapter, monkeypatch):
    calls = setup(adapter, monkeypatch)
    terminal(adapter, 'game_over')
    adapter.request_round_boundary('game-over', time.monotonic() + 1, None)
    r = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    assert r['status'] == 'reached' and r['outcome'] == 'game_over'
    assert 'checkpoint' not in r and 'sha256' not in r
    assert not any(c in calls for c in ['SAVE_STATE', 'LOAD_STATE', 'PAUSE_TOGGLE'])


@pytest.mark.parametrize('reason', ['screen_stalled', 'input_stalled', 'game_over'])
def test_manual_stop_keeps_detected_terminal_reason_and_its_save_policy(adapter, monkeypatch, reason):
    calls = setup(adapter, monkeypatch)
    terminal(adapter, reason)
    game_switch.atomic_write_json(adapter.spec.runtime_dir / MANUAL_SAVE_FILE,
        dict(identity(adapter.spec, 'manual-terminal-stop'), allow_unsaved_stop=True))
    adapter.request_round_boundary('manual-terminal-stop', time.monotonic() + 2, None)
    receipt = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    assert receipt['outcome'] == reason and receipt['frame_sha256'] == 'a' * 64
    assert calls.count('SAVE_STATE') == (0 if reason == 'game_over' else 1)
    assert ('checkpoint' in receipt) == (reason != 'game_over')


@pytest.mark.parametrize('bad', ['missing', 'changed', 'legacy'])
def test_cleanup_cannot_discard_a_stalled_game_without_intact_checkpoint(adapter, monkeypatch, bad):
    setup(adapter, monkeypatch)
    adapter.request_round_boundary('natural-stop', time.monotonic() + 2, None)
    r = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    path = adapter.spec.runtime_dir / 'states/hanjuku.state'
    if bad == 'missing': path.unlink()
    elif bad == 'changed': path.write_bytes(b'other save')
    else:
        r.pop('checkpoint'); r.pop('sha256')
        game_switch.atomic_write_json(adapter.spec.runtime_dir / BOUNDARY_FILE, r)
    monkeypatch.setattr('docich.agent.fence.read_canonical',
        lambda _: {'active': {'runtime_id': adapter.spec.runtime_id}})
    with pytest.raises(AdapterError):
        adapter.cleanup_runtime(time.monotonic() + 1, None)
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)


def test_save_fsync_failure_does_not_acknowledge_and_undoes_own_pause(adapter, monkeypatch):
    calls = setup(adapter, monkeypatch)
    path = adapter.spec.runtime_dir / 'states/hanjuku.state'
    fsync = os.fsync
    def fail_checkpoint(fd):
        if path.exists() and os.fstat(fd).st_ino == path.stat().st_ino:
            raise OSError('disk error')
        return fsync(fd)
    monkeypatch.setattr('docich.adapters.retroarch.os.fsync', fail_checkpoint)
    with pytest.raises(OSError):
        adapter.request_round_boundary('natural-stop', time.monotonic() + 2, None)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'
    assert calls.count('PAUSE_TOGGLE') == 2
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)


def test_states_directory_fsync_failure_also_prevents_shutdown(adapter, monkeypatch):
    calls = setup(adapter, monkeypatch)
    directory = adapter.spec.runtime_dir / 'states'
    fsync = os.fsync
    def fail_directory(fd):
        if os.fstat(fd).st_ino == directory.stat().st_ino:
            raise OSError('directory flush error')
        return fsync(fd)
    monkeypatch.setattr('docich.adapters.retroarch.os.fsync', fail_directory)
    with pytest.raises(OSError):
        adapter.request_round_boundary('natural-stop', time.monotonic() + 2, None)
    r = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    assert r['status'] == 'waiting' and r['save_error'] == 'save_failed'
    assert calls.count('PAUSE_TOGGLE') == 2
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)


def test_validated_save_allows_cleanup_and_remains_after_game_exit(adapter, monkeypatch):
    setup(adapter, monkeypatch)
    adapter.request_round_boundary('natural-stop', time.monotonic() + 2, None)
    monkeypatch.setattr('docich.agent.fence.read_canonical',
        lambda _: {'active': {'runtime_id': adapter.spec.runtime_id}})
    adapter.cleanup_runtime(time.monotonic() + 1, None)
    assert any(call[0].startswith('kill') for call in adapter.tmux.calls)
    assert (adapter.spec.runtime_dir / 'states/hanjuku.state').read_bytes() == b'completed save'
    r = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    assert r['checkpoint_bytes'] == len(b'completed save')
    assert r['checkpoint_mtime_ns'] == (adapter.spec.runtime_dir / 'states/hanjuku.state').stat().st_mtime_ns
    # Idempotent cleanup verifies the saved bytes after the window is gone.
    adapter._require_safe_cleanup(time.monotonic() + 1, None)


@pytest.mark.parametrize('reason', ['screen_stalled', 'input_stalled'])
def test_unchanged_future_mtime_cannot_acknowledge_or_allow_cleanup(adapter, monkeypatch, reason):
    calls = setup(adapter, monkeypatch, save=False)
    terminal(adapter, reason)
    path = adapter.spec.runtime_dir / 'states/hanjuku.state'
    path.write_bytes(b'old checkpoint from before this request')
    future = time.time_ns() + 60_000_000_000
    os.utime(path, ns=(future, future))
    before = path.stat()
    fingerprint = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    monkeypatch.setattr('docich.adapters.retroarch.MANUAL_SAVE_TIMEOUT_S', .3)
    with pytest.raises(ManualSaveTimeout):
        adapter.request_round_boundary('natural-stop', time.monotonic() + 1, None)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'
    after = path.stat()
    assert fingerprint == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    assert calls.count('SAVE_STATE') == 1 and calls.count('PAUSE_TOGGLE') == 2
    monkeypatch.setattr('docich.agent.fence.read_canonical',
        lambda _: {'active': {'runtime_id': adapter.spec.runtime_id}})
    with pytest.raises(AdapterError):adapter.cleanup_runtime(time.monotonic() + 1, None)
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)
