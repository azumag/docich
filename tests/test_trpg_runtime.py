"""Manual TRPG integration contracts; no Twitch, processes or real audio."""
from copy import deepcopy
from dataclasses import replace
from functools import partial
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock
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
    adapter.preflight(time.monotonic() + 2, None)
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


def test_chat_cursor_partial_oversize_and_retained_rotation(setup, monkeypatch):
    g, spec = setup
    monkeypatch.setattr(narration, 'publish', Mock())
    session.prepare(g, spec, now=0)
    path, expected = spec.runtime_dir / session.SESSION_FILE, session.identity(spec)
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
