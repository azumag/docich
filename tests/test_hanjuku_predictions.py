"""No real Twitch calls, game processes, tokens, ROMs or production writes."""
from __future__ import annotations

import copy
from dataclasses import replace
import io
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error

import pytest

from docich import hanjuku_predictions as p, hanjuku_progress as progress
from docich import hanjuku_prediction_api as api
from docich.config import load_global
from docich.game_switch import atomic_write_json
from docich.retroarch_boundary import read_record

ROOT = Path(__file__).resolve().parents[1]
ID = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef', 'generation': 1, 'lease_id': 'lease1'}
ID2 = {**ID, 'runtime_id': 'g2-abcdef', 'generation': 2, 'lease_id': 'lease2'}
DIGEST = 'a' * 64


class FakeAPI:
    def __init__(self):
        self.rows = []
        self.calls = []
        self.failure = None
        self.after_create = None
        self.before_list_return = None

    def list(self, prediction_id=None):
        self.calls.append(('GET', prediction_id))
        if self.failure:
            raise self.failure
        if self.before_list_return:
            self.before_list_return()
        return copy.deepcopy([r for r in self.rows if not prediction_id or r['id'] == prediction_id])

    def create(self, title, outcomes, window):
        self.calls.append(('POST', title, tuple(outcomes), window))
        row = {'id': f'prediction-{len(self.rows)}', 'title': title, 'status': 'ACTIVE',
               'outcomes': [{'id': f'outcome-{i}', 'title': title} for i, title in enumerate(outcomes)]}
        self.rows.append(row)
        if self.after_create:
            raise self.after_create
        return [copy.deepcopy(row)]

    def end(self, prediction_id, status, outcome_id=None):
        self.calls.append(('PATCH', prediction_id, status, outcome_id))
        if self.failure:
            raise self.failure
        row = next(r for r in self.rows if r['id'] == prediction_id)
        row['status'] = status
        if outcome_id is not None:
            row['winning_outcome_id'] = outcome_id
        return [copy.deepcopy(row)]


@pytest.fixture
def env(tmp_path):
    g = replace(load_global(ROOT, ROOT / 'config/docich.soren-live.toml'), state_dir=tmp_path / 'run')
    remote = FakeAPI()
    def setup(identity=ID, chapter=1):
        runtime = g.state_dir / 'runtimes' / identity['runtime_id']
        runtime.mkdir(parents=True, exist_ok=True)
        atomic_write_json(g.state_dir / 'game_switch.json', {'phase': 'ready', 'active': identity})
        atomic_write_json(runtime / 'hanjuku_run.json', {**identity, 'schema': 1})
        if chapter is not None:
            progress.record(runtime, identity, {'chapter': chapter, 'chapter_evidence': 'header'}, [], DIGEST)
        return runtime
    runtime = setup()
    tick = lambda now=100, identity=ID: p.tick(g, identity, now=now, client_factory=lambda _: (remote, None))
    state = lambda: read_record(g.state_dir / p.FILE)
    return SimpleNamespace(g=g, remote=remote, runtime=runtime, setup=setup, tick=tick, state=state)


def terminal(env, reason='game_over', identity=ID):
    runtime = env.g.state_dir / 'runtimes' / identity['runtime_id']
    row = {**identity, 'schema': 1, 'terminal_reason': reason, 'frame_sha256': DIGEST,
           'name_entered': True, 'gameplay_seen': True, 'phase': 'title', 'title_count': 3,
           'observed_monotonic': 10, 'title_since': 7, 'unchanged_seconds': 300}
    atomic_write_json(runtime / 'hanjuku_run.json', row)


@pytest.mark.parametrize('best, expected', [(1,(2,1)), (2,(3,1)), (3,(4,2)), (4,(5,2)), (5,(6,3)), (11,(12,6))])
def test_thresholds(best, expected):
    assert tuple(p.thresholds(best).values()) == expected
    assert p.thresholds(12) is None


@pytest.mark.parametrize('bad', [True, False, -1, 13, '1', 1.5, None])
def test_no_count_coercion(bad):
    with pytest.raises(ValueError):
        p.thresholds(bad)


def test_outcomes_cover_disjoint_ranges():
    for target in range(2, 13):
        middle = target // 2
        expected = [2] * middle + [1] * (target - middle) + [0] * (13 - target)
        assert [p.winner(i, target, middle) for i in range(13)] == expected
    assert p.outcome_titles(2,1) == ['2話突破（新記録）', '1話突破', '1話突破できず']
    assert p.outcome_titles(6,3) == ['6話突破（新記録）', '3〜5話突破', '3話突破できず']


