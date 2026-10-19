"""Manual TRPG integration contracts; no Twitch, processes or real audio."""
from copy import deepcopy
from dataclasses import replace
from functools import partial
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace
from urllib.request import urlopen
import threading
import time
import uuid

import pytest

from docich import trpg_corner as core, trpg_narration as narration, trpg_session as session, trpg_view, webui
from docich.adapters import make_coordinator_adapter, program
from docich.adapters.cli_game import CliCoordinatorAdapter
from docich.config import load_global
from docich.corner_adapters import TrpgCornerAdapter
from docich.corner_catalog import load_catalog
from docich.game_switch import RuntimeSpec, ReadinessTimeoutError, DeadlineExceededError
from docich.naming import runtime_names
from docich.overlay_queue import append_event, load_events


@pytest.fixture
def setup(tmp_path):
    story = {"id": "reviewed-fixture", "title": "人間レビュー用 fixture", "genre": "both",
             "scenes": [{"id": "s1", "text": "手がかりを確認する", "choices": ["調べる", "話す"]}],
             "climax": {"suspects": ["A", "B"], "answer": "B", "trick": "毒"}}
    (tmp_path / "scenario.json").write_text(json.dumps(story, ensure_ascii=False))
    config = tmp_path / "config.toml"
    config.write_text(f'''[paths]
state_dir="{tmp_path / 'run'}"
[webui]
soren_root="{tmp_path / 'soren'}"
[trpg_corner]
scenario_file="scenario.json"
chat_file="chat.log"
[corner_rotation]
enabled=true
schedule_mode="queue"
corners=[{{id="trpg-corner",adapter="trpg",game="trpg-view",enabled=true,audio_enabled=true,manual_only=true}}]
''')
    g = load_global(tmp_path, config)
    names = runtime_names(4)
    spec = RuntimeSpec(game="trpg-view", adapter="program", generation=4,
                       runtime_id="g4-a1b2c3d4", lease_id=str(uuid.uuid4()),
                       runtime_dir=g.state_dir / "runtimes/g4-a1b2c3d4",
                       game_window=names.game_window, agent_window=names.agent_window,
                       adapter_session=names.adapter_session)
    return g, spec


def test_daemon_chat_to_core_reveal_ledger_and_real_overlay(setup, monkeypatch):
    g, spec = setup
    speech = Mock()
    publish = narration.publish
    monkeypatch.setattr(narration, "publish", lambda g, **kw: publish(
        g, **kw, overlay=partial(append_event, regenerate=False), speech=speech))
    # Existing old lines must never be replayed into this new session.
    chat = session.chat_path(g)
    chat.write_text('id=old\tuser-id=9\tlogin=old\tdisplay=Old\tflags=\tOld: !1\n')
    session.prepare(g, spec, now=0)
    path = spec.runtime_dir / session.SESSION_FILE
    expected = session.identity(spec)
    def tick(now):
        return session.tick(g, path, expected, audio_enabled=True, now=now)
    assert tick(0) is False
    assert tick(3) is False
    with chat.open('a') as stream:
        stream.write('id=new\tuser-id=123\tlogin=alice\tdisplay=Alice\tflags=\tAlice: !2\n')
    tick(4)
    data = session.load(path, expected)
    assert data['state']['vote_order'] == {'123': '2'}
    projected = session.public(data, now=4)
    assert projected['votes'] == {'1': 0, '2': 1}
    assert 'answer' not in projected and 'trick' not in projected
    assert '"123"' not in json.dumps(projected)
    tick(5)  # never replay the line
    assert session.load(path, expected)['state']['vote_order'] == {'123': '2'}
    tick(63)
    tick(66)
    assert session.load(path, expected)['state']['phase'] == 'CLIMAX'
    with chat.open('a') as stream:
        stream.write('id=acc\tuser-id=123\tlogin=alice\tdisplay=Alice\tflags=\tAlice: !accuse B\n')
    tick(67)
    tick(186)
    projected = session.public(session.load(path, expected), now=186)
    assert projected['phase'] == 'REVEAL'
    assert projected['answer'] == 'B' and projected['correct_count'] == 1
    assert tick(196) is True
    assert tick(197) is True
    ledger = core.load_ledger(g.state_dir / 'trpg' / core.LEDGER_FILE)
    assert len(ledger['entries']) == 1
    assert ledger['entries'][0]['scores'] == {'123': 10}
    events = load_events(Path(g.webui.soren_root), strict=True)
    assert events and all(row['category'] == 'game' for row in events)
    assert {call.kwargs['context'] for call in speech.call_args_list} == set(narration.CONTEXTS.values())


@pytest.mark.parametrize('phase', ['CHOICE', 'CLIMAX'])
@pytest.mark.parametrize('api', ['helper', 'observe_chat'])
def test_rejected_new_participant_does_not_mutate_state(phase, api, monkeypatch, setup):
    g, _ = setup
    monkeypatch.setattr(core, 'MAX_PARTICIPANTS', 1)
    st = core.new_state()
    core.start(st, session.scenario(g))
    core.open_choice(st)
    if phase == 'CLIMAX':
        core.close_choice(st)
        core.open_climax(st)
    record = core.record_vote if phase == 'CHOICE' else core.record_accusation
    values = (1, 2) if phase == 'CHOICE' else ('A', 'B')
    if api == 'observe_chat':
        record = core.observe_chat
        values = ('!1', '!2') if phase == 'CHOICE' else ('!accuse A', '!accuse B')
    record(st, 'u1', values[0])
    before = deepcopy(st)
    if api == 'helper':
        with pytest.raises(core.TrpgCornerError, match='too many participants'):
            record(st, 'u2', values[1])
    else:
        assert record(st, 'u2', values[1]) == 'ignored'
    assert st == before
    record(st, 'u1', values[1])  # existing users may replace at capacity
    assert len(st['vote_order'] if phase == 'CHOICE' else st['accusations']) == 1


