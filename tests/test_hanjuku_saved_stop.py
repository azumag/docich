"""Operator stop saves first and only an explicit policy permits unsaved exit."""
import time
from unittest.mock import Mock

import pytest

from test_retroarch_safe_boundary import adapter
from test_hanjuku_retro_registration import manager
from docich import game_switch
from docich.adapters.base import AdapterError
from docich.tmux import PaneState
from docich.retroarch_boundary import BOUNDARY_FILE, MANUAL_SAVE_FILE, identity, read_record, require_input_open


def emulator(adapter, monkeypatch, *, paused=False, save=True):
    adapter.game.raw['hanjuku'] = {'script_bot': True}
    game_switch.atomic_write_json(adapter.spec.runtime_dir / MANUAL_SAVE_FILE,
                                 identity(adapter.spec, 'manual-stop'))
    calls = []
    def command(cmd, **kwargs):
        nonlocal paused
        calls.append(cmd)
        if cmd == 'PAUSE_TOGGLE':
            paused = not paused
        if cmd == 'SAVE_STATE' and save:
            (adapter.spec.runtime_dir / 'states/hanjuku.state').write_bytes(b'completed save')
        if cmd == 'GET_STATUS':
            return f"GET_STATUS {'PAUSED' if paused else 'PLAYING'} snes,hanjuku,crc32=abcd"
    monkeypatch.setattr('docich.adapters.retroarch.send_ra_cmd', command)
    return calls


@pytest.mark.parametrize('paused', [False, True])
def test_operator_stop_saves_and_fences_input_without_terminal_evidence(adapter, monkeypatch, paused):
    calls = emulator(adapter, monkeypatch, paused=paused)
    adapter.request_round_boundary('manual-stop', time.monotonic() + 2, None)
    record = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    assert record['outcome'] == 'suspended'
    assert record['checkpoint'] == 'hanjuku.state'
    assert calls.count('PAUSE_TOGGLE') == (0 if paused else 1)
    assert calls.count('SAVE_STATE') == 1
    with pytest.raises(AdapterError, match='holds input'):
        require_input_open(adapter.spec.runtime_dir)
    adapter._verify_manual_save(record, time.monotonic() + 1, None)
    # Replay verifies the existing save rather than overwriting it.
    adapter.request_round_boundary('manual-stop', time.monotonic() + 1, None)
    assert calls.count('SAVE_STATE') == 1
    # Cleanup runs again after the owned game process/window has exited.
    adapter.tmux.pane_states = [PaneState(dead=True, pid=1234)]
    adapter._verify_manual_save(record, time.monotonic() + 1, None, allow_stopped=True)
    adapter.tmux.windows.clear()
    adapter._verify_manual_save(record, time.monotonic() + 1, None, allow_stopped=True)
    (adapter.spec.runtime_dir / 'states/hanjuku.state').write_bytes(b'changed save')
    with pytest.raises(AdapterError, match='changed'):
        adapter._verify_manual_save(record, time.monotonic() + 1, None, allow_stopped=True)


@pytest.mark.parametrize('paused', [False, True])
def test_missing_save_never_acknowledges_and_restores_only_own_pause(adapter, monkeypatch, paused):
    calls = emulator(adapter, monkeypatch, paused=paused, save=False)
    with pytest.raises(game_switch.DeadlineExceededError):
        adapter.request_round_boundary('manual-stop', time.monotonic() + .15, None)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'
    assert calls.count('PAUSE_TOGGLE') == (0 if paused else 2)
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)
    require_input_open(adapter.spec.runtime_dir)


def test_request_scope_does_not_turn_natural_switch_into_saved_stop(adapter, monkeypatch):
    calls = emulator(adapter, monkeypatch)
    with pytest.raises(game_switch.DeadlineExceededError):
        adapter.request_round_boundary('natural-end', time.monotonic() + .02, None)
    assert 'SAVE_STATE' not in calls and 'PAUSE_TOGGLE' not in calls