def test_reach_two_is_one_clear_and_monotonic(env):
    progress.record(env.runtime, ID, {'chapter': 2, 'chapter_evidence': 'ピオーネ'}, [], DIGEST)
    progress.record(env.runtime, ID, {'chapter': 1}, [], 'b'*64)
    assert progress.completed_count(env.runtime, ID) == 1
    assert progress.read(env.runtime, ID)['frame_sha256'] == DIGEST


@pytest.mark.parametrize('policy', [{'chapter': 2}, {'chapter': '2'}, {'chapter': True},
                                  {'chapter': 2, 'chapter_evidence': 'アルマムーン'},
                                  {'year': 6, 'month': 3, 'boss_defeated': 1}])
def test_unverified_chapter_is_unknown(policy):
    assert progress.policy_cleared(policy) is None


def test_only_classified_final_boss_win_can_clear_twelve(env):
    policy = {'chapter': 12, 'chapter_evidence': 'header'}
    progress.record(env.runtime, ID, policy, [{'outcome': 'win', 'enemy': 'クーモン'}], DIGEST)
    assert progress.completed_count(env.runtime, ID) == 11
    progress.record(env.runtime, ID, policy, [{'decision': 'battle_result', 'outcome': 'win',
                    'enemy': 'クーモン', 'resulting_event': 'chapter_12_boss_defeated'}], DIGEST)
    assert progress.completed_count(env.runtime, ID) == 12


def test_new_game_in_same_generation_is_ambiguous(env):
    progress.record(env.runtime, ID, {'chapter': 1}, [{'decision': 'new_game_detected'}], DIGEST)
    assert progress.completed_count(env.runtime, ID) is None


def test_initial_create_and_no_duplicate_after_restart(env):
    assert env.tick()['mode'] == 'active'
    state = env.state()
    assert state['round']['target'] == 2 and state['round']['middle'] == 1
    assert state['round']['window'] == 120
    assert len(state['round']['title']) <= 45
    for t in (101, 130, 160, 190):
        assert env.tick(t)['mode'] == 'active'
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1


@pytest.mark.parametrize('status', ['ACTIVE', 'LOCKED'])
def test_existing_unrelated_remote_is_never_modified(env, status):
    env.remote.rows = [{'id': 'other', 'title': '他のゲーム', 'status': status, 'outcomes': []}]
    for t in (100, 130, 160):
        assert env.tick(t)['mode'] == 'blocked'
    assert all(c[0] == 'GET' for c in env.remote.calls)
    env.remote.rows[0]['status'] = 'RESOLVED'
    assert env.tick(190)['mode'] == 'active'


@pytest.mark.parametrize('reason', ['game_over', 'screen_stalled'])
@pytest.mark.parametrize('chapter, index', [(1,2), (2,1), (3,0), (7,0)])
def test_terminal_uses_cleared_maximum_and_frozen_thresholds(env, reason, chapter, index):
    env.tick()
    progress.record(env.runtime, ID, {'chapter': chapter, 'chapter_evidence': 'header'}, [], DIGEST)
    terminal(env, reason)
    # New terminal bypasses the normal 30-second API cooldown.
    assert env.tick(101)['mode'] == 'resolved'
    row = env.state()['round']
    assert (row['target'], row['middle']) == (2,1)
    assert env.remote.rows[0]['winning_outcome_id'] == f'outcome-{index}'
    assert row['result']['cleared'] == chapter-1 and row['result']['reason'] == reason
    assert env.state()['best_cleared'] == max(1, chapter-1)
    assert read_record(env.runtime / p.RESULT_FILE)['cleared'] == chapter-1
    env.tick(131)
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1
    assert len([c for c in env.remote.calls if c[0] == 'PATCH' and c[2] == 'RESOLVED']) == 1


def test_next_run_uses_new_record(env):
    env.tick()
    progress.record(env.runtime, ID, {'chapter': 6, 'chapter_evidence': 'header'}, [], DIGEST)
    terminal(env, 'screen_stalled')
    env.tick(101)
    env.setup(ID2)
    env.tick(140, ID2)
    row = env.state()['round']
    assert (row['target'], row['middle']) == (6,3)