def test_missing_reviewed_scenario_is_ineligible_and_preflight_fails(setup, monkeypatch):
    g, spec = setup
    (g.repo_root / 'scenario.json').unlink()
    owner = TrpgCornerAdapter(g, load_catalog(g)[0])
    assert owner.eligible() is False
    monkeypatch.setattr(program.ProgramViewAdapter, 'preflight', Mock())
    with pytest.raises(OSError):
        make_coordinator_adapter(g, spec).preflight(time.monotonic() + 1, None)
    program.ProgramViewAdapter.preflight.assert_not_called()


def test_factory_preflight_and_identity_readiness(setup, monkeypatch):
    g, spec = setup
    adapter = make_coordinator_adapter(g, spec)
    monkeypatch.setattr(program.ProgramViewAdapter, 'preflight', lambda *_: None)
    monkeypatch.setattr(program.ProgramViewAdapter, 'materialize_runtime', lambda *_: None)
    adapter.preflight(time.monotonic() + 2, None)
    assert not (spec.runtime_dir / session.SESSION_FILE).exists()
    adapter.materialize_runtime(time.monotonic() + 2, None)
    assert '--session-file' in adapter._game_command()
    # Actual local HTTP responses and the real readiness parser. Only the
    # process/X boundary is stubbed; no GUI or game process is started.
    monkeypatch.setattr(CliCoordinatorAdapter, 'readiness', lambda *_: None)
    def check(runtime_id):
        server = ThreadingHTTPServer(('127.0.0.1', 0), trpg_view.handler_for(
            runtime_id, generation=spec.generation, lease_id=spec.lease_id,
            session_file=spec.runtime_dir / session.SESSION_FILE))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        adapter.dashboard_port = server.server_port
        try:
            adapter.readiness(time.monotonic() + .15, None)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)
    check(spec.runtime_id)
    with pytest.raises((ReadinessTimeoutError, DeadlineExceededError)):
        check('g99-foreign')


@pytest.mark.parametrize('field,value', [('runtime_id', 'foreign'), ('generation', 99),
                                        ('generation', True), ('lease_id', '11111111-1111-4111-8111-111111111111')])
def test_readiness_rejects_foreign_identity_even_with_http_200(setup, monkeypatch, field, value):
    from docich.adapters import trpg_program
    g, spec = setup
    data = session.prepare(g, spec, now=0)
    body = session.public(data, now=0)
    body[field] = value
    response = Mock(status=200)
    response.read.return_value = json.dumps(body).encode()
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(CliCoordinatorAdapter, 'readiness', lambda *_: None)
    monkeypatch.setattr(trpg_program, 'urlopen', lambda *_a, **_kw: context)
    with pytest.raises((ReadinessTimeoutError, DeadlineExceededError)):
        make_coordinator_adapter(g, spec).readiness(time.monotonic() + .03, None)


@pytest.mark.parametrize('field,value', [('runtime_id', 'foreign'), ('generation', 99),
                                        ('lease_id', '11111111-1111-4111-8111-111111111111')])
def test_view_launch_binding_does_not_allow_unproven_header_changes(setup, field, value):
    g, spec = setup
    data = session.prepare(g, spec)
    data[field] = value
    path = spec.runtime_dir / session.SESSION_FILE
    path.write_text(json.dumps(data))
    with pytest.raises(core.TrpgCornerError, match='identity'):
        session.load(path, session.identity(spec), view=True)


def test_chat_cursor_partial_oversize_and_retained_rotation(setup, monkeypatch):
    g, spec = setup
    monkeypatch.setattr(narration, 'publish', Mock())
    session.prepare(g, spec, now=0)
    path, expected = spec.runtime_dir / session.SESSION_FILE, session.identity(spec)
    session.tick(g, path, expected, now=0)
    session.tick(g, path, expected, now=3)
    chat = session.chat_path(g)
    line = json.dumps({'user_id': 'u1', 'text': '!2'})
    chat.write_text(line)
    session.tick(g, path, expected, now=4)
    assert session.load(path, expected)['state']['vote_order'] == {}
    with chat.open('a') as stream:
        stream.write('\n')
    session.tick(g, path, expected, now=5)
    assert session.load(path, expected)['state']['vote_order'] == {'u1': '2'}
    # An oversized physical line's JSON-looking continuation is not a message.
    with chat.open('a') as stream:
        stream.write('x' * (session.READ_BYTES + 10) + json.dumps({'user_id': 'u2', 'text': '!1'}) + '\n')
    session.tick(g, path, expected, now=6)
    session.tick(g, path, expected, now=7)
    assert session.load(path, expected)['state']['vote_order'] == {'u1': '2'}
    # A retained old tail must not overwrite a later vote after rotation.
    session.tick(g, path, expected, now=8)
    with chat.open('a') as stream:
        stream.write(json.dumps({'user_id': 'u1', 'text': '!1'}) + '\n')
    session.tick(g, path, expected, now=9)
    chat.rename(chat.with_suffix('.old'))
    chat.write_text(line + '\n')
    session.tick(g, path, expected, now=10)
    assert session.load(path, expected)['state']['vote_order'] == {'u1': '1'}


