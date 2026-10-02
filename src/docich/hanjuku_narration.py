"""Hanjuku commentary delivery: a non-blocking side channel to the audio queue.

The bot writes candidate lines to the runtime's ``hanjuku_commentary.jsonl``.
Whichever long-lived process observes the runtime next (agent or corner
monitor) claims new candidates under a non-blocking file lock and enqueues at
most one line on a daemon thread through the existing Soren audio queue. It
never restarts or plays audio itself, never retries, and keeps no backlog:
stale, repeated or too-frequent lines are skipped and logged.
"""
from __future__ import annotations

import fcntl
import hashlib
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
    except Exception:
        # No exception text: queue errors can carry private payloads.
        status = 'delivery_failed'
    finally:
        if not terminal:
            _busy.clear()
    try:
        append_log(runtime_dir, 'hanjuku_narration', {
            'schema': 1, 'at': time.time(), 'seq': item['seq'], 'key': item.get('key'),
            'status': status, 'text': item['text'], **identity})
    except Exception:
        pass
    return status


def consider(g, game, runtime_dir: Path, *, terminal=False, now=None, enqueue=None):
    """Claim new candidates and start at most one enqueue. Never blocks on audio.

    At a terminal only the ``terminal_recap`` candidate (the game-over recap,
    owner rule 2026-09-28) may be claimed; ordinary lines stay silent so a
    dying run never narrates stale situations.
    """
    cfg = settings(game)
    if not cfg['enabled']:
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
            if candidate and prior not in {'enqueued', 'skipped:generation_mismatch',
                                           'skipped:too_long'}:
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
                                    'skipped:too_long'}:
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
        _tail(runtime_dir / 'hanjuku_commentary.jsonl'))
        if item.get('terminal_recap')
        and all(item.get(key) == identity.get(key)
                for key in ('game', 'runtime_id', 'generation', 'lease_id'))), None)
    if candidate is None:
        return False
    state = read_record(runtime_dir / STATE) or {}
    deliveries = state.get(TERMINAL_DELIVERIES_KEY)
    if not isinstance(deliveries, dict):
        return True
    status = deliveries.get(_terminal_delivery_key(identity))
    return status not in {'enqueued', 'skipped:generation_mismatch', 'skipped:too_long'}


def retry_pending_terminal_deliveries(g, *, exclude_key: str = '') -> bool:
    """Drain durable terminal handoffs before a later audio item is enqueued.

    The frozen candidate and its validated run receipt live in the existing
    runtime narration files. A pending status is committed under the run's
    narration lock only after the active identity and terminal evidence were
    checked. This lets corner lifecycle finish without waiting on audio while
    preserving the old result's position ahead of future shared-queue audio.
    """
    state_dir = getattr(g, 'state_dir', None)
    if not state_dir:
        return True
    runtime_root = Path(state_dir) / 'runtimes'
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        return True

    try:
        from . import webui
        from .trading.soren_output import resolve_soren_root
        from .hanjuku_run import load
        soren_root = resolve_soren_root(g)
    except Exception:
        return False

    for state_path in sorted(runtime_root.glob('*/' + STATE)):
        runtime_dir = state_path.parent
        if runtime_dir.is_symlink() or state_path.is_symlink():
            continue
        state = read_record(state_path) or {}
        deliveries = state.get(TERMINAL_DELIVERIES_KEY)
        if not isinstance(deliveries, dict):
            continue
        pending = [key for key, status in deliveries.items()
                   if status == 'pending' and key != exclude_key]
        if not pending:
            continue

        lock_path = runtime_dir / 'hanjuku_narration.lock'
        if lock_path.is_symlink():
            return False
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False
            state = read_record(state_path) or {}
            deliveries = state.get(TERMINAL_DELIVERIES_KEY)
            speakers = state.get(TERMINAL_SPEAKERS_KEY)
            if not isinstance(deliveries, dict):
                continue
            if not isinstance(speakers, dict):
                speakers = {}
                state[TERMINAL_SPEAKERS_KEY] = speakers
            pending = [key for key, status in deliveries.items()
                       if status == 'pending' and key != exclude_key]
            if not pending:
                continue

            candidates = [item for item in _tail(runtime_dir / 'hanjuku_commentary.jsonl')
                          if item.get('terminal_recap')]
            for delivery_key in pending:
                candidate = next((item for item in reversed(candidates)
                                  if _terminal_delivery_key({
                                      key: item.get(key)
                                      for key in ('game', 'runtime_id', 'generation', 'lease_id')
                                  }) == delivery_key), None)
                if candidate is None:
                    return False
                identity = {key: candidate.get(key)
                            for key in ('game', 'runtime_id', 'generation', 'lease_id')}
                text = candidate.get('text')
                if (not isinstance(text, str) or not text.strip()
                        or len(text) > TERMINAL_MAX_TEXT):
                    return False
                try:
                    run = load(runtime_dir, identity)
                except Exception:
                    return False
                if not run or run.get('terminal_reason') != 'game_over':
                    return False
                try:
                    result = webui._enqueue_audio_text(
                        soren_root, text, 'hanjuku_terminal',
                        speaker=str(speakers.get(delivery_key, '') or ''),
                        delivery_key=delivery_key,
                    )
                    if not isinstance(result, dict) or result.get('ok') is not True:
                        return False
                except Exception:
                    # Preserve this result and block later audio from
                    # overtaking it. The game/corner has already completed.
                    return False

                deliveries[delivery_key] = 'enqueued'
                speakers.pop(delivery_key, None)
                try:
                    atomic_write_json(state_path, state)
                except Exception:
                    # The outbox receipt already committed. Retrying the same
                    # key after a crash reconciles without a second queue item.
                    pass
                try:
                    append_log(runtime_dir, 'hanjuku_narration', {
                        'schema': 1, 'at': time.time(), 'seq': candidate.get('seq'),
                        'key': candidate.get('key'), 'status': 'enqueued', 'text': text,
                        **identity,
                    })
                except Exception:
                    pass
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
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
