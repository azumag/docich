"""Deliver Hanjuku commentary and durable run-end recaps to the audio queue.

Ordinary candidates in ``hanjuku_commentary.jsonl`` remain an opportunistic,
non-blocking side channel: the observer claims them under a non-blocking file
lock, enqueues at most one line on a daemon thread, and skips stale, repeated,
or too-frequent lines. Terminal recaps instead persist a run-scoped handoff in
the existing narration state before bounded outbox attempts; later audio
producers retry that handoff ahead of newer audio. This module never plays or
restarts audio itself.
"""
from __future__ import annotations

import fcntl
import hashlib
import errno
import json
import math
import os
from pathlib import Path
import re
import threading
import time

from .game_switch import atomic_write_json
from .hanjuku_run import append_log
from .retroarch_boundary import read_record

STATE = 'hanjuku_narration.json'
TAIL_BYTES = 65536
MAX_TEXT = 120
TERMINAL_MAX_TEXT = 1000
RECENT = 32
PLAN_MAX_AGE_S = 5
TERMINAL_DELIVERIES_KEY = 'terminal_deliveries'
TERMINAL_SPEAKERS_KEY = 'terminal_delivery_speakers'
_busy = threading.Event()


def _permanent_io_error(exc: OSError) -> bool:
    return isinstance(exc, (PermissionError, FileNotFoundError, NotADirectoryError,
                            IsADirectoryError)) or getattr(exc, 'errno', None) in {
        errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENOTDIR, errno.EISDIR,
        errno.EINVAL, errno.ENAMETOOLONG,
    }


def settings(game) -> dict:
    raw = game.raw.get('hanjuku', {}).get('narration', {}) if isinstance(game.raw.get('hanjuku'), dict) else {}
    if not isinstance(raw, dict):
        raise ValueError('hanjuku.narration must be a table')
    enabled = raw.get('enabled', False)
    cooldown = raw.get('cooldown_s', 25)
    max_age = raw.get('max_age_s', 20)
    speaker = raw.get('speaker', '')
    if type(enabled) is not bool:
        raise ValueError('hanjuku.narration.enabled must be boolean')
    for name, value, lo, hi in (('cooldown_s', cooldown, 10, 300), ('max_age_s', max_age, 5, 120)):
        if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f'hanjuku.narration.{name} must be between {lo} and {hi}')
    if not isinstance(speaker, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{0,64}', speaker):
        raise ValueError('invalid hanjuku.narration.speaker')
    return {'enabled': enabled, 'cooldown_s': float(cooldown), 'max_age_s': float(max_age),
            'speaker': speaker}


def _tail(path: Path) -> list[dict]:
    if not path.exists() or path.is_symlink():
        return []
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - TAIL_BYTES))
        data = stream.read().decode('utf-8', 'ignore').splitlines()
    out = []
    for line in data:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and type(item.get('seq')) is int:
            out.append(item)
    return out


def _item_max_age(item, max_age):
    return min(max_age, PLAN_MAX_AGE_S) if item.get('evidence_kind') == 'plan' else max_age


def deliver_scene(g, game, runtime_dir, snapshot, text):
    """Deliver generated event commentary through the existing fenced sink.

    Legacy template delivery may be disabled independently. The scene worker
    supplies an original evidence timestamp; generation never renews its TTL.
    Soren still owns playback and never cuts an already-speaking line for TTL.
    """
    from . import hanjuku_scene as scene
    from .hanjuku_scene_worker import current, SceneError
    from .trading.soren_output import enqueue_audio_text

    cfg = scene.config(g.repo_root)
    if not cfg['enabled']:
        return 'skipped:disabled'
    current(g, runtime_dir, snapshot)
    request = snapshot['request']
    item = {**{key: snapshot[key] for key in scene.KEYS}, 'seq': request['seq'],
            'key': request['event_key'], 'text': text, 'at': request['at'],
            'evidence_kind': 'observation'}

    def enqueue(*args, **kwargs):
        # This check is immediately before queue I/O, after the transition
        # fence check in _deliver. It does not hold the game input lock.
        if not scene.config(g.repo_root)['enabled']:
            raise SceneError('disabled')
        current(g, runtime_dir, snapshot)
        return enqueue_audio_text(*args, **kwargs)

    return _deliver(g, runtime_dir, item, settings(game)['speaker'], enqueue,
                    min(cfg['max_age_s'], request['expires_at'] - request['at']))