def append_chat(g, user, text, *, newline=True):
    with session.chat_path(g).open('a') as stream:
        stream.write(json.dumps({'user_id': user, 'text': text}) + ('\n' if newline else ''))


def test_activation_and_choice_open_exclude_preexisting_and_partial_ballots(setup, monkeypatch):
    g, spec = setup
    publishing = Mock()
    monkeypatch.setattr(narration, 'publish', publishing)
    session.prepare(g, spec, now=0)
    path, expected = spec.runtime_dir / session.SESSION_FILE, session.identity(spec)
    append_chat(g, 'precommit', '!2')
    assert session.load(path, expected)['state']['phase'] == 'IDLE'
    assert session.public(session.load(path, expected), now=100)['phase'] == 'IDLE'
    publishing.assert_not_called()
    session.tick(g, path, expected, now=100)
    data = session.load(path, expected)
    assert data['phase_at'] == data['activated_at'] == 100
    assert data['state']['phase'] == 'SCENE'
    assert [call.kwargs['kind'] for call in publishing.call_args_list] == ['intro', 'scene']
    append_chat(g, 'before-choice', '!2')
    append_chat(g, 'partial-before-choice', '!2', newline=False)
    session.tick(g, path, expected, now=103)
    with session.chat_path(g).open('a') as stream:
        stream.write('\n')
    append_chat(g, 'valid', '!1')
    session.tick(g, path, expected, now=104)
    session.tick(g, path, expected, now=105)
    assert session.load(path, expected)['state']['vote_order'] == {'valid': '1'}


@pytest.mark.parametrize('phase,command,next_phase', [
    ('CHOICE', '!2', 'RESOLVE'), ('CLIMAX', '!accuse B', 'REVEAL')])
def test_ballot_closing_drains_frozen_boundary_before_tally(setup, monkeypatch, phase, command, next_phase):
    g, spec = setup
    monkeypatch.setattr(narration, 'publish', Mock())
    session.prepare(g, spec)
    path, expected = spec.runtime_dir / session.SESSION_FILE, session.identity(spec)
    for now in (0, 3):
        session.tick(g, path, expected, now=now)
    opened, closes = (3, 63) if phase == 'CHOICE' else (66, 186)
    if phase == 'CLIMAX':
        session.tick(g, path, expected, now=63)
        append_chat(g, 'before-climax', command)
        session.tick(g, path, expected, now=66)
    session.tick(g, path, expected, now=closes-1)
    # More than one bounded poll, all written before the closing observation.
    users = {f'u{i}' for i in range(session.MAX_LINES + 2)}
    for user in sorted(users):
        append_chat(g, user, command)
    session.tick(g, path, expected, now=closes)
    data = session.load(path, expected)
    assert data['state']['phase'] == phase and data['phase_at'] == opened
    boundary = data['closing']
    append_chat(g, 'after-close', command)
    session.tick(g, path, expected, now=closes+.1)
    data = session.load(path, expected)
    assert data['state']['phase'] == next_phase and 'closing' not in data
    accepted = data['state']['vote_order'] if phase == 'CHOICE' else data['state']['accusations']
    assert set(accepted) == users
    assert data['cursor']['offset'] == boundary['offset']
    session.tick(g, path, expected, now=closes+1)
    data = session.load(path, expected)
    accepted = data['state']['vote_order'] if phase == 'CHOICE' else data['state']['accusations']
    assert set(accepted) == users


def test_last_ballot_before_deadline_is_counted_and_unterminated_tail_is_not(setup, monkeypatch):
    g, spec = setup
    monkeypatch.setattr(narration, 'publish', Mock())
    session.prepare(g, spec)
    path, expected = spec.runtime_dir / session.SESSION_FILE, session.identity(spec)
    for now in (0, 3, 62):
        session.tick(g, path, expected, now=now)
    append_chat(g, 'last', '!2')
    append_chat(g, 'unfinished', '!1', newline=False)
    session.tick(g, path, expected, now=63)
    data = session.load(path, expected)
    assert data['state']['phase'] == 'RESOLVE'
    assert data['state']['vote_order'] == {'last': '2'}


