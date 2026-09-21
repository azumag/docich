"""Offline safe-boundary/contain contracts; never launch RetroArch or a ROM."""
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import cli, game_switch, presentation
from docich.actions import Action
from docich.adapters import make_adapter
from docich.adapters.base import AdapterError
from docich.adapters.retroarch import RetroArchCoordinatorAdapter, retroarch_cfg_lines
from docich.agent.fence import AgentFence
from docich.retroarch_boundary import (BOUNDARY_FILE, identity, input_gate,
                                     read_record, require_input_open, checkpoint_digest)
from test_coordinator_adapter_retroarch import RetroArchCoordinatorTestBase, FakeTmux


@pytest.fixture
def adapter(monkeypatch):
    base = RetroArchCoordinatorTestBase()
    base.setUp()
    try:
        g = replace(base.g, display=replace(base.g.display, width=1280, height=720,
                    viewport_x=0, viewport_y=90, viewport_width=960, viewport_height=540))
        spec = replace(base.spec, runtime_dir=g.state_dir / 'runtimes' / base.spec.runtime_id)
        safe_game = replace(
            base.adapter.game,
            lifecycle=replace(base.adapter.game.lifecycle, require_round_boundary=True),
        )
        result = RetroArchCoordinatorAdapter(g, safe_game, spec)
        result.tmux = base.tmux
        result.spec.runtime_dir.mkdir(parents=True)
        (result.spec.runtime_dir / 'states').mkdir()
        result.tmux.windows['docich:game-g1'] = ('g1-abcdef', 1, 'game')
        monkeypatch.setattr('docich.adapters.retroarch.send_ra_cmd',
                            Mock(return_value='GET_STATUS PAUSED snes,hanjuku,crc32=abcd\n'))
        yield result
    finally:
        base.tearDown()


def pending(adapter, request='request-1', **changes):
    record = dict(identity(adapter.spec, request), status='waiting', requested_ns=time.time_ns())
    record.update(changes)
    game_switch.atomic_write_json(adapter.spec.runtime_dir / BOUNDARY_FILE, record)
    return record


def saved(adapter):
    path = adapter.spec.runtime_dir / 'states/hanjuku.state'
    path.write_bytes(b'synthetic checkpoint fixture, not ROM data')
    return path


def confirm(adapter):
    adapter.confirm_safe_boundary('request-1', 'hanjuku.state', time.monotonic() + 1, None)


def test_boundary_capability_is_opt_in_for_legacy_retroarch_games():
    base = RetroArchCoordinatorTestBase()
    base.setUp()
    try:
        assert base.adapter.requires_round_boundary is False
        assert base.adapter.requires_stop_boundary is False
        assert base.adapter.request_round_boundary is None
        assert base.adapter.cancel_round_boundary is None
    finally:
        base.tearDown()


def test_wait_leaves_input_and_processes_running_and_timeout_is_reversible(adapter):
    with pytest.raises(game_switch.DeadlineExceededError):
        adapter.request_round_boundary('request-1', time.monotonic() + .03, None)
    require_input_open(adapter.spec.runtime_dir)
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'
    assert adapter.cancel_round_boundary('request-1', time.monotonic() + 1, None)
    assert adapter.tmux.windows


def test_explicit_checkpoint_then_ack_and_hold(adapter):
    pending(adapter)
    saved(adapter)
    confirm(adapter)
    adapter.request_round_boundary('request-1', time.monotonic() + 1, None)
    record = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    assert record['outcome'] == 'suspended' and len(record['sha256']) == 64
    with pytest.raises(AdapterError, match='holds input'):
        require_input_open(adapter.spec.runtime_dir)
    assert not adapter.cancel_round_boundary('request-1', time.monotonic() + 1, None)
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)


@pytest.mark.parametrize('reply', [None, 'OK', 'GET_STATUS CONTENTLESS',
    'GET_STATUS PLAYING snes,hanjuku,crc32=abcd', 'GET_STATUS PAUSED snes,other'])
def test_not_paused_or_wrong_content_cannot_ack(adapter, monkeypatch, reply):
    pending(adapter)
    saved(adapter)
    monkeypatch.setattr('docich.adapters.retroarch.send_ra_cmd', Mock(return_value=reply))
    with pytest.raises(AdapterError, match='paused content'):
        confirm(adapter)
    require_input_open(adapter.spec.runtime_dir)


