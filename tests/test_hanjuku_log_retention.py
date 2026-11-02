import json
import os

import pytest

from docich import hanjuku_log_retention as retention
from docich.hanjuku_log_retention import RetentionError, prune_completed

LOGS = retention.LOG_FILES


def _canonical(active=None, retiring=()):
    return {
        'schema_version': 2, 'phase': 'ready' if active else 'idle', 'active': active,
        'candidate': None, 'previous': None, 'retiring': list(retiring),
        'operation': None, 'request_id': None, 'next_generation': 100, 'revision': 1,
    }


def _generation(state_dir, number, *, terminal='game_over', game='hanjuku-hero', files=LOGS):
    name = f'g{number}-{"a" * 8}'
    runtime = state_dir / 'runtimes' / name
    runtime.mkdir(parents=True)
    run = {'schema': 1, 'game': game, 'runtime_id': name, 'generation': number,
           'lease_id': 'lease-1', 'terminal_reason': terminal}
    (runtime / 'hanjuku_run.json').write_text(json.dumps(run), encoding='utf-8')
    for log in files:
        (runtime / log).write_bytes(b'x' * 10)
    return name


def _exists(state_dir, name, log):
    return (state_dir / 'runtimes' / name / log).exists()


def test_keeps_newest_completed_and_prunes_older(tmp_path):
    names = {n: _generation(tmp_path, n) for n in range(1, 13)}
    result = prune_completed(tmp_path, lambda: _canonical(), keep=10)
    assert sorted(result.generations) == sorted([names[1], names[2]])
    for log in LOGS:
        assert not _exists(tmp_path, names[1], log)
        assert _exists(tmp_path, names[3], log)
    assert result.files_removed == 8
    assert result.bytes_removed == 80


def test_active_and_retiring_generations_are_never_touched(tmp_path):
    old = _generation(tmp_path, 1)
    retiring = _generation(tmp_path, 2)
    for n in range(3, 15):
        _generation(tmp_path, n)
    canonical = _canonical(
        active={'runtime_id': 'g14-' + 'a' * 8},
        retiring=[{'runtime_id': retiring}],
    )
    prune_completed(tmp_path, lambda: canonical, keep=10)
    assert _exists(tmp_path, old, LOGS[0]) is False
    assert _exists(tmp_path, retiring, LOGS[0]) is True


def test_reloaded_canonical_state_protects_generation_switched_after_scan(tmp_path):
    victim = _generation(tmp_path, 1)
    for n in range(2, 13):
        _generation(tmp_path, n)
    # First read is the pre-scan snapshot; from the second read on, the victim
    # is retiring, as if a switch started while the pass was running.
    reads = []

    def reload():
        reads.append(1)
        return _canonical() if len(reads) == 1 else _canonical(retiring=[{'runtime_id': victim}])

    result = prune_completed(tmp_path, reload, keep=10)
    assert victim not in result.generations
    assert _exists(tmp_path, victim, LOGS[0]) is True


def test_non_hanjuku_and_non_run_ids_are_ignored(tmp_path):
    other = _generation(tmp_path, 1, game='nethack')
    manual = tmp_path / 'runtimes' / 'gtest-manual'
    manual.mkdir(parents=True)
    (manual / LOGS[0]).write_text('x')
    for n in range(2, 14):
        _generation(tmp_path, n)
    prune_completed(tmp_path, lambda: _canonical(), keep=10)
    assert _exists(tmp_path, other, LOGS[0]) is True
    assert (manual / LOGS[0]).exists()


def test_non_terminal_generation_is_not_pruned(tmp_path):
    running = _generation(tmp_path, 1, terminal=None)
    for n in range(2, 13):
        _generation(tmp_path, n)
    prune_completed(tmp_path, lambda: _canonical(), keep=10)
    assert _exists(tmp_path, running, LOGS[0]) is True


def test_only_allowlisted_files_are_removed(tmp_path):
    extra = ('hanjuku_commentary.jsonl', 'hanjuku_bot.json')
    name = _generation(tmp_path, 1, files=LOGS + extra)
    for n in range(2, 13):
        _generation(tmp_path, n)
    prune_completed(tmp_path, lambda: _canonical(), keep=10)
    for log in extra:
        assert _exists(tmp_path, name, log)
    assert (tmp_path / 'runtimes' / name / 'hanjuku_run.json').exists()


def test_symlinked_log_skips_generation_without_partial_delete(tmp_path):
    name = _generation(tmp_path, 1)
    runtime = tmp_path / 'runtimes' / name
    (runtime / LOGS[0]).unlink()
    target = tmp_path / 'outside.jsonl'
    target.write_text('x')
    os.symlink(target, runtime / LOGS[0])
    for n in range(2, 13):
        _generation(tmp_path, n)
    result = prune_completed(tmp_path, lambda: _canonical(), keep=10)
    assert name not in result.generations
    assert _exists(tmp_path, name, LOGS[1]) is True
    assert target.exists()


def test_idempotent_second_pass_removes_nothing(tmp_path):
    for n in range(1, 13):
        _generation(tmp_path, n)
    first = prune_completed(tmp_path, lambda: _canonical(), keep=10)
    second = prune_completed(tmp_path, lambda: _canonical(), keep=10)
    assert first.generations and second.generations == []
    assert second.files_removed == 0


def test_limit_bounds_generations_per_pass(tmp_path):
    for n in range(1, 31):
        _generation(tmp_path, n)
    result = prune_completed(tmp_path, lambda: _canonical(), keep=10, limit=5)
    assert len(result.generations) == 5


def test_malformed_canonical_state_fails_closed(tmp_path):
    _generation(tmp_path, 1)
    with pytest.raises(RetentionError):
        prune_completed(tmp_path, lambda: _canonical(active='not-a-dict'), keep=0)


def test_missing_runtimes_is_noop(tmp_path):
    result = prune_completed(tmp_path, lambda: _canonical())
    assert result.generations == []


def test_repeated_passes_converge_past_first_window(tmp_path):
    # Regression for the position-based window: with more completed generations
    # than one pass can cover, later passes must still reach the older ones.
    for n in range(1, 31):
        _generation(tmp_path, n)
    passes = 0
    while True:
        passes += 1
        result = prune_completed(tmp_path, lambda: _canonical(), keep=10, limit=5)
        if not result.generations:
            break
    assert passes > 1
    remaining = [n for n in range(1, 31) if _exists(tmp_path, f'g{n}-{"a" * 8}', LOGS[0])]
    assert remaining == list(range(21, 31))


def test_policy_constants_match_run_state_definitions(tmp_path):
    from docich import hanjuku_run
    assert retention.RUN_FILE == hanjuku_run.RUN_FILE
    assert retention.TERMINAL_REASONS == hanjuku_run.TERMINAL_REASONS
    assert retention.GAME == 'hanjuku-hero'