@pytest.fixture
def real_trpg_coordinator(setup, monkeypatch):
    """Real coordinator/adapter/HTTP; only OS processes and target faults are fake."""
    from docich.game_switch import GameSwitchCoordinator, GameSwitchStore, StepTimeouts
    from docich.tmux import TmuxOwnership
    g, _ = setup
    helper_path = Path(__file__).with_name('test_puyo_program.py')
    module_spec = importlib.util.spec_from_file_location('trpg_rollback_processes', helper_path)
    helpers = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(helpers)
    processes, adapters, preflights = {}, {}, []
    fixture = SimpleNamespace(g=g, fault=None, fail_game='broken-target', now=0)

    class FakeTmux:
        def __init__(self, spec): self.spec = spec
        def session_target_exists(self, *_):
            return processes.get(self.spec.runtime_id, {}).get('live', False)
        def window_target_exists(self, *_): return False
        def read_session_ownership(self, *_):
            return TmuxOwnership(self.spec.runtime_id, self.spec.generation, 'adapter')

    def close(runtime_id):
        process = processes.get(runtime_id)
        if process and process['live']:
            process['live'] = False
            process['server'].shutdown()
            process['server'].server_close()
            process['thread'].join(2)

    def materialize(adapter, *_):
        spec = adapter.spec
        server = ThreadingHTTPServer(('127.0.0.1', 0), trpg_view.handler_for(
            spec.runtime_id, generation=spec.generation, lease_id=spec.lease_id,
            session_file=spec.runtime_dir / session.SESSION_FILE))
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        processes[spec.runtime_id] = {'live': True, 'server': server, 'thread': worker}
        adapter.dashboard_port = server.server_port
        worker.start()

    def fail(*_): raise RuntimeError('synthetic target failure')

    def factory(spec):
        key = (spec.runtime_id, spec.lease_id)
        if key not in adapters:
            if spec.game == 'trpg-view':
                adapter = make_coordinator_adapter(g, spec)
                adapter.tmux = FakeTmux(spec)
                adapter.cleanup_runtime = lambda *_: close(spec.runtime_id)
                if spec.runtime_id in processes:
                    adapter.dashboard_port = processes[spec.runtime_id]['server'].server_port
            else:
                adapter = helpers.FakeRuntimeAdapter(spec)
                if spec.game == fixture.fail_game and fixture.fault is not None:
                    if fixture.fault == 'live': adapter.preflight = fail
                    else: adapter.materialize_runtime = fail
            adapters[key] = adapter
        return adapters[key]

    monkeypatch.setattr(program.ProgramViewAdapter, 'preflight',
                        lambda adapter, *_: preflights.append(adapter.spec.runtime_id))
    monkeypatch.setattr(program.ProgramViewAdapter, 'materialize_runtime', materialize)
    monkeypatch.setattr(CliCoordinatorAdapter, 'readiness', lambda *_: None)
    monkeypatch.setattr(narration, 'publish', Mock())
    fixture.store = GameSwitchStore(g.state_dir)
    fixture.switch = GameSwitchCoordinator(fixture.store, factory, default_timeout_s=3,
        rollback_timeout_s=2, quiesce_verify_timeout_s=.2, poll_interval_s=.01,
        step_timeouts=StepTimeouts(preflight_s=.5, stop_agent_s=.5, start_s=.5,
                                  agent_start_s=.5, cleanup_s=.5, probe_s=.2))
    fixture.preflights, fixture.adapters = preflights, adapters
    try:
        yield fixture
    finally:
        for runtime_id in processes: close(runtime_id)


class PauseManager(Exception):
    pass


def pause_manager(_):
    raise PauseManager


def test_committed_manager_activates_after_delayed_readiness(real_trpg_coordinator):
    from docich.trpg_program import TrpgProgramManager
    fixture = real_trpg_coordinator
    g, switch = fixture.g, fixture.switch
    def delayed_commit(_):
        # Logical time spent between materialization and the post-commit hook.
        fixture.now = 100
        append_chat(g, 'before-activation', '!2')
    switch.post_commit = delayed_commit
    manager = TrpgProgramManager(g, coordinator=switch, clock=lambda: fixture.now, sleep=pause_manager)
    state = manager._new_state(str(uuid.uuid4()))
    assert manager._dispatch_start(state) is None
    active = state['trpg_program_runtime_identity']
    path = g.state_dir / 'runtimes' / active['runtime_id'] / session.SESSION_FILE
    assert session.load(path, active)['state']['phase'] == 'IDLE'
    narration.publish.assert_not_called()
    with pytest.raises(PauseManager):
        manager._wait_and_restore(state)
    data = session.load(path, active)
    assert data['phase_at'] == data['activated_at'] == 100
    assert data['state']['phase'] == 'SCENE' and data['state']['vote_order'] == {}
    assert [call.kwargs['kind'] for call in narration.publish.call_args_list] == ['intro', 'scene']


@pytest.mark.parametrize('mode', ['live', 'fresh'])
def test_real_rollback_preserves_session_and_manager_rebinds_identity(real_trpg_coordinator, mode):
    from docich.trpg_program import TrpgProgramManager
    fixture = real_trpg_coordinator
    g, switch, store = fixture.g, fixture.switch, fixture.store
    manager = TrpgProgramManager(g, coordinator=switch, clock=lambda: fixture.now, sleep=pause_manager)
    state = manager._new_state(str(uuid.uuid4()))
    assert manager._dispatch_start(state) is None
    lifetime = (state['started_at'], state['ends_at'])
    source = state['trpg_program_runtime_identity']
    path = g.state_dir / 'runtimes' / source['runtime_id'] / session.SESSION_FILE
    for now in (0, 3):
        session.tick(g, path, source, now=now, owner_request_id=state['rotation_request_id'])
    append_chat(g, 'existing-voter', '!2')
    session.tick(g, path, source, now=4)
    before = session.load(path, source)
    fixture.fault = mode
    result = switch.switch('broken-target')
    canonical = store.canonical.load()[0]
    assert result.status == 'rolled_back' and result.error_code != 'rollback_failed'
    assert canonical['phase'] == 'ready'
    active = {key: canonical['active'][key] for key in session.IDENTITY_KEYS}
    assert active['lease_id'] != source['lease_id']
    assert (active['runtime_id'] == source['runtime_id']) is (mode == 'live')
    assert len(fixture.preflights) == 1  # restore never calls preflight
    restored_path = g.state_dir / 'runtimes' / active['runtime_id'] / session.SESSION_FILE
    after = session.load(restored_path, active)
    for key in ('state', 'scenario', 'phase_at', 'activated_at', 'cursor', 'announced',
                'intro_sent', 'recorded', 'owner_request_id'):
        assert after[key] == before[key]
    adapter = fixture.adapters[(active['runtime_id'], active['lease_id'])]
    with urlopen(f'http://127.0.0.1:{adapter.dashboard_port}/api/trpg') as response:
        projected = json.load(response)
    assert all(projected[key] == active[key] for key in session.IDENTITY_KEYS)
    assert projected['phase'] == 'CHOICE' and projected['votes']['2'] == 1
    fixture.now = 5
    with pytest.raises(PauseManager):
        manager._wait_and_restore(state)
    assert state['status'] == 'active' and state['trpg_program_runtime_identity'] == active
    assert (state['started_at'], state['ends_at']) == lifetime
    assert manager._adopt_rollback(state, canonical, {**source, 'lease_id': 'foreign'}) is None
    assert manager._adopt_rollback({**state, 'rotation_request_id': str(uuid.uuid4())}, canonical, source) is None
    foreign = {**canonical, 'active': {**canonical['active'], 'game': 'robots'}}
    assert manager._adopt_rollback(state, foreign, source) is None
    # A stop immediately after another rollback must also adopt before restoring.
    result = switch.switch('broken-target')
    assert result.status == 'rolled_back'
    assert manager._restore(state, end_reason='manual') == 'completed'
    assert store.canonical.load()[0]['phase'] == 'idle'
    ledger = core.load_ledger(g.state_dir / 'trpg' / core.LEDGER_FILE)
    assert len(ledger['entries']) == 1 and ledger['entries'][0]['outcome'] == 'aborted'