@pytest.mark.parametrize('field,value', [('generation', 2), ('runtime_id', 'g2-other'),
    ('lease_id', 'stale'), ('request_id', 'stale'), ('game', 'other')])
def test_identity_mismatch_never_ack_or_cancel(adapter, field, value):
    pending(adapter, **{field: value})
    saved(adapter)
    with pytest.raises(AdapterError):
        confirm(adapter)
    assert not adapter.cancel_round_boundary('request-1', time.monotonic() + 1, None)


@pytest.mark.parametrize('kind', ['missing', 'empty', 'old', 'symlink', 'outside', 'other'])
def test_checkpoint_must_be_current_owned_nonempty_file(adapter, kind):
    pending(adapter)
    path = saved(adapter)
    name = path.name
    if kind == 'missing':
        path.unlink()
    elif kind == 'empty':
        path.write_bytes(b'')
    elif kind == 'old':
        os.utime(path, ns=(1, 1))
    elif kind == 'symlink':
        path.unlink()
        path.symlink_to(adapter.game.path)
    elif kind == 'outside':
        name = '../hanjuku.state'
    else:
        name = 'other.state'
    with pytest.raises(AdapterError):
        adapter.confirm_safe_boundary('request-1', name, time.monotonic() + 1, None)


def test_checkpoint_changed_after_ack_fails_closed(adapter):
    pending(adapter)
    path = saved(adapter)
    confirm(adapter)
    path.write_bytes(b'changed')
    with pytest.raises(AdapterError, match='checkpoint changed'):
        adapter.request_round_boundary('request-1', time.monotonic() + 1, None)
    assert not adapter.cancel_round_boundary('request-1', time.monotonic() + 1, None)