def _terminal_delivery_key(identity: dict) -> str:
    canonical = json.dumps(
        {key: identity.get(key) for key in ('game', 'runtime_id', 'generation', 'lease_id')},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    )
    return 'hanjuku-terminal:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _deliver(g, runtime_dir: Path, item: dict, speaker: str, enqueue, max_age, *,
             terminal=False, before_publish=None):
    from .agent.fence import AgentFence, FenceLost, check_fence, read_canonical, shared_section
    from .hanjuku_run import load
    from .game_switch import GameSwitchBusyError
    from .trading.soren_output import HanjukuTerminalPendingError

    status = 'enqueued'
    max_age = _item_max_age(item, max_age)
    identity = {k: item.get(k) for k in ('game', 'runtime_id', 'generation', 'lease_id')}

    def deliver_active():
        # Check briefly under the transition lock; never hold it across
        # ordinary queue I/O. The terminal outbox publish is deliberately
        # included in the shared section so a new corner cannot enqueue its
        # opening first.
        if (identity['game'] != 'hanjuku-hero'
                or not isinstance(identity['runtime_id'], str) or not identity['runtime_id']
                or type(identity['generation']) is not int
                or not isinstance(identity['lease_id'], str) or not identity['lease_id']):
            raise FenceLost('incomplete commentary identity')
        if not terminal and time.time() - item['at'] > max_age:
            return 'skipped:stale'
        canonical = read_canonical(g.state_dir)
        active = canonical.get('active') or {}
        if any(active.get(key) != value for key, value in identity.items()):
            if terminal:
                return 'skipped:generation_mismatch'
            raise FenceLost('commentary runtime identity changed')
        check_fence(AgentFence(**identity), active)
        try:
            run = load(runtime_dir, identity)
        except Exception:
            if terminal:
                return 'skipped:generation_mismatch'
            raise
        if terminal and (not run or run.get('terminal_reason') != 'game_over'):
            return 'skipped:terminal_unconfirmed'
        if not run or ((run.get('terminal_reason') or run.get('terminal_candidate'))
                       and not item.get('terminal_recap')):
            return 'skipped:terminal'
        if item.get('evidence_kind') == 'plan':
            bot = read_record(runtime_dir / 'hanjuku_bot.json', limit=256 * 1024)
            trace = bot.get('decision_trace') or {}
            if (not isinstance(item.get('decision_id'), str) or not item['decision_id']
                    or trace.get('decision_id') != item['decision_id']
                    or any(trace.get(k) != v for k, v in identity.items())):
                return 'skipped:plan_superseded'
        return 'ready'

    try:
        if terminal:
            def publish_terminal():
                status = deliver_active()
                if status != 'ready':
                    return status
                if before_publish is not None:
                    before_publish()
                enqueue(g, item['text'], context='hanjuku_terminal', speaker=speaker,
                        delivery_key=_terminal_delivery_key(identity))
                return 'enqueued'

            # Bounded shared lock + nonblocking receipt lock: a contention
            # leaves the immutable candidate available for the next observer.
            status = shared_section(g.state_dir, publish_terminal, timeout_s=0)
        else:
            status = shared_section(g.state_dir, deliver_active, timeout_s=0)
            if status == 'ready':
                fence = {**identity, 'expires_at': item['at'] + max_age}
                enqueue(g, item['text'], context='hanjuku_commentary', speaker=speaker,
                        runtime_fence=fence)
                status = 'enqueued'
    except FenceLost:
        status = 'skipped:generation_mismatch' if terminal else 'skipped:fence_lost'
    except GameSwitchBusyError:
        status = 'skipped:switch_busy'
    except HanjukuTerminalPendingError:
        # An older recap temporarily owns the next queue position. Retry this
        # candidate later rather than classifying it as malformed.
        status = 'delivery_failed'
    except (BlockingIOError, InterruptedError, TimeoutError):
        status = 'delivery_failed'
    except OSError as exc:
        status = ('delivery_failed_permanent'
                  if terminal and _permanent_io_error(exc) else 'delivery_failed')
    except (TypeError, ValueError, RuntimeError) as exc:
        # A corrupt durable outbox receipt or invalid terminal payload will
        # not heal on retry. Keep its failure on this run, but do not let it
        # hold later, unrelated audio producers.
        cause = getattr(exc, '__cause__', None)
        transient = (isinstance(cause, (BlockingIOError, InterruptedError, TimeoutError))
                     or isinstance(cause, OSError) and not _permanent_io_error(cause))
        status = ('delivery_failed' if transient else
                  'delivery_failed_permanent' if terminal else 'delivery_failed')
    except Exception:
        # No exception text: queue errors can carry private payloads.
        status = 'delivery_failed'
    finally:
        if not terminal:
            _busy.clear()
    try:
        append_log(runtime_dir, 'hanjuku_narration', {
            'schema': 1, 'at': time.time(), 'seq': item['seq'], 'key': item.get('key'),
            'status': status, 'text': item['text'],
            **({'delivery_key': _terminal_delivery_key(identity)} if terminal else {}),
            **identity})
    except Exception:
        pass
    return status


