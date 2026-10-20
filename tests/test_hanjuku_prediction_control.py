"""Owner-only fixed control of one stalled Hanjuku prediction round (#2216).

No real Twitch calls, tokens, game processes or production writes.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from docich import hanjuku_prediction_control as control
from docich import hanjuku_prediction_control_check as check
from docich import hanjuku_predictions as p
from docich import hanjuku_progress as progress
from docich.config import load_global
from docich.game_switch import atomic_write_json
from docich.retroarch_boundary import read_record

ROOT = Path(__file__).resolve().parents[1]
ID = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef', 'generation': 1, 'lease_id': 'lease1'}
DIGEST = 'a' * 64
WF = ROOT / ".github/workflows/corner-rotation-operator.yml"
AUTH = ROOT / "ops/vm_actions/authorize_corner_rotation.py"
CONTROL_SCRIPT = ROOT / "ops/vm_actions/control_hanjuku_prediction.sh"
CANONICAL_REF = "azumag/docich/.github/workflows/corner-rotation-operator.yml@refs/heads/main"


class FakeAPI:
    """Minimal Helix double; records only what the operator is allowed to call."""

    def __init__(self):
        self.rows = []
        self.calls = []
        self.failure = None

    def list(self, prediction_id=None):
        self.calls.append(('GET', prediction_id))
        if self.failure:
            raise self.failure
        return copy.deepcopy([r for r in self.rows if not prediction_id or r['id'] == prediction_id])

    def create(self, title, outcomes, window):  # pragma: no cover - must never run
        raise AssertionError('prediction control must never create a round')

    def end(self, prediction_id, status, outcome_id=None):
        self.calls.append(('PATCH', prediction_id, status, outcome_id))
        if self.failure:
            raise self.failure
        row = next(r for r in self.rows if r['id'] == prediction_id)
        row['status'] = status
        if outcome_id is not None:
            row['winning_outcome_id'] = outcome_id
        return [copy.deepcopy(row)]


def _row(prediction_id='prediction-0', status='LOCKED', outcomes=4):
    tokens = p.outcome_titles(2, 1)[:outcomes]
    nonce = 'abcdef123456'
    return {'id': prediction_id, 'title': p._title(nonce), 'status': status,
            'outcomes': [{'id': f'outcome-{i}', 'title': t} for i, t in enumerate(tokens)]}


@pytest.fixture
def env(tmp_path):
    """One durable ledger that owns a live round, built without a real tick.

    The round is written in exactly the shape `_load` accepts, so the operator
    is exercised against the same contract the tick produces.
    """
    g = replace(load_global(ROOT, ROOT / 'config/docich.soren-live.toml'), state_dir=tmp_path / 'run')
    remote = FakeAPI()
    tokens = p.outcome_titles(2, 1)
    nonce = 'abcdef123456'
    remote.rows.append({'id': 'prediction-0', 'title': p._title(nonce), 'status': 'LOCKED',
                        'outcomes': [{'id': f'outcome-{i}', 'title': t}
                                     for i, t in enumerate(tokens)]})
    runtime = g.state_dir / 'runtimes' / ID['runtime_id']
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_write_json(g.state_dir / 'game_switch.json', {'phase': 'ready', 'active': ID})
    atomic_write_json(runtime / 'hanjuku_run.json', {**ID, 'schema': 1})
    atomic_write_json(g.state_dir / p.FILE, {
        'schema': 1, 'best_cleared': 1, 'mode': 'active', 'error': None,
        'next_poll_at': 400, 'last_poll_at': 100,
        'round': {**ID, 'target': 2, 'middle': 1, 'nonce': nonce,
                  'title': p._title(nonce), 'outcome_titles': tokens,
                  'window': 300, 'attempted': True, 'status': 'LOCKED',
                  'id': 'prediction-0',
                  'outcome_ids': [o['id'] for o in remote.rows[0]['outcomes']]}})
    factory = lambda _: (remote, None)
    return SimpleNamespace(g=g, remote=remote, runtime=runtime, factory=factory,
                           state=lambda: read_record(g.state_dir / p.FILE))


def test_cancel_ends_the_owned_round_and_clears_local_ownership(env):
    result = control.cancel(env.g, client_factory=env.factory, now=200)
    assert result == {'operation': 'cancel', 'outcome': 'canceled',
                      'prediction_id': env.remote.rows[0]['id']}
    assert env.remote.rows[0]['status'] == 'CANCELED'
    state = env.state()
    assert state['round'] is None and state['mode'] == 'canceled' and state['error'] is None
    # Never picks a winner and never creates a second round.
    assert ('PATCH', env.remote.rows[0]['id'], 'CANCELED', None) in env.remote.calls
    assert not [c for c in env.remote.calls if c[0] == 'POST']


def test_cancel_is_idempotent_when_the_remote_no_longer_has_the_round(env):
    env.remote.rows.clear()
    result = control.cancel(env.g, client_factory=env.factory, now=200)
    assert result['outcome'] == 'remote_absent'
    assert env.state()['round'] is None
    assert not [c for c in env.remote.calls if c[0] == 'PATCH']


def test_cancel_refuses_when_there_is_no_live_round(env):
    state = env.state()
    state['round'] = None
    atomic_write_json(env.g.state_dir / p.FILE, state)
    with pytest.raises(control.ControlRefused) as exc:
        control.cancel(env.g, client_factory=env.factory, now=200)
    assert exc.value.code == 'no_round'


def test_cancel_refuses_an_intent_that_never_reached_twitch(env):
    state = env.state()
    state['round'].update(id=None, outcome_ids=None, status='INTENT')
    atomic_write_json(env.g.state_dir / p.FILE, state)
    with pytest.raises(control.ControlRefused) as exc:
        control.cancel(env.g, client_factory=env.factory, now=200)
    assert exc.value.code == 'round_not_remote'


def test_control_recovers_a_round_the_tick_ledger_rejects(env):
    """A durable result the strict loader refuses must still be recoverable.

    `_load` is deliberately fail-closed, so a malformed durable `result` makes
    the tick spin in `error=invalid_state` with the fixed 300 s retry and the
    round can neither settle nor cancel (#2216). The owner-only operator must
    still be able to end the exact round the ledger owns, without the strict
    loader having to be loosened for the tick.
    """
    state = env.state()
    # PR #2213 closed the reason-set mismatch; the remaining reachable class is
    # a result record that violates the durable result contract.
    state['round']['result'] = {'schema': 1, **ID, 'reason': 'input_stalled',
                                'frame_sha256': 'not-a-digest', 'cleared': None}
    atomic_write_json(env.g.state_dir / p.FILE, state)
    with pytest.raises(ValueError):
        p._load(env.g.state_dir, 1)  # the tick's strict loader still refuses
    assert p._load_recoverable(env.g.state_dir, 1)['round']['status'] == 'LOCKED'

    # A genuinely corrupt ledger is still refused, and never rewritten.
    report = check.inspect(env.g, 'cancel', now=200)
    assert report['actionable'] is True and report['reason'] == 'reviewed_round_live'
    result = control.cancel(env.g, client_factory=env.factory, now=200)
    assert result['outcome'] == 'canceled'
    assert env.state()['round'] is None


def test_load_recoverable_stays_strict_about_ownership(env):
    """Only the result contract is relaxed; ownership stays strictly checked."""
    state = env.state()
    state['round']['outcome_ids'] = ['only-one']
    atomic_write_json(env.g.state_dir / p.FILE, state)
    with pytest.raises(ValueError):
        p._load_recoverable(env.g.state_dir, 1)
    state = env.state()
    state['round']['title'] = 'forged title'
    atomic_write_json(env.g.state_dir / p.FILE, state)
    with pytest.raises(ValueError):
        p._load_recoverable(env.g.state_dir, 1)


def test_control_refuses_a_genuinely_unreadable_ledger_without_rewriting_it(env):
    path = env.g.state_dir / p.FILE
    path.write_text(json.dumps({'schema': 1, 'mode': 'active', 'error': None,
                                'best_cleared': 1, 'round': {'target': 'bad'}}))
    before = path.read_bytes()
    with pytest.raises(control.ControlRefused) as exc:
        control.cancel(env.g, client_factory=env.factory, now=200)
    assert exc.value.code == 'ledger_unreadable'
    assert path.read_bytes() == before  # never repaired, rewritten or discarded


def test_cancel_refuses_a_round_the_remote_already_resolved(env):
    """A finished prediction cannot be canceled; sync is the correct operation."""
    env.remote.rows[0]['status'] = 'RESOLVED'
    env.remote.rows[0]['winning_outcome_id'] = 'outcome-0'
    with pytest.raises(control.ControlRefused) as exc:
        control.cancel(env.g, client_factory=env.factory, now=200)
    assert exc.value.code == 'remote_resolved_use_sync'
    # No PATCH was attempted at all, and ownership was left intact for the sync.
    assert not [c for c in env.remote.calls if c[0] == 'PATCH']
    assert env.state()['round'] is not None


def test_sync_clears_ownership_the_remote_already_finished(env):
    env.remote.rows[0]['status'] = 'RESOLVED'
    env.remote.rows[0]['winning_outcome_id'] = 'outcome-0'
    result = control.sync(env.g, client_factory=env.factory, now=200)
    assert result['outcome'] == 'resolved'
    state = env.state()
    assert state['round'] is None and state['mode'] == 'resolved'


def test_sync_readopts_a_live_round_without_changing_its_selection(env):
    row_before = copy.deepcopy(env.state()['round'])
    env.remote.rows[0]['status'] = 'ACTIVE'
    result = control.sync(env.g, client_factory=env.factory, now=200)
    assert result['outcome'] == 'adopted'
    row = env.state()['round']
    assert row['status'] == 'ACTIVE'
    for key in ('target', 'middle', 'outcome_titles', 'nonce', 'window'):
        assert row[key] == row_before[key]


def test_sync_clears_ownership_when_the_remote_round_is_gone(env):
    env.remote.rows.clear()
    result = control.sync(env.g, client_factory=env.factory, now=200)
    assert result['outcome'] == 'remote_absent'
    assert env.state()['round'] is None


def test_sync_never_patches_or_creates(env):
    control.sync(env.g, client_factory=env.factory, now=200)
    assert [c[0] for c in env.remote.calls] == ['GET']


def test_control_refuses_an_unsupported_operation(env):
    with pytest.raises(control.ControlRefused) as exc:
        control.run(env.g, 'resolve')
    assert exc.value.code == 'unsupported_operation'
    with pytest.raises(ValueError):
        check.inspect(env.g, 'create')


def test_control_refuses_when_the_lock_is_held(env):
    import fcntl, os
    lock = env.g.state_dir / 'locks' / 'hanjuku-predictions.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(control.ControlRefused) as exc:
            control.cancel(env.g, client_factory=env.factory, now=200)
        assert exc.value.code == 'busy'
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_check_reports_a_bounded_projection_without_mutating(env):
    before = (env.g.state_dir / p.FILE).read_bytes()
    report = check.inspect(env.g, 'sync', now=200)
    assert report['actionable'] is True and report['status'] == 'LOCKED'
    assert report['target'] == 2 and report['middle'] == 1
    assert isinstance(report['retry_delay_seconds'], int)
    assert (env.g.state_dir / p.FILE).read_bytes() == before
    # No token, remote id or viewer material crosses the boundary.
    assert env.remote.rows[0]['id'] not in json.dumps(report)


def test_check_refuses_a_terminal_round(env):
    state = env.state()
    state['round']['status'] = 'CANCELED'
    atomic_write_json(env.g.state_dir / p.FILE, state)
    report = check.inspect(env.g, 'cancel', now=200)
    assert report['actionable'] is False and report['reason'] == 'round_already_terminal'


def test_control_script_uses_only_a_fixed_operation_enum():
    text = CONTROL_SCRIPT.read_text(encoding='utf-8')
    assert 'PREDICTION_MODE' in text and 'PREDICTION_OPERATION' in text
    assert 'cancel|sync' in text
    assert 'prediction control accepts no arguments' in text
    assert '\\$@' not in text and '$@' not in text
    assert 'eval ' not in text
    for forbidden in ('resolve', 'create', 'outcome_index', 'prediction_id'):
        assert f'PREDICTION_{forbidden.upper()}' not in text


def test_control_script_rejects_extra_arguments_before_any_launch():
    result = subprocess.run(['bash', str(CONTROL_SCRIPT), 'arbitrary-command'],
                            capture_output=True, text=True)
    assert result.returncode == 64
    assert 'accepts no arguments' in result.stderr


def test_control_script_rejects_an_unknown_mode_or_operation():
    import os
    for env_changes in ({'PREDICTION_MODE': 'apply;id', 'PREDICTION_OPERATION': 'cancel'},
                        {'PREDICTION_MODE': 'apply', 'PREDICTION_OPERATION': 'resolve'},
                        {'PREDICTION_MODE': '', 'PREDICTION_OPERATION': 'cancel'},
                        {'PREDICTION_MODE': 'apply', 'PREDICTION_OPERATION': ''}):
        result = subprocess.run(['bash', str(CONTROL_SCRIPT)],
                                env={**os.environ, **env_changes}, capture_output=True, text=True)
        with pytest.MonkeyPatch.context():
            assert result.returncode == 64, (env_changes, result.stderr)


def run_auth(**overrides):
    import sys
    import tempfile
    env = {
        "GITHUB_REPOSITORY": "azumag/docich",
        "GITHUB_REPOSITORY_ID": "1327276249",
        "GITHUB_REPOSITORY_OWNER": "azumag",
        "GITHUB_REPOSITORY_OWNER_ID": "9018513",
        "GITHUB_ACTOR": "azumag",
        "GITHUB_ACTOR_ID": "9018513",
        "GITHUB_TRIGGERING_ACTOR": "azumag",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_DEFAULT_BRANCH": "main",
        "GITHUB_WORKFLOW_REF": CANONICAL_REF,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_SHA": "a" * 40,
        "INPUT_OPERATION": "restart-service",
        "INPUT_CONFIRM": "production",
    }
    env.update(overrides)
    with tempfile.TemporaryDirectory() as directory:
        event = Path(directory) / "event.json"
        event.write_text(json.dumps({"inputs": {"expected_reservation": ""}}))
        env.setdefault("GITHUB_EVENT_PATH", str(event))
        return subprocess.run([sys.executable, str(AUTH)], capture_output=True, text=True, env=env)


def test_owner_dispatch_allows_the_fixed_prediction_operations():
    for operation in ('check-cancel-prediction', 'cancel-prediction',
                      'check-sync-prediction', 'sync-prediction'):
        result = run_auth(INPUT_OPERATION=operation)
        assert result.returncode == 0, (operation, result.stderr)
    for operation in ('cancel-prediction;id', 'sync-prediction;id', 'check-cancel-prediction;id',
                      'resolve-prediction', 'create-prediction'):
        assert run_auth(INPUT_OPERATION=operation).returncode != 0, operation


def test_prediction_operations_require_the_canonical_owner_workflow():
    legacy = "azumag/docich/.github/workflows/retro-corner-operator.yml@refs/heads/main"
    assert run_auth(INPUT_OPERATION='cancel-prediction', GITHUB_WORKFLOW_REF=legacy).returncode != 0
    assert run_auth(INPUT_OPERATION='check-sync-prediction', GITHUB_WORKFLOW_REF=legacy).returncode != 0
    for changes in ({'GITHUB_ACTOR_ID': '42'}, {'GITHUB_REF_PROTECTED': 'false'},
                    {'INPUT_CONFIRM': ''}, {'GITHUB_REF': 'refs/heads/feature'}):
        assert run_auth(INPUT_OPERATION='cancel-prediction', **changes).returncode != 0


def test_workflow_exposes_only_fixed_prediction_operations():
    text = WF.read_text(encoding='utf-8')
    for required in ('check-cancel-prediction', 'cancel-prediction',
                     'check-sync-prediction', 'sync-prediction',
                     'control/ops/vm_actions/control_hanjuku_prediction.sh',
                     "OPERATION: ${{ steps.auth.outputs.operation }}",
                     "control=cancel", "control=sync",
                     "printf 'PREDICTION_MODE=%s\\nPREDICTION_OPERATION=%s\\n'",
                     "exec docich production $SHA"):
        assert required in text, required
    assert 'inputs.command' not in text
    assert 'inputs.prediction_id' not in text
    assert 'inputs.outcome_index' not in text