def test_failed_live_rollback_can_retry_with_old_canonical_lease(real_trpg_coordinator, monkeypatch):
    fixture = real_trpg_coordinator
    assert fixture.switch.start('trpg-view').status == 'succeeded'
    source = fixture.store.canonical.load()[0]['active']
    fixture.fault = 'live'
    with monkeypatch.context() as fault:
        def fail(*_): raise ReadinessTimeoutError('synthetic process readiness failure')
        fault.setattr(CliCoordinatorAdapter, 'readiness', fail)
        result = fixture.switch.switch('broken-target')
    assert result.status == 'failed' and result.error_code == 'rollback_failed'
    failed = fixture.store.canonical.load()[0]
    assert failed['phase'] == 'failed' and failed['previous']['lease_id'] == source['lease_id']
    fixture.switch.recover()
    canonical = fixture.store.canonical.load()[0]
    assert canonical['phase'] == 'ready' and canonical['last_result']['status'] == 'rolled_back'
    active = {key: canonical['active'][key] for key in session.IDENTITY_KEYS}
    assert active['runtime_id'] == source['runtime_id'] and active['lease_id'] != source['lease_id']
    path = fixture.g.state_dir / 'runtimes' / active['runtime_id'] / session.SESSION_FILE
    assert session.load(path, active)['state']['phase'] == 'IDLE'


@pytest.mark.parametrize('mode', ['live', 'fresh'])
@pytest.mark.parametrize('resume', [False, True])
def test_manager_retries_restoration_after_proven_rollback(real_trpg_coordinator, mode, resume, monkeypatch):
    from docich.trpg_program import TrpgProgramManager
    fixture = real_trpg_coordinator
    assert fixture.switch.start('robots').status == 'succeeded'
    manager = TrpgProgramManager(fixture.g, coordinator=fixture.switch, clock=lambda: fixture.now)
    state = manager._new_state(str(uuid.uuid4()))
    assert manager._dispatch_start(state) is None
    source = state['trpg_program_runtime_identity']
    manager._session_tick(source, request_id=state['rotation_request_id'])
    fixture.fail_game, fixture.fault = 'robots', mode
    if resume:
        retry = manager._retry_rolled_back_restore
        def pause_after_receipt(state, canonical, identity):
            receipt = fixture.store.receipts.load(state['restore_request_id'])
            if receipt and receipt['status'] == 'rolled_back':
                raise PauseManager
            return retry(state, canonical, identity)
        with monkeypatch.context() as interrupted:
            interrupted.setattr(manager, '_retry_rolled_back_restore', pause_after_receipt)
            with pytest.raises(PauseManager):
                manager._restore(state, end_reason='manual')
        # Resume from the saved lifecycle file, not the interrupted call's dict.
        state = manager._read_state()
        assert manager.run_rotation(state['rotation_request_id']) == 'queued'
        state = manager._read_state()
    else:
        assert manager._restore(state, end_reason='manual') == 'queued'
    canonical = fixture.store.canonical.load()[0]
    assert canonical['phase'] == 'ready' and canonical['active']['game'] == 'trpg-view'
    assert state['status'] == 'restoring' and state['end_reason'] == 'manual'
    assert state['trpg_program_runtime_identity'] != source
    assert state['restore_request_id'] != canonical['last_result']['request_id']
    fixture.fault = None
    assert manager.run_rotation(state['rotation_request_id']) == 'completed'
    assert fixture.store.canonical.load()[0]['active']['game'] == 'robots'
    ledger = core.load_ledger(fixture.g.state_dir / 'trpg' / core.LEDGER_FILE)
    assert len(ledger['entries']) == 1 and ledger['entries'][0]['outcome'] == 'aborted'