def test_symlinked_save_is_rejected_before_pause(adapter, monkeypatch):
    calls = emulator(adapter, monkeypatch)
    (adapter.spec.runtime_dir / 'states/hanjuku.state').symlink_to(adapter.game.path)
    with pytest.raises(AdapterError, match='symlink'):
        adapter.request_round_boundary('manual-stop', time.monotonic() + 1, None)
    assert 'PAUSE_TOGGLE' not in calls


@pytest.mark.parametrize('status', ['active', 'failed'])
def test_stop_retries_same_owned_runtime_with_a_fresh_request(manager, monkeypatch, status):
    expected = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef', 'generation': 1, 'lease_id': 'lease'}
    state = {**manager._default_state(), 'status': status, 'game': 'hanjuku-hero', 'previous_game': 'sorengame',
             'bot_identity': expected, 'switch_request_id': 'old-failed-request'}
    manager._write_state(state)
    monkeypatch.setattr(manager.store.canonical, 'load', lambda: ({'phase': 'ready', 'active': expected}, False))
    monkeypatch.setattr(manager, '_finish_locked', lambda state, now: state)
    result = manager._stop_direct()
    assert result['status'] == 'active'
    assert result['switch_request_id'] != 'old-failed-request'
    assert result['manual_stop_requested'] is True
    record = read_record(manager.g.state_dir / 'runtimes/g1-abcdef' / MANUAL_SAVE_FILE)
    assert record['request_id'] == result['switch_request_id']
    assert record['runtime_id'] == expected['runtime_id']
    assert record['allow_unsaved_stop'] is True


def test_failed_stop_cannot_suspend_another_generation(manager, monkeypatch):
    expected = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef', 'generation': 1, 'lease_id': 'lease'}
    manager._write_state({**manager._default_state(), 'status': 'failed', 'game': 'hanjuku-hero', 'bot_identity': expected})
    monkeypatch.setattr(manager.store.canonical, 'load', lambda: ({'phase': 'ready', 'active': {**expected, 'generation': 2}}, False))
    finish = Mock()
    monkeypatch.setattr(manager, '_finish_locked', finish)
    with pytest.raises(Exception, match='identity'):
        manager._stop_direct()
    finish.assert_not_called()
    assert not list(manager.g.state_dir.glob('runtimes/*/' + MANUAL_SAVE_FILE))


@pytest.mark.parametrize('forced', [False, True])
def test_manager_reports_save_outcome_only_after_successful_restore(manager, monkeypatch, forced):
    from types import SimpleNamespace
    expected = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef', 'generation': 1, 'lease_id': 'lease'}
    state = {**manager._default_state(), 'status': 'failed', 'game': 'hanjuku-hero',
             'previous_game': 'sorengame', 'bot_identity': expected}
    manager._write_state(state)
    monkeypatch.setattr(manager.store.canonical, 'load', lambda: ({'phase': 'ready', 'active': expected}, False))
    monkeypatch.setattr(manager, '_active_game_reader', lambda: 'hanjuku-hero')
    def switched(*args, **kwargs):
        game_switch.atomic_write_json(manager.g.state_dir / 'runtimes/g1-abcdef' / BOUNDARY_FILE,
            {**expected, 'schema': 1, 'request_id': kwargs['request_id'], 'status': 'reached',
             'outcome': 'manual_forced_stop' if forced else 'suspended'})
        return SimpleNamespace(status='succeeded')
    manager.coordinator.switch.side_effect = switched
    result = manager._stop_direct()
    assert result.status == 'completed'
    assert manager._read_state()['end_reason'] == ('manual_forced_stop' if forced else 'manual_saved_stop')
    assert manager.coordinator.switch.call_args.kwargs['payload']['expected_source'] == expected


def test_wrong_rom_status_never_saves_or_pauses(adapter, monkeypatch):
    calls = emulator(adapter, monkeypatch)
    command = Mock(return_value='GET_STATUS PLAYING snes,another-game,crc32=abcd')
    monkeypatch.setattr('docich.adapters.retroarch.send_ra_cmd', command)
    with pytest.raises(AdapterError, match='content identity'):
        adapter.request_round_boundary('manual-stop', time.monotonic() + 1, None)
    assert [call.args[0] for call in command.call_args_list] == ['GET_STATUS']