def consider(g, game, runtime_dir: Path, *, terminal=False, now=None, enqueue=None):
    """Claim ordinary lines asynchronously and hand off a terminal recap.

    At a terminal only the ``terminal_recap`` candidate (the game-over recap,
    owner rule 2026-09-28) may be claimed; ordinary lines stay silent so a
    dying run never narrates stale situations. Its pending receipt is committed
    before at most three nonblocking terminal outbox attempts.
    """
    cfg = settings(game)
    if not cfg['enabled']:
        if terminal:
            candidate = next((item for item in reversed(_terminal_candidates(runtime_dir))
                              if item.get('terminal_recap')), None)
            try:
                run_record = read_record(runtime_dir / 'hanjuku_run.json', limit=256 * 1024)
            except Exception:
                run_record = {}
            source = candidate or run_record
            identity = {key: source.get(key)
                        for key in ('game', 'runtime_id', 'generation', 'lease_id')}
            if (identity.get('game') == 'hanjuku-hero'
                    and identity.get('runtime_id') == runtime_dir.name
                    and type(identity.get('generation')) is int
                    and isinstance(identity.get('lease_id'), str) and identity['lease_id']):
                _record_terminal_delivery(
                    runtime_dir, _terminal_delivery_key(identity), 'disabled',
                    identity=identity, candidate=candidate,
                )
        return None
    now = time.time() if now is None else now
    lock_path = runtime_dir / 'hanjuku_narration.lock'
    if lock_path.is_symlink():
        return None
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None              # another observer is handling it
        state = read_record(runtime_dir / STATE) or {}
        last_seq = int(state.get('last_seq', 0))
        all_items = _tail(runtime_dir / 'hanjuku_commentary.jsonl')
        new = [i for i in all_items if i['seq'] > last_seq]
        if not new and not terminal:
            return None
        if new:
            state['last_seq'] = max(i['seq'] for i in new)
        ordinary_new = [item for item in new if not item.get('terminal_recap')]
        results = []
        chosen = None
        for item in reversed(ordinary_new):   # newest first; older ones are dropped
            text = item.get('text')
            reason = None
            if not text:
                reason = 'held'
            elif terminal:
                reason = 'terminal'
            elif chosen is not None:
                reason = 'superseded'
            elif not isinstance(item.get('at'), (int, float)) or now - item['at'] > _item_max_age(item, cfg['max_age_s']):
                reason = 'stale'
            elif len(text) > MAX_TEXT:
                reason = 'too_long'
            elif item.get('key') == state.get('last_key'):
                reason = 'same_key'
            elif hashlib.sha256(text.encode()).hexdigest()[:16] in state.get('recent', []):
                reason = 'repeat'
            elif now - float(state.get('last_at', 0)) < cfg['cooldown_s']:
                reason = 'cooldown'
            elif _busy.is_set():
                reason = 'in_flight'
            if reason is None:
                chosen = item
            else:
                results.append((item, 'skipped:' + reason))
        for item, status in reversed(results):
            append_log(runtime_dir, 'hanjuku_narration', {
                'schema': 1, 'at': now, 'seq': item['seq'], 'key': item.get('key'),
                'status': status, 'text': item.get('text')})
        if terminal:
            # Terminal recaps are selected by immutable run identity, not by
            # the ordinary commentary sequence cursor. Failed writes can
            # therefore retry after a restart; the durable outbox receipt
            # makes a post-publish retry a no-op.
            candidate = next((item for item in reversed(all_items)
                              if item.get('terminal_recap')), None)
            terminal_done = state.get(TERMINAL_DELIVERIES_KEY)
            if not isinstance(terminal_done, dict):
                terminal_done = {}
                state[TERMINAL_DELIVERIES_KEY] = terminal_done
            terminal_speakers = state.get(TERMINAL_SPEAKERS_KEY)
            if not isinstance(terminal_speakers, dict):
                terminal_speakers = {}
                state[TERMINAL_SPEAKERS_KEY] = terminal_speakers
            delivery_key = _terminal_delivery_key({
                key: candidate.get(key) if candidate else None
                for key in ('game', 'runtime_id', 'generation', 'lease_id')
            }) if candidate else None
            prior = terminal_done.get(delivery_key) if delivery_key else None
            if candidate and not (isinstance(prior, str) and prior in {
                    'enqueued', 'skipped:generation_mismatch', 'skipped:too_long',
                    'delivery_failed_permanent', 'disabled'}):
                text = candidate.get('text')
                if not isinstance(text, str) or not text.strip() or len(text) > TERMINAL_MAX_TEXT:
                    status = 'skipped:too_long'
                    terminal_done[delivery_key] = status
                    append_log(runtime_dir, 'hanjuku_narration', {
                        'schema': 1, 'at': now, 'seq': candidate.get('seq'),
                        'key': candidate.get('key'), 'status': status, 'text': text,
                        **{key: candidate.get(key) for key in ('game', 'runtime_id', 'generation', 'lease_id')},
                    })
                else:
                    # Commit ordinary cursor movement before the terminal
                    # handoff. A validated pending record is durable before
                    # publishing, so a lifecycle transition never depends on
                    # a later audio-lock retry succeeding.
                    atomic_write_json(runtime_dir / STATE, state)
                    if enqueue is None:
                        from .trading.soren_output import enqueue_hanjuku_terminal as enqueue

                    def persist_pending_handoff():
                        terminal_done[delivery_key] = 'pending'
                        terminal_speakers[delivery_key] = cfg['speaker']
                        atomic_write_json(runtime_dir / STATE, state)

                    for attempt in range(3):
                        status = _deliver(g, runtime_dir, candidate, cfg['speaker'], enqueue,
                                          cfg['max_age_s'], terminal=True,
                                          before_publish=persist_pending_handoff)
                        if status not in {'delivery_failed', 'skipped:switch_busy'} or attempt == 2:
                            break
                        time.sleep(0.05 * (attempt + 1))
                    if status in {'delivery_failed', 'skipped:switch_busy'}:
                        # If the transition lock itself was busy, _deliver
                        # could not validate the active runtime before its
                        # publish callback. Confirm it still matches before
                        # committing a replayable handoff intent.
                        if terminal_done.get(delivery_key) != 'pending':
                            try:
                                from .agent.fence import read_canonical
                                active = (read_canonical(g.state_dir).get('active') or {})
                                if all(active.get(key) == candidate.get(key)
                                       for key in ('game', 'runtime_id', 'generation', 'lease_id')):
                                    persist_pending_handoff()
                            except Exception:
                                pass
                    elif status in {'enqueued', 'skipped:generation_mismatch',
                                    'skipped:too_long', 'delivery_failed_permanent'}:
                        terminal_done[delivery_key] = status
                        terminal_speakers.pop(delivery_key, None)
            atomic_write_json(runtime_dir / STATE, state)
            for item, status in reversed(results):
                append_log(runtime_dir, 'hanjuku_narration', {
                    'schema': 1, 'at': now, 'seq': item['seq'], 'key': item.get('key'),
                    'status': status, 'text': item.get('text')})
            return candidate
        if chosen:
            digest = hashlib.sha256(chosen['text'].encode()).hexdigest()[:16]
            state.update(last_at=now, last_key=chosen.get('key'),
                         recent=(state.get('recent', []) + [digest])[-RECENT:])
            _busy.set()
            if enqueue is None:
                from .trading.soren_output import enqueue_audio_text as enqueue
            try:
                threading.Thread(target=_deliver, args=(g, runtime_dir, chosen, cfg['speaker'], enqueue, cfg['max_age_s']),
                                 daemon=True, name='hanjuku-narration').start()
            except Exception:
                _busy.clear()
        atomic_write_json(runtime_dir / STATE, state)
        return chosen
    finally:
        os.close(fd)


