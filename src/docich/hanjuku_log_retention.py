"""Bounded retention for completed Hanjuku decision/event logs (docich#3132).

Every hanjuku-hero runtime generation writes four append_log files (current
and ``.previous`` for decisions and events). ``append_log`` already rotates
each file at MAX_LOG_BYTES, but nothing limits how many completed generations
stay on disk, so the total grows with every run. ``prune_completed`` keeps the
newest HANJUKU_LOG_RETENTION_GENERATIONS completed generations and removes only
the four allowlisted files from older ones. Generation directories, state
files and every other artifact are left alone. Called from game-switch
post-terminal housekeeping; the caller treats failures as warnings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
from typing import Callable

from .naming import RUNTIME_ID_PATTERN

# Named policy constant: changing it requires a reviewed PR (DSH decision, #3132).
HANJUKU_LOG_RETENTION_GENERATIONS = 10
# Bounds one housekeeping pass; the initial convergence spans several passes.
MAX_GENERATIONS_PER_RUN = 16
MAX_RUNTIME_ENTRIES = 4096
MAX_RUN_BYTES = 1024 * 1024
LOG_FILES = (
    'hanjuku_decisions.jsonl', 'hanjuku_decisions.previous.jsonl',
    'hanjuku_events.jsonl', 'hanjuku_events.previous.jsonl',
)
RUN_FILE = 'hanjuku_run.json'
GAME = 'hanjuku-hero'
TERMINAL_REASONS = frozenset({'game_over', 'screen_stalled', 'input_stalled'})
CANONICAL_KEYS = ('active', 'candidate', 'previous')


class RetentionError(Exception):
    """Fixed-code failure; never carries paths or log content."""


@dataclass
class PruneResult:
    generations: list[str] = field(default_factory=list)
    files_removed: int = 0
    bytes_removed: int = 0
    skipped: int = 0


def _protected_ids(state: dict) -> set[str]:
    """Runtime ids the game switch still owns (active, switching, retiring)."""
    ids: set[str] = set()
    for key in CANONICAL_KEYS:
        entry = state.get(key)
        if entry is None:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get('runtime_id'), str):
            raise RetentionError('canonical_unverified')
        ids.add(entry['runtime_id'])
    retiring = state.get('retiring') or []
    if not isinstance(retiring, list):
        raise RetentionError('canonical_unverified')
    for entry in retiring:
        if not isinstance(entry, dict) or not isinstance(entry.get('runtime_id'), str):
            raise RetentionError('canonical_unverified')
        ids.add(entry['runtime_id'])
    return ids


def _completed(runtime_dir: Path, name: str) -> bool:
    """True only for a hanjuku-hero generation whose run state is terminal.

    Conservative mirror of the terminal identity checks: anything unexpected
    is treated as not completed, so it is never pruned.
    """
    match = RUNTIME_ID_PATTERN.fullmatch(name)
    if match is None:
        return False
    run_path = runtime_dir / RUN_FILE
    try:
        meta = os.lstat(run_path)
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > MAX_RUN_BYTES:
            return False
        run = json.loads(run_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return (isinstance(run, dict)
            and type(run.get('schema')) is int and run['schema'] == 1
            and run.get('game') == GAME
            and run.get('runtime_id') == name
            and type(run.get('generation')) is int and run['generation'] == int(match[1])
            and isinstance(run.get('lease_id'), str) and bool(run['lease_id'])
            and run.get('terminal_reason') in TERMINAL_REASONS)


def _collect(runtimes: Path, protected: set[str]) -> list[tuple[int, str]]:
    try:
        with os.scandir(runtimes) as entries:
            names = []
            for index, entry in enumerate(entries):
                if index >= MAX_RUNTIME_ENTRIES:
                    raise RetentionError('runtime_scan_limit')
                names.append(entry.name)
    except OSError:
        raise RetentionError('runtime_scan') from None
    completed = []
    for name in names:
        match = RUNTIME_ID_PATTERN.fullmatch(name)
        if match is None or name in protected:
            continue
        runtime_dir = runtimes / name
        if runtime_dir.is_symlink() or not runtime_dir.is_dir():
            continue
        if _completed(runtime_dir, name):
            completed.append((int(match[1]), name))
    completed.sort(reverse=True)
    return completed


def _remove_allowlisted(runtime_dir: Path) -> tuple[list[str], int]:
    """Unlink the allowlisted logs in one generation, or none at all.

    Every present file is checked before anything is removed, so a symlink or
    hardlink in the generation skips the whole generation (fail-closed).
    """
    planned = []
    for name in LOG_FILES:
        path = runtime_dir / name
        try:
            meta = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            raise RetentionError('unsafe_file') from None
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
            raise RetentionError('unsafe_file')
        planned.append((path, meta.st_size))
    removed, size = [], 0
    for path, bytes_ in planned:
        os.unlink(path)
        removed.append(path.name)
        size += bytes_
    return removed, size


def prune_completed(state_dir: Path, reload: Callable[[], dict], *,
                    keep: int = HANJUKU_LOG_RETENTION_GENERATIONS,
                    limit: int = MAX_GENERATIONS_PER_RUN) -> PruneResult:
    """Remove allowlisted logs from completed hanjuku generations beyond ``keep``.

    Caller must hold the game-switch lock. ``reload`` returns the canonical
    state and is consulted again before each generation is touched, so a
    generation that became active or retiring after the scan is skipped.
    """
    if type(keep) is not int or keep < 0 or type(limit) is not int or limit < 1:
        raise RetentionError('invalid_policy')
    result = PruneResult()
    runtimes = Path(state_dir) / 'runtimes'
    if not runtimes.is_dir() or runtimes.is_symlink():
        return result
    protected = _protected_ids(reload())
    completed = _collect(runtimes, protected)
    # The budget counts only generations actually pruned in this pass. A
    # completed generation whose logs are already gone stays in the list (its
    # run state is terminal) and must not occupy a slot, or later passes would
    # keep selecting the same window and never reach older generations.
    for _generation, name in completed[keep:]:
        if len(result.generations) >= limit:
            break
        runtime_dir = runtimes / name
        if not any(os.path.lexists(runtime_dir / log) for log in LOG_FILES):
            continue
        if name in _protected_ids(reload()) or not _completed(runtime_dir, name):
            result.skipped += 1
            continue
        try:
            removed, size = _remove_allowlisted(runtime_dir)
        except RetentionError:
            result.skipped += 1
            continue
        if removed:
            result.generations.append(name)
            result.files_removed += len(removed)
            result.bytes_removed += size
    return result
