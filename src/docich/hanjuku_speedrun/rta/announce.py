"""Short, measured split announcements for the hanjuku-speedrun RTA corner.

The split timer records a sentence-worthy event at three places:

* every confirmed segment (``timer.confirm_split``),
* a finished attempt (``timer.finish``),
* a DNF attempt (``timer.dnf``).

This module turns exactly those measured fields into one short Japanese
sentence and hands it to the *copied* corner's existing narration sink
(``docich.hanjuku_speedrun.hanjuku_narration``), which owns the Soren audio
queue and its runtime fence.  It never reads a screen, calls a model or adds a
network path.

The bot entry point runs once per observation, so delivering inside that
process would delay its printed input.  A recorded announcement is therefore
kept in a small durable queue in the runtime directory and a detached child
process drains it.  The queue is bounded and oldest events are discarded;

* a repeated ``(attempt_id, event)`` is never spoken twice, even after the
  bot restarts (the receipt lives next to the timer records),
* events older than :data:`MAX_AGE_S` are dropped, and
* at most :data:`MAX_PENDING` events wait; the oldest is dropped first.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

try:  # pragma: no cover - the VM is Linux; tests also run on macOS.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from .timer import SEGMENT_COUNT

GAME = 'hanjuku-speedrun'
STATE_FILE = 'rta_announce.json'
LOCK_FILE = 'rta_announce.lock'
LAUNCH_MARKER = '.rta-announce.launched'

SCHEMA = 1
MAX_TEXT = 120
MAX_AGE_S = 120.0
COOLDOWN_S = 25.0
MAX_PENDING = 8
MAX_SEEN = 256
LAUNCH_DEBOUNCE_S = 5.0

IDENTITY_KEYS = ('game', 'runtime_id', 'generation', 'lease_id')
_REASON = {
    'game_over': 'ゲームオーバー',
    'stalled': '画面停止',
    'stopped': '中断',
}


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


def _int(value):
    return value if type(value) is int else None


def _seconds(ms):
    value = _int(ms)
    return None if value is None or value < 0 else value // 1000


def _segment_time(ms):
    """``分:秒`` for a measured segment (whole seconds, never rounded up)."""
    seconds = _seconds(ms)
    if seconds is None:
        return None
    minutes, seconds = divmod(seconds, 60)
    return f'{minutes}分{seconds:02d}秒'


def _delta_time(ms):
    seconds = _seconds(ms)
    if seconds is None:
        return None
    if seconds == 0:
        return '1秒未満'
    minutes, seconds = divmod(seconds, 60)
    return f'{minutes}分{seconds:02d}秒' if minutes else f'{seconds}秒'


def _total_time(ms):
    """``時:分:秒`` for a clear time, omitting a leading zero hour."""
    seconds = _seconds(ms)
    if seconds is None:
        return None
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours}時間{minutes:02d}分{seconds:02d}秒'
    return f'{minutes}分{seconds:02d}秒'


def _pb_phrase(delta_ms):
    """The PB comparison only when the timer measured one."""
    if type(delta_ms) is not int:
        return None
    if delta_ms == 0:
        return 'PBと同じタイムです。'
    amount = _delta_time(abs(delta_ms))
    if amount is None:
        return None
    return f'PBより{amount}{"先行" if delta_ms < 0 else "遅れ"}です。'


def compose_split(split) -> str | None:
    """One measured sentence for a confirmed split, or ``None``.

    ``split`` is the object appended by :func:`rta.timer.confirm_split`: it
    already carries ``index``/``end_index``, ``segment_ms``, ``verdict``,
    ``delta_ms`` and ``merged``.  No other field is read.
    """
    if not isinstance(split, dict):
        return None
    index = _int(split.get('index'))
    if index is None or not 0 <= index < SEGMENT_COUNT:
        return None
    end = _int(split.get('end_index'))
    if end is None or end < index or end >= SEGMENT_COUNT:
        end = index
    segment_ms = _int(split.get('segment_ms'))
    if segment_ms is None or segment_ms < 0:
        return None
    duration = _segment_time(segment_ms)
    if duration is None:
        return None
    merged = split.get('merged') is True or end > index
    if merged:
        head = f'第{index + 1}〜{end + 1}話をまとめて{duration}で通過。'
    else:
        head = f'第{index + 1}話を{duration}で通過。'
    tail = ''
    # A merged range has no individual times, so it can never be a best.
    if not merged and split.get('verdict') == 'gold':
        tail += '区間ベスト更新です。'
    phrase = _pb_phrase(split.get('delta_ms'))
    if phrase:
        tail += phrase
    text = head + tail
    return text if len(text) <= MAX_TEXT else None


def compose_finish(timer) -> str | None:
    """Clear time and whether this attempt updated the PB."""
    if not isinstance(timer, dict) or timer.get('status') != 'finished':
        return None
    total = _total_time(timer.get('total_ms'))
    updated = timer.get('pb_updated')
    if total is None or type(updated) is not bool:
        return None
    suffix = 'PBを更新しました。' if updated else 'PB更新はありません。'
    text = f'{SEGMENT_COUNT}話を完走。クリアタイムは{total}。{suffix}'
    return text if len(text) <= MAX_TEXT else None


def compose_dnf(timer) -> str | None:
    """The running chapter and a fixed one-word reason; nothing else."""
    if not isinstance(timer, dict) or timer.get('status') != 'dnf':
        return None
    current = _int(timer.get('current_index'))
    if current is None or current < 0:
        current = 0
    chapter = min(current, SEGMENT_COUNT - 1) + 1
    reason = _REASON.get(timer.get('dnf_reason'), '中断')
    text = f'第{chapter}話の途中で終了。理由は{reason}です。'
    return text if len(text) <= MAX_TEXT else None


def _cooldown_seconds():
    """The live scene-commentary interval, never below this module's floor."""
    try:
        from docich.hanjuku_speedrun import hanjuku_scene
        value = hanjuku_scene.config(Path(__file__).resolve().parents[4])['cooldown_s']
        return max(COOLDOWN_S, float(value))
    except Exception:
        return COOLDOWN_S