def terminal_delivery_pending(runtime_dir: Path, identity: dict) -> bool:
    """Whether this confirmed run has a durable recap awaiting audio handoff."""
    candidate = next((item for item in reversed(
        _terminal_candidates(runtime_dir))
        if item.get('terminal_recap')
        and all(item.get(key) == identity.get(key)
                for key in ('game', 'runtime_id', 'generation', 'lease_id'))), None)
    if candidate is None:
        return False
    state = read_record(runtime_dir / STATE) or {}
    deliveries = state.get(TERMINAL_DELIVERIES_KEY)
    delivery_key = _terminal_delivery_key(identity)
    status = deliveries.get(delivery_key) if isinstance(deliveries, dict) else None
    for record in _narration_records(runtime_dir):
        if record.get('delivery_key') == delivery_key:
            status = record.get('status')
    return not (isinstance(status, str) and status in {
        'enqueued', 'skipped:generation_mismatch', 'skipped:too_long',
        'delivery_failed_permanent', 'disabled',
    })


def _terminal_candidates(runtime_dir: Path) -> list[dict]:
    """Read terminal candidates across the existing commentary log rotation."""
    return [item for name in ('hanjuku_commentary.previous.jsonl',
                              'hanjuku_commentary.jsonl')
            for item in _tail(runtime_dir / name)]


