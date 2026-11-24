"""Owner-only fixed control of one stalled Hanjuku prediction round (#2216).

No real Twitch calls, tokens, game processes or production writes.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from docich import hanjuku_prediction_control as control
from docich import hanjuku_prediction_control_check as check
from docich import hanjuku_prediction_api as api
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
        self.allow_create = False

    def list(self, prediction_id=None):
        self.calls.append(('GET', prediction_id))
        if self.failure:
            raise self.failure
        return copy.deepcopy([r for r in self.rows if not prediction_id or r['id'] == prediction_id])

    def create(self, title, outcomes, window):
        self.calls.append(('POST', title, tuple(outcomes), window))
        if not self.allow_create:
            raise AssertionError('prediction control must never create a round')
        row = {'id': f'prediction-{len(self.rows)}', 'title': title, 'status': 'ACTIVE',
               'outcomes': [{'id': f'outcome-{i}', 'title': x}
                            for i, x in enumerate(outcomes)]}
        self.rows.append(row)
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
    # A terminal tombstone keeps this run from opening a second wager.
    assert state['round']['status'] == 'CANCELED'
    assert state['mode'] == 'canceled' and state['error'] is None
    # Never picks a winner and never creates a second round.
    assert ('PATCH', env.remote.rows[0]['id'], 'CANCELED', None) in env.remote.calls
    assert not [c for c in env.remote.calls if c[0] == 'POST']


def test_cancel_is_idempotent_when_the_remote_no_longer_has_the_round(env):
    env.remote.rows.clear()
    result = control.cancel(env.g, client_factory=env.factory, now=200)
    assert result['outcome'] == 'remote_absent'
    assert env.state()['round']['status'] == 'CANCELED'
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
    assert env.state()['round']['status'] == 'CANCELED'


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
    assert env.state()['round']['status'] == 'LOCKED'


def test_sync_clears_ownership_the_remote_already_finished(env):
    env.remote.rows[0]['status'] = 'RESOLVED'
    env.remote.rows[0]['winning_outcome_id'] = 'outcome-0'
    result = control.sync(env.g, client_factory=env.factory, now=200)
    assert result['outcome'] == 'resolved'
    state = env.state()
    assert state['round']['status'] == 'RESOLVED' and state['mode'] == 'resolved'


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
    assert env.state()['round']['status'] == 'CANCELED'


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


def test_control_script_passes_the_reviewed_live_config_to_both_entrypoints():
    """P1 regression: the gateway env has no DOCICH_CONFIG, so bind it here."""
    import os
    import tempfile
    text = CONTROL_SCRIPT.read_text(encoding='utf-8')
    assert 'config="$root/config/docich.soren-live.toml"' in text
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'src/docich').mkdir(parents=True)
        (root / 'src/docich/hanjuku_prediction_control.py').touch()
        (root / 'src/docich/hanjuku_prediction_control_check.py').touch()
        (root / 'config').mkdir()
        (root / 'config/docich.soren-live.toml').touch()
        python = root / 'python3'
        python.write_text(f'#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
        python.chmod(0o755)
        env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
               'DOCICH_PROD_ROOT': str(root)}
        live = str(root / 'config/docich.soren-live.toml')
        for mode, module, operation in (
                ('check', 'docich.hanjuku_prediction_control_check', 'cancel'),
                ('check', 'docich.hanjuku_prediction_control_check', 'sync'),
                ('apply', 'docich.hanjuku_prediction_control', 'cancel'),
                ('apply', 'docich.hanjuku_prediction_control', 'sync')):
            result = subprocess.run(['bash', str(CONTROL_SCRIPT)], capture_output=True, text=True,
                                    env={**env, 'PREDICTION_MODE': mode,
                                         'PREDICTION_OPERATION': operation})
            assert result.returncode == 0, result.stderr
            argv = json.loads(result.stdout)
            assert argv == ['-B', '-P', '-m', module, '--config', live, operation], (mode, operation, argv)


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


# --- independent-review regressions (#2216 review 5582196292) -----------------

def test_p1_cancel_and_sync_bind_the_reviewed_live_profile(monkeypatch, tmp_path):
    """P1: the gateway does not inherit DOCICH_CONFIG.

    Both entrypoints must select config/docich.soren-live.toml (state_dir
    run-soren-live) rather than falling back to config/docich.toml (run).
    """
    import docich.hanjuku_prediction_control as m
    assert m.LIVE_CONFIG == "config/docich.soren-live.toml"
    seen = {}
    real = m.load_global

    def spy(root, config):
        seen['config'] = config
        return real(root, config)

    monkeypatch.setattr(m, 'load_global', spy)
    monkeypatch.setattr(m, 'run', lambda g, op, **kw: seen.setdefault('op', op) or {'ok': True})
    for operation in ('cancel', 'sync'):
        seen.clear()
        monkeypatch.setenv('DOCICH_CONFIG', '/nonexistent/ignored.toml')
        assert m.main([operation]) == m.EXIT_OK
        assert seen['config'] == ROOT / 'config/docich.soren-live.toml'
        assert seen['op'] == operation
    # An explicit path is still honoured, but never silently omitted.
    seen.clear()
    assert m.main(['--config', str(ROOT / 'config/docich.soren-live.toml'), 'sync']) == m.EXIT_OK
    assert seen['config'] == ROOT / 'config/docich.soren-live.toml'


def test_p1_live_profile_selects_the_production_state_dir(tmp_path):
    """The bound profile is the one that owns run-soren-live, not run."""
    live = load_global(ROOT, ROOT / 'config/docich.soren-live.toml')
    default = load_global(ROOT, ROOT / 'config/docich.toml')
    assert live.state_dir.name == 'run-soren-live'
    assert default.state_dir.name == 'run'


def test_p1_cancel_does_not_open_a_second_wager_in_the_same_run(env):
    """P1: the terminal tombstone must survive, or the next tick re-POSTs."""
    control.cancel(env.g, client_factory=env.factory, now=200)
    assert env.state()['round']['status'] == 'CANCELED'
    # The same run is still ready and active: _advance must not create.
    env.remote.calls.clear()
    p.tick(env.g, ID, now=201, client_factory=env.factory)
    assert not [c for c in env.remote.calls if c[0] == 'POST']
    assert env.state()['round']['status'] == 'CANCELED'


def test_p1_sync_does_not_open_a_second_wager_in_the_same_run(env):
    env.remote.rows[0]['status'] = 'CANCELED'
    control.sync(env.g, client_factory=env.factory, now=200)
    assert env.state()['round']['status'] == 'CANCELED'
    env.remote.calls.clear()
    p.tick(env.g, ID, now=201, client_factory=env.factory)
    assert not [c for c in env.remote.calls if c[0] == 'POST']


def test_p1_a_later_runtime_creates_normally(env):
    """Only the next, different runtime may open a fresh wager."""
    control.cancel(env.g, client_factory=env.factory, now=200)
    later = {**ID, 'runtime_id': 'g2-abcdef', 'generation': 2, 'lease_id': 'lease2'}
    runtime = env.g.state_dir / 'runtimes' / later['runtime_id']
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_write_json(runtime / 'hanjuku_run.json', {**later, 'schema': 1})
    atomic_write_json(env.g.state_dir / 'game_switch.json',
                      {'phase': 'ready', 'active': later})
    env.remote.allow_create = True
    env.remote.calls.clear()
    result = p.tick(env.g, later, now=300, client_factory=env.factory)
    assert result['mode'] == 'active'
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1
    assert env.state()['round']['runtime_id'] == later['runtime_id']


def test_p2_bookkeeping_failure_keeps_retry_evidence_and_stays_unresolved(env, monkeypatch):
    """P2: a swallowed record write must not erase the retry path."""
    import docich.hanjuku_predictions as hp

    # No contest: prove the participation-record path is the one that runs.
    for outcome in env.remote.rows[0]['outcomes']:
        outcome['users'] = 0
        outcome['channel_points'] = 0

    def fail(*a, **kw):
        raise api.APIError('leaderboard')

    monkeypatch.setattr(hp, '_record_canceled', fail)
    result = control.cancel(env.g, client_factory=env.factory, now=200)
    assert result['outcome'] == 'canceled_record_pending'
    state = env.state()
    # Unresolved, so the next tick re-observes CANCELED and retries the record.
    assert state['round']['status'] == 'LOCKED'
    assert state['mode'] == 'settling'
    assert env.remote.rows[0]['status'] == 'CANCELED'

    # The next tick records the same id exactly once, then the round goes DONE.
    recorded = []
    monkeypatch.setattr(hp, '_record_canceled',
                        lambda root, now, client, sources=(): recorded.append(sources[0]['id']))
    env.remote.calls.clear()
    p.tick(env.g, ID, now=201, client_factory=env.factory)
    assert recorded == [env.remote.rows[0]['id']]
    assert env.state()['round']['status'] == 'CANCELED'
    assert not [c for c in env.remote.calls if c[0] == 'POST']


def test_p2_same_id_with_a_different_shape_is_a_mismatch_not_an_absence(env):
    """P2: a row for this id in another shape must fail closed."""
    # Same id, different outcome ids: ownership can no longer be proven.
    for index, outcome in enumerate(env.remote.rows[0]['outcomes']):
        outcome['id'] = f'other-{index}'
    for operation in (control.cancel, control.sync):
        before = env.state()['round']
        with pytest.raises(control.ControlRefused) as exc:
            operation(env.g, client_factory=env.factory, now=200)
        assert exc.value.code == 'remote_mismatch'
        # Local ownership is untouched, not silently dropped.
        assert env.state()['round']['id'] == before['id']


def test_p2_same_id_with_a_different_title_is_a_mismatch(env):
    env.remote.rows[0]['title'] = 'another corner wager'
    with pytest.raises(control.ControlRefused) as exc:
        control.cancel(env.g, client_factory=env.factory, now=200)
    assert exc.value.code == 'remote_mismatch'


def test_p2_check_refuses_a_ledger_it_cannot_verify(env):
    """P2: check must agree with apply, which refuses `ledger_unreadable`."""
    state = env.state()
    state['round']['outcome_ids'] = ['broken']
    atomic_write_json(env.g.state_dir / p.FILE, state)
    before = (env.g.state_dir / p.FILE).read_bytes()
    report = check.inspect(env.g, 'cancel', now=200)
    assert report['actionable'] is False
    assert report['reason'] == 'ledger_unreadable'
    assert (env.g.state_dir / p.FILE).read_bytes() == before
    with pytest.raises(control.ControlRefused) as exc:
        control.cancel(env.g, client_factory=env.factory, now=200)
    assert exc.value.code == 'ledger_unreadable'


def _break_result(env):
    """A durable result the ordinary loader refuses (the #2213-class stall)."""
    state = env.state()
    state['round']['result'] = {'schema': 1, **ID, 'reason': 'input_stalled',
                                'frame_sha256': 'not-a-digest', 'cleared': None}
    atomic_write_json(env.g.state_dir / p.FILE, state)
    with pytest.raises(ValueError):
        p._load(env.g.state_dir, 1)
    return state


@pytest.mark.parametrize('operation', ['cancel', 'sync'])
def test_p1_malformed_result_converges_so_the_normal_tick_recovers(env, operation):
    """P1: a tombstone must not keep a result the ordinary loader refuses.

    `_load` validates `row['result']` even for a DONE round, so leaving the
    malformed result in place would keep every later tick, and every later
    runtime, stuck in `invalid_state`.
    """
    _break_result(env)
    if operation == 'sync':
        env.remote.rows[0]['status'] = 'RESOLVED'
        env.remote.rows[0]['winning_outcome_id'] = 'outcome-0'
        result = control.sync(env.g, client_factory=env.factory, now=200)
        assert result['outcome'] == 'resolved'
    else:
        result = control.cancel(env.g, client_factory=env.factory, now=200)
        assert result['outcome'] == 'canceled'

    # The original untrusted bytes are preserved out of band, not discarded.
    evidence = list(env.g.state_dir.glob('hanjuku_prediction_recovered_result_*.json'))
    assert len(evidence) == 1
    preserved = json.loads(evidence[0].read_text())
    assert preserved['frame_sha256'] == 'not-a-digest'

    # The ordinary loader accepts the tombstone again...
    state = p._load(env.g.state_dir, 1)
    assert state['round']['status'] in ('CANCELED', 'RESOLVED')
    assert state['round']['result'] is None

    # ...so the next tick no longer reports invalid_state, and the same run
    # still cannot open a second wager.
    env.remote.calls.clear()
    outcome = p.tick(env.g, ID, now=201, client_factory=env.factory)
    assert outcome['error'] is None
    assert not [c for c in env.remote.calls if c[0] == 'POST']

    # A later runtime creates normally: the stall no longer blocks the corner.
    later = {**ID, 'runtime_id': 'g2-abcdef', 'generation': 2, 'lease_id': 'lease2'}
    runtime = env.g.state_dir / 'runtimes' / later['runtime_id']
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_write_json(runtime / 'hanjuku_run.json', {**later, 'schema': 1})
    atomic_write_json(env.g.state_dir / 'game_switch.json', {'phase': 'ready', 'active': later})
    env.remote.allow_create = True
    env.remote.calls.clear()
    assert p.tick(env.g, later, now=300, client_factory=env.factory)['mode'] == 'active'
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1


def test_p1_quiesce_refuses_rather_than_discarding_oversized_evidence(env):
    """Defense in depth: evidence too large to preserve refuses, never drops.

    The durable ledger is itself bounded well below this limit, so the guard is
    unreachable through the real path; it is asserted directly so a future
    ledger bound cannot silently start discarding evidence.
    """
    row = {'id': 'prediction-0', **ID, 'target': 2, 'middle': 1,
           'result': {'schema': 1, 'extra': 'x' * (control.MAX_RECOVERED_RESULT_BYTES + 1)}}
    with pytest.raises(control.ControlRefused) as exc:
        control._quiesce_result(env.g.state_dir, row)
    assert exc.value.code == 'evidence_unpreservable'
    # Nothing was cleared and no partial evidence file was left behind.
    assert row['result'] is not None
    assert not list(env.g.state_dir.glob('hanjuku_prediction_recovered_result_*.json'))


def test_p2_sparse_canceled_response_still_records_from_the_snapshot(env):
    """P2: the CANCELED response may omit the counts that prove no-contest.

    The snapshot saved before the irreversible cancel is an equally
    authoritative basis, and the round must not go DONE while the
    participation record is still unwritten.
    """
    # LOCKED GET proves no-contest from its counts.
    for outcome in env.remote.rows[0]['outcomes']:
        outcome['users'] = 0
        outcome['channel_points'] = 0

    recorded = []
    real_record = p._record_canceled

    def spy(root, now, client, *, sources=()):
        recorded.append([tuple(sorted(s.get('outcomes', [{}])[0].get('users', -1)
                                      for s in sources if s))])
        return real_record(root, now, client, sources=sources)

    original_end = env.remote.end

    def end_without_counts(prediction_id, status, outcome_id=None):
        rows = original_end(prediction_id, status, outcome_id)
        # Twitch may omit the counts on the CANCELED response.
        for outcome in rows[0]['outcomes']:
            outcome.pop('users', None)
            outcome.pop('channel_points', None)
        return rows

    env.remote.end = end_without_counts
    import docich.hanjuku_predictions as hp
    hp._record_canceled, saved = spy, hp._record_canceled
    try:
        result = control.cancel(env.g, client_factory=env.factory, now=200)
    finally:
        hp._record_canceled = saved

    assert result['outcome'] == 'canceled'
    # The record was attempted exactly once, and the round went terminal.
    assert len(recorded) == 1
    assert env.state()['round']['status'] == 'CANCELED'
    # The pre-cancel snapshot was consumed, not orphaned.
    assert not list(env.g.state_dir.glob('hanjuku_prediction_cancel_*.json'))


def test_p2_sparse_canceled_without_evidence_keeps_the_round_pending(env, monkeypatch):
    """No proven basis means the record stays retryable, never silently lost."""
    import docich.hanjuku_predictions as hp
    for outcome in env.remote.rows[0]['outcomes']:
        outcome['users'] = 0

    def sparse_end(prediction_id, status, outcome_id=None):
        row = env.remote.rows[0]
        row['status'] = status
        sparse = copy.deepcopy(row)
        for outcome in sparse['outcomes']:
            outcome.pop('users', None)
            outcome.pop('channel_points', None)
        return [sparse]

    env.remote.end = sparse_end
    real_record = hp._record_canceled
    calls = []
    monkeypatch.setattr(hp, '_record_canceled',
                        lambda root, now, client, *, sources=(): calls.append(sources))

    result = control.cancel(env.g, client_factory=env.factory, now=200)
    # The snapshot preserved the LOCKED counts, so it is recorded from there.
    assert result['outcome'] == 'canceled'
    assert len(calls) == 1
    assert any(s.get('outcomes', [{}])[0].get('users') == 0 for s in calls[0] if s)
    assert env.state()['round']['status'] == 'CANCELED'


@pytest.mark.parametrize('operation', ['cancel', 'sync'])
def test_p1_malformed_result_survives_a_transient_record_failure(env, operation, monkeypatch):
    """P1: the pending (retry) row must also be loader-acceptable.

    A malformed result that survives in a *pending* row makes the next ordinary
    tick fail in `_load` with `invalid_state`, so the retry that consumes the
    snapshot can never run.
    """
    import docich.hanjuku_predictions as hp

    for outcome in env.remote.rows[0]['outcomes']:
        outcome['users'] = 1 if outcome['id'] == 'outcome-0' else 0
        outcome['channel_points'] = 10 if outcome['id'] == 'outcome-0' else 0

    if operation == 'sync':
        env.remote.rows[0]['status'] = 'CANCELED'
        _break_result(env)
        monkeypatch.setattr(hp, '_record_canceled',
                            lambda *a, **kw: (_ for _ in ()).throw(api.APIError('leaderboard')))
        result = control.sync(env.g, client_factory=env.factory, now=200)
        assert result['outcome'] == 'canceled_record_pending'
    else:
        _break_result(env)
        calls = {'n': 0}
        real = hp._record_canceled

        def flaky(root, now, client, *, sources=()):
            calls['n'] += 1
            if calls['n'] == 1:
                raise api.APIError('leaderboard')
            return real(root, now, client, sources=sources)

        monkeypatch.setattr(hp, '_record_canceled', flaky)
        result = control.cancel(env.g, client_factory=env.factory, now=200)
        assert result['outcome'] == 'canceled_record_pending'

    # The untrusted evidence was preserved and the pending row is loadable.
    evidence = list(env.g.state_dir.glob('hanjuku_prediction_recovered_result_*.json'))
    assert len(evidence) == 1
    state = p._load(env.g.state_dir, 1)          # ordinary loader accepts
    assert state['round']['status'] == 'LOCKED'
    assert state['round']['result'] is None

    # The next ordinary tick reaches the retry and records the same id once.
    recorded = []
    monkeypatch.setattr(hp, '_record_canceled',
                        lambda root, now, client, **kw: recorded.append(kw.get('sources')))
    env.remote.calls.clear()
    outcome = p.tick(env.g, ID, now=201, client_factory=env.factory)
    assert outcome['error'] is None
    assert len(recorded) == 1
    assert not [c for c in env.remote.calls if c[0] == 'POST']

    # And a later runtime still creates normally.
    later = {**ID, 'runtime_id': 'g2-abcdef', 'generation': 2, 'lease_id': 'lease2'}
    runtime = env.g.state_dir / 'runtimes' / later['runtime_id']
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_write_json(runtime / 'hanjuku_run.json', {**later, 'schema': 1})
    atomic_write_json(env.g.state_dir / 'game_switch.json', {'phase': 'ready', 'active': later})
    env.remote.allow_create = True
    env.remote.calls.clear()
    assert p.tick(env.g, later, now=300, client_factory=env.factory)['mode'] == 'active'
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1


def test_p2_transient_failure_then_tick_keeps_the_snapshot_counts(env, monkeypatch):
    """P2: a sparse CANCELED response must not overwrite richer saved evidence.

    After a transient `leaderboard` failure the ordinary tick passes
    `(remote, snapshot)` to `_record_canceled`. The sparse remote must not win
    over the snapshot that still carries the real 1 person / 10 points.
    """
    import docich.hanjuku_predictions as hp

    for outcome in env.remote.rows[0]['outcomes']:
        outcome['users'] = 1 if outcome['id'] == 'outcome-0' else 0
        outcome['channel_points'] = 10 if outcome['id'] == 'outcome-0' else 0

    titles = [{'id': o['id'], 'title': o['title']} for o in env.remote.rows[0]['outcomes']]
    title = env.remote.rows[0]['title']

    # The CANCELED response, and any later GET, omit the counts entirely.
    patched = {'v': False}

    def sparse_end(prediction_id, status, outcome_id=None):
        patched['v'] = True
        return [{'id': prediction_id, 'title': title, 'status': status, 'outcomes': titles}]

    def sparse_list(prediction_id=None):
        # Before the PATCH the round is still LOCKED and carries its counts;
        # only the post-PATCH CANCELED response omits them.
        env.remote.calls.append(('GET', prediction_id))
        return [{'id': 'prediction-0', 'title': title,
                 'status': 'CANCELED' if patched['v'] else 'LOCKED',
                 'outcomes': titles if patched['v'] else
                 [{'id': o['id'], 'title': o['title'], 'users': o['users'],
                   'channel_points': o['channel_points']}
                  for o in env.remote.rows[0]['outcomes']]}]

    env.remote.end = sparse_end
    env.remote.list = sparse_list

    real = hp._record_canceled
    calls = {'n': 0}

    def flaky(root, now, client, *, sources=()):
        calls['n'] += 1
        if calls['n'] == 1:
            raise api.APIError('leaderboard')
        return real(root, now, client, sources=sources)

    monkeypatch.setattr(hp, '_record_canceled', flaky)
    assert control.cancel(env.g, client_factory=env.factory, now=200)['outcome'] == \
        'canceled_record_pending'
    # Evidence is still on disk for the retry.
    assert len(list(env.g.state_dir.glob('hanjuku_prediction_cancel_*.json'))) == 1

    # Let the ordinary tick perform the retry for real.
    monkeypatch.setattr(hp, '_record_canceled', real)
    env.remote.calls.clear()
    outcome = p.tick(env.g, ID, now=201, client_factory=env.factory)
    assert outcome['error'] is None

    ledger = json.loads((env.g.state_dir / 'prediction_leaderboard.json').read_text())
    rounds = ledger['rounds']
    assert list(rounds) == ['prediction-0']
    row = rounds['prediction-0']
    # The saved 1 person / 10 points survived the sparse response.
    assert sum(o['users'] for o in row['outcomes']) == 1
    assert sum(o['points'] for o in row['outcomes']) == 10
    assert row['cancel_reason'] == 'no-contest'
    # Recorded exactly once and the snapshot was consumed.
    assert not list(env.g.state_dir.glob('hanjuku_prediction_cancel_*.json'))