# ---------------------------------------------------------------------------
# durable queue
# ---------------------------------------------------------------------------


def _identity(value):
    if not isinstance(value, dict):
        return None
    result = {key: value.get(key) for key in IDENTITY_KEYS}
    if (result['game'] != GAME
            or not isinstance(result['runtime_id'], str) or not result['runtime_id']
            or type(result['generation']) is not int
            or not isinstance(result['lease_id'], str) or not result['lease_id']):
        return None
    return result


def valid_identity(value):
    """The four measured identity fields, or ``None`` when incomplete."""
    return _identity(value)


def _attempt_id(timer, identity):
    value = timer.get('attempt_id') if isinstance(timer, dict) else None
    if isinstance(value, str) and value:
        return value
    return f"{identity['runtime_id']}:{identity['generation']}"


def _state(raw):
    state = {'schema': SCHEMA, 'pending': [], 'seen': [], 'last_at': None}
    if not isinstance(raw, dict):
        return state
    pending = []
    for item in raw.get('pending') if isinstance(raw.get('pending'), list) else ():
        if not isinstance(item, dict):
            continue
        key, text, at = item.get('key'), item.get('text'), item.get('at')
        ident = _identity(item.get('identity'))
        if (not isinstance(key, str) or not key or not isinstance(text, str)
                or not text.strip() or len(text) > MAX_TEXT
                or type(at) not in (int, float) or not math.isfinite(at) or at <= 0
                or ident is None):
            continue
        pending.append({'key': key, 'text': text, 'at': float(at), 'identity': ident,
                        'terminal': item.get('terminal') is True})
    state['pending'] = pending[-MAX_PENDING:]
    seen = raw.get('seen')
    state['seen'] = ([key for key in seen if isinstance(key, str)][-MAX_SEEN:]
                     if isinstance(seen, list) else [])
    last_at = raw.get('last_at')
    if type(last_at) in (int, float) and math.isfinite(last_at):
        state['last_at'] = float(last_at)
    return state


