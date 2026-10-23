from pathlib import Path

SOURCE = Path("src/docich/hanjuku_predictions.py")
text = SOURCE.read_text()


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one source match, got {count}: {old[:100]!r}")
    text = text.replace(old, new, 1)


replace_once(
    "LEGACY_OUTCOME_COUNT = 3\n",
    "LEGACY_OUTCOME_COUNT = 3\n"
    "RESUME_PREDICTION_KIND = 'resume-chapter'\n"
    "RESUME_STALLED_OUTCOME_TITLE = '停滞で一時停止'\n",
)

replace_once(
    "def resolve_index(row, result):\n",
    "def resume_outcome_titles(target):\n"
    "    if type(target) is not int or not 1 <= target <= progress.MAX_CHAPTER:\n"
    "        raise ValueError('invalid resume prediction target')\n"
    "    return [f'{target}話突破', f'{target}話突破ならず', RESUME_STALLED_OUTCOME_TITLE]\n\n\n"
    "def resolve_index(row, result):\n",
)

replace_once(
    "    titles = row.get('outcome_titles')\n"
    "    if (isinstance(titles, list) and len(titles) > LEGACY_OUTCOME_COUNT\n",
    "    if row.get('kind') == RESUME_PREDICTION_KIND:\n"
    "        cleared = result.get('cleared')\n"
    "        if cleared is not None:\n"
    "            progress.count(cleared)\n"
    "            if cleared >= row['target']:\n"
    "                return 0\n"
    "        if result.get('reason') in {'screen_stalled', 'input_stalled'}:\n"
    "            return 2\n"
    "        if result.get('reason') == 'game_over' and cleared is not None:\n"
    "            return 1\n"
    "        return None\n"
    "    titles = row.get('outcome_titles')\n"
    "    if (isinstance(titles, list) and len(titles) > LEGACY_OUTCOME_COUNT\n",
)

replace_once(
    "        progress.checked_identity(row)\n"
    "        winner(0, row.get('target'), row.get('middle'))\n"
    "        if (not isinstance(row.get('nonce'), str) or len(row['nonce']) != 12\n"
    "                or any(c not in '0123456789abcdef' for c in row['nonce'])\n"
    "                or type(row.get('attempted')) is not bool\n"
    "                or row.get('status') not in LIVE | DONE | {'INTENT'}\n"
    "                or type(row.get('window')) is not int or not 1 <= row['window'] <= 1800\n"
    "                or row.get('title') != _title(row['nonce'])\n"
    "                or row.get('outcome_titles')\n"
    "                   not in accepted_outcome_titles(row['target'], row['middle'])):\n"
    "            raise ValueError('invalid prediction intent')\n",
    "        progress.checked_identity(row)\n"
    "        kind = row.get('kind')\n"
    "        if kind is None:\n"
    "            winner(0, row.get('target'), row.get('middle'))\n"
    "            expected_titles = accepted_outcome_titles(row['target'], row['middle'])\n"
    "            expected_title = _title(row.get('nonce'))\n"
    "        elif kind == RESUME_PREDICTION_KIND:\n"
    "            if row.get('middle') is not None:\n"
    "                raise ValueError('resume prediction cannot carry range middle')\n"
    "            expected_titles = (resume_outcome_titles(row.get('target')),)\n"
    "            expected_title = _resume_title(row['target'], row.get('nonce'))\n"
    "        else:\n"
    "            raise ValueError('invalid prediction kind')\n"
    "        if (not isinstance(row.get('nonce'), str) or len(row['nonce']) != 12\n"
    "                or any(c not in '0123456789abcdef' for c in row['nonce'])\n"
    "                or type(row.get('attempted')) is not bool\n"
    "                or row.get('status') not in LIVE | DONE | {'INTENT'}\n"
    "                or type(row.get('window')) is not int or not 1 <= row['window'] <= 1800\n"
    "                or row.get('title') != expected_title\n"
    "                or row.get('outcome_titles') not in expected_titles):\n"
    "            raise ValueError('invalid prediction intent')\n",
)

replace_once(
    "def _title(nonce):\n"
    "    return f'半熟英雄：どこまでゲーム到達できるか？ #{nonce}'\n\n\n",
    "def _title(nonce):\n"
    "    return f'半熟英雄：どこまでゲーム到達できるか？ #{nonce}'\n\n\n"
    "def _resume_title(target, nonce):\n"
    "    return f'半熟英雄：{target}話を突破できる？ #{nonce}'\n\n\n",
)