@pytest.mark.parametrize('mode', ['live', 'fresh'])
def test_manager_can_adopt_rollback_before_first_activation(real_trpg_coordinator, mode, monkeypatch):
    from docich.trpg_program import TrpgProgramManager
    fixture = real_trpg_coordinator
    start = fixture.switch.start
    def start_then_rollback(*args, **kwargs):
        result = start(*args, **kwargs)
        fixture.fault = mode
        assert fixture.switch.switch('broken-target').status == 'rolled_back'
        return result
    monkeypatch.setattr(fixture.switch, 'start', start_then_rollback)
    manager = TrpgProgramManager(fixture.g, coordinator=fixture.switch, clock=lambda: fixture.now,
                                 sleep=pause_manager)
    state = manager._new_state(str(uuid.uuid4()))
    assert manager._dispatch_start(state) is None
    with pytest.raises(PauseManager):
        manager._wait_and_restore(state)
    identity = state['trpg_program_runtime_identity']
    path = fixture.g.state_dir / 'runtimes' / identity['runtime_id'] / session.SESSION_FILE
    data = session.load(path, identity)
    assert data['state']['phase'] == 'SCENE' and data['activated_at'] == 0
    assert data['owner_request_id'] == state['rotation_request_id']


@pytest.mark.parametrize('mode', ['live', 'fresh'])
@pytest.mark.parametrize('resume', ['before-q', 'after-q', 'before-retry-save', 'after-retry-save',
                                   'legacy-after-adopt'])
def test_fifo_restore_keeps_payload_until_terminal_then_retries(real_trpg_coordinator, mode, resume, monkeypatch):
    from docich.trpg_program import TrpgProgramManager
    fixture = real_trpg_coordinator
    g, store, switch = fixture.g, fixture.store, fixture.switch
    observed = Mock(wraps=core.observe_chat)
    monkeypatch.setattr(core, 'observe_chat', observed)
    def manager():
        return TrpgProgramManager(g, coordinator=switch, clock=lambda: fixture.now)
    owner = manager()
    assert switch.start('robots').status == 'succeeded'
    state = owner._new_state(str(uuid.uuid4()))
    assert owner._dispatch_start(state) is None
    source = state['trpg_program_runtime_identity']
    owner._session_tick(source, request_id=state['rotation_request_id'])
    fixture.now = 3
    owner._session_tick(source, request_id=state['rotation_request_id'])
    append_chat(g, 'one-voter', '!2')
    fixture.now = 4
    owner._session_tick(source, request_id=state['rotation_request_id'])
    speech_count = narration.publish.call_count
    lifetime = (state['started_at'], state['ends_at'])
    fixture.fault = mode
    q = str(uuid.uuid4())
    store.enqueue_request(q, 'switch', 'broken-target', {'expected_source': source})
    attempts = []
    original = switch.switch
    def recording_switch(target, **kwargs):
        result = original(target, **kwargs)
        attempts.append((kwargs['request_id'], deepcopy(kwargs['payload']), result.status, result.error_code))
        return result
    monkeypatch.setattr(switch, 'switch', recording_switch)
    assert owner._restore(state, end_reason='manual') == 'queued'
    r = state['restore_request_id']
    queued_hash = store.receipts.load(r)['payload_hash']
    assert [receipt['request_id'] for receipt in store.receipts.queued()] == [q, r]
    if resume == 'before-q':
        owner = manager()
        state = owner._read_state()
        assert owner.run_rotation(state['rotation_request_id']) == 'queued'
        state = owner._read_state()
        assert [receipt['request_id'] for receipt in store.receipts.queued()] == [q, r]
    assert switch.switch('broken-target', request_id=q, payload={'expected_source': source}).status == 'rolled_back'
    canonical = store.canonical.load()[0]
    active = {key: canonical['active'][key] for key in session.IDENTITY_KEYS}
    assert active != source and store.receipts.load(r)['status'] == 'queued'
    if resume == 'legacy-after-adopt':
        # Persisted old-version state after the reported conflict lost A's
        # explicit binding; its immutable receipt must prove the recovered A.
        assert owner._adopt_rollback(state, canonical, source) == active
        state.pop('restore_source_runtime_identity', None)
        owner._save(state)
    owner = manager()
    state = owner._read_state()
    if resume in {'before-retry-save', 'after-retry-save'}:
        save = owner._save
        def pause_retry(updated):
            if updated.get('restore_request_id') != r:
                if resume == 'after-retry-save':
                    save(updated)
                raise PauseManager
            save(updated)
        with monkeypatch.context() as interruption:
            interruption.setattr(owner, '_save', pause_retry)
            with pytest.raises(PauseManager):
                owner.run_rotation(state['rotation_request_id'])
        owner = manager()
        state = owner._read_state()
        if state['restore_request_id'] == r:
            assert owner.run_rotation(state['rotation_request_id']) == 'queued'
            state = owner._read_state()
    else:
        assert owner.run_rotation(state['rotation_request_id']) == 'queued'
        state = owner._read_state()
    old = store.receipts.load(r)
    assert old['status'] == 'failed' and old['result']['error_code'] == 'source_fence_lost'
    assert old['payload_hash'] == queued_hash
    assert all(payload == {'expected_source': source} for req, payload, _, _ in attempts if req == r)
    assert state['restore_request_id'] != r and state['restore_source_runtime_identity'] == active
    assert state['trpg_program_runtime_identity'] == active and state['end_reason'] == 'manual'
    assert store.canonical.load()[0]['active'] == canonical['active']
    assert not store.receipts.queued()
    assert owner.run_rotation(state['rotation_request_id']) == 'completed'
    assert owner.run_rotation(state['rotation_request_id']) == 'completed'
    assert store.canonical.load()[0]['active']['game'] == 'robots'
    assert not store.receipts.queued()
    assert all(status != 'request_conflict' for _, _, status, _ in attempts)
    final = owner._read_state()
    assert (final['started_at'], final['ends_at']) == lifetime
    ledger = core.load_ledger(g.state_dir / 'trpg' / core.LEDGER_FILE)
    assert len(ledger['entries']) == 1 and ledger['entries'][0]['outcome'] == 'aborted'
    assert observed.call_count == 1 and narration.publish.call_count == speech_count