def test_write_failure_does_not_ack(adapter, monkeypatch):
    pending(adapter)
    saved(adapter)
    monkeypatch.setattr('docich.adapters.retroarch.atomic_write_json', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        confirm(adapter)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'


def test_cancel_interrupts_wait_without_killing_or_unpausing(adapter):
    event = threading.Event()
    event.set()
    with pytest.raises(game_switch.DeadlineExceededError):
        adapter.request_round_boundary('request-1', time.monotonic() + 1, event)
    assert not (adapter.spec.runtime_dir / BOUNDARY_FILE).exists()


@pytest.mark.parametrize('contents', ['{broken', '[]', '{"status":"unknown"}'])
def test_malformed_boundary_never_opens_input_or_acknowledges(adapter, contents):
    (adapter.spec.runtime_dir / BOUNDARY_FILE).write_text(contents)
    with pytest.raises(AdapterError):
        require_input_open(adapter.spec.runtime_dir)
    with pytest.raises(AdapterError):
        adapter.request_round_boundary('request-1', time.monotonic() + 1, None)


def test_checkpoint_verification_respects_cancel(adapter):
    path = saved(adapter)
    event = threading.Event()
    event.set()
    with pytest.raises(game_switch.DeadlineExceededError):
        checkpoint_digest(path, time.monotonic() + 1, event)


def test_wrong_window_owner_cannot_confirm(adapter):
    pending(adapter)
    saved(adapter)
    adapter.tmux.windows['docich:game-g1'] = ('g2-fedcba', 2, 'game')
    with pytest.raises(AdapterError, match='ownership'):
        confirm(adapter)


def test_registration_requires_boundary_and_keeps_vm_only_rom_path():
    from docich.config import load_game, load_global
    root = Path(__file__).resolve().parents[1]
    game = load_game(load_global(root), 'hanjuku-hero')
    assert game.lifecycle.require_round_boundary is True
    assert game.lifecycle.boundary_timeout_s == 300
    assert game.raw['retroarch']['rom'] == 'games/roms/hanjuku-hero.sfc'
    assert game.agent.enabled is True and game.raw['retro_corner']['enabled'] is True


def test_confirmation_serializes_with_input(adapter):
    pending(adapter)
    saved(adapter)
    with input_gate(adapter.spec.runtime_dir, time.monotonic() + 1):
        with pytest.raises(game_switch.DeadlineExceededError):
            adapter.confirm_safe_boundary('request-1', 'hanjuku.state', time.monotonic() + .02, None)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'


def test_contain_command_keeps_native_window_and_common_canvas(adapter):
    command = adapter._game_command()
    for option, value in [('--x', '0'), ('--y', '90'), ('--width', '960'), ('--height', '540')]:
        assert command[command.index(option) + 1] == value
    assert '--runtime-state' in command and '--window-pattern' in command
    lines = retroarch_cfg_lines(adapter.g, adapter.game, adapter._cfg_path(), adapter._network_port())
    assert 'video_fullscreen = "false"' in lines
    assert 'video_scale = "3.0"' in lines
    assert 'video_crop_overscan = "false"' in lines
    assert not any('1280' in line or '540' in line for line in lines)


def test_native_observation_and_input_do_not_target_presenter(adapter, monkeypatch):
    fence = AgentFence(adapter.spec.game, adapter.spec.runtime_id, 1, adapter.spec.lease_id)
    io = make_adapter(adapter.g, adapter.game, fence=fence)
    monkeypatch.setattr(io, '_check_fence', lambda: None)
    source = Mock()
    source.screenshot.return_value = Path('/unused/screenshot.png')
    xkit = Mock(return_value=source)
    monkeypatch.setattr('docich.adapters.retroarch.XKit', xkit)
    game_switch.atomic_write_json(adapter._presentation_path(),
        dict(status='ready', display=':123', window='987', width=896, height=672))
    pending(adapter)
    io.observe()
    io.act(Action(type='pad', buttons=['a'], hold_ms=100))
    source.screenshot.assert_called_once_with(adapter.g.state_dir / 'screenshots/latest.png',
                                               896, 672, window_id='987')
    source.focus.assert_called_once_with('987')
    source.tap.assert_called_once_with(['x'], 100)
    assert all(call.args == (':123',) for call in xkit.call_args_list)
    saved(adapter)
    confirm(adapter)
    with pytest.raises(AdapterError, match='holds input'):
        io.act(Action(type='pad', buttons=['start']))
    assert source.tap.call_count == 1


def test_missing_native_record_never_falls_back_to_common_display(adapter, monkeypatch):
    fence = AgentFence(adapter.spec.game, adapter.spec.runtime_id, 1, adapter.spec.lease_id)
    io = make_adapter(adapter.g, adapter.game, fence=fence)
    monkeypatch.setattr(io, '_check_fence', lambda: None)
    with pytest.raises(AdapterError, match='not ready'):
        io.observe()


@pytest.mark.parametrize('status', ['starting', 'ready', 'cleanup_failed'])
def test_disappeared_pane_is_not_proof_of_child_cleanup(adapter, status):
    adapter.tmux.windows.clear()
    game_switch.atomic_write_json(adapter._presentation_path(), {'status': status})
    with pytest.raises(AdapterError, match='shutdown'):
        adapter.alive(time.monotonic() + 1, None)
    game_switch.atomic_write_json(adapter._presentation_path(), {'status': 'stopped'})
    assert not adapter.alive(time.monotonic() + 1, None)


def test_group_leader_exit_does_not_prove_children_stopped(monkeypatch):
    child = Mock(pid=54321)
    child.poll.return_value = 0
    killpg = Mock(return_value=None)
    monkeypatch.setattr(presentation.os, 'killpg', killpg)
    assert not presentation._groups_stopped([child])
    killpg.assert_called_once_with(54321, 0)
    killpg.side_effect = ProcessLookupError
    assert presentation._groups_stopped([child])


def test_cli_confirmation_rejects_non_draining_state(adapter):
    with pytest.raises(cli.CliError, match='draining'):
        cli.cmd_ra_boundary(adapter.g, 'request-1', 'hanjuku.state')


@pytest.mark.parametrize('operation', ['stop', 'switch', 'restart'])
def test_coordinator_timeout_keeps_active_runtime_and_never_starts_next(adapter, monkeypatch, operation):
    """Use the real adapter boundary inside the coordinator's unlocked drain."""
    from test_round_boundary import _coordinator
    adapters = {}
    events = []

    def factory(spec):
        if spec.runtime_id not in adapters:
            item = RetroArchCoordinatorAdapter(adapter.g, adapter.game, spec)
            item.tmux = FakeTmux()
            item.preflight = lambda *args: None
            materialize = item.materialize_runtime
            def start(*args):
                events.append(('start', spec.game))
                materialize(*args)
                game_switch.atomic_write_json(item._presentation_path(), {'status': 'ready'})
            item.materialize_runtime = start
            item.readiness = lambda *args: None
            item.stop_agent = Mock()
            adapters[spec.runtime_id] = item
        return adapters[spec.runtime_id]

    store, coordinator = _coordinator(factory, adapter.g.state_dir, round_boundary_s=.04)
    assert coordinator.start('hanjuku-hero').status == 'succeeded'
    active = store.canonical.load()[0]['active']
    old = adapters[active['runtime_id']]
    result = (coordinator.switch('robots') if operation == 'switch'
              else getattr(coordinator, operation)())
    assert result.status == 'failed'
    state = store.canonical.load()[0]
    assert state['phase'] == 'ready' and state['active'] == active
    assert events == [('start', 'hanjuku-hero')]
    old.stop_agent.assert_not_called()
    assert not any(call[0].startswith('kill') for call in old.tmux.calls)


@pytest.mark.parametrize('operation', ['switch', 'stop'])
def test_coordinator_ack_stops_only_owned_runtime_before_next_start(adapter, monkeypatch, operation):
    from test_round_boundary import _coordinator, _wait_for_phase
    import uuid
    adapters = {}
    events = []

    def factory(spec):
        if spec.runtime_id not in adapters:
            item = RetroArchCoordinatorAdapter(adapter.g, adapter.game, spec)
            item.tmux = FakeTmux()
            item.preflight = lambda *args: None
            materialize, cleanup = item.materialize_runtime, item.cleanup_runtime
            def start(*args):
                events.append(('start', spec.game))
                materialize(*args)
                game_switch.atomic_write_json(item._presentation_path(), {'status': 'ready'})
            def stop(*args):
                events.append(('cleanup', spec.game))
                cleanup(*args)
                game_switch.atomic_write_json(item._presentation_path(), {'status': 'stopped'})
            item.materialize_runtime, item.cleanup_runtime = start, stop
            item.readiness = lambda *args: None
            stop_agent = item.stop_agent
            def stop_ai(*args):
                events.append(('stop_agent', spec.game))
                stop_agent(*args)
            item.stop_agent = stop_ai
            adapters[spec.runtime_id] = item
        return adapters[spec.runtime_id]

    store, coordinator = _coordinator(factory, adapter.g.state_dir, round_boundary_s=1)
    assert coordinator.start('hanjuku-hero').status == 'succeeded'
    active = store.canonical.load()[0]['active']
    old = adapters[active['runtime_id']]
    request = str(uuid.uuid4())
    results = []
    worker = threading.Thread(target=lambda: results.append(
        coordinator.switch('robots', request_id=request) if operation == 'switch'
        else coordinator.stop(request_id=request)))
    worker.start()
    try:
        _wait_for_phase(store, 'draining')
        limit = time.monotonic() + 1
        while not (old.spec.runtime_dir / BOUNDARY_FILE).exists():
            assert time.monotonic() < limit
            time.sleep(.005)
        saved(old)
        monkeypatch.setattr(cli, 'make_coordinator_adapter', lambda *args: old)
        assert cli.cmd_ra_boundary(adapter.g, request, 'hanjuku.state') == 0
    finally:
        worker.join(2)
    assert not worker.is_alive()
    assert results[0].status == 'succeeded', results[0]
    expected = [('start', 'hanjuku-hero'), ('stop_agent', 'hanjuku-hero'), ('cleanup', 'hanjuku-hero')]
    if operation == 'switch':
        expected += [('start', 'robots')]
    assert events[:len(expected)] == expected
    assert all(call[1] == 'docich:game-g1' for call in old.tmux.calls if call[0].startswith('kill'))
    assert (old.spec.runtime_dir / 'states/hanjuku.state').is_file()


def test_ra_commands_cannot_release_confirmed_input_hold(adapter, monkeypatch):
    active = game_switch.runtime_state_dict(adapter.spec, '2026-09-21T00:00:00Z')
    store = game_switch.GameSwitchStore(adapter.g.state_dir)
    state, _ = store.canonical.load()
    state.update(phase='ready', active=active, next_generation=2)
    store.canonical.save(state)
    pending(adapter)
    saved(adapter)
    confirm(adapter)
    send = Mock(return_value='OK')
    monkeypatch.setattr(cli, 'send_ra_cmd', send)
    for command in ['PAUSE_TOGGLE', 'LOAD_STATE', 'QUIT', 'GET_STATUS\nPAUSE_TOGGLE']:
        with pytest.raises(AdapterError, match='holds input'):
            cli.cmd_ra_cmd(adapter.g, [command])
    send.assert_not_called()
    assert cli.cmd_ra_cmd(adapter.g, ['GET_STATUS']) == 0


def test_unconfirmed_recovery_cannot_stop_agent_or_game(adapter):
    active = game_switch.runtime_state_dict(adapter.spec, '2026-09-21T00:00:00Z')
    store = game_switch.GameSwitchStore(adapter.g.state_dir)
    state, _ = store.canonical.load()
    state.update(phase='ready', active=active, next_generation=2)
    store.canonical.save(state)
    pending(adapter)
    for method in (adapter.stop_agent, adapter.cleanup_runtime):
        with pytest.raises(AdapterError, match='no safe boundary'):
            method(time.monotonic() + 1, None)
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)


