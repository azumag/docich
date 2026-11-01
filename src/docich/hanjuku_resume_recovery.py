"""Recover one failed-before-AI checkpoint transfer, never a game over.

The logical resume and sealed checkpoint stay unchanged. The archived switch
receipt stays terminal; a single derived execution attempt has its own receipt.
No process is stopped, no state is loaded here, and no newer save is selected.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import time
import uuid

from .config import load_global, load_game
from .corner_catalog import rotation_enabled
from .corner_rotation import CornerRotationManager, timestamp
from .game_switch import GameSwitchStore, atomic_write_json, validate_request_id
from .hanjuku_manual_cancel import (
    CancelRefused, DIGEST, _existing_lock, _object, _released_runtime, _ProbeTmux,
)
from .hanjuku_resume import REQUEST_FILE, RECEIPT_FILE, START_POLICY, core_digest
from .naming import runtime_directory, runtime_id_generation, runtime_names
from .retroarch_boundary import BOUNDARY_FILE, MANUAL_SAVE_FILE, checkpoint_digest
from .tmux import SESSION
from .trading.soren_output import resolve_soren_root


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _no_newer_hanjuku_progress(base, store, generation):
    """Do not replay a sealed checkpoint over any newer Hanjuku execution."""
    for receipt in store.receipts.receipts():
        result = receipt.get('result') or {}
        if ((receipt.get('target') == 'hanjuku-hero'
             or (isinstance(result, dict) and result.get('to_game') == 'hanjuku-hero'))
                and receipt.get('generation', 0) > generation
                and receipt.get('status') not in {'failed', 'rolled_back'}):
            return False
    runtimes = base / 'runtimes'
    if runtimes.is_symlink() or not runtimes.is_dir():
        return False
    for runtime in runtimes.glob('g*'):
        if runtime.is_symlink():
            return False
        if not runtime.is_dir():
            continue
        relevant = any((runtime / n).exists() or (runtime / n).is_symlink()
                       for n in ('hanjuku_run.json', RECEIPT_FILE, MANUAL_SAVE_FILE, BOUNDARY_FILE))
        states = runtime / 'states'
        if states.is_symlink():
            return False
        relevant = relevant or any(states.glob('hanjuku-hero.state*'))
        if relevant and runtime_id_generation(runtime.name) > generation:
            return False
    return True


def queue_failed_start(g, *, tmux=None, now=time.time):
    """Handle an explicit WebUI resume of an interrupted transfer, if present."""
    request = _object(Path(g.state_dir) / 'hanjuku-resume' / REQUEST_FILE, optional=True)
    if not request:
        return None
    recovery = request.get('startup_recovery')
    if request.get('status') != 'claimed' and not (
            isinstance(recovery, dict) and request.get('status') == 'queued'):
        return None
    if isinstance(recovery, dict) and (request.get('status') == 'claimed'
                                      or 'failed_attempt' in recovery):
        return _queue_failed_retry(g, tmux=tmux, now=now)
    original = recovery.get('original') if isinstance(recovery, dict) else request
    if not isinstance(original, dict):
        raise CancelRefused('invalid_evidence')
    logical = validate_request_id(original.get('request_id'))
    result = recover(g, expected=fingerprint(original), apply=True, tmux=tmux, now=now)
    if result.get('status') not in {'recovery-queued', 'already-dispatched'}:
        raise CancelRefused('reservation_not_in_scope')
    current = _object(Path(g.state_dir) / 'hanjuku-resume' / REQUEST_FILE)
    attempt = (current.get('startup_recovery') or {}).get('attempt_id')
    if attempt not in _attempt_ids(logical):
        raise CancelRefused('invalid_evidence')
    return {'status': 'queued', 'corner': 'hanjuku-hero', 'request_id': attempt}


def _attempt_ids(logical):
    return tuple(str(uuid.uuid5(uuid.UUID(logical), f'failed-before-ai-recovery-{n}')) for n in (1, 2))


def _released_unclaimed_attempt(base, store, state, attempt, tmux):
    """Prove a derived attempt was refused before it claimed, then released.

    g1073 2026-10-28: the first attempt's request still said
    explicit_resume=False, so claim refused the identity before writing any
    local receipt or checkpoint copy. The rotation released that slot; the
    request stayed ``queued`` and every later resume was out of scope.
    """
    released = (state.get('released_requests') or {}).get('hanjuku-hero') or {}
    if released.get('request_id') != attempt and not any(
            isinstance(row, dict) and row.get('corner') == 'hanjuku-hero'
            and row.get('request_id') == attempt and row.get('resolution') == 'released-unverified'
            for row in (state.get('failed_corners') or [])):
        return False
    receipt = store.receipts.load(attempt)
    result = receipt.get('result') if receipt else None
    if (not receipt or receipt.get('operation') != 'switch'
            or receipt.get('target') != 'hanjuku-hero'
            or receipt.get('status') not in {'failed', 'rolled_back'}
            or not isinstance(result, dict) or result.get('request_id') != attempt
            or result.get('status') != receipt['status']
            or result.get('cleanup_pending') not in (None, False)):
        return False
    runtime = runtime_directory(base, receipt.get('runtime_id'))
    if runtime.is_symlink() or (runtime / 'states').is_symlink():
        return False
    if any((runtime / name).exists() or (runtime / name).is_symlink()
           for name in (RECEIPT_FILE, 'presentation.json', 'hanjuku_bot.json',
                        'hanjuku_run.json', 'hanjuku_events.jsonl')):
        return False
    if any((runtime / 'states').glob('hanjuku-hero.state*')):
        return False
    names = runtime_names(runtime_id_generation(runtime.name))
    return not (any(tmux.window_target_exists(f'{SESSION}:{name}', strict=True)
                    for name in (names.game_window, names.agent_window))
                or tmux.session_target_exists(names.adapter_session, strict=True))


def _queue_failed_retry(g, *, tmux=None, now=time.time):
    """One explicit retry after the derived attempt itself rolled back pre-AI."""
    base = Path(g.state_dir)
    manager = CornerRotationManager(g)
    store = GameSwitchStore(base)
    tmux = tmux or _ProbeTmux()
    with ExitStack() as held:
        for path in (base / 'locks/corner-rotation.lock', base / 'corner-manual-queue.lock',
                     base / 'locks/retro-corner.lock', base / 'locks/retro-corner-manual.lock',
                     resolve_soren_root(g) / 'tmp/state/docich_program.lock',
                     base / 'locks/game-switch.lock', base / 'hanjuku-resume/request.lock'):
            held.enter_context(_existing_lock(path))
        path = base / 'hanjuku-resume' / REQUEST_FILE
        request = _object(path)
        recovery = request.get('startup_recovery')
        if not isinstance(recovery, dict):
            raise CancelRefused('reservation_not_in_scope')
        failed = recovery.get('failed_attempt')
        claim = failed.get('claim') if isinstance(failed, dict) else request
        previous = claim.get('startup_recovery') if isinstance(claim, dict) else None
        original = previous.get('original') if isinstance(previous, dict) else None
        if (not isinstance(original, dict) or type(original.get('schema')) is not int
                or original.get('schema') != 1
                or original.get('status') != 'claimed' or original.get('game') != 'hanjuku-hero'
                or original.get('corner') != 'hanjuku-hero' or original.get('start_policy') != START_POLICY
                or not isinstance(claim, dict) or type(claim.get('schema')) is not int
                or claim.get('schema') != 1 or claim.get('status') != 'claimed'
                or claim.get('start_mode') != 'resume' or claim.get('game') != 'hanjuku-hero'
                or claim.get('corner') != 'hanjuku-hero' or type(claim.get('generation')) is not int
                or original.get('startup_recovery') is not None):
            raise CancelRefused('reservation_not_in_scope')
        logical = validate_request_id(claim.get('request_id'))
        first = str(uuid.uuid5(uuid.UUID(logical), 'failed-before-ai-recovery-1'))
        second = str(uuid.uuid5(uuid.UUID(logical), 'failed-before-ai-recovery-2'))
        if (original.get('request_id') != logical or previous.get('attempt_id') != first
                or previous.get('fingerprint') != fingerprint(original)
                or any(claim.get(k) != original.get(k) for k in (
                    'start_policy', 'start_mode', 'source_boundary', 'sha256', 'bytes',
                    'selected_source', 'core_state_sha256', 'rom_sha256', 'core_sha256'))):
            raise CancelRefused('invalid_evidence')
        from .hanjuku_resume import _start_mode
        if _start_mode(claim) != 'resume':
            raise CancelRefused('reservation_not_in_scope')
        if failed is not None:
            if (type(request.get('schema')) is not int or request.get('schema') != 1
                    or request.get('status') != 'queued' or recovery.get('attempt_id') != second
                    or recovery.get('original') != original
                    or recovery.get('fingerprint') != fingerprint(original)
                    or failed.get('attempt_id') != first
                    or failed.get('fingerprint') != fingerprint(claim)
                    or request.get('request_id') != logical
                    or any(request.get(k) != claim.get(k) for k in (
                        'start_policy', 'start_mode', 'source_boundary', 'sha256', 'bytes', 'selected_source',
                        'core_state_sha256', 'rom_sha256', 'core_sha256'))):
                raise CancelRefused('invalid_evidence')
        elif request != claim:
            raise CancelRefused('context_changed')
        validate_request_id(claim.get('lease_id'))
        if runtime_id_generation(claim.get('runtime_id')) != claim['generation']:
            raise CancelRefused('invalid_evidence')
        runtime = runtime_directory(base, claim['runtime_id'])
        if _object(runtime / RECEIPT_FILE) != claim:
            raise CancelRefused('context_changed')
        if any((runtime / name).exists() or (runtime / name).is_symlink()
               for name in ('hanjuku_bot.json', 'hanjuku_run.json', 'hanjuku_events.jsonl')):
            raise CancelRefused('gameplay_started')
        _released_runtime(base, runtime.name, tmux)
        terminal = store.receipts.load(first)
        result = terminal.get('result') if terminal else None
        if (not terminal or terminal.get('operation') != 'switch'
                or terminal.get('target') != 'hanjuku-hero' or terminal.get('status') != 'rolled_back'
                or terminal.get('generation') != claim['generation']
                or terminal.get('runtime_id') != claim['runtime_id']
                or not isinstance(result, dict) or result.get('status') != 'rolled_back'
                or result.get('request_id') != first or result.get('generation') != claim['generation']):
            raise CancelRefused('startup_rollback_unverified')
        canonical, missing = store.canonical.load()
        if (missing or canonical.get('phase') not in {'ready', 'idle'}
                or any(canonical.get(k) for k in ('candidate', 'previous', 'retiring', 'operation', 'request_id'))
                or (canonical.get('active') or {}).get('game') == 'hanjuku-hero'):
            raise CancelRefused('switch_not_stable')
        if not _no_newer_hanjuku_progress(base, store, claim['generation']):
            raise CancelRefused('newer_progress_unverified')
        if not rotation_enabled(g) or 'hanjuku-hero' not in manager._eligible(include_manual_only=True)[0]:
            raise CancelRefused('target_paused_or_unavailable')
        from .adapters.retroarch import resolve_rom, resolve_core
        game = load_game(g, 'hanjuku-hero')
        sealed = base / 'hanjuku-resume' / (logical + '.state')
        if (checkpoint_digest(sealed) != claim.get('sha256')
                or core_digest(sealed) != claim.get('core_state_sha256')
                or checkpoint_digest(resolve_rom(g, game)) != claim.get('rom_sha256')
                or checkpoint_digest(Path(resolve_core(game))) != claim.get('core_sha256')):
            raise CancelRefused('checkpoint_changed')
        at = timestamp(now())
        state = manager.load(at)
        if at < max(timestamp(claim['selected_at']), timestamp(state['last_seen_at'])):
            raise CancelRefused('clock_regressed')
        queued = state.get('queued_manual') or {}
        if (state.get('status') not in {'ready', 'waiting'} or state.get('pending')
                or state.get('manual_pending') or manager._read_manual_queue()
                or (queued and (queued.get('request_id') != second
                                or queued.get('resume_request_id') != logical))):
            raise CancelRefused('manual_queue_conflict')
        owner = _object(base / 'retro_corner.json')
        if owner.get('status') not in {'idle', 'completed', 'interrupted', 'expired'}:
            raise CancelRefused('owner_not_terminal')
        if failed is None:
            audit = {'claim': claim, 'attempt_id': first, 'fingerprint': fingerprint(claim)}
            new_recovery = dict(previous, attempt_id=second, failed_attempt=audit)
            request = {k: v for k, v in claim.items()
                       if k not in {'runtime_id', 'generation', 'lease_id', 'game'}}
            request.update(status='queued', startup_recovery=new_recovery,
                           dispatch_source='manual', explicit_resume=True)
            atomic_write_json(path, request)
        if not queued:
            queued = dict(corner='hanjuku-hero', selected_at=at, request_id=second,
                          resume_request_id=logical, resume_latest=True)
            state.update(status='waiting', reason='startup-resume-recovery-queued',
                         error_kind=None, pending=None, queued_manual=queued)
            manager.save(state)
        return {'status':'queued', 'corner':'hanjuku-hero', 'request_id':second}


def recover(g, *, expected=None, apply=False, tmux=None, now=time.time):
    if apply and (not isinstance(expected, str) or not DIGEST.fullmatch(expected)):
        raise CancelRefused('expected_reservation_required')
    base = Path(g.state_dir)
    manager = CornerRotationManager(g)
    store = GameSwitchStore(base)
    tmux = tmux or _ProbeTmux()
    with ExitStack() as held:
        for path in (
            base / 'locks/corner-rotation.lock',
            base / 'corner-manual-queue.lock',
            base / 'locks/retro-corner.lock',
            base / 'locks/retro-corner-manual.lock',
            resolve_soren_root(g) / 'tmp/state/docich_program.lock',
            base / 'locks/game-switch.lock',
        ):
            held.enter_context(_existing_lock(path))
        root = base / 'hanjuku-resume'
        held.enter_context(_existing_lock(root / 'request.lock'))
        request = _object(root / REQUEST_FILE)
        recovery = request.get('startup_recovery')
        original = recovery.get('original') if isinstance(recovery, dict) else request
        if (not isinstance(original, dict) or original.get('schema') != 1
                or original.get('game') != 'hanjuku-hero'
                or original.get('corner') != 'hanjuku-hero'
                or original.get('status') != 'claimed'
                or type(original.get('generation')) is not int):
            raise CancelRefused('reservation_not_in_scope')
        logical = validate_request_id(original.get('request_id'))
        automatic = (original.get('start_policy') == START_POLICY
                     and original.get('start_mode') == 'resume'
                     and original.get('dispatch_source') == 'automatic'
                     and original.get('explicit_resume') is False)
        validate_request_id(original.get('lease_id'))
        if runtime_id_generation(original.get('runtime_id')) != original['generation']:
            raise CancelRefused('invalid_evidence')
        digest = fingerprint(original)
        if expected is not None and expected != digest:
            raise CancelRefused('reservation_changed')
        attempt, retry = _attempt_ids(logical)
        unclaimed = recovery.get('unclaimed_attempt') if isinstance(recovery, dict) else None
        current = attempt if unclaimed is None else retry
        if recovery is not None:
            if (recovery.get('attempt_id') != current or recovery.get('fingerprint') != digest
                    or (unclaimed is not None and unclaimed != {'attempt_id': attempt})
                    or request.get('request_id') != logical):
                raise CancelRefused('invalid_evidence')
            # An operator retry cannot rewind an already claimed/verified
            # attempt, create another UUID, or reset its execution receipt.
            if request.get('status') in {'claimed', 'verified'}:
                return {'status': 'already-dispatched', 'fingerprint': digest}
            if request.get('status') != 'queued':
                raise CancelRefused('reservation_not_in_scope')
        elif request.get('status') != 'claimed':
            raise CancelRefused('reservation_not_in_scope')
        runtime = runtime_directory(base, original.get('runtime_id'))
        if _object(runtime / RECEIPT_FILE) != original:
            raise CancelRefused('context_changed')
        # These are created before the AI can issue gameplay. Any such evidence
        # (including an empty file) excludes game over, stall and partial play.
        for name in ('hanjuku_bot.json', 'hanjuku_run.json', 'hanjuku_events.jsonl'):
            path = runtime / name
            if path.exists() or path.is_symlink():
                raise CancelRefused('gameplay_started')
        _released_runtime(base, runtime.name, tmux)
        terminal = store.receipts.load(logical)
        result = terminal.get('result') if terminal else None
        if (not terminal or terminal.get('operation') != 'switch'
                or terminal.get('target') != 'hanjuku-hero'
                or terminal.get('status') != 'rolled_back'
                or terminal.get('generation') != original['generation']
                or terminal.get('runtime_id') != original['runtime_id']
                or not isinstance(result, dict) or result.get('status') != 'rolled_back'
                or result.get('request_id') != logical
                or result.get('generation') != original['generation']):
            raise CancelRefused('startup_rollback_unverified')
        canonical, missing = store.canonical.load()
        if (missing or canonical.get('phase') not in {'ready', 'idle'}
                or canonical.get('candidate') is not None or canonical.get('previous') is not None
                or canonical.get('retiring') or canonical.get('operation') is not None
                or canonical.get('request_id') is not None
                or (canonical.get('active') or {}).get('game') == 'hanjuku-hero'):
            raise CancelRefused('switch_not_stable')
        if automatic and not _no_newer_hanjuku_progress(base, store, original['generation']):
            raise CancelRefused('newer_progress_unverified')
        if not rotation_enabled(g) or 'hanjuku-hero' not in manager._eligible(include_manual_only=True)[0]:
            raise CancelRefused('target_paused_or_unavailable')
        from .adapters.retroarch import resolve_rom, resolve_core
        game = load_game(g, 'hanjuku-hero')
        sealed = root / (logical + '.state')
        if (checkpoint_digest(sealed) != original.get('sha256')
                or core_digest(sealed) != original.get('core_state_sha256')
                or checkpoint_digest(resolve_rom(g, game)) != original.get('rom_sha256')
                or checkpoint_digest(Path(resolve_core(game))) != original.get('core_sha256')):
            raise CancelRefused('checkpoint_changed')
        recovery_at = timestamp(now())
        state = manager.load(recovery_at)
        if recovery_at < max(timestamp(original['selected_at']), timestamp(state['last_seen_at'])):
            raise CancelRefused('clock_regressed')
        # g1063 2026-10-28: a manual transfer whose probe hit its deadline was
        # closed by the rotation as interrupted, leaving no pending slot or
        # failed-corner audit. The ledger's last result naming this exact
        # logical request proves the same release.
        last = state.get('last_result') or {}
        interrupted_release = (not automatic and last.get('corner') == 'hanjuku-hero'
                               and last.get('request_id') == logical
                               and last.get('status') == 'interrupted')
        if interrupted_release and not _no_newer_hanjuku_progress(base, store, original['generation']):
            raise CancelRefused('newer_progress_unverified')
        owner_path = base / 'retro_corner.json'
        owner = _object(owner_path)
        # A crash after any of the writes below is repairable with the same
        # attempt. It must still own the failed reservation or this recovery.
        pending = state.get('pending') or {}
        queued = state.get('queued_manual') or {}
        if state.get('manual_pending') or manager._read_manual_queue():
            raise CancelRefused('manual_queue_conflict')
        original_pending = (state.get('status') == 'recovery_required'
                            and pending.get('request_id') == logical
                            and pending.get('corner') == 'hanjuku-hero'
                            and pending.get('source') == ('automatic' if automatic else 'manual')
                            and pending.get('phase') == 'dispatched'
                            and (pending.get('resume_latest') is True) == (not automatic)
                            and not queued)
        # The bounded rotation audit may have evicted the original release.
        # A matching rolled-back receipt plus stopped runtime and absence of
        # any newer Hanjuku progress also proves this particular transfer safe
        # to retry; all those checks run under the same writer locks.
        released = (state.get('released_requests') or {}).get('hanjuku-hero') or {}
        audited_release = released.get('request_id') == logical or any(
            isinstance(row, dict) and row.get('corner') == 'hanjuku-hero'
            and row.get('request_id') == logical
            and row.get('resolution') == 'released-unverified'
            for row in (state.get('failed_corners') or []))
        released_proven = audited_release or automatic or interrupted_release
        detached = ((automatic or interrupted_release) and released_proven and not pending
                    and not queued and state.get('status') in {'ready', 'waiting'})
        recovered_pending = (recovery is not None and not pending
                             and queued.get('request_id') == current
                             and queued.get('resume_request_id') == logical
                             and state.get('status') == 'waiting')
        unclaimed_release = (recovery is not None and unclaimed is None
                             and not pending and not queued
                             and state.get('status') in {'ready', 'waiting'}
                             and _released_unclaimed_attempt(base, store, state, attempt, tmux))
        # A manual claimed original whose reservation resolved before the
        # bounded audit could keep it: later corners overwrote the released /
        # failed-corner / last-result rows naming this request (g1077
        # 2026-10-31).  The durable rolled-back switch receipt already proved
        # the dispatch died before the corner became active; together with
        # the empty runtime, released windows, stable canonical state, no
        # newer Hanjuku progress and a settled ledger, that still proves this
        # exact sealed transfer safe to re-queue -- never a newer save.
        detached_claimed = (recovery is None and not automatic
                            and original.get('dispatch_source') == 'manual'
                            and not pending and not queued
                            and state.get('status') in {'ready', 'waiting'})
        if not (original_pending or detached or recovered_pending
                or unclaimed_release or detached_claimed):
            raise CancelRefused('reservation_not_in_scope')
        if unclaimed_release and not _no_newer_hanjuku_progress(base, store, original['generation']):
            raise CancelRefused('newer_progress_unverified')
        if detached_claimed and not _no_newer_hanjuku_progress(base, store, original['generation']):
            raise CancelRefused('newer_progress_unverified')
        owns_failure = (owner.get('rotation_request_id') == logical
                        and owner.get('game') == 'hanjuku-hero')
        terminal_owner = owner.get('status') in {'idle', 'completed', 'interrupted', 'expired'}
        # The refused attempt's own owner must already be retired; a ``failed``
        # owner would refuse the next dispatch as an owner mismatch.
        owns_unclaimed = (owner.get('rotation_request_id') == attempt
                          and owner.get('game') == 'hanjuku-hero' and terminal_owner)
        foreign_terminal_owner = (terminal_owner
                                  and owner.get('game') != 'hanjuku-hero')
        if not ((owns_failure and (owner.get('status') == 'failed'
                                  or (owner.get('status') == 'interrupted'
                                      and owner.get('startup_resume_recovery') == attempt)))
                or (automatic and released_proven and terminal_owner)
                or (interrupted_release and owns_failure and owner.get('status') == 'interrupted')
                or ((unclaimed_release or unclaimed is not None) and owns_unclaimed)
                or ((detached_claimed or recovered_pending) and foreign_terminal_owner)):
            raise CancelRefused('owner_not_terminal')
        if not apply:
            return {'status': 'recovery-eligible', 'fingerprint': digest}
        if recovery is None:
            recovery = {'original': original, 'attempt_id': attempt, 'fingerprint': digest}
            request = {k: v for k, v in original.items()
                       if k not in {'runtime_id', 'generation', 'lease_id', 'game'}}
            # This is now an explicit operator retry of the SAME sealed
            # transfer, not a fresh selection or an automatic replay loop. The
            # queued row below is resume_latest, so the request must say so
            # for every original (g1071 11:44: a manual original kept
            # explicit_resume=False and the start refused the identity).
            request.update(status='queued', startup_recovery=recovery,
                           dispatch_source='manual', explicit_resume=True)
            atomic_write_json(root / REQUEST_FILE, request)
        if unclaimed_release:
            # Same logical request and sealed bytes; only the execution id is
            # new, because the refused attempt already owns a terminal receipt.
            current = retry
            recovery = dict(recovery, attempt_id=retry, unclaimed_attempt={'attempt_id': attempt})
            request = dict(request, startup_recovery=recovery,
                           dispatch_source='manual', explicit_resume=True)
            atomic_write_json(root / REQUEST_FILE, request)
        if owns_failure and owner['status'] == 'failed':
            atomic_write_json(owner_path, dict(owner, status='interrupted',
                                               startup_resume_recovery=attempt))
        if not recovered_pending:
            queued = dict(corner='hanjuku-hero', selected_at=recovery_at,
                          request_id=current, resume_request_id=logical, resume_latest=True)
            state.update(status='waiting', reason='startup-resume-recovery-queued',
                         error_kind=None, pending=None, queued_manual=queued)
            manager.save(state)
        return {'status': 'recovery-queued', 'fingerprint': digest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'recover', 'retry'))
    parser.add_argument('--expected')
    args = parser.parse_args(argv)
    try:
        root = Path(__file__).resolve().parents[2]
        g = load_global(root, root / 'config/docich.soren-live.toml')
        if args.mode == 'retry':
            result = queue_failed_start(g)
            if result is None:
                result = {'status': 'noop'}
        else:
            result = recover(g, expected=args.expected, apply=args.mode == 'recover')
    except Exception:
        # Never publish raw exception text, private checkpoint paths or bytes.
        print(json.dumps({'status': 'refused'}))
        return 77
    print(json.dumps(result, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
