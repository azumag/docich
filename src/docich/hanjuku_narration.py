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
RECENT = 32
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


def _deliver(g, runtime_dir: Path, item: dict, speaker: str, enqueue, max_age):
    from .agent.fence import AgentFence, FenceLost, check_fence, read_canonical, shared_section
    from .hanjuku_run import load
    from .game_switch import GameSwitchBusyError

    status = 'enqueued'
    identity = {k: item.get(k) for k in ('game', 'runtime_id', 'generation', 'lease_id')}

    def deliver_active():
        # Check briefly under the transition lock; never hold it across
        # queue I/O. The consumer rechecks identity/expiry before playback.
        if (identity['game'] != 'hanjuku-hero' or not identity['runtime_id']
                or type(identity['generation']) is not int or not identity['lease_id']):
            raise FenceLost('incomplete commentary identity')
        if time.time() - item['at'] > max_age:
            return 'skipped:stale'
        canonical = read_canonical(g.state_dir)
        check_fence(AgentFence(**identity), canonical.get('active'))
        run = load(runtime_dir, identity)
        if not run or run.get('terminal_reason') or run.get('terminal_candidate'):
            return 'skipped:terminal'
        return 'ready'

    try:
        status = shared_section(g.state_dir, deliver_active, timeout_s=0)
        if status == 'ready':
            enqueue(g, item['text'], context='hanjuku_commentary', speaker=speaker,
                    runtime_fence={**identity, 'expires_at': item['at'] + max_age})
            status = 'enqueued'
    except FenceLost:
        status = 'skipped:fence_lost'
    except GameSwitchBusyError:
        status = 'skipped:switch_busy'
    except Exception:
        # No exception text: queue errors can carry private payloads.
        status = 'delivery_failed'
    finally:
        _busy.clear()
    try:
        append_log(runtime_dir, 'hanjuku_narration', {
            'schema': 1, 'at': time.time(), 'seq': item['seq'], 'key': item.get('key'),
            'status': status, 'text': item['text'], **identity})
    except Exception:
        pass


def consider(g, game, runtime_dir: Path, *, terminal=False, now=None, enqueue=None):
    """Claim new candidates and start at most one enqueue. Never blocks on audio."""
    cfg = settings(game)
    if not cfg['enabled'] or terminal:
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
        new = [i for i in _tail(runtime_dir / 'hanjuku_commentary.jsonl') if i['seq'] > last_seq]
        if not new:
            return None
        state['last_seq'] = max(i['seq'] for i in new)
        results = []
        chosen = None
        for item in reversed(new):   # newest first; older ones are dropped
            text = item.get('text')
            reason = None
            if not text:
                reason = 'held'
            elif chosen is not None:
                reason = 'superseded'
            elif not isinstance(item.get('at'), (int, float)) or now - item['at'] > cfg['max_age_s']:
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