def test_stale_queued_restore_finishes_without_touching_unrelated_owner(real_trpg_coordinator, monkeypatch):
    from docich.trpg_program import TrpgProgramManager
    fixture = real_trpg_coordinator
    assert fixture.switch.start('robots').status == 'succeeded'
    owner = TrpgProgramManager(fixture.g, coordinator=fixture.switch, clock=lambda: fixture.now)
    state = owner._new_state(str(uuid.uuid4()))
    assert owner._dispatch_start(state) is None
    source = state['trpg_program_runtime_identity']
    tick = Mock(wraps=owner._session_tick)
    monkeypatch.setattr(owner, '_session_tick', tick)
    q = str(uuid.uuid4())
    fixture.store.enqueue_request(q, 'switch', 'operator-game', {'expected_source': source})
    assert owner._restore(state, end_reason='manual') == 'queued'
    r = state['restore_request_id']
    old_hash = fixture.store.receipts.load(r)['payload_hash']
    assert fixture.switch.switch('operator-game', request_id=q, payload={'expected_source': source}).status == 'succeeded'
    active = deepcopy(fixture.store.canonical.load()[0]['active'])
    tick_count, speech_count = tick.call_count, narration.publish.call_count
    assert owner._restore(state) == 'completed'
    assert state['status'] == 'interrupted' and state['restore_request_id'] == r
    receipt = fixture.store.receipts.load(r)
    assert receipt['status'] == 'failed' and receipt['result']['error_code'] == 'source_fence_lost'
    assert receipt['payload_hash'] == old_hash and not fixture.store.receipts.queued()
    assert fixture.store.canonical.load()[0]['active'] == active
    assert tick.call_count == tick_count and narration.publish.call_count == speech_count


def test_unverified_restore_binding_does_not_change_queued_receipt(real_trpg_coordinator):
    from docich.trpg_program import TrpgProgramManager, TrpgProgramError
    fixture = real_trpg_coordinator
    assert fixture.switch.start('robots').status == 'succeeded'
    owner = TrpgProgramManager(fixture.g, coordinator=fixture.switch, clock=lambda: fixture.now)
    state = owner._new_state(str(uuid.uuid4()))
    assert owner._dispatch_start(state) is None
    source = state['trpg_program_runtime_identity']
    q = str(uuid.uuid4())
    fixture.store.enqueue_request(q, 'switch', 'broken-target', {'expected_source': source})
    assert owner._restore(state, end_reason='manual') == 'queued'
    r = state['restore_request_id']
    before = deepcopy(fixture.store.receipts.load(r))
    canonical = deepcopy(fixture.store.canonical.load()[0])
    state['restore_source_runtime_identity'] = {**source, 'lease_id': str(uuid.uuid4())}
    owner._save(state)
    with pytest.raises(TrpgProgramError, match='source binding cannot be verified'):
        owner._restore(state)
    assert fixture.store.receipts.load(r) == before
    assert fixture.store.canonical.load()[0] == canonical
    assert [receipt['request_id'] for receipt in fixture.store.receipts.queued()] == [q, r]


def test_webui_start_schedule_stop_and_state_projection(setup, monkeypatch):
    from docich.corner_rotation import CornerRotationManager
    g, _ = setup
    calls = []
    monkeypatch.setattr(CornerRotationManager, 'queue_manual', lambda self, game, **kw:
                        calls.append((game, kw)) or {'status': 'queued', 'corner': 'trpg-corner'})
    monkeypatch.setattr(TrpgCornerAdapter, 'eligible', lambda self: True)
    monkeypatch.setattr('docich.trpg_program.TrpgProgramManager.stop', lambda self: 'queued')
    for action in ('start', 'schedule', 'stop'):
        handler = object.__new__(webui._Handler)
        handler.g, handler.headers = g, {}
        body = {'action': action, 'corner': 'trpg-corner', 'confirm': True, 'trigger_at': time.time() + 600}
        handler._read_body = lambda: (json.dumps(body).encode(), None)
        handler._send_json = Mock()
        handler._send_error_json = Mock()
        assert handler._handle_post_corners() == 200
        handler._send_error_json.assert_not_called()
    assert len(calls) == 2 and all(game == 'trpg-view' for game, _ in calls)
    g.state_dir.mkdir(exist_ok=True)
    (g.state_dir / 'trpg_corner_state.json').write_text(json.dumps({'status': 'active', 'game': 'trpg-view'}))
    view = webui._corners_view(g)
    row = view['catalog'][0]
    assert row['manual_start'] is True and row['manual_stop'] is True
    assert row['state_file'] == 'trpg_corner_state'
    assert view['corners'][row['state_file']]['status'] == 'active'