def test_api_outage_persists_result_and_common_tick_retries_after_teardown(env):
    env.tick()
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    terminal(env, 'screen_stalled')
    env.remote.failure = api.APIError('transport')
    assert env.tick(101)['error'] == 'transport'
    assert env.state()['round']['result']['cleared'] == 2
    assert env.state()['best_cleared'] == 2
    env.setup(ID2)
    env.remote.failure = None
    assert env.tick(401, None)['mode'] == 'resolved'
    assert env.remote.rows[0]['winning_outcome_id'] == 'outcome-0'


def test_unknown_score_refunds_instead_of_guessing_zero(env):
    (env.runtime / progress.FILE).unlink()
    env.tick()
    terminal(env, 'screen_stalled')
    assert env.tick(101)['mode'] == 'canceled'
    assert not any(c[0] == 'PATCH' and c[2] == 'RESOLVED' for c in env.remote.calls)
    assert env.state()['round']['result']['cleared'] is None


def test_wrong_lease_or_bad_terminal_never_settles(env):
    env.tick()
    terminal(env)
    path = env.runtime / 'hanjuku_run.json'
    evidence = read_record(path)
    for override in ({'lease_id':'wrong'}, {'title_count':1}, {'frame_sha256':'bad'}):
        atomic_write_json(path, {**evidence, **override})
        assert env.tick(131)['mode'] == 'error'
    assert not any(c[0] == 'PATCH' for c in env.remote.calls)
    assert not (env.runtime / p.RESULT_FILE).exists()


def test_read_failure_does_not_mean_no_prediction(env):
    env.remote.failure = api.APIError('auth')
    assert env.tick()['error'] == 'auth'
    assert not any(c[0] == 'POST' for c in env.remote.calls)
    assert env.state()['round'] is None


def test_create_timeout_reconciles_exact_owned_intent_without_duplicate(env):
    env.remote.after_create = api.APIError('transport')
    assert env.tick()['error'] == 'transport'
    assert env.state()['round']['attempted'] is True
    env.remote.after_create = None
    assert env.tick(400)['mode'] == 'active'
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1
    assert env.state()['round']['id'] == 'prediction-0'


def test_ambiguous_create_without_remote_never_reposts(env):
    def fail(*args):
        raise api.APIError('transport')
    env.remote.create = fail
    env.tick()
    assert env.tick(400)['error'] == 'create_unknown'
    assert env.state()['round']['attempted'] is True


def test_rejected_create_may_retry_but_not_after_result_is_known(env):
    def fail(*args):
        raise api.APIError('rate_limited', rejected=True)
    env.remote.create = fail
    env.tick()
    assert env.state()['round']['attempted'] is False
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    assert env.tick(400)['mode'] == 'known_result'


def test_create_rechecks_owner_after_remote_get(env):
    env.remote.before_list_return = lambda: atomic_write_json(
        env.g.state_dir / 'game_switch.json', {'phase':'ready', 'active': ID2})
    assert env.tick()['mode'] == 'pending'
    assert not any(c[0] == 'POST' for c in env.remote.calls)


def test_manual_interruption_only_cancels_owned_prediction(env):
    env.tick()
    env.setup(ID2)
    assert env.tick(131, None)['mode'] == 'canceled'
    assert not (env.runtime / p.RESULT_FILE).exists()


def test_remote_canceled_owned_prediction_is_not_recreated_in_same_run(env):
    env.tick()
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1
    env.remote.rows[0]['status'] = 'CANCELED'
    assert env.tick(131)['mode'] == 'canceled'
    assert env.tick(161)['mode'] == 'canceled'
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1


def test_global_retry_never_creates_prediction(env):
    assert env.tick(100, None)['mode'] == 'idle'
    assert env.remote.calls == []
    assert not (env.g.state_dir / p.FILE).exists()


def test_foreign_game_or_unready_canonical_cannot_create(env):
    atomic_write_json(env.g.state_dir / 'game_switch.json', {'phase':'switching', 'active':ID})
    env.tick()
    assert not any(c[0] == 'POST' for c in env.remote.calls)
    atomic_write_json(env.g.state_dir / 'game_switch.json', {'phase':'ready', 'active':ID2})
    env.tick(131)
    assert not any(c[0] == 'POST' for c in env.remote.calls)


def test_top_outcome_already_known_does_not_open_betting(env):
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    assert env.tick()['mode'] == 'known_result'
    assert env.remote.calls == []


