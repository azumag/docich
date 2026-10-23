from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich import hanjuku_predictions as p, hanjuku_progress as progress
from docich.config import load_global
from docich.game_switch import atomic_write_json
from docich.retroarch_boundary import read_record

ROOT = Path(__file__).resolve().parents[1]
ID = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef', 'generation': 1, 'lease_id': 'lease1'}
DIGEST = 'a' * 64


class FakeAPI:
    def __init__(self):
        self.rows = []
        self.calls = []

    def list(self, prediction_id=None):
        self.calls.append(('GET', prediction_id))
        return [dict(row, outcomes=[dict(outcome) for outcome in row['outcomes']])
                for row in self.rows if not prediction_id or row['id'] == prediction_id]

    def create(self, title, outcomes, window):
        self.calls.append(('POST', title, tuple(outcomes), window))
        row = {'id': f'prediction-{len(self.rows)}', 'title': title, 'status': 'ACTIVE',
               'outcomes': [{'id': f'outcome-{i}', 'title': outcome}
                            for i, outcome in enumerate(outcomes)]}
        self.rows.append(row)
        return [dict(row, outcomes=[dict(outcome) for outcome in row['outcomes']])]

    def end(self, prediction_id, status, outcome_id=None):
        self.calls.append(('PATCH', prediction_id, status, outcome_id))
        row = next(row for row in self.rows if row['id'] == prediction_id)
        row['status'] = status
        if outcome_id is not None:
            row['winning_outcome_id'] = outcome_id
        return [dict(row, outcomes=[dict(outcome) for outcome in row['outcomes']])]


@pytest.fixture
def env(tmp_path):
    g = replace(load_global(ROOT, ROOT / 'config/docich.soren-live.toml'), state_dir=tmp_path / 'run')
    remote = FakeAPI()
    runtime = g.state_dir / 'runtimes' / ID['runtime_id']
    runtime.mkdir(parents=True)
    atomic_write_json(g.state_dir / 'game_switch.json', {'phase': 'ready', 'active': ID})
    atomic_write_json(runtime / 'hanjuku_run.json', {**ID, 'schema': 1})
    progress.record(runtime, ID, {'chapter': 1, 'chapter_evidence': 'header'}, [], DIGEST)
    return SimpleNamespace(
        g=g, remote=remote, runtime=runtime,
        tick=lambda now=100: p.tick(g, ID, now=now, client_factory=lambda _: (remote, None)),
        state=lambda: read_record(g.state_dir / p.FILE),
    )


def verified_resume(env, chapter):
    atomic_write_json(env.runtime / 'hanjuku_resume.json', {
        **ID, 'schema': 1, 'status': 'verified', 'explicit_resume': True,
        'start_mode': 'resume', 'chapter': chapter,
        'gameplay_context': 'verified_checkpoint_foreground',
    })
    if chapter is not None:
        progress.record(env.runtime, ID, {'chapter': chapter, 'chapter_evidence': 'header'}, [], DIGEST)


def terminal(env, reason):
    row = {**ID, 'schema': 1, 'terminal_reason': reason, 'frame_sha256': DIGEST,
           'name_entered': True, 'gameplay_seen': True, 'phase': 'title', 'title_count': 3,
           'observed_monotonic': 10, 'title_since': 7, 'unchanged_seconds': 300}
    if reason == 'input_stalled':
        row.update(observed_monotonic=1000, observations=500,
                   last_input_monotonic=100, last_input_observations=200)
    atomic_write_json(env.runtime / 'hanjuku_run.json', row)


def test_resume_prediction_uses_current_chapter_three_way_contract(env):
    verified_resume(env, 4)
    assert env.tick()['mode'] == 'active'
    row = env.state()['round']
    assert row['kind'] == p.RESUME_PREDICTION_KIND
    assert row['target'] == 4 and row.get('middle') is None
    assert row['title'].startswith('半熟英雄：4話を突破できる？ #')
    assert row['outcome_titles'] == ['4話突破', '4話突破ならず', '停滞で一時停止']
    assert env.tick(130)['mode'] == 'active'


def test_resume_prediction_resolves_immediately_when_target_chapter_clears(env):
    verified_resume(env, 2)
    assert env.tick()['mode'] == 'active'
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    assert env.tick(101)['mode'] == 'resolved'
    assert env.remote.rows[0]['winning_outcome_id'] == 'outcome-0'


@pytest.mark.parametrize('reason, outcome', [
    ('game_over', 1),
    ('screen_stalled', 2),
    ('input_stalled', 2),
])
def test_resume_prediction_terminal_contract(env, reason, outcome):
    verified_resume(env, 2)
    assert env.tick()['mode'] == 'active'
    terminal(env, reason)
    assert env.tick(101)['mode'] == 'resolved'
    assert env.remote.rows[0]['winning_outcome_id'] == f'outcome-{outcome}'


def test_resume_without_verified_chapter_does_not_open_prediction(env):
    verified_resume(env, None)
    assert env.tick()['mode'] == 'pending'
    assert env.state()['round'] is None
    assert not [call for call in env.remote.calls if call[0] == 'POST']