replace_once(
    "def _result(root, identity):\n",
    "def _resume_prediction_context(root, identity):\n"
    "    receipt = read_record(runtime_directory(root, identity['runtime_id']) / 'hanjuku_resume.json')\n"
    "    if not receipt or receipt.get('explicit_resume') is not True or receipt.get('start_mode') != 'resume':\n"
    "        return None\n"
    "    if (receipt.get('status') != 'verified'\n"
    "            or receipt.get('gameplay_context') != 'verified_checkpoint_foreground'\n"
    "            or any(receipt.get(k) != identity[k] for k in progress.KEYS)):\n"
    "        raise ValueError('invalid verified Hanjuku resume receipt')\n"
    "    chapter = receipt.get('chapter')\n"
    "    if chapter is not None and (type(chapter) is not int or not 1 <= chapter <= progress.MAX_CHAPTER):\n"
    "        raise ValueError('invalid verified Hanjuku resume chapter')\n"
    "    return {'chapter': chapter}\n\n\n"
    "def _resume_target_reached(root, row):\n"
    "    if row.get('kind') != RESUME_PREDICTION_KIND:\n"
    "        return False\n"
    "    cleared = progress.completed_count(runtime_directory(root, row['runtime_id']),\n"
    "                                       {k: row[k] for k in progress.KEYS})\n"
    "    return cleared is not None and cleared >= row['target']\n\n\n"
    "def _result(root, identity):\n",
)

replace_once(
    "    new_terminal = bool(owned_completed and not row.get('result'))\n"
    "    if owned_completed:\n"
    "        row['result'] = owned_completed\n"
    "    atomic_write_json(root / FILE, state)\n"
    "    if now < state['last_poll_at']:\n"
    "        raise APIError('clock_regressed')\n"
    "    if now < state['next_poll_at'] and not new_terminal:\n",
    "    resume_reached = bool(row and row['status'] not in DONE\n"
    "                          and _resume_target_reached(root, row))\n"
    "    new_settlement = bool(owned_completed and not row.get('result')) or resume_reached\n"
    "    if owned_completed:\n"
    "        row['result'] = owned_completed\n"
    "    atomic_write_json(root / FILE, state)\n"
    "    if now < state['last_poll_at']:\n"
    "        raise APIError('clock_regressed')\n"
    "    if now < state['next_poll_at'] and not new_settlement:\n",
)

replace_once(
    "            index = resolve_index(row, result) if result else None\n"
    "            cancel = bool(result) and index is None\n",
    "            index = 0 if resume_reached else resolve_index(row, result) if result else None\n"
    "            cancel = bool(result) and index is None\n",
)

replace_once(
    "            if result or cancel:\n",
    "            if result or resume_reached or cancel:\n",
)

replace_once(
    "        bounds = thresholds(state['best_cleared'])\n"
    "        if bounds is None:\n"
    "            state['mode'] = 'complete_record'\n"
    "            return\n"
    "        cleared = progress.completed_count(runtime_directory(root, identity['runtime_id']), identity)\n"
    "        if cleared is not None and cleared >= bounds['target']:\n"
    "            state['mode'] = 'known_result'\n"
    "            return\n"
    "        remote_rows = client.list()\n"
    "        if any(r['status'] in LIVE for r in remote_rows):\n"
    "            state['mode'] = 'blocked'\n"
    "            return\n"
    "        nonce = secrets.token_hex(6)\n"
    "        row = {**identity, **bounds, 'nonce': nonce, 'title': _title(nonce),\n"
    "               'outcome_titles': outcome_titles(**bounds),\n"
    "               'window': voting_window(bounds['target'], ending_window),\n"
    "               'attempted': False, 'status': 'INTENT'}\n"
    "        state['round'] = row\n",
    "        resume = _resume_prediction_context(root, identity)\n"
    "        if resume is not None:\n"
    "            target = resume['chapter']\n"
    "            if target is None:\n"
    "                # The game may continue, but do not invent the resumed chapter just to open a wager.\n"
    "                state['mode'] = 'pending'\n"
    "                return\n"
    "            bounds = {'target': target}\n"
    "        else:\n"
    "            bounds = thresholds(state['best_cleared'])\n"
    "            if bounds is None:\n"
    "                state['mode'] = 'complete_record'\n"
    "                return\n"
    "        cleared = progress.completed_count(runtime_directory(root, identity['runtime_id']), identity)\n"
    "        if cleared is not None and cleared >= bounds['target']:\n"
    "            state['mode'] = 'known_result'\n"
    "            return\n"
    "        remote_rows = client.list()\n"
    "        if any(r['status'] in LIVE for r in remote_rows):\n"
    "            state['mode'] = 'blocked'\n"
    "            return\n"
    "        nonce = secrets.token_hex(6)\n"
    "        if resume is not None:\n"
    "            row = {**identity, **bounds, 'kind': RESUME_PREDICTION_KIND, 'nonce': nonce,\n"
    "                   'title': _resume_title(bounds['target'], nonce),\n"
    "                   'outcome_titles': resume_outcome_titles(bounds['target']),\n"
    "                   'window': voting_window(bounds['target'], ending_window),\n"
    "                   'attempted': False, 'status': 'INTENT'}\n"
    "        else:\n"
    "            row = {**identity, **bounds, 'nonce': nonce, 'title': _title(nonce),\n"
    "                   'outcome_titles': outcome_titles(**bounds),\n"
    "                   'window': voting_window(bounds['target'], ending_window),\n"
    "                   'attempted': False, 'status': 'INTENT'}\n"
    "        state['round'] = row\n",
)

SOURCE.write_text(text)

TEST = Path("tests/test_hanjuku_resume_predictions.py")
TEST.write_text(r'''from dataclasses import replace
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
''')