def test_missing_shutdown_manifest_after_materialize_is_unknown(adapter):
    adapter._cfg_path().write_text('fixture')
    adapter.tmux.windows.clear()
    with pytest.raises(AdapterError, match='shutdown'):
        adapter.alive(time.monotonic() + 1, None)


def test_saved_runtime_cannot_be_respawned_without_restoration(adapter):
    pending(adapter)
    saved(adapter)
    confirm(adapter)
    adapter.tmux.windows.clear()
    game_switch.atomic_write_json(adapter._presentation_path(), {'status': 'stopped'})
    with pytest.raises(AdapterError, match='restoration'):
        adapter.materialize_runtime(time.monotonic() + 1, None)
    assert not any(call[0] == 'create_window_owned' for call in adapter.tmux.calls)


def test_presenter_tracks_child_groups_and_private_window_without_resizing(tmp_path, monkeypatch):
    """Mock X11/children, exercising the real presenter startup/finally path."""
    children, commands, environments, signals = [], [], [], []
    living = set()

    def launch(command, **kwargs):
        pid = 40000 + len(children)
        child = Mock(pid=pid, returncode=1)
        child.poll.return_value = None if not children else 1
        # Native viewer must remain live long enough for window discovery.
        if len(children) == 1:
            child.poll.return_value = None
        commands.append(command)
        environments.append(kwargs.get('env', {}))
        children.append(child)
        living.add(pid)
        if command[0] == 'Xvfb':
            os.write(kwargs['pass_fds'][0], b'123\n')
            child.poll.side_effect = [None, 1]
        return child

    def killpg(pid, sig):
        if pid not in living:
            raise ProcessLookupError
        signals.append((pid, sig))
        if sig:
            living.remove(pid)

    monkeypatch.setattr(presentation.subprocess, 'Popen', launch)
    monkeypatch.setattr(presentation.subprocess, 'run', Mock(return_value=Mock(returncode=0, stdout='987\n')))
    monkeypatch.setattr(presentation.subprocess, 'check_output', Mock(return_value='WIDTH=896\nHEIGHT=672\n'))
    monkeypatch.setattr(presentation.os, 'killpg', killpg)
    monkeypatch.setattr(presentation.signal, 'signal', Mock())
    monkeypatch.setattr(presentation.time, 'sleep', Mock())
    writes = Mock(wraps=presentation._write_state)
    monkeypatch.setattr(presentation, '_write_state', writes)
    state = tmp_path / 'presentation.json'
    presentation.main(['--display', ':98', '--title', 'test', '--x', '0', '--y', '90',
                      '--width', '960', '--height', '540', '--window-pattern', '^RetroArch',
                      '--runtime-state', str(state), '--', 'dbus-run-session', '--', 'retroarch'])
    assert read_record(state) == {'status': 'stopped'}
    assert [call.kwargs['status'] for call in writes.call_args_list] == [
        'ready', 'presentation_failed', 'stopped']
    assert commands[1] == ['dbus-run-session', '--', 'retroarch']
    assert environments[1]['DISPLAY'] == ':123'
    assert environments[2]['DISPLAY'] == ':98'
    assert commands[2][commands[2].index('-video_size') + 1] == '896x672'
    assert set(pid for pid, sig in signals if sig) == {40000, 40001, 40002}