@contextmanager
def _locked(runtime, wait_s=0.0):
    """Exclusive state lock; ``False`` when another owner holds it too long."""
    if fcntl is None:  # pragma: no cover
        yield False
        return
    path = Path(runtime) / LOCK_FILE
    fd = None
    locked = False
    deadline = time.monotonic() + max(0.0, wait_s)
    try:
        if not path.is_symlink():
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.005)
    except OSError:
        locked = False
    try:
        yield locked
    finally:
        if fd is not None:
            if locked:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass


def _state_path(runtime):
    return Path(runtime) / STATE_FILE


def _read_state(runtime):
    path = _state_path(runtime)
    if path.is_symlink() or not path.exists():
        return _state(None)
    try:
        if not path.is_file() or path.stat().st_size > 64 * 1024:
            return _state(None)
        return _state(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError, UnicodeError):
        return _state(None)


def _write_state(runtime, state):
    path = _state_path(runtime)
    if path.is_symlink():
        return
    try:
        from docich.game_switch import atomic_write_json
        atomic_write_json(path, state)
    except Exception:
        # A missing receipt costs one duplicate later, never bot input.
        pass


def pending(runtime):
    """A copy of the queued items (tests and diagnostics only)."""
    return [dict(item) for item in _read_state(runtime)['pending']]


def _record(runtime, key, text, at_ms, identity, *, terminal, now):
    """Add one bounded event; returns ``True`` only when it was queued."""
    ident = _identity(identity)
    if ident is None or not isinstance(key, str) or not key:
        return False
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        return False
    now = time.time() if now is None else now
    if type(at_ms) not in (int, float) or not math.isfinite(at_ms) or at_ms <= 0:
        at_ms = now * 1000
    # A screenshot mtime from another clock may be slightly ahead; it is
    # still "now" for the age bound and must not hide the event.
    at = min(float(at_ms) / 1000.0, now)
    if now - at > MAX_AGE_S:
        return False
    # A detached drainer holds this lock for a few milliseconds; wait briefly
    # rather than dropping a measured split on that collision.
    with _locked(runtime, wait_s=0.25) as locked:
        if not locked:
            return False
        state = _read_state(runtime)
        if key in state['seen'] or any(item['key'] == key for item in state['pending']):
            return False
        state['seen'] = (state['seen'] + [key])[-MAX_SEEN:]
        state['pending'].append({'key': key, 'text': text, 'at': at,
                                 'identity': ident, 'terminal': bool(terminal)})
        if len(state['pending']) > MAX_PENDING:
            # The newest events win; the oldest must not clog the voice queue.
            state['pending'] = state['pending'][-MAX_PENDING:]
        _write_state(runtime, state)
        return True


def split_key(split, *, attempt_id):
    """The stable dedupe key for one confirmed segment (or ``None``)."""
    if not isinstance(attempt_id, str) or not attempt_id:
        return None
    if not isinstance(split, dict):
        return None
    index = _int(split.get('index'))
    end = _int(split.get('end_index'))
    return f'{attempt_id}:split:{index}:{end if end is not None else index}'


def unannounced_splits(runtime, timer):
    """Timer splits whose dedupe key is neither received nor already queued.

    The hook calls this on every observation.  Comparing the timer's
    confirmed segments with the durable ``seen`` receipts means a split whose
    first ``_record`` lost the state-lock race is retried on the next
    observation; a before/after index difference would already have moved
    past it and lose the segment forever.
    """
    splits = timer.get('splits') if isinstance(timer, dict) else None
    if not isinstance(splits, list):
        return []
    attempt_id = timer.get('attempt_id')
    state = _read_state(Path(runtime))
    known = set(state['seen'])
    known.update(item['key'] for item in state['pending'])
    result = []
    for split in splits:
        key = split_key(split, attempt_id=attempt_id)
        if key is None or key in known:
            continue
        result.append(split)
    return result


