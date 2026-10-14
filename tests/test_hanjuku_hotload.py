"""#1469: stable Hanjuku hot-load trampoline, generation publication, gate.

The agent and the corner are long-lived processes.  These tests pin the
contract that lets them move to a newly deployed Hanjuku terminal logic
*without* a restart, prove that they did, and keep the irreversible stall
teardown out of a mixed-generation state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_bot, hanjuku_hotload as hotload, hanjuku_run
from docich.adapters.base import AdapterError
from docich.game_switch import atomic_write_json
from docich.naming import runtime_directory

IDENTITY = {
    'game': 'hanjuku-hero', 'runtime_id': 'g7-a1b2c3',
    'generation': 7, 'lease_id': 'lease-hotload',
}
OTHER_GENERATION = 'f' * 64


def _runtime(tmp_path):
    return tmp_path / 'runtimes' / IDENTITY['runtime_id']


def _write_record(runtime_dir, identity, role, generation):
    atomic_write_json(hotload.record_path(runtime_dir, role), {
        'schema': hotload.RECORD_SCHEMA, **identity, 'role': role,
        'logic_generation': generation, 'bot_version': 'test', 'at': 1.0, 'pid': 1,
    })


def _fixture_sources(tmp_path, value):
    bot = tmp_path / 'hanjuku_bot.py'
    run = tmp_path / 'hanjuku_run.py'
    bot.write_text(f"VALUE = '{value}'\n", encoding='utf-8')
    run.write_text("from .hanjuku_bot import VALUE\nRESULT = VALUE\n", encoding='utf-8')
    return {'hanjuku_bot': bot, 'hanjuku_run': run}


# --- generation -------------------------------------------------------------

def test_generation_is_the_digest_of_the_logic_sources():
    assert hotload.LOGIC_MODULES == ('hanjuku_bot', 'hanjuku_run')
    assert hotload.deployed_generation() == hotload.digest_sources(hotload.logic_sources())
    # Every logic module records the generation it actually loaded, which is how
    # the trampoline tells the process' own copy apart from a redeployed one
    # regardless of import order.
    assert hanjuku_bot.LOGIC_GENERATION == hotload.deployed_generation()
    assert hanjuku_run.LOGIC_GENERATION == hotload.deployed_generation()


def test_digest_follows_the_source_bytes(tmp_path):
    sources = _fixture_sources(tmp_path, 'one')
    first = hotload.digest_sources(sources)
    # Same length, same second: the digest must still move (that is exactly the
    # case a stale __pycache__ entry would hide).
    sources = _fixture_sources(tmp_path, 'two')
    assert hotload.digest_sources(sources) != first


# --- versioned fresh load ---------------------------------------------------

def test_fresh_load_is_versioned_isolated_and_does_not_touch_the_process(tmp_path):
    first = hotload._fresh('ignored', _fixture_sources(tmp_path, 'one'))
    assert first.generation == hotload.digest_sources(
        {'hanjuku_bot': tmp_path / 'hanjuku_bot.py',
         'hanjuku_run': tmp_path / 'hanjuku_run.py'})
    assert first.modules['hanjuku_run'].RESULT == 'one'

    second = hotload._fresh('ignored', _fixture_sources(tmp_path, 'two'))
    assert second.generation != first.generation
    assert second.modules['hanjuku_run'].RESULT == 'two'
    assert second.modules['hanjuku_bot'].VALUE == 'two'
    # The already loaded generation keeps the code it was loaded with.
    assert first.modules['hanjuku_run'].RESULT == 'one'

    # The process' own modules (and sys.modules) are restored untouched.
    assert sys.modules['docich.hanjuku_run'] is hanjuku_run
    assert sys.modules['docich.hanjuku_bot'] is hanjuku_bot


def test_failed_fresh_load_restores_sys_modules(tmp_path):
    sources = _fixture_sources(tmp_path, 'one')
    (tmp_path / 'hanjuku_run.py').write_text("raise RuntimeError('boom')\n", encoding='utf-8')

    with pytest.raises(RuntimeError, match='boom'):
        hotload._fresh('ignored', sources)

    assert sys.modules['docich.hanjuku_run'] is hanjuku_run
    assert sys.modules['docich.hanjuku_bot'] is hanjuku_bot


def test_the_process_own_modules_are_used_while_they_are_the_deployed_generation():
    handle = hotload.handle()
    assert handle.modules['hanjuku_run'] is hanjuku_run
    assert handle.modules['hanjuku_bot'] is hanjuku_bot
    assert hotload.adopted_generation() == hotload.deployed_generation()


def test_trampoline_delegates_to_the_loaded_module(monkeypatch):
    calls = []

    def terminal(runtime_dir, identity):
        calls.append((runtime_dir, identity))
        return {'ok': True}

    monkeypatch.setattr(hanjuku_run, 'terminal', terminal)
    assert hotload.terminal(Path('/run/x'), IDENTITY) == {'ok': True}
    assert calls == [(Path('/run/x'), IDENTITY)]


# --- durable publication ----------------------------------------------------

def test_publication_binds_the_runtime_identity_role_and_generation(tmp_path):
    runtime = _runtime(tmp_path)
    published = hotload.publish(runtime, IDENTITY, 'corner', now=1000.0, refresh=True)

    assert published['schema'] == hotload.RECORD_SCHEMA
    assert published['role'] == 'corner'
    assert published['logic_generation'] == hotload.adopted_generation()
    assert published['bot_version'] == hanjuku_bot.BOT_VERSION
    assert (published['game'], published['runtime_id'], published['generation'],
            published['lease_id']) == ('hanjuku-hero', IDENTITY['runtime_id'], 7, IDENTITY['lease_id'])
    assert hotload.adoption(runtime, IDENTITY, 'corner') == published
    assert hotload.adoption(runtime, IDENTITY, 'agent') is None


def test_publication_is_rate_limited_but_always_rewritable(tmp_path):
    runtime = _runtime(tmp_path)
    assert hotload.publish(runtime, IDENTITY, 'agent', now=1000.0, refresh=True) is not None
    assert hotload.publish(runtime, IDENTITY, 'agent', now=1000.5) is None
    assert hotload.publish(runtime, IDENTITY, 'agent', now=1000.5, refresh=True) is not None


@pytest.mark.parametrize('identity', [
    {'game': 'robots', 'runtime_id': 'g7-a1b2c3', 'generation': 7, 'lease_id': 'l'},
    {'game': 'hanjuku-hero', 'runtime_id': '', 'generation': 7, 'lease_id': 'l'},
    {'game': 'hanjuku-hero', 'runtime_id': 'g7-a1b2c3', 'generation': 0, 'lease_id': 'l'},
    {'game': 'hanjuku-hero', 'runtime_id': 'g7-a1b2c3', 'generation': 7, 'lease_id': ''},
    {'game': 'hanjuku-hero', 'runtime_id': 'g7-a1b2c3', 'generation': 7},
    {'game': 'hanjuku-hero', 'runtime_id': 'g7-a1b2c3', 'generation': 7, 'lease_id': 'l', 'extra': 1},
])
def test_malformed_identity_is_refused(tmp_path, identity):
    with pytest.raises(AdapterError, match='identity is malformed'):
        hotload.publish(tmp_path, identity, 'agent')


def test_unknown_role_is_refused(tmp_path):
    with pytest.raises(AdapterError, match='role'):
        hotload.publish(tmp_path, IDENTITY, 'observer')


def test_a_foreign_runtime_record_is_refused(tmp_path):
    runtime = _runtime(tmp_path)
    hotload.publish(runtime, IDENTITY, 'agent', refresh=True)
    with pytest.raises(AdapterError, match='identity mismatch'):
        hotload.adoption(runtime, {**IDENTITY, 'generation': 8}, 'agent')


def test_a_symlinked_record_is_refused(tmp_path):
    target = tmp_path / 'elsewhere.json'
    target.write_text('{}', encoding='utf-8')
    hotload.record_path(tmp_path, 'agent').symlink_to(target)

    with pytest.raises(AdapterError, match='symlink'):
        hotload.publish(tmp_path, IDENTITY, 'agent', refresh=True)


def test_publication_from_an_observation_uses_the_observed_runtime(tmp_path):
    runtime = _runtime(tmp_path)
    obs = SimpleNamespace(meta={'runtime_dir': str(runtime),
                                'hanjuku': dict(IDENTITY, phase='field')})

    record = hotload.publish_observation(obs, 'agent')

    assert record['role'] == 'agent' and record['runtime_id'] == IDENTITY['runtime_id']
    assert hotload.publish_observation(SimpleNamespace(meta={}), 'agent') is None
    assert hotload.publish_observation(SimpleNamespace(meta={'runtime_dir': str(runtime)}), 'agent') is None


# --- fail-closed gate -------------------------------------------------------

def test_consistency_states(tmp_path):
    runtime = _runtime(tmp_path)
    assert hotload.consistency(runtime, IDENTITY)['state'] == 'unestablished'

    hotload.publish(runtime, IDENTITY, 'corner', refresh=True)
    assert hotload.consistency(runtime, IDENTITY)['state'] == 'unestablished'

    _write_record(runtime, IDENTITY, 'agent', OTHER_GENERATION)
    status = hotload.consistency(runtime, IDENTITY)
    assert status['state'] == 'mixed'
    assert status['agent'] == OTHER_GENERATION
    assert status['corner'] == hotload.adopted_generation()

    _write_record(runtime, IDENTITY, 'agent', hotload.adopted_generation())
    status = hotload.consistency(runtime, IDENTITY)
    assert status['state'] == 'consistent' and status['deployed'] == hotload.deployed_generation()


def test_mixed_generation_fails_the_stall_teardown_closed(tmp_path):
    runtime = _runtime(tmp_path)
    # The boundary was never established: the pre-existing semantics stand.
    assert hotload.generation_gate(runtime, IDENTITY, 'screen_stalled')[0]

    hotload.publish(runtime, IDENTITY, 'corner', refresh=True)
    _write_record(runtime, IDENTITY, 'agent', OTHER_GENERATION)

    allowed, status = hotload.generation_gate(runtime, IDENTITY, 'screen_stalled')
    assert not allowed and status['state'] == 'mixed'

    # Policy-independent terminals are never gated.
    for reason in ('game_over', 'input_stalled', None):
        assert hotload.generation_gate(runtime, IDENTITY, reason)[0]

    _write_record(runtime, IDENTITY, 'agent', hotload.adopted_generation())
    assert hotload.generation_gate(runtime, IDENTITY, 'screen_stalled')[0]


# --- corner integration -----------------------------------------------------

from test_hanjuku_retro_registration import manager  # noqa: E402


def test_corner_holds_a_stall_teardown_until_both_observers_agree(manager, monkeypatch):
    from docich import hanjuku_narration, hanjuku_predictions
    from docich.agent import fence as fence_module

    state = {'status': 'active', 'game': 'hanjuku-hero', 'previous_game': 'sorengame',
             'bot_identity': dict(IDENTITY)}
    runtime = runtime_directory(manager.g.state_dir, IDENTITY['runtime_id'])
    runtime.mkdir(parents=True, exist_ok=True)
    # The agent is already on a different generation than this corner.
    _write_record(runtime, IDENTITY, 'agent', OTHER_GENERATION)

    stall = {'phase': 'field', 'terminal_reason': 'screen_stalled',
             'actions_sent': 6, 'unchanged_seconds': 300}
    observations = [dict(stall), dict(stall)]
    calls = []

    def observe():
        calls.append(True)
        if len(calls) == 2:  # the agent catches up to the deployed generation
            _write_record(runtime, IDENTITY, 'agent', hotload.adopted_generation())
        return SimpleNamespace(meta={'hanjuku': observations.pop(0)})

    manager.store.canonical.load = Mock(return_value=({'active': IDENTITY}, False))
    monkeypatch.setattr(fence_module, 'shared_section', lambda root, fn: fn())
    monkeypatch.setattr('docich.adapters.make_adapter',
                        lambda *a, **kw: SimpleNamespace(observe=observe))
    monkeypatch.setattr('docich.hanjuku_predictions.tick', lambda *a, **kw: None)
    monkeypatch.setattr(hanjuku_narration, 'consider', lambda *a, **kw: None)
    monkeypatch.setattr(hotload, 'terminal', Mock(return_value={
        'terminal_reason': 'screen_stalled', 'terminal_evidence': 'no_input_sent_while_observing',
        'generation': IDENTITY['generation']}))
    monkeypatch.setattr(manager, '_rotation_stop_result', lambda: None)
    monkeypatch.setattr(manager, '_read_state', lambda: dict(state))
    monkeypatch.setattr(manager, '_write_state', lambda update: state.update(update))
    monkeypatch.setattr(manager, '_repair_active_agent', Mock())
    sleep = Mock()
    monkeypatch.setattr(manager, '_sleep', sleep)
    finish = Mock(return_value='restored')
    monkeypatch.setattr(manager, '_finish_locked', finish)

    assert manager._wait_hanjuku(dict(state)) == 'restored'

    # The first observation held the teardown; the second, with both observers on
    # one generation, restored as before.
    assert finish.call_count == 1
    assert sleep.call_count == 1
    events = [json.loads(line) for line in
              (runtime / 'hanjuku_events.jsonl').read_text(encoding='utf-8').splitlines()]
    held = [event for event in events if event.get('event') == 'hotload_generation_hold']
    assert len(held) == 1 and held[0]['state'] == 'mixed'
    assert held[0]['terminal_reason'] == 'screen_stalled'
    assert state['end_reason'] == 'screen_stalled'
    assert state['hotload']['state'] == 'consistent'


def test_corner_generation_timeout_is_bounded(manager, monkeypatch):
    """A mixed state that never converges must not wedge the corner forever."""
    from docich import hanjuku_narration
    from docich.agent import fence as fence_module

    state = {'status': 'active', 'game': 'hanjuku-hero', 'previous_game': 'sorengame',
             'bot_identity': dict(IDENTITY)}
    runtime = runtime_directory(manager.g.state_dir, IDENTITY['runtime_id'])
    runtime.mkdir(parents=True, exist_ok=True)
    _write_record(runtime, IDENTITY, 'agent', OTHER_GENERATION)

    clock = {'now': 1000.0}
    monkeypatch.setattr('docich.retro_corner.time.monotonic', lambda: clock['now'])
    stall = {'phase': 'field', 'terminal_reason': 'screen_stalled',
             'actions_sent': 6, 'unchanged_seconds': 300}
    seen = []

    def observe():
        clock['now'] += 0.5
        seen.append(True)
        return SimpleNamespace(meta={'hanjuku': dict(stall)})

    manager.store.canonical.load = Mock(return_value=({'active': IDENTITY}, False))
    monkeypatch.setattr(fence_module, 'shared_section', lambda root, fn: fn())
    monkeypatch.setattr('docich.adapters.make_adapter',
                        lambda *a, **kw: SimpleNamespace(observe=observe))
    monkeypatch.setattr('docich.hanjuku_predictions.tick', lambda *a, **kw: None)
    monkeypatch.setattr(hanjuku_narration, 'consider', lambda *a, **kw: None)
    monkeypatch.setattr(hotload, 'terminal', Mock(return_value={
        'terminal_reason': 'screen_stalled', 'terminal_evidence': 'no_input_sent_while_observing',
        'generation': IDENTITY['generation']}))
    monkeypatch.setattr(manager, '_rotation_stop_result', lambda: None)
    monkeypatch.setattr(manager, '_read_state', lambda: dict(state))
    monkeypatch.setattr(manager, '_write_state', lambda update: state.update(update))
    monkeypatch.setattr(manager, '_repair_active_agent', Mock())
    sleep = Mock(side_effect=lambda _seconds: clock.update(now=clock['now'] + 2.0))
    monkeypatch.setattr(manager, '_sleep', sleep)
    finish = Mock(return_value='restored')
    monkeypatch.setattr(manager, '_finish_locked', finish)

    assert manager._wait_hanjuku(dict(state)) == 'restored'

    assert len(seen) > 20  # held for the whole grace, then converged on teardown
    events = [json.loads(line) for line in
              (runtime / 'hanjuku_events.jsonl').read_text(encoding='utf-8').splitlines()]
    kinds = [event.get('event') for event in events]
    assert 'hotload_generation_hold' in kinds
    assert kinds[-1] == 'hotload_generation_timeout'
    assert finish.call_count == 1