@pytest.mark.parametrize('action', ['start', 'schedule', 'cancel-schedule'])
def test_webui_uses_real_manual_queue_and_keeps_auto_excluded(setup, action):
    from docich.corner_rotation import CornerRotationManager
    g, _ = setup
    manager = CornerRotationManager(g)
    assert manager._eligible(include_manual_only=False)[1] == {'trpg-corner': 'manual-only'}
    trigger = time.time() + 600
    if action == 'cancel-schedule':
        manager.queue_manual('trpg-view', trigger_at=trigger)
    handler = object.__new__(webui._Handler)
    handler.g, handler.headers = g, {}
    body = {'action': action, 'corner': 'trpg-corner', 'confirm': True, 'trigger_at': trigger}
    handler._read_body = lambda: (json.dumps(body).encode(), None)
    handler._send_json, handler._send_error_json = Mock(), Mock()
    assert handler._handle_post_corners() == 200
    handler._send_error_json.assert_not_called()
    queue = g.state_dir / 'corner_manual_queue.json'
    if action == 'cancel-schedule':
        assert not queue.exists()
    else:
        assert json.loads(queue.read_text())['corner'] == 'trpg-corner'
        assert webui._rotation_view(g)['queued_manual']['corner'] == 'trpg-corner'


@pytest.mark.parametrize('ending', ['completed', 'manual', 'duration'])
def test_real_manager_runs_session_audio_and_restores(setup, monkeypatch, ending):
    # Reuse the existing tested fake process adapter, not a fake manager.
    helper_path = Path(__file__).with_name('test_puyo_program.py')
    spec_import = importlib.util.spec_from_file_location('trpg_fake_processes', helper_path)
    helpers = importlib.util.module_from_spec(spec_import)
    spec_import.loader.exec_module(helpers)
    g, _ = setup
    holder = g.repo_root / 'coordinator'
    holder.mkdir()
    _, now, factory, store, switch = helpers._setup(holder)
    # The coordinator and manager must operate on the same canonical store.
    g.state_dir = store.state_dir if hasattr(store, 'state_dir') else holder / 'run'
    original_factory = switch.adapter_factory
    def factory_with_session(spec):
        adapter = original_factory(spec)
        if spec.game == 'trpg-view':
            original = adapter.preflight
            def preflight(deadline, cancel):
                session.prepare(g, spec, now=now[0])
                original(deadline, cancel)
            adapter.preflight = preflight
        return adapter
    switch.adapter_factory = factory_with_session
    corner = load_catalog(g)[0]
    if ending == 'duration':
        corner = replace(corner, duration_minutes=1)
    owner = TrpgCornerAdapter(g, corner)
    owner.manager.coordinator = switch
    owner.manager.store = store
    owner.manager.clock = lambda: now[0]
    def advance(seconds):
        now[0] += 5 if ending == 'manual' else 60
        if ending == 'manual':
            handler = object.__new__(webui._Handler)
            handler.g, handler.headers = g, {}
            handler._read_body = lambda: (b'{"action":"stop","corner":"trpg-corner","confirm":true}', None)
            handler._send_json, handler._send_error_json = Mock(), Mock()
            assert handler._handle_post_corners() == 200
            handler._send_error_json.assert_not_called()
    owner.manager.sleep = advance
    publishing = Mock()
    monkeypatch.setattr(narration, 'publish', publishing)
    assert owner.run({'request_id': str(uuid.uuid4())}) == 'completed'
    assert publishing.call_count >= (5 if ending == 'completed' else 2)
    assert all(call.kwargs['audio_enabled'] is True for call in publishing.call_args_list)
    state = json.loads(owner.state_path.read_text())
    assert state['end_reason'] == {'completed': 'game-completed', 'manual': 'manual', 'duration': 'duration'}[ending]
    assert store.canonical.load()[0]['active']['game'] == 'robots'
    assert owner.manager.duration_minutes == (1 if ending == 'duration' else 8)
    ledger = core.load_ledger(g.state_dir / 'trpg' / core.LEDGER_FILE)
    assert ledger['entries'][0]['outcome'] == {'completed': 'completed', 'manual': 'aborted', 'duration': 'timeout'}[ending]
    from docich.corner_rotation import CornerRotationManager
    retired = object.__new__(CornerRotationManager)
    retired.g, retired.adapters = g, {}
    old = {'id': 'trpg-corner', 'adapter': 'trpg', 'game': 'trpg-view'}
    observer = retired._observers({'known_corners': {'trpg-corner': old}})['trpg-corner']
    assert list(observer.observations())[0]['status'] == 'completed'


def test_audio_disabled_and_sink_failures_are_best_effort(setup):
    g, _ = setup
    speech = Mock()
    overlay = Mock(side_effect=ValueError('fixture failure'))
    result = narration.publish(g, kind='intro', title=narration.TITLE,
                               overlay_body=narration.intro_text(), speech_text=narration.intro_text(),
                               request_id='fixture', audio_enabled=False, overlay=overlay, speech=speech)
    speech.assert_not_called()
    assert result['speech']['skipped'] == 1 and result['overlay']['failed'] == 1


@pytest.mark.parametrize('bad', [0, 1, 'yes'])
def test_audio_opt_in_requires_boolean(setup, bad):
    g, _ = setup
    text = g.config_path.read_text().replace('audio_enabled=true', f'audio_enabled={json.dumps(bad)}')
    g.config_path.write_text(text)
    with pytest.raises(ValueError):
        load_catalog(g)
