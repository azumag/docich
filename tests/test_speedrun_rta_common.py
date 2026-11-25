"""Common-side hook and generator-patch tests for the speedrun RTA records."""
from __future__ import annotations

import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys

from docich import game_switch, hanjuku_family
from docich.hanjuku_speedrun.rta import hook, store

BASE = 1_700_000_000.0
ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'hanjuku_speedrun_fork.py'


def make_runtime(state_dir, runtime_id='g1-abc123'):
    runtime = Path(state_dir) / 'runtimes' / runtime_id
    runtime.mkdir(parents=True)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b'frame')
    return runtime, shot


def start(runtime, shot):
    os.utime(shot, (BASE, BASE))
    return hook.observe(runtime, [], {},
                        {'hanjuku': {'name_entered': True,
                                     'phase': 'name',
                                     'gameplay_seen': False,
                                     'clear_ending_seen': False,
                                     'terminal_reason': None}},
                        screenshot=shot)


def test_hanjuku_hero_never_touches_the_rta_package(tmp_path, monkeypatch):
    calls = []

    def forbidden(game, name):
        calls.append((game, name))
        raise AssertionError('rta imported for hanjuku-hero')

    monkeypatch.setattr(game_switch, 'module', forbidden)
    state_dir = tmp_path / 'run-soren-live'
    assert game_switch.finalize_speedrun_attempts(
        state_dir, [{'game': 'hanjuku-hero', 'runtime_id': 'g1-abc123'}]) == 0
    assert game_switch.finalize_speedrun_attempts(
        state_dir, [{'game': 'sorengame', 'runtime_id': 'g2-def456'},
                    {'game': None, 'runtime_id': 'g3-ghi789'}]) == 0
    assert calls == []


def test_speedrun_retiring_runtime_is_finalized_once(tmp_path):
    state_dir = tmp_path / 'run-soren-live'
    runtime, shot = make_runtime(state_dir)
    start(runtime, shot)
    (runtime / 'hanjuku_run.json').write_text(
        '{"terminal_reason": null, "clear_ending_seen": false}',
        encoding='utf-8')
    entry = {'game': 'hanjuku-speedrun', 'runtime_id': 'g1-abc123',
             'generation': 1, 'adapter': 'retroarch'}
    warnings = []
    assert game_switch.finalize_speedrun_attempts(
        state_dir, [entry], warnings) == 1
    assert warnings == []
    assert store.load_timer(runtime)['status'] == 'dnf'
    assert store.load_timer(runtime)['dnf_reason'] == 'stopped'
    assert len(store.load_attempts(runtime)) == 1
    # A retry (cleanup_pending) must not append a second row.
    game_switch.finalize_speedrun_attempts(state_dir, [entry], warnings)
    assert len(store.load_attempts(runtime)) == 1


def test_speedrun_module_resolution_points_at_the_rta_package():
    store_module = hanjuku_family.module(hanjuku_family.SPEEDRUN, 'rta.store')
    assert store_module is store
    assert store_module.__name__ == 'docich.hanjuku_speedrun.rta.store'


def test_recovery_uses_the_timer_owners_run_state(tmp_path):
    state_dir = tmp_path / 'run-soren-live'
    runtime_a, shot_a = make_runtime(state_dir, 'g1-abc123')
    start(runtime_a, shot_a)
    (runtime_a / 'hanjuku_run.json').write_text(json.dumps({
        'phase': 'title', 'name_entered': True, 'gameplay_seen': True,
        'clear_ending_seen': False, 'terminal_reason': 'game_over',
    }), encoding='utf-8')
    runtime_b, shot_b = make_runtime(state_dir, 'g2-def456')
    os.utime(shot_b, (BASE + 100, BASE + 100))
    result = hook.observe(
        runtime_b, [], {},
        {'hanjuku': {'phase': 'battle', 'gameplay_seen': True,
                     'terminal_reason': None}},
        screenshot=shot_b)
    rows = store.load_attempts(runtime_b)
    assert len(rows) == 1
    # The stale attempt of runtime A ends as A actually ended (game over),
    # not as a generic "stopped" from runtime B's missing run state.
    assert rows[0]['runtime_id'] == 'g1-abc123'
    assert rows[0]['status'] == 'dnf'
    assert rows[0]['dnf_reason'] == 'game_over'
    # The mid-run observation in B does not invent an attempt.
    assert result['runtime_id'] == 'g2-def456'
    assert result['status'] == 'pending'
    assert result['reason'] == 'no_start_seen'


def test_recovery_of_a_deleted_owner_runtime_is_stopped(tmp_path):
    state_dir = tmp_path / 'run-soren-live'
    runtime_a, shot_a = make_runtime(state_dir, 'g1-abc123')
    start(runtime_a, shot_a)
    for path in sorted(runtime_a.rglob('*'), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    runtime_a.rmdir()
    runtime_b, shot_b = make_runtime(state_dir, 'g2-def456')
    os.utime(shot_b, (BASE + 100, BASE + 100))
    hook.observe(runtime_b, [], {},
                 {'hanjuku': {'phase': 'battle', 'gameplay_seen': True,
                              'terminal_reason': None}},
                 screenshot=shot_b)
    rows = store.load_attempts(runtime_b)
    assert len(rows) == 1
    assert rows[0]['runtime_id'] == 'g1-abc123'
    assert rows[0]['dnf_reason'] == 'stopped'
    assert rows[0]['reason'] == 'recovered'


def test_generator_adds_the_rta_side_effect_and_check_passes(tmp_path):
    output = tmp_path / 'generated'
    result = subprocess.run(
        [sys.executable, str(TOOL), 'generate', '--source', 'HEAD',
         '--no-tests', '--output-root', str(output)],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    generated = (output / 'brains/hanjuku_speedrun/bot.py').read_text(
        encoding='utf-8')
    original = (ROOT / 'brains/hanjuku/bot.py').read_text(encoding='utf-8')
    assert generated != original
    assert ('from docich.hanjuku_speedrun.rta import hook as rta_hook'
            in generated)
    assert generated.count("('rta_failed'") == 1
    assert "rta_hook.observe(runtime, records, state, meta, " in generated
    assert "screenshot=Path(obs['screenshot'])" in generated
    assert generated.index("('persist_failed'") < generated.index("('rta_failed'")
    # The effect sits inside the same stderr-per-line loop as the others.
    loop_start = generated.index('for label, effect in (')
    loop_end = generated.index('if isinstance(updated_experience,dict):',
                               loop_start)
    loop = generated[loop_start:loop_end]
    assert "('rta_failed'" in loop
    py_compile.compile(str(output / 'brains/hanjuku_speedrun/bot.py'),
                       doraise=True)

    check = subprocess.run(
        [sys.executable, str(TOOL), 'check', '--source', 'HEAD',
         '--no-tests', '--output-root', str(output)],
        cwd=ROOT, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert 'check: OK' in check.stdout