def test_suspended_cleanup_requires_operator_marker(adapter, monkeypatch):
    emulator(adapter, monkeypatch)
    adapter.request_round_boundary('manual-stop', time.monotonic() + 2, None)
    record = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    (adapter.spec.runtime_dir / MANUAL_SAVE_FILE).unlink()
    with pytest.raises(AdapterError, match='request mismatch'):
        adapter._verify_manual_save(record, time.monotonic() + 1, None)


def allow_unsaved(adapter):
    game_switch.atomic_write_json(adapter.spec.runtime_dir / MANUAL_SAVE_FILE,
                                 dict(identity(adapter.spec, 'manual-stop'), allow_unsaved_stop=True))


def test_save_timeout_with_explicit_policy_fences_input_and_allows_owned_cleanup(adapter, monkeypatch):
    calls = emulator(adapter, monkeypatch, save=False)
    allow_unsaved(adapter)
    monkeypatch.setattr('docich.adapters.retroarch.MANUAL_SAVE_TIMEOUT_S', .05)
    adapter.request_round_boundary('manual-stop', time.monotonic() + 2, None)
    record = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    assert record['status'] == 'reached'
    assert record['outcome'] == 'manual_forced_stop'
    assert record['save_error'] == 'save_timeout'
    assert 'checkpoint' not in record and 'sha256' not in record
    with pytest.raises(AdapterError, match='holds input'):
        require_input_open(adapter.spec.runtime_dir)
    adapter.request_round_boundary('manual-stop', time.monotonic() + 1, None)
    assert calls.count('SAVE_STATE') == 1
    adapter._verify_manual_boundary(record, time.monotonic() + 1, None, allow_stopped=True)
    (adapter.spec.runtime_dir / MANUAL_SAVE_FILE).unlink()
    with pytest.raises(AdapterError, match='authorization mismatch'):
        adapter._verify_manual_boundary(record, time.monotonic() + 1, None, allow_stopped=True)


def test_save_failure_without_force_policy_preserves_game(adapter, monkeypatch):
    from docich.adapters.retroarch import ManualSaveTimeout
    emulator(adapter, monkeypatch, save=False)
    monkeypatch.setattr('docich.adapters.retroarch.MANUAL_SAVE_TIMEOUT_S', .05)
    with pytest.raises(ManualSaveTimeout):
        adapter.request_round_boundary('manual-stop', time.monotonic() + 2, None)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'


def test_outer_deadline_does_not_authorize_forced_stop(adapter, monkeypatch):
    emulator(adapter, monkeypatch, save=False)
    allow_unsaved(adapter)
    with pytest.raises(game_switch.DeadlineExceededError):
        adapter.request_round_boundary('manual-stop', time.monotonic() + .05, None)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'


def test_wrong_content_after_save_timeout_cannot_force_stop(adapter, monkeypatch):
    calls = emulator(adapter, monkeypatch, save=False)
    allow_unsaved(adapter)
    monkeypatch.setattr('docich.adapters.retroarch.MANUAL_SAVE_TIMEOUT_S', .05)
    original = adapter._verify_manual_content
    def wrong_content(deadline, cancel):
        monkeypatch.setattr('docich.adapters.retroarch.send_ra_cmd',
                            lambda *a, **k: 'GET_STATUS PLAYING snes,other-game')
        original(deadline, cancel)
    monkeypatch.setattr(adapter, '_verify_manual_content', wrong_content)
    with pytest.raises(AdapterError, match='identity'):
        adapter.request_round_boundary('manual-stop', time.monotonic() + 2, None)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'


def test_hanjuku_save_config_has_fixed_path_and_core_serialization_check(adapter):
    from docich.adapters.retroarch import retroarch_cfg_lines
    adapter.game.raw['hanjuku'] = {'script_bot': True}
    lines = retroarch_cfg_lines(adapter.g, adapter.game, adapter._cfg_path(), 55555)
    assert 'core_info_savestate_bypass = "true"' in lines
    assert 'sort_savestates_enable = "false"' in lines
    assert 'sort_savestates_by_content_enable = "false"' in lines