def split_event(runtime, split, *, attempt_id, identity, at_ms=None, terminal=False,
                now=None):
    """Queue one confirmed segment (false for repeated/stale/overflowed)."""
    text = compose_split(split)
    if not text:
        return False
    key = split_key(split, attempt_id=attempt_id)
    if key is None:
        return False
    return _record(Path(runtime), key, text, at_ms, identity, terminal=terminal, now=now)


def finish_event(runtime, timer, *, identity, now=None):
    """Queue one clear-time sentence."""
    text = compose_finish(timer)
    ident = _identity(identity)
    if not text or ident is None:
        return False
    key = f'{_attempt_id(timer, ident)}:finish'
    ended = timer.get('ended_at_ms')
    return _record(Path(runtime), key, text, ended, ident, terminal=True, now=now)


def dnf_event(runtime, timer, *, identity, now=None):
    """Queue one DNF sentence."""
    text = compose_dnf(timer)
    ident = _identity(identity)
    if not text or ident is None:
        return False
    key = f'{_attempt_id(timer, ident)}:dnf'
    ended = timer.get('ended_at_ms')
    return _record(Path(runtime), key, text, ended, ident, terminal=True, now=now)


# ---------------------------------------------------------------------------
# delivery through the copied corner's narration sink
# ---------------------------------------------------------------------------


def _default_config(runtime):
    """The GlobalConfig whose state dir owns this runtime, or ``None``.

    The production corner is normally started with an explicit
    ``--config config/docich.soren-live.toml`` and the bot process does not
    inherit that argument.  Resolve by the runtime's own state dir instead of
    assuming the default profile.
    """
    try:
        repo = Path(__file__).resolve().parents[4]
        target = Path(runtime).parent.parent.resolve()
        candidates = []
        configured = os.environ.get('DOCICH_CONFIG')
        if configured:
            candidates.append(None)  # load_global honours the environment
        candidates.extend([
            repo / 'config' / 'docich.soren-live.toml',
            repo / 'config' / 'docich.toml',
        ])
        try:
            candidates.extend(sorted((repo / 'config').glob('docich*.toml')))
        except OSError:
            pass
        from docich.config import load_global
        seen = set()
        for path in candidates:
            key = str(path) if path is not None else '<env>'
            if key in seen:
                continue
            seen.add(key)
            try:
                if path is not None and not path.is_file():
                    continue
                g = load_global(repo, path)
                if Path(g.state_dir).resolve() == target:
                    return g
            except Exception:
                continue
    except Exception:
        return None
    return None


def _deliver_item(runtime, item, *, g, enqueue):
    if enqueue is not None:
        enqueue(g, item['text'], context='hanjuku_commentary', speaker='')
        return 'enqueued'
    if g is None:
        g = _default_config(runtime)
    if g is None:
        return 'skipped:no_config'
    from docich.config import load_game
    from docich.hanjuku_speedrun import hanjuku_narration
    from docich.trading.soren_output import enqueue_audio_text
    game = load_game(g, GAME)
    try:
        speaker = hanjuku_narration.settings(game)['speaker']
    except Exception:
        speaker = ''
    payload = {
        'seq': 0,
        'key': item['key'],
        'text': item['text'],
        'at': item['at'],
        'evidence_kind': 'observation',
        # Finish/DNF confirm the run terminal before the hook records the last
        # segment; this lets the copied sink accept that measured line while
        # still using its ordinary (non-terminal-outbox) fence path.
        'terminal_recap': True,
        **item['identity'],
    }
    return hanjuku_narration._deliver(
        g, Path(runtime), payload, speaker, enqueue_audio_text, MAX_AGE_S)