def test_top_outcome_during_voting_locks_but_waits_for_terminal(env):
    env.tick()
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    env.tick(131)
    assert env.remote.rows[0]['status'] == 'LOCKED'
    assert not (env.runtime / p.RESULT_FILE).exists()


def test_pause_blocks_creation_not_result_recording_or_pending_settlement(env):
    pause = env.g.state_dir / 'hanjuku_predictions.paused'
    pause.touch()
    assert env.tick()['mode'] == 'paused'
    assert env.remote.calls == []
    pause.unlink()
    env.tick(131)
    pause.touch()
    terminal(env)
    assert env.tick(132)['mode'] == 'resolved'
    assert pause.exists()


@pytest.mark.parametrize('unavailable', ['disabled','explore','unconfigured'])
def test_existing_global_twitch_gates_hold_api_but_keep_record(env, unavailable):
    terminal(env)
    result = p.tick(env.g, ID, now=100, client_factory=lambda _: (None, unavailable))
    assert result['mode'] == unavailable
    assert read_record(env.runtime / p.RESULT_FILE)['cleared'] == 0
    assert env.remote.calls == []


def test_invalid_ledger_is_not_overwritten_or_treated_as_empty(env):
    env.g.state_dir.joinpath(p.FILE).write_text('{corrupt')
    assert env.tick()['error'] == 'invalid_state'
    assert env.g.state_dir.joinpath(p.FILE).read_text() == '{corrupt'
    assert env.remote.calls == []


def test_clock_rollback_stops_network(env):
    env.tick()
    before = list(env.remote.calls)
    assert env.tick(99)['error'] == 'clock_regressed'
    assert env.remote.calls == before


def test_legacy_snapshot_requires_identity_and_real_chapter_evidence(env):
    (env.runtime / progress.FILE).unlink()
    path = env.runtime / 'hanjuku_bot.json'
    atomic_write_json(path, {'decision_trace':ID, 'policy':{'chapter':2, 'chapter_evidence':'ピオーネ'}})
    assert progress.completed_count(env.runtime, ID) == 1
    atomic_write_json(path, {'decision_trace':ID2, 'policy':{'chapter':2, 'chapter_evidence':'header'}})
    with pytest.raises(Exception, match='identity'):
        progress.completed_count(env.runtime, ID)


@pytest.mark.parametrize('field,value', [('cleared',True), ('cleared','2'), ('cleared',-1),
                                        ('cleared',13), ('schema',True), ('lease_id','wrong')])
def test_malformed_progress_does_not_settle(env, field, value):
    env.tick()
    row = read_record(env.runtime / progress.FILE)
    atomic_write_json(env.runtime / progress.FILE, {**row, field:value})
    terminal(env)
    assert env.tick(101)['mode'] == 'error'
    assert not any(c[0] == 'PATCH' for c in env.remote.calls)


def test_dotenv_literal_loading_never_executes_and_respects_latest_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'resolve_soren_root', lambda _: tmp_path)
    monkeypatch.setenv('TWITCH_PREDICTIONS_ENABLED', '1')
    monkeypatch.setenv('TWITCH_PREDICTIONS_TOKEN', 'old-secret')
    tmp_path.joinpath('.env').write_text("export TWITCH_PREDICTIONS_ENABLED=0\nTWITCH_PREDICTIONS_TOKEN='new-secret' # comment\nIGNORE=$(touch should-not-exist)\n")
    settings = api.settings(None)
    assert settings['TWITCH_PREDICTIONS_TOKEN'] == 'new-secret'
    assert api.configured_client(None) == (None, 'disabled')
    assert not tmp_path.joinpath('should-not-exist').exists()


def test_transport_is_fixed_host_bounded_no_secrets_in_errors():
    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith(api.ENDPOINT + '?')
            assert timeout == 5
            assert request.get_header('Authorization') == 'Bearer test-secret'
            raise urllib.error.HTTPError(request.full_url, 401, 'test-secret body', {}, None)
    client = api.Helix('test-secret', 'client', 'channel', opener=Opener())
    with pytest.raises(api.APIError) as error:
        client.list()
    assert str(error.value) == 'auth' and 'test-secret' not in str(error.value)
    assert error.value.rejected
    assert api.NoRedirect().redirect_request(None,None,302,'',{},'https://other.example') is None


@pytest.mark.parametrize('body', [b'{}', b'{"data":null}', b'{bad', b'x'*(api.MAX_BYTES+1),
                                 b'{"data":[{"id":"a","title":"b","status":"OTHER","outcomes":[]}]}'])