def _narration_records(runtime_dir: Path) -> list[dict]:
    """Read delivery outcomes across the existing one-generation log rotation."""
    return [item for name in ('hanjuku_narration.previous.jsonl',
                              'hanjuku_narration.jsonl')
            for item in _tail_any(runtime_dir / name)]


def _record_terminal_delivery(runtime_dir: Path, delivery_key: str, status: str,
                              *, identity: dict | None = None, candidate: dict | None = None,
                              speaker: str = '') -> None:
    """Best-effort state receipt plus a durable narration-log outcome.

    The immutable recap candidate and validated terminal run record remain the
    recovery source if another observer currently owns the narration lock.
    The outbox itself deduplicates by ``delivery_key`` after a publish/crash.
    """
    state_path = runtime_dir / STATE
    lock_path = runtime_dir / 'hanjuku_narration.lock'
    if not lock_path.is_symlink() and not state_path.is_symlink():
        fd = None
        locked = False
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                pass
            if locked:
                try:
                    state = read_record(state_path) or {}
                except Exception:
                    state = {}
                deliveries = state.get(TERMINAL_DELIVERIES_KEY)
                if not isinstance(deliveries, dict):
                    deliveries = {}
                    state[TERMINAL_DELIVERIES_KEY] = deliveries
                speakers = state.get(TERMINAL_SPEAKERS_KEY)
                if not isinstance(speakers, dict):
                    speakers = {}
                    state[TERMINAL_SPEAKERS_KEY] = speakers
                current = deliveries.get(delivery_key)
                final = {'enqueued', 'skipped:generation_mismatch', 'skipped:too_long',
                         'delivery_failed_permanent', 'disabled'}
                if not isinstance(current, str) or current not in final:
                    deliveries[delivery_key] = status
                    if status == 'pending' and speaker:
                        speakers[delivery_key] = speaker
                    elif status in final:
                        speakers.pop(delivery_key, None)
                    atomic_write_json(state_path, state)
        except Exception:
            pass
        finally:
            if fd is not None:
                if locked:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                os.close(fd)

    record = {
        'schema': 1, 'at': time.time(), 'status': status,
        'delivery_key': delivery_key,
    }
    if candidate:
        record.update(seq=candidate.get('seq'), key=candidate.get('key'), text=candidate.get('text'))
    if identity:
        record.update(identity)
    try:
        append_log(runtime_dir, 'hanjuku_narration', record)
        log_path = runtime_dir / 'hanjuku_narration.jsonl'
        if not log_path.is_symlink():
            with log_path.open('ab') as stream:
                os.fsync(stream.fileno())
    except Exception:
        pass