def deliver_pending(runtime, *, now=None, g=None, enqueue=None):
    """Claim and deliver at most one queued sentence.

    Returns a fixed status string; never raises for an audio failure.  A
    transient failure puts the item back so the next detached run can retry,
    but the age bound still removes it after :data:`MAX_AGE_S`.
    """
    runtime = Path(runtime)
    now = time.time() if now is None else now
    claimed = None
    with _locked(runtime) as locked:
        if not locked:
            return 'locked'
        state = _read_state(runtime)
        fresh = []
        for item in state['pending']:
            if now - item['at'] > MAX_AGE_S:
                continue
            fresh.append(item)
        state['pending'] = fresh
        if not state['pending']:
            _write_state(runtime, state)
            return 'idle'
        last_at = state['last_at']
        if (type(last_at) in (int, float)
                and now - last_at < _cooldown_seconds()):
            _write_state(runtime, state)
            return 'cooldown'
        claimed = state['pending'].pop(0)
        state['last_at'] = now
        _write_state(runtime, state)

    try:
        status = _deliver_item(runtime, claimed, g=g, enqueue=enqueue)
    except Exception as exc:
        # One bounded line; exception text can carry private queue payloads.
        print(f'rta: announcement skipped: {type(exc).__name__}', file=sys.stderr)
        status = 'delivery_failed'

    if status in {'delivery_failed', 'delivery_timeout', 'skipped:switch_busy',
                  'skipped:no_config'}:
        with _locked(runtime) as locked:
            if locked:
                state = _read_state(runtime)
                if now - claimed['at'] <= MAX_AGE_S:
                    state['pending'].insert(0, claimed)
                    state['pending'] = state['pending'][-MAX_PENDING:]
                    _write_state(runtime, state)
    return status


# ---------------------------------------------------------------------------
# detached launcher
# ---------------------------------------------------------------------------


def _ready(runtime, now):
    state = _read_state(runtime)
    if not state['pending']:
        return False
    last_at = state['last_at']
    if type(last_at) in (int, float) and now - last_at < _cooldown_seconds():
        return False
    if any(now - item['at'] <= MAX_AGE_S for item in state['pending']):
        return True
    return False


def launch_pending(runtime, *, now=None):
    """Start the detached drain for a queued sentence; never raises.

    The bot entry point is a short-lived process, so the actual queue I/O is
    delegated to a child that survives the observation.  A recent marker keeps
    one launch per debounce window even when several observations race.
    """
    try:
        runtime = Path(runtime)
        now = time.time() if now is None else now
        if not _read_state(runtime)['pending']:
            return False
        with _locked(runtime) as locked:
            if not locked or not _ready(runtime, now):
                return False
            marker = runtime / LAUNCH_MARKER
            if marker.is_symlink():
                return False
            try:
                if marker.exists() and now - marker.stat().st_mtime < LAUNCH_DEBOUNCE_S:
                    return False
                marker.write_text(str(now), encoding='utf-8')
                os.utime(marker, (now, now))
            except OSError:
                return False
        source_root = Path(__file__).resolve().parents[3]
        env = dict(os.environ)
        existing = env.get('PYTHONPATH')
        env['PYTHONPATH'] = (f'{source_root}{os.pathsep}{existing}'
                             if existing else str(source_root))
        subprocess.Popen(
            [sys.executable, '-m', 'docich.hanjuku_speedrun.rta.announce',
             '--runtime', str(runtime)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=env,
        )
        return True
    except Exception:
        return False


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog='rta-announce')
    parser.add_argument('--runtime', required=True)
    args = parser.parse_args(argv)
    try:
        runtime = Path(args.runtime)
        if (runtime.name in {'', '.', '..'} or runtime.is_symlink()
                or runtime.parent.name != 'runtimes'):
            return 0
        deliver_pending(runtime)
    except Exception:
        print('rta: announcement failed', file=sys.stderr)
    return 0


if __name__ == '__main__':  # pragma: no cover - detached entry point
    raise SystemExit(main())
