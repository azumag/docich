"""The existing radio dispatcher accepts an active Hanjuku corner.

Only the provider backend is replaced. Queue/role/policy/budget code comes from
the pinned Soren source, and every write remains in a temporary fixture root.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
PINNED_LIB = ROOT / 'games/soviet_now/lib'


def test_hanjuku_active_with_game_only_pauses_uses_existing_radio_dispatcher(tmp_path):
    libraries = ['ai_generate.sh', 'ai_generate_policy.sh', 'ai_prepass_budget.sh', 'ai_queue_observability.sh']
    if not all((PINNED_LIB / name).is_file() for name in libraries):
        pytest.skip('pinned Soren dispatcher source is not checked out')
    root = tmp_path / 'soren'
    (root / 'tmp/state').mkdir(parents=True)
    active = root / 'active_game.json'
    active.write_text(json.dumps({'phase': 'ready', 'active': {'game': 'hanjuku-hero'}}))
    markers = [root / f'tmp/state/{name}.paused' for name in
               ('improve_daemon', 'prediction_worker', 'soviet_watchdog', 'soren_loop')]
    for marker in markers:
        marker.write_text('game-lifecycle-fixture\n')
    bootstrap = ('ELOOP_LIB_DIR=' + shlex.quote(str(root)) + '\n'
                 'SOREN_ACTIVE_GAME_CONTEXT_FILE=' + shlex.quote(str(active)) + '\n'
                 "BATCH_COMMENTARY_AGENTS='opencode:fixture-only'\n"
                 'AI_GENERATION_QUEUE_ENABLED=1\nAI_RADIO_LANE_LOCK=1\n'
                 'log() { :; }\n')
    bootstrap += ''.join('source ' + shlex.quote(str(PINNED_LIB / name)) + '\n' for name in libraries)
    bootstrap += '''
_scene_fixture_output() {
  [ -f tmp/state/.ai_generation_locks/radio/owner ] || return 96
  [ "$(sed -n 's/^pid=//p' tmp/state/.ai_generation_locks/radio/owner)" = "$AI_GENERATION_QUEUE_OWNER_PID" ] || return 97
  printf '%s' 'radio-owned' > reached_backend
  printf '%s' '{"text":"開戦時のココットのHPは40でした。","fact_ids":["f1"]}'
}
_ai_call_opencode() { _ai_generation_queue_run "$1" _scene_fixture_output; }
_ai_call_codex() { return 99; }
_ai_call_local_llm() { return 99; }
'''
    (root / 'eloop_lib.sh').write_text(bootstrap)
    paths = [tmp_path / name for name in ('prompt', 'output', 'agent', 'failure', 'meta')]
    paths[0].write_text('fixture observations only')
    result = subprocess.run(
        ['bash', str(ROOT / 'scripts/hanjuku_scene_generate.sh'), str(root),
         *(str(path) for path in paths), '5'],
        env={**os.environ, 'DOCICH_ALLOW_REAL_AI': '1'},
        capture_output=True, text=True, timeout=8,
    )
    assert result.returncode == 0, paths[3].read_text() if paths[3].exists() else 'no failure receipt'
    assert result.stdout == result.stderr == ''
    assert (root / 'reached_backend').read_text() == 'radio-owned'
    assert json.loads(paths[4].read_text()) == {'role': 'BATCH_COMMENTARY_AGENTS'}
    assert json.loads(paths[1].read_text())['fact_ids'] == ['f1']
    assert not (root / 'tmp/state/.ai_generation_locks/radio').exists()
    assert all(marker.read_text() == 'game-lifecycle-fixture\n' for marker in markers)
    assert json.loads(active.read_text())['active']['game'] == 'hanjuku-hero'
