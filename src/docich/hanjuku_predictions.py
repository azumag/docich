"""One durable Hanjuku prediction, separate from Soren's 48-game round state.

Only the corner owner may create. Common rotation ticks may retry settlement
of an already-owned round after game teardown. All Twitch I/O is outside the
RetroArch input gate and failures cannot prevent game completion.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import time

from . import hanjuku_hotload
from . import hanjuku_progress as progress
from . import hanjuku_run
from .config import load_game
from .game_switch import atomic_write_json
from .hanjuku_prediction_api import APIError, configured_client
from .naming import runtime_directory
from .retroarch_boundary import read_record

FILE = 'hanjuku_predictions.json'
RESULT_FILE = 'hanjuku_prediction_result.json'
#: Consecutive failing ticks, kept in a separate bounded record so that a ledger
#: `_load` rejection (the #2213 class) is still observable. The ledger itself is
#: never written when it cannot be read.
HEALTH_FILE = 'hanjuku_predictions_health.json'
HEALTH_SCHEMA = 1
MAX_ERROR_STREAK = 1_000_000
MAX_CANCEL_SNAPSHOT_BYTES = 256 * 1024
POLL_SECONDS = 30
RETRY_SECONDS = 300
LIVE = {'ACTIVE', 'LOCKED'}
DONE = {'RESOLVED', 'CANCELED'}
ERRORS = {'transport', 'auth', 'rate_limited', 'rejected', 'invalid_response',
          'configuration', 'invalid_state', 'unexpected', 'create_unknown', 'remote_missing',
          'remote_mismatch', 'clock_regressed', 'leaderboard'}
MODES = {'incompatible_soren', 'idle', 'disabled', 'explore', 'unconfigured', 'paused', 'blocked', 'pending',
         'active', 'settling', 'resolved', 'canceled', 'known_result', 'complete_record', 'error'}

#: 4つ目の選択肢（owner 決定 2026-10-15 / Issue #2238）。画面停滞（フレーム静止）または
#: 入力ループで「一時終了」した run をここで確定する。予想は run 開始時に作られ選択肢が
#: 固定されるため、checkpoint の有無で出し分けず常に4択にする。
STALLED_OUTCOME_TITLE = '画面停滞で一時終了（同じ状態から再開予定）'
#: 4択目を使う終端理由。`hanjuku_run.TERMINAL_REASONS` のうち `game_over` は従来どおり
#: 突破数で判定し、`input_stalled` は4択目に含めない。
STALLED_REASON = 'screen_stalled'
#: デプロイ前に作られた予想の選択肢数（受理だけ legacy として許す）。
LEGACY_OUTCOME_COUNT = 3


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
    return [f'{target}話突破（新記録）', middle_title, f'{middle}話突破できず',
            STALLED_OUTCOME_TITLE]


def accepted_outcome_titles(target, middle):
    """Titles a stored intent may declare: the new 4-choice and the legacy 3-choice.

    選択肢は予想の作成時に固定される。4択へ変えた後に、デプロイ前に作られた3択の
    row を `_load` が拒否すると #2213 の `invalid_state` ループが再発するため、
    「生成は4択・受理は row が自己申告する3か4択」とする（Issue #2238）。
    """
    titles = outcome_titles(target, middle)
    return (titles[:LEGACY_OUTCOME_COUNT], titles)


def resolve_index(row, result):
    """Outcome index for one terminal result, or ``None`` when it must be canceled.

    4択の row で画面停滞/ループ終了（``reason=screen_stalled``）だったときだけ
    4択目を使う。``game_over`` は従来どおり突破数で判定し、``input_stalled``
    （画面は観測できているが操作AIが無入力）は4択目に含めない（owner 決定
    2026-10-15 / Issue #2238）。legacy 3択の row は作成時の選択肢を変えられない
    ため、停滞終了でも従来の判定のままにする。
    """
    titles = row.get('outcome_titles')
    if (isinstance(titles, list) and len(titles) > LEGACY_OUTCOME_COUNT
            and result.get('reason') == STALLED_REASON):
        return LEGACY_OUTCOME_COUNT
    if result.get('cleared') is None:
        return None
    return winner(result['cleared'], row['target'], row['middle'])


def voting_window(target, ending_window):
    if (type(target) is not int or not 1 <= target <= progress.MAX_CHAPTER
            or type(ending_window) is not int or not 30 <= ending_window <= 1800):
        raise ValueError('invalid prediction window')
    return max(30, ending_window * target // progress.MAX_CHAPTER)


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
    ending_window = raw.get('ending_window_seconds', 1800)
    if (type(enabled) is not bool or type(seed) is not int or not 1 <= seed <= 12
            or type(ending_window) is not int or not 30 <= ending_window <= 1800):
        raise ValueError('invalid predictions config')
    return enabled, seed, ending_window


def _stamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid prediction clock')
    return value


def _checked_result(value, identity):
    # The accepted reasons must be exactly the terminals hanjuku_run can report:
    # _result() writes evidence['terminal_reason'] verbatim into the durable
    # record, so a narrower set here would refuse what the module itself just
    # wrote and spin forever in error=invalid_state (#933).
    if (not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1
            or any(value.get(k) != v for k, v in identity.items())
            or value.get('reason') not in hanjuku_run.TERMINAL_REASONS
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
                or row.get('outcome_titles')
                   not in accepted_outcome_titles(row['target'], row['middle'])):
            raise ValueError('invalid prediction intent')
        if row.get('id') is not None:
            ids = row.get('outcome_ids')
            expected = len(row['outcome_titles'])
            if (not isinstance(row['id'], str) or not 1 <= len(row['id']) <= 128
                    or not isinstance(ids, list) or len(ids) != expected
                    or len(set(ids)) != expected
                    or not all(isinstance(i, str) and 1 <= len(i) <= 128 for i in ids)):
                raise ValueError('invalid prediction ownership')
        if row.get('result') is not None:
            _checked_result(row['result'], {k: row[k] for k in progress.KEYS})
    return state


def _load_recoverable(root, seed):
    """``_load`` for the owner-only recovery operator (#2216).

    The ordinary tick must stay fail-closed, so a ledger it cannot validate is
    never rewritten. That is exactly why a stalled round needs an operator: the
    #2213 class is a record the module *itself* wrote and then refused. This
    loader applies the same checks but classifies the durable terminal result
    loosely, so a reviewed recovery can still cancel or sync the round it owns.

    It stays strict about everything that decides *which* round is owned and
    whether that ownership is well-formed: identity, thresholds, title, window,
    nonce, status and the remote ids. It refuses anything else, and it never
    repairs or rewrites the offending field.
    """
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
                or row.get('outcome_titles')
                   not in accepted_outcome_titles(row['target'], row['middle'])):
            raise ValueError('invalid prediction intent')
        if row.get('id') is not None:
            ids = row.get('outcome_ids')
            expected = len(row['outcome_titles'])
            if (not isinstance(row['id'], str) or not 1 <= len(row['id']) <= 128
                    or not isinstance(ids, list) or len(ids) != expected
                    or len(set(ids)) != expected
                    or not all(isinstance(i, str) and 1 <= len(i) <= 128 for i in ids)):
                raise ValueError('invalid prediction ownership')
        if row.get('result') is not None and not isinstance(row['result'], dict):
            raise ValueError('invalid prediction result')
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
    evidence = hanjuku_hotload.terminal(runtime, identity)
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


def _has_predictors(remote):
    outcomes = remote.get('outcomes') if isinstance(remote, dict) else None
    if not isinstance(outcomes, list):
        return False
    return any(isinstance(outcome, dict) and isinstance(outcome.get('top_predictors'), list)
               and outcome['top_predictors'] for outcome in outcomes)


def _contested_outcomes(remote):
    """Number of outcomes that received at least one bet, or None if unreadable.

    Twitch exposes ``users`` and ``channel_points`` per outcome to the
    broadcaster token. Unreadable counts return None so the caller keeps the
    existing behaviour instead of inventing a settle rule.
    """
    outcomes = remote.get('outcomes') if isinstance(remote, dict) else None
    if not isinstance(outcomes, list) or not outcomes:
        return None
    counted = known = 0
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            return None
        for key in ('users', 'channel_points'):
            value = outcome.get(key)
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            known += 1
            if value > 0:
                counted += 1
                break
    return counted if known else None


def _no_contest(remote):
    """True only while the counts prove that fewer than two outcomes were bet.

    A prediction where only one outcome (or none) ever received a bet never
    formed a contest: resolving it would take points from viewers who had no
    opposing side, so it is canceled and refunded instead (オーナー判断
    2026-10-15).
    """
    counted = _contested_outcomes(remote)
    return counted is not None and counted <= 1


def _record_settled(root, remote, now, client):
    """Record one RESOLVED prediction in the 予想王 leaderboard (fail-closed).

    Twitch's PATCH response may omit ``top_predictors``, so a single GET by id is
    used only when the settled row carries none. A row that violates the record
    contract is refused (never recorded wrong) and a ledger read/write failure
    raises ``APIError('leaderboard')`` so the tick retries the same id later.
    """
    from . import prediction_leaderboard as leaderboard

    candidate = remote
    if not _has_predictors(remote):
        prediction_id = remote.get('id') if isinstance(remote, dict) else None
        if isinstance(prediction_id, str):
            rows = client.list(prediction_id)
            match = next((r for r in rows
                          if isinstance(r, dict) and r.get('id') == prediction_id), None)
            if match is not None and match.get('status') == 'RESOLVED':
                candidate = match
    try:
        leaderboard.extract(candidate)
    except leaderboard.LeaderboardError:
        raise APIError('invalid_response') from None
    try:
        return leaderboard.record_resolved(root, candidate, corner='hanjuku', now=int(now))
    except (leaderboard.LeaderboardError, OSError):
        raise APIError('leaderboard') from None


def _canceled_candidate(row):
    """Reuse a verified row as evidence of a refunded no-contest round.

    A ``CANCELED`` row is already in the right shape. A ``LOCKED`` row is the
    authoritative betting snapshot (betting is closed), and the cancellation we
    already verified turns it into refunded participation evidence.
    """
    if not isinstance(row, dict):
        return None
    if row.get('status') == 'CANCELED':
        return row
    if row.get('status') == 'LOCKED':
        return {**row, 'status': 'CANCELED', 'winning_outcome_id': None}
    return None


def _cancel_snapshot_path(root, row):
    # Never put a remote id into a filesystem path. Each owned prediction has
    # its own retry evidence; another generation cannot consume or replace it.
    digest = hashlib.sha256(row['id'].encode('utf-8')).hexdigest()
    return root / f'hanjuku_prediction_cancel_{digest}.json'


def _check_cancel_snapshot(snapshot, row):
    from . import prediction_leaderboard as leaderboard

    if (not isinstance(snapshot, dict) or snapshot.get('status') != 'CANCELED'
            or not _matches(snapshot, row)
            or not _no_contest(snapshot)):
        raise ValueError('invalid cancel snapshot ownership')
    leaderboard.extract(snapshot)
    return snapshot


def _load_cancel_snapshot(root, row):
    # Keep the main intent small: its existing 16 KiB readers need no change.
    path = _cancel_snapshot_path(root, row)
    snapshot = read_record(path, limit=MAX_CANCEL_SNAPSHOT_BYTES)
    if not snapshot and not path.exists() and not path.is_symlink():
        return None
    return _check_cancel_snapshot(snapshot, row)


def _save_cancel_snapshot(root, row, remote):
    """Write verified closed-betting evidence BEFORE the irreversible cancel."""
    if remote.get('status') != 'LOCKED':
        raise APIError('invalid_response')
    existing = _load_cancel_snapshot(root, row)
    if existing is not None:
        # A failed/unknown PATCH may still leave the remote LOCKED. Never
        # overwrite known participation with a poorer response on that retry.
        return existing
    try:
        snapshot = _check_cancel_snapshot(_canceled_candidate(remote), row)
        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
        if len(payload.encode('utf-8')) > MAX_CANCEL_SNAPSHOT_BYTES:
            raise ValueError('cancel snapshot exceeds the byte limit')
    except ValueError:
        raise APIError('invalid_response') from None
    try:
        atomic_write_json(_cancel_snapshot_path(root, row), snapshot)
    except OSError:
        raise APIError('leaderboard') from None
    return snapshot


def _record_canceled(root, now, client, *, sources=()):
    """Record a no-contest CANCELED round's participation in the 予想王 ledger.

    The round is stored once per prediction id with the refunded stake and the
    fixed reason ``no-contest``. A row without predictors is still stored (the
    per-outcome counts are the evidence); no viewer, count or point is invented
    when the row does not prove one. A contract violation is refused and a
    ledger read/write failure raises ``APIError('leaderboard')`` so the same id
    is retried later.
    """
    from . import prediction_leaderboard as leaderboard

    candidate = None
    for source in sources:
        annotated = _canceled_candidate(source)
        if annotated is None:
            continue
        try:
            leaderboard.extract(annotated)
        except leaderboard.LeaderboardError:
            raise APIError('invalid_response') from None
        if candidate is None or (_has_predictors(annotated)
                                 and not _has_predictors(candidate)):
            candidate = annotated
    if candidate is None or not _has_predictors(candidate):
        prediction_id = next((s.get('id') for s in sources
                              if isinstance(s, dict) and isinstance(s.get('id'), str)), None)
        if isinstance(prediction_id, str):
            rows = client.list(prediction_id)
            match = next((r for r in rows
                          if isinstance(r, dict) and r.get('id') == prediction_id), None)
            annotated = _canceled_candidate(match)
            if annotated is not None and (candidate is None or _has_predictors(annotated)):
                candidate = annotated
    if candidate is None:
        raise APIError('invalid_response')
    try:
        leaderboard.extract(candidate)
    except leaderboard.LeaderboardError:
        raise APIError('invalid_response') from None
    try:
        return leaderboard.record_settled(root, candidate, corner='hanjuku',
                                          now=int(now), cancel_reason='no-contest')
    except (leaderboard.LeaderboardError, OSError):
        raise APIError('leaderboard') from None


def summary(state):
    row = state.get('round') or {}
    return {'mode': state.get('mode'), 'error': state.get('error'),
            'best_cleared': state.get('best_cleared'), 'target': row.get('target'),
            'middle': row.get('middle'), 'status': row.get('status'),
            'cleared': (row.get('result') or {}).get('cleared'),
            'next_poll_at': state.get('next_poll_at')}


def _bounded_clock(value):
    if type(value) not in (int, float) or isinstance(value, bool):
        return None
    try:
        if not math.isfinite(value) or value < 0:
            return None
    except (OverflowError, ValueError, TypeError):
        return None
    return value


def _health(root):
    """Prior health record, or an empty one when unreadable/untrusted."""
    try:
        record = read_record(root / HEALTH_FILE, limit=4096)
    except Exception:
        return {}
    if type(record.get('schema')) is not int or record['schema'] != HEALTH_SCHEMA:
        return {}
    return record


def _update_health(root, outcome, now):
    """Best-effort consecutive-error counter; never changes the prediction.

    A failing tick must stay observable even when ``_load`` rejects the durable
    ledger and the ledger therefore cannot be rewritten. Only fixed error codes,
    counters and clocks are persisted, and a health write failure is ignored so
    the prediction path is never held or failed by telemetry.
    """
    try:
        prior = _health(root)
        streak = prior.get('consecutive_errors')
        streak = streak if type(streak) is int and 0 <= streak <= MAX_ERROR_STREAK else 0
        failed = outcome.get('mode') == 'error'
        error = outcome.get('error') if failed else None
        error = error if isinstance(error, str) and error in ERRORS else ('unexpected' if failed else None)
        record = {
            'schema': HEALTH_SCHEMA,
            'consecutive_errors': min(streak + 1, MAX_ERROR_STREAK) if failed else 0,
            'last_error': error,
            'last_error_at': _bounded_clock(now if failed else prior.get('last_error_at')),
            'last_ok_at': _bounded_clock(prior.get('last_ok_at') if failed else now),
        }
        atomic_write_json(root / HEALTH_FILE, record)
    except Exception:
        pass


def _note_health_after_failure(root, now):
    """Record an unreadable-ledger tick; skip when another tick holds the lock."""
    try:
        with locked(root) as acquired:
            if acquired:
                _update_health(root, {'mode': 'error', 'error': 'invalid_state'}, now)
    except Exception:
        pass


def _advance(g, root, state, identity, now, enabled, ending_window, client_factory):
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
            if remote['status'] == 'RESOLVED':
                # Retry ledger I/O, but do not retry malformed statistics for
                # a prediction whose owned terminal status is already verified.
                try:
                    _record_settled(root, remote, now, client)
                except APIError as exc:
                    if exc.code == 'invalid_response':
                        row['status'] = 'RESOLVED'
                    raise
            elif remote['status'] == 'CANCELED':
                # 不成立キャンセルで落ちた参加実績を、再観測・再起動のあとでも
                # 一度だけ記録する（#933 追記）。
                snapshot = _load_cancel_snapshot(root, row)
                if snapshot is not None or _no_contest(remote):
                    try:
                        _record_canceled(root, now, client, sources=(remote, snapshot))
                    except APIError as exc:
                        if exc.code == 'invalid_response':
                            row['status'] = 'CANCELED'
                        raise
                    _cancel_snapshot_path(root, row).unlink(missing_ok=True)
            row['status'] = remote['status']
            # Persist adopted ownership before any PATCH; API retries are idempotent.
            atomic_write_json(root / FILE, state)
            if remote['status'] in DONE:
                state['mode'] = remote['status'].lower()
                return
            result = owned_completed
            # 4択の row では画面停滞/ループ終了を4択目で確定する。突破数の証拠が
            # 無い停滞終了（従来は CANCELED）も、この選択肢がある run は記帳する。
            index = resolve_index(row, result) if result else None
            cancel = bool(result) and index is None
            # An explicit ready different runtime is an interruption, not a loss.
            cancel = cancel or (ready and not same(row) and result is None)
            if not ready and result is None:
                state['mode'] = 'pending'
                return
            if result or cancel:
                state['mode'] = 'settling'
                atomic_write_json(root / FILE, state)
                cancel_reason = None
                if cancel:
                    response = client.end(row['id'], 'CANCELED')
                    desired = 'CANCELED'
                else:
                    # Lock before publishing the winner, including early endings.
                    settled_from = remote
                    if remote['status'] == 'ACTIVE':
                        locked = client.end(row['id'], 'LOCKED')
                        # Betting is closed after the lock, so the locked row is
                        # the authoritative count when it carries one.
                        if (isinstance(locked, list) and len(locked) == 1
                                and _matches(locked[0], row)
                                and _contested_outcomes(locked[0]) is not None):
                            settled_from = locked[0]
                    if _no_contest(settled_from):
                        # 賭けが成立していない予想は勝者を出さず中止し、投票済みの
                        # チャンネルポイントを全額返却する。
                        snapshot = _save_cancel_snapshot(root, row, settled_from)
                        response = client.end(row['id'], 'CANCELED')
                        desired = 'CANCELED'
                        cancel_reason = 'no-contest'
                    else:
                        response = client.end(row['id'], 'RESOLVED', row['outcome_ids'][index])
                        desired = 'RESOLVED'
                if len(response) != 1 or not _matches(response[0], row) or response[0]['status'] != desired:
                    raise APIError('invalid_response')
                if desired == 'RESOLVED':
                    try:
                        _record_settled(root, response[0], now, client)
                    except APIError as exc:
                        if exc.code == 'invalid_response':
                            row['status'] = desired
                        raise
                elif cancel_reason is not None:
                    # 不成立キャンセルの参加実績を残す。記録に失敗したら round を
                    # terminal にせず、次の観測で CANCELED を再読取して再試行する。
                    try:
                        _record_canceled(root, now, client,
                                         sources=(response[0], snapshot))
                    except APIError as exc:
                        if exc.code == 'invalid_response':
                            row['status'] = desired
                        raise
                    _cancel_snapshot_path(root, row).unlink(missing_ok=True)
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
               'outcome_titles': outcome_titles(**bounds),
               'window': voting_window(bounds['target'], ending_window),
               'attempted': False, 'status': 'INTENT'}
        state['round'] = row
    # A previous rejected POST may be retried much later. Recheck that the
    # result/owner has not changed while GET was in flight before accepting bets.
    canonical = read_record(root / 'game_switch.json', limit=256 * 1024)
    active = canonical.get('active') if isinstance(canonical.get('active'), dict) else {}
    if (canonical.get('phase') != 'ready' or not same(row)
            or hanjuku_hotload.terminal(runtime_directory(root, row['runtime_id']),
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
        enabled, seed, ending_window = config(g)
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
                _advance(g, root, state, identity, now, enabled, ending_window, client_factory)
            except APIError as exc:
                state.update(mode='error', error=exc.code if exc.code in ERRORS else 'unexpected',
                             next_poll_at=now + RETRY_SECONDS)
            except Exception:
                # The ledger was validated by _load; annotate failure of an
                # external progress/canonical record without discarding ownership.
                # Malformed ledgers themselves never reach this write path.
                state.update(mode='error', error='invalid_state', next_poll_at=now + RETRY_SECONDS)
            atomic_write_json(root / FILE, state)
            outcome = summary(state)
            _update_health(root, outcome, now)
            return outcome
    except Exception:
        _note_health_after_failure(root, now if now is not None else time.time())
        return {'mode': 'error', 'error': 'invalid_state'}