def test_bad_remote_response_fails_closed(body):
    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(body)
    with pytest.raises(api.APIError):
        api.Helix('test-secret','client','channel',opener=Opener()).list()


def test_diagnostics_is_allowlisted_and_contains_no_titles_or_tokens(env):
    import importlib.util
    spec = importlib.util.spec_from_file_location('prediction_diagnostics_test', ROOT/'ops/vm_actions/collect_diagnostics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env.tick()
    state = env.state()
    state.update(token='not-for-logs', error='not-for-logs')
    state['round']['title'] = 'not-for-logs'
    atomic_write_json(env.g.state_dir/p.FILE, state)
    result = module._collect_hanjuku_predictions(env.g.state_dir)
    assert result['target'] == 2 and result['middle'] == 1 and result['best_cleared'] == 1
    assert 'not-for-logs' not in json.dumps(result)


def test_integration_uses_side_channel_outside_input_gate():
    source = (ROOT/'src/docich/retro_corner.py').read_text()
    loop = source[source.index('    def _wait_hanjuku'):source.index('    def _retry_restoring_tick')]
    assert loop.index('shared_section(self.g.state_dir, adapter.observe)') < loop.index('prediction_tick(self.g, owned_identity)')
    assert loop.index('prediction_tick(self.g, owned_identity)') < loop.index('with self._locked():', loop.index('prediction_tick(self.g, owned_identity)'))
    assert 'prediction_tick(self.g)' in (ROOT/'src/docich/corner_rotation.py').read_text()


def test_saved_settlement_survives_runtime_retention(env):
    import shutil
    env.tick()
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    terminal(env, 'screen_stalled')
    env.remote.failure = api.APIError('transport')
    env.tick(101)
    env.setup(ID2)
    shutil.rmtree(env.runtime)
    env.remote.failure = None
    assert env.tick(401, None)['mode'] == 'resolved'
    assert env.remote.rows[0]['winning_outcome_id'] == 'outcome-0'
    assert env.state()['best_cleared'] == 2


@pytest.mark.parametrize('override', [{'cleared': True}, {'generation': 2}, {'frame_sha256':'bad'},
                                     {'reason':'manual_stop'}, {'schema': True}])
def test_corrupt_saved_result_never_resolves(env, override):
    env.tick()
    terminal(env)
    env.remote.failure = api.APIError('transport')
    env.tick(101)
    state = env.state()
    state['round']['result'].update(override)
    atomic_write_json(env.g.state_dir/p.FILE, state)
    env.remote.failure = None
    assert env.tick(401, None)['error'] == 'invalid_state'
    assert env.state() == state
    assert not any(c[0] == 'PATCH' for c in env.remote.calls)


def test_lock_contention_never_creates_duplicate(env):
    with p.locked(env.g.state_dir) as held:
        assert held
        assert env.tick()['mode'] == 'pending'
    assert env.remote.calls == []


def test_invalid_progress_is_observable_without_losing_round_ownership(env):
    env.tick()
    terminal(env)
    env.runtime.joinpath(progress.FILE).write_text('{bad')
    assert env.tick(101)['error'] == 'invalid_state'
    assert env.state()['error'] == 'invalid_state'
    assert env.state()['round']['id'] == 'prediction-0'
    assert env.runtime.joinpath(progress.FILE).read_text() == '{bad'


def test_production_client_requires_ownership_aware_soren_before_enabling(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'resolve_soren_root', lambda _: tmp_path)
    for key, value in {'TWITCH_PREDICTIONS_ENABLED':'1', 'TWITCH_PREDICTIONS_TOKEN':'test-token',
                       'TWITCH_CLIENT_ID':'test-client', 'TWITCH_BROADCASTER_ID':'test-channel',
                       'EXPLORE_MODE':'0'}.items():
        monkeypatch.setenv(key, value)
    assert api.configured_client(None) == (None, 'incompatible_soren')
    wrapper = tmp_path / 'twitch_predictions.sh'
    wrapper.write_text('#!/bin/bash\n# legacy\n')
    assert api.configured_client(None) == (None, 'incompatible_soren')
    wrapper.write_text('# SOREN_REMOTE_PREDICTION_OWNERSHIP_V1: verified by the companion rollout\n')
    client, reason = api.configured_client(None)
    assert isinstance(client, api.Helix) and reason is None
