"""One durable Hanjuku prediction, separate from Soren's 48-game round state.

Only the corner owner may create. Common rotation ticks may retry settlement
of an already-owned round after game teardown. All Twitch I/O is outside the
RetroArch input gate and failures cannot prevent game completion.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import math
import os
from pathlib import Path
import secrets
import time

from . import hanjuku_progress as progress
from . import hanjuku_run
from .config import load_game
from .game_switch import atomic_write_json
from .hanjuku_prediction_api import APIError, configured_client
from .naming import runtime_directory
from .retroarch_boundary import read_record

FILE = 'hanjuku_predictions.json'
RESULT_FILE = 'hanjuku_prediction_result.json'
POLL_SECONDS = 30
RETRY_SECONDS = 300
LIVE = {'ACTIVE', 'LOCKED'}
DONE = {'RESOLVED', 'CANCELED'}
ERRORS = {'transport', 'auth', 'rate_limited', 'rejected', 'invalid_response',
          'configuration', 'invalid_state', 'unexpected', 'create_unknown', 'remote_missing',
          'remote_mismatch', 'clock_regressed'}
MODES = {'incompatible_soren', 'idle', 'disabled', 'explore', 'unconfigured', 'paused', 'blocked', 'pending',
         'active', 'settling', 'resolved', 'canceled', 'known_result', 'complete_record', 'error'}


def thresholds(best):
    progress.count(best)
    if best >= progress.MAX_CHAPTER:
        return None  # Never offer an impossible chapter-13 record.
    target = best + 1
    # Owner's known best is seeded at one; no overlapping 0/1 midpoint menu.
    if target < 2:
        raise ValueError('three-way prediction requires one verified cleared chapter')
    return {'target': target, 'middle': max(1, target // 2)}


def outcome_titles(target, middle):
    middle_title = (f'{middle}話突破' if middle == target - 1
                    else f'{middle}〜{target - 1}話突破')
    return [f'{target}話突破（新記録）', middle_title, f'{middle}話突破できず']


def winner(cleared, target, middle):
    progress.count(cleared)
    if (type(target) is not int or type(middle) is not int
            or not 1 <= middle < target <= progress.MAX_CHAPTER):
        raise ValueError('invalid prediction thresholds')
    return 0 if cleared >= target else 1 if cleared >= middle else 2


def config(g):
    raw = load_game(g, 'hanjuku-hero').raw.get('hanjuku', {}).get('predictions', {})
    if not isinstance(raw, dict):
        raise ValueError('invalid predictions config')
    enabled = raw.get('enabled', False)
    seed = raw.get('seed_best_cleared', 1)
    window = raw.get('window_seconds', 120)
    if (type(enabled) is not bool or type(seed) is not int or not 1 <= seed <= 12
            or type(window) is not int or not 1 <= window <= 1800):
        raise ValueError('invalid predictions config')
    return enabled, seed, window


def _stamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid prediction clock')
    return value


def _checked_result(value, identity):
    if (not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1
            or any(value.get(k) != v for k, v in identity.items())
            or value.get('reason') not in {'game_over', 'screen_stalled'}
            or not isinstance(value.get('frame_sha256'), str)
            or len(value['frame_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in value['frame_sha256'])
            or 'cleared' not in value):
        raise ValueError('invalid durable prediction result')
    if value['cleared'] is not None:
        progress.count(value['cleared'])
    return value


def _load(root, seed):
    state = read_record(root / FILE)
    if not state:
        return {'schema': 1, 'best_cleared': seed, 'round': None, 'mode': 'idle',
                'next_poll_at': 0, 'last_poll_at': 0, 'error': None}
    if (type(state.get('schema')) is not int or state['schema'] != 1
            or state.get('mode') not in MODES or state.get('error') not in ERRORS | {None}):
        raise ValueError('invalid predictions state')
    progress.count(state.get('best_cleared'))
    if state['best_cleared'] < seed:
        state['best_cleared'] = seed
    for key in ('next_poll_at', 'last_poll_at'):
        _stamp(state.get(key))
    row = state.get('round')
    if row is not None:
        progress.checked_identity(row)
        winner(0, row.get('target'), row.get('middle'))
        if (not isinstance(row.get('nonce'), str) or len(row['nonce']) != 12
                or any(c not in '0123456789abcdef' for c in row['nonce'])
                or type(row.get('attempted')) is not bool
                or row.get('status') not in LIVE | DONE | {'INTENT'}
                or type(row.get('window')) is not int or not 1 <= row['window'] <= 1800
                or row.get('title') != _title(row['nonce'])
                or row.get('outcome_titles') != outcome_titles(row['target'], row['middle'])):
            raise ValueError('invalid prediction intent')
        if row.get('id') is not None:
            ids = row.get('outcome_ids')
            if (not isinstance(row['id'], str) or not 1 <= len(row['id']) <= 128
                    or not isinstance(ids, list) or len(ids) != 3 or len(set(ids)) != 3
                    or not all(isinstance(i, str) and 1 <= len(i) <= 128 for i in ids)):
                raise ValueError('invalid prediction ownership')
        if row.get('result') is not None:
            _checked_result(row['result'], {k: row[k] for k in progress.KEYS})
    return state


def _title(nonce):
    return f'半熟英雄：どこまでゲーム到達できるか？ #{nonce}'


@contextmanager
def locked(root):
    path = root / 'locks' / 'hanjuku-predictions.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _result(root, identity):
    identity = progress.checked_identity(identity)
    runtime = runtime_directory(root, identity['runtime_id'])
    # Re-validate terminal provenance even if a result from an earlier tick exists.
    evidence = hanjuku_run.terminal(runtime, identity)
    if evidence is None:
        return None
    saved = read_record(runtime / RESULT_FILE)
    if saved:
        _checked_result(saved, identity)
        if (type(saved.get('schema')) is not int or saved['schema'] != 1
                or any(saved.get(k) != v for k, v in identity.items())
                or saved.get('reason') != evidence['terminal_reason']
                or saved.get('frame_sha256') != evidence['frame_sha256']):
            raise ValueError('invalid prediction result')
        if saved.get('cleared') is not None:
            progress.count(saved['cleared'])
        return saved
    result = {'schema': 1, **identity, 'reason': evidence['terminal_reason'],
              'frame_sha256': evidence['frame_sha256'],
              'cleared': progress.completed_count(runtime, identity)}
    atomic_write_json(runtime / RESULT_FILE, result)
    return result


def _matches(remote, row):
    return (remote.get('title') == row['title']
            and [o.get('title') for o in remote.get('outcomes', [])] == row['outcome_titles']
            and (row.get('id') is None or remote.get('id') == row['id'])
            and (not row.get('outcome_ids')
                 or [o.get('id') for o in remote.get('outcomes', [])] == row['outcome_ids']))


def summary(state):
    row = state.get('round') or {}
    return {'mode': state.get('mode'), 'error': state.get('error'),
            'best_cleared': state.get('best_cleared'), 'target': row.get('target'),
            'middle': row.get('middle'), 'status': row.get('status'),
            'cleared': (row.get('result') or {}).get('cleared'),
            'next_poll_at': state.get('next_poll_at')}


def _advance(g, root, state, identity, now, enabled, window, client_factory):
    row = state.get('round')
    # Capture completion and record BEFORE checking pauses, auth or API cooldown.
    # A previously verified, fsynced receipt survives runtime retention and
    # reset. Never downgrade a saved completion into a later interruption.
    cached = row.get('result') if row else None
    completed = (cached if cached and identity and all(cached[k] == identity[k] for k in progress.KEYS)
                 else _result(root, identity) if identity else None)
    owned_completed = (cached or _result(root, {k: row[k] for k in progress.KEYS})
                       if row and row['status'] not in DONE else None)
    for result in (completed, owned_completed):
        if result and result['cleared'] is not None:
            state['best_cleared'] = max(state['best_cleared'], result['cleared'])
    new_terminal = bool(owned_completed and not row.get('result'))
    if owned_completed:
        row['result'] = owned_completed
    atomic_write_json(root / FILE, state)
    if now < state['last_poll_at']:
        raise APIError('clock_regressed')
    if now < state['next_poll_at'] and not new_terminal:
        return
    state.update(last_poll_at=now, next_poll_at=now + POLL_SECONDS, error=None)
    if not enabled:
        state['mode'] = 'disabled'
        return
    client, unavailable = client_factory(g)
    if unavailable:
        state['mode'] = unavailable
        return
    # Canonical state is authority for creation, never the displayed game label.
    canonical = read_record(root / 'game_switch.json', limit=256 * 1024)
    active = canonical.get('active') if isinstance(canonical.get('active'), dict) else {}
    ready = canonical.get('phase') == 'ready'
    same = lambda who: all(active.get(k) == who.get(k) for k in progress.KEYS)
    live_owner = identity is not None and ready and same(identity) and completed is None
    paused = (root / 'hanjuku_predictions.paused').exists()
    # An unresolved/ambiguous round always takes priority over a new generation.
    if row and row['status'] not in DONE:
        remote_rows = client.list(row.get('id'))
        matches = [r for r in remote_rows if _matches(r, row)]
        if len(matches) > 1:
            raise APIError('remote_mismatch')
        remote = matches[0] if matches else None
        if remote:
            if row.get('id') is None:
                row.update(id=remote['id'], outcome_ids=[o['id'] for o in remote['outcomes']])
            row['status'] = remote['status']
            # Persist adopted ownership before any PATCH; API retries are idempotent.
            atomic_write_json(root / FILE, state)
            if remote['status'] in DONE:
                state['mode'] = remote['status'].lower()
                return
            result = owned_completed
            cancel = bool(result and result['cleared'] is None)
            # An explicit ready different runtime is an interruption, not a loss.
            cancel = cancel or (ready and not same(row) and result is None)
            if not ready and result is None:
                state['mode'] = 'pending'
                return
            if result or cancel:
                state['mode'] = 'settling'
                atomic_write_json(root / FILE, state)
                if cancel:
                    response = client.end(row['id'], 'CANCELED')
                    desired = 'CANCELED'
                else:
                    index = winner(result['cleared'], row['target'], row['middle'])
                    # Lock before publishing the winner, including early endings.
                    if remote['status'] == 'ACTIVE':
                        client.end(row['id'], 'LOCKED')
                    response = client.end(row['id'], 'RESOLVED', row['outcome_ids'][index])
                    desired = 'RESOLVED'
                if len(response) != 1 or not _matches(response[0], row) or response[0]['status'] != desired:
                    raise APIError('invalid_response')
                row['status'] = desired
                state['mode'] = desired.lower()
                return
            state['mode'] = 'active'
            # End voting when the top outcome is already provable; do not resolve
            # early (the final record still belongs to the entire attempt).
            cleared = progress.completed_count(runtime_directory(root, row['runtime_id']),
                                               {k: row[k] for k in progress.KEYS})
            if remote['status'] == 'ACTIVE' and cleared is not None and cleared >= row['target']:
                client.end(row['id'], 'LOCKED')
            return
        if row.get('id'):
            raise APIError('remote_missing')
        if row['attempted']:
            # POST may have succeeded despite timeout. Never blindly POST again.
            raise APIError('create_unknown')
        if not live_owner or not same(row) or paused:
            # No POST was sent and no remote object belongs to this intent.
            row['status'] = 'CANCELED'
            state['mode'] = 'canceled'
            return
    else:
        if not live_owner:
            return
        if row and same(row) and row['status'] in DONE:
            # A remotely ended prediction belongs to this attempt even when it
            # was canceled/refunded. Respect that terminal state and do not
            # silently open a second wager in the same run.
            return
        if paused:
            state['mode'] = 'paused'
            return
        bounds = thresholds(state['best_cleared'])
        if bounds is None:
            state['mode'] = 'complete_record'
            return
        cleared = progress.completed_count(runtime_directory(root, identity['runtime_id']), identity)
        if cleared is not None and cleared >= bounds['target']:
            state['mode'] = 'known_result'
            return
        remote_rows = client.list()
        if any(r['status'] in LIVE for r in remote_rows):
            state['mode'] = 'blocked'
            return
        nonce = secrets.token_hex(6)
        row = {**identity, **bounds, 'nonce': nonce, 'title': _title(nonce),
               'outcome_titles': outcome_titles(**bounds), 'window': window,
               'attempted': False, 'status': 'INTENT'}
        state['round'] = row
    # A previous rejected POST may be retried much later. Recheck that the
    # result/owner has not changed while GET was in flight before accepting bets.
    canonical = read_record(root / 'game_switch.json', limit=256 * 1024)
    active = canonical.get('active') if isinstance(canonical.get('active'), dict) else {}
    if (canonical.get('phase') != 'ready' or not same(row)
            or hanjuku_run.terminal(runtime_directory(root, row['runtime_id']),
                                    {k: row[k] for k in progress.KEYS}) is not None):
        state['mode'] = 'pending'
        return
    cleared = progress.completed_count(runtime_directory(root, row['runtime_id']),
                                       {k: row[k] for k in progress.KEYS})
    if cleared is not None and cleared >= row['target']:
        row['status'] = 'CANCELED'
        state['mode'] = 'known_result'
        return
    # Recheck the remote at every create attempt. No API read failure means empty.
    if any(r['status'] in LIVE for r in remote_rows):
        state['mode'] = 'blocked'
        return
    row['attempted'] = True
    state['mode'] = 'pending'
    atomic_write_json(root / FILE, state)
    try:
        created = client.create(row['title'], row['outcome_titles'], row['window'])
    except APIError as exc:
        if exc.rejected:  # a definitive 4xx rejection did not create this intent
            row['attempted'] = False
        raise
    if len(created) != 1 or not _matches(created[0], row) or created[0]['status'] not in LIVE:
        raise APIError('invalid_response')
    row.update(id=created[0]['id'], outcome_ids=[o['id'] for o in created[0]['outcomes']],
               status=created[0]['status'])
    state['mode'] = 'active'


def tick(g, identity=None, *, now=None, client_factory=configured_client):
    """Best-effort side channel. A failure never tears down or holds game input."""
    state = None
    root = Path(g.state_dir)
    try:
        now = _stamp(time.time() if now is None else now)
        enabled, seed, window = config(g)
        if identity is not None:
            identity = progress.checked_identity(identity)
        # Ordinary timer ticks without an existing prediction have no work and
        # do not read OAuth settings or create files in unrelated game fixtures.
        if identity is None and not (root / FILE).exists():
            return {'mode': 'idle', 'error': None}
        with locked(root) as acquired:
            if not acquired:
                return {'mode': 'pending', 'error': None}
            state = _load(root, seed)
            try:
                _advance(g, root, state, identity, now, enabled, window, client_factory)
            except APIError as exc:
                state.update(mode='error', error=exc.code if exc.code in ERRORS else 'unexpected',
                             next_poll_at=now + RETRY_SECONDS)
            except Exception:
                # The ledger was validated by _load; annotate failure of an
                # external progress/canonical record without discarding ownership.
                # Malformed ledgers themselves never reach this write path.
                state.update(mode='error', error='invalid_state', next_poll_at=now + RETRY_SECONDS)
            atomic_write_json(root / FILE, state)
            return summary(state)
    except Exception:
        return {'mode': 'error', 'error': 'invalid_state'}