def retry_pending_terminal_deliveries(g, *, exclude_key: str = '') -> bool:
    """Drain validated terminal candidates before a later audio item.

    Candidates and terminal evidence are already durable in the run logs. Do
    not require the narration state lock to discover or enqueue them: a lock
    collision must not lose a recap when its corner changes generation. The
    shared outbox receipt deduplicates concurrent attempts. Invalid candidates
    are recorded as permanent failures and do not block unrelated audio; a
    valid recap with a transient outbox failure keeps its queue position.
    """
    # This path is also called by unrelated audio producers, after the old
    # Hanjuku monitor may have exited. A live disable must stop durable fixed
    # recaps here too; otherwise a pending old line can reappear on next enqueue.
    try:
        from .config import load_game
        if not settings(load_game(g, 'hanjuku-hero'))['enabled']:
            return True
    except Exception:
        # Unreadable config is not permission to resume retired fixed speech,
        # and must not hold another producer's queue position.
        return True
    state_dir = getattr(g, 'state_dir', None)
    if not state_dir:
        return True
    runtime_root = Path(state_dir) / 'runtimes'
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        return True

    from .hanjuku_run import terminal as terminal_evidence

    final_statuses = {'enqueued', 'skipped:generation_mismatch', 'skipped:too_long',
                      'delivery_failed_permanent', 'disabled'}
    def is_final(status):
        return isinstance(status, str) and status in final_statuses

    candidates_by_key = {}
    state_by_runtime = {}
    orphaned = []
    for runtime_dir in sorted(runtime_root.iterdir()):
        if runtime_dir.is_symlink() or not runtime_dir.is_dir():
            continue
        state_path = runtime_dir / STATE
        try:
            state = {} if state_path.is_symlink() else (read_record(state_path) or {})
        except Exception:
            # A bad mutable receipt must not hide an intact immutable recap.
            state = {}
        state_by_runtime[runtime_dir] = state
        deliveries = state.get(TERMINAL_DELIVERIES_KEY)
        if not isinstance(deliveries, dict):
            deliveries = {}
        logs = _narration_records(runtime_dir)
        log_statuses = {}
        for entry in logs:
            key = entry.get('delivery_key')
            if isinstance(key, str):
                log_statuses[key] = entry.get('status')
        speakers = state.get(TERMINAL_SPEAKERS_KEY)
        if not isinstance(speakers, dict):
            speakers = {}
        for candidate in _terminal_candidates(runtime_dir):
            if not candidate.get('terminal_recap'):
                continue
            identity = {key: candidate.get(key)
                        for key in ('game', 'runtime_id', 'generation', 'lease_id')}
            delivery_key = _terminal_delivery_key(identity)
            if delivery_key == exclude_key:
                continue
            state_status = deliveries.get(delivery_key)
            log_status = log_statuses.get(delivery_key)
            status = (state_status if is_final(state_status)
                      else log_status if is_final(log_status)
                      else state_status or log_status)
            if is_final(status):
                continue
            # Commentary is append-only; later entries for the same run are
            # the freshest frozen candidate and retain their original order.
            candidates_by_key[delivery_key] = (runtime_dir, candidate, speakers.get(delivery_key, ''))
        for delivery_key, status in deliveries.items():
            if (isinstance(delivery_key, str) and delivery_key != exclude_key
                    and not is_final(status)
                    and delivery_key not in candidates_by_key):
                orphaned.append((runtime_dir, delivery_key))

        # A confirmed game-over run is itself enough to detect a missing
        # recap candidate, even when narration lock contention prevented a
        # mutable state receipt from ever being created.
        try:
            run_record = read_record(runtime_dir / 'hanjuku_run.json', limit=256 * 1024)
        except Exception:
            run_record = {}
        if run_record.get('terminal_reason') == 'game_over':
            identity = {key: run_record.get(key)
                        for key in ('game', 'runtime_id', 'generation', 'lease_id')}
            delivery_key = _terminal_delivery_key(identity)
            if delivery_key != exclude_key and delivery_key not in candidates_by_key:
                state_status = deliveries.get(delivery_key)
                log_status = log_statuses.get(delivery_key)
                status = (state_status if is_final(state_status)
                          else log_status if is_final(log_status)
                          else state_status or log_status)
                if not is_final(status):
                    orphaned.append((runtime_dir, delivery_key))

    for runtime_dir, delivery_key in orphaned:
        _record_terminal_delivery(runtime_dir, delivery_key, 'delivery_failed_permanent')

    if not candidates_by_key:
        return True

    verified = []
    for delivery_key, (runtime_dir, candidate, speaker) in candidates_by_key.items():
        identity = {key: candidate.get(key)
                    for key in ('game', 'runtime_id', 'generation', 'lease_id')}
        deliveries = state_by_runtime[runtime_dir].get(TERMINAL_DELIVERIES_KEY)
        status = deliveries.get(delivery_key) if isinstance(deliveries, dict) else None
        if status == 'delivery_failed_permanent':
            continue
        valid_identity = (
            identity.get('game') == 'hanjuku-hero'
            and identity.get('runtime_id') == runtime_dir.name
            and type(identity.get('generation')) is int
            and isinstance(identity.get('lease_id'), str) and bool(identity['lease_id'])
        )
        text = candidate.get('text')
        timestamp = candidate.get('at')
        if (not valid_identity or not isinstance(text, str) or not text.strip()
                or len(text) > TERMINAL_MAX_TEXT
                or type(timestamp) not in (int, float) or not math.isfinite(timestamp)):
            final = 'skipped:too_long' if isinstance(text, str) and len(text) > TERMINAL_MAX_TEXT else 'delivery_failed_permanent'
            _record_terminal_delivery(runtime_dir, delivery_key, final,
                                      identity=identity, candidate=candidate)
            continue
        try:
            evidence = terminal_evidence(runtime_dir, identity)
        except Exception:
            _record_terminal_delivery(runtime_dir, delivery_key, 'delivery_failed_permanent',
                                      identity=identity, candidate=candidate)
            continue
        if not evidence or evidence.get('terminal_reason') != 'game_over':
            # The candidate is durable but not yet authorized by a complete
            # terminal latch. A later observer can revisit it after evidence
            # becomes complete; meanwhile unrelated audio may proceed.
            _record_terminal_delivery(runtime_dir, delivery_key, 'skipped:terminal_unconfirmed',
                                      identity=identity, candidate=candidate)
            continue
        _record_terminal_delivery(runtime_dir, delivery_key, 'pending', identity=identity,
                                  candidate=candidate, speaker=str(speaker or ''))
        verified.append((runtime_dir, delivery_key, candidate, identity, str(speaker or '')))

    if not verified:
        return True
    verified.sort(key=lambda item: (item[2]['at'], item[2]['seq']))
    try:
        from . import webui
        from .trading.soren_output import resolve_soren_root
        soren_root = resolve_soren_root(g)
    except Exception:
        return False

    for runtime_dir, delivery_key, candidate, identity, speaker in verified:
        try:
            result = webui._enqueue_audio_text(
                soren_root, candidate['text'], 'hanjuku_terminal',
                speaker=speaker, delivery_key=delivery_key,
            )
            if not isinstance(result, dict) or result.get('ok') is not True:
                _record_terminal_delivery(runtime_dir, delivery_key, 'pending',
                                          identity=identity, candidate=candidate, speaker=speaker)
                return False
        except (BlockingIOError, InterruptedError, TimeoutError):
            _record_terminal_delivery(runtime_dir, delivery_key, 'pending',
                                      identity=identity, candidate=candidate, speaker=speaker)
            return False
        except OSError as exc:
            if _permanent_io_error(exc):
                _record_terminal_delivery(runtime_dir, delivery_key, 'delivery_failed_permanent',
                                          identity=identity, candidate=candidate)
                continue
            _record_terminal_delivery(runtime_dir, delivery_key, 'pending',
                                      identity=identity, candidate=candidate, speaker=speaker)
            return False
        except (TypeError, ValueError, RuntimeError):
            _record_terminal_delivery(runtime_dir, delivery_key, 'delivery_failed_permanent',
                                      identity=identity, candidate=candidate)
            continue
        except Exception:
            _record_terminal_delivery(runtime_dir, delivery_key, 'pending',
                                      identity=identity, candidate=candidate, speaker=speaker)
            return False
        _record_terminal_delivery(runtime_dir, delivery_key, 'enqueued',
                                  identity=identity, candidate=candidate)
    return True


def delivery_summary(runtime_dir: Path) -> dict:
    """Counts of narration outcomes for this runtime (no text)."""
    counts = {}
    for item in _tail_any(runtime_dir / 'hanjuku_narration.jsonl'):
        status = str(item.get('status', ''))
        key = status.split(':', 1)[0] if status else 'unknown'
        counts[key] = counts.get(key, 0) + 1
    return {k: counts.get(k, 0) for k in ('enqueued', 'delivery_failed', 'skipped')}


def _tail_any(path: Path) -> list[dict]:
    if not path.exists() or path.is_symlink():
        return []
    out = []
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines()[-2000:]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out
