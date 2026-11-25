"""One bounded periodic-commentary job through Soren's radio dispatcher.

The corner starts this short-lived worker at most once per interval.  It reads
one bounded situation snapshot, asks for several candidate lines, rejects
ungrounded candidates, chooses the least repetitive one and hands it to the
existing fenced audio queue.  It never posts a chat comment and never touches
game input.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import stat
import sys
import time

from . import hanjuku_periodic as periodic
from .config import load_global, load_game
from .game_switch import atomic_write_json
from .hanjuku_run import append_log
from .hanjuku_scene_process import SceneProcessUnavailable, enable_subreaper
from .hanjuku_scene_worker import ROLES, SceneError, generate_prompt
from .retroarch_boundary import read_record

IDENTITY_KEYS = ('game', 'runtime_id', 'generation', 'lease_id')


def _log(runtime, payload):
    try:
        append_log(Path(runtime), periodic.LOG, {'schema': 1, **payload})
    except Exception:
        pass


def checked_request(runtime, identity, now):
    value = read_record(Path(runtime) / periodic.REQUEST, limit=65536)
    if (type(value.get('schema')) is not int or value['schema'] != 1
            or value.get('version') != periodic.VERSION
            or any(value.get(key) != item for key, item in identity.items())
            or type(value.get('seq')) is not int or value['seq'] < 1
            or type(value.get('at')) not in (int, float)
            or not math.isfinite(value['at'])
            or type(value.get('expires_at')) not in (int, float)
            or not math.isfinite(value['expires_at'])
            or not 0 < value['expires_at'] - value['at'] <= periodic.REQUEST_TTL_S
            or not isinstance(value.get('recent_texts'), list)
            or len(value['recent_texts']) > periodic.RECENT
            or any(not isinstance(text, str) or len(text) > periodic.MAX_TEXT
                   for text in value['recent_texts'])
            or not isinstance(value.get('situation'), dict)
            or not periodic.flatten(value['situation'])):
        raise periodic.PeriodicError('stale')
    if not value['at'] <= now < value['expires_at']:
        raise periodic.PeriodicError('stale')
    return value


def current(g, game, runtime, request, *, now=None):
    from .agent.fence import read_canonical

    now = time.time() if now is None else now
    try:
        enabled = periodic.config(game, periodic._game_config_path(g))['enabled']
    except Exception:
        enabled = False
    if not enabled:
        raise periodic.PeriodicError('disabled')
    try:
        canonical = read_canonical(g.state_dir)
        run = read_record(Path(runtime) / 'hanjuku_run.json', limit=256 * 1024)
    except Exception:
        raise periodic.PeriodicError('fence_lost') from None
    identity = {key: request.get(key) for key in IDENTITY_KEYS}
    if (canonical.get('phase') != 'ready'
            or any((canonical.get('active') or {}).get(key) != value
                   for key, value in identity.items())
            or any(run.get(key) != value for key, value in identity.items())
            or run.get('playing') is not True or run.get('terminal_reason')
            or run.get('terminal_candidate')):
        raise periodic.PeriodicError('fence_lost')
    if not request['at'] <= now < request['expires_at']:
        raise periodic.PeriodicError('stale')
    return run


def _delivery_status(value):
    if value == 'enqueued':
        return 'enqueued'
    if value == 'skipped:disabled' or value == 'disabled':
        return 'disabled'
    if value in {'skipped:stale', 'stale'}:
        return 'stale'
    if value in {'skipped:fence_lost', 'skipped:generation_mismatch',
                 'skipped:terminal', 'fence_lost', 'run_ended'}:
        return 'fence_lost'
    return 'delivery_failed'


def consume(g, runtime, *, generate_fn=None, deliver_fn=None, now=None):
    """Consume one request.  Returns ``(status, text, attempts, role)``."""
    runtime = Path(runtime)
    now = time.time() if now is None else now
    try:
        request_file = read_record(runtime / periodic.REQUEST, limit=65536)
        identity = periodic.checked_identity(request_file)
    except Exception:
        return 'stale', None, 0, None
    if runtime.name != identity['runtime_id']:
        return 'stale', None, 0, None
    try:
        game = load_game(g, 'hanjuku-hero')
        cfg = periodic.config(game, periodic._game_config_path(g))
    except Exception:
        _write_result(runtime, identity, int(request_file.get('seq') or 0),
                      'unavailable', None, 0, None)
        return 'unavailable', None, 0, None
    if not cfg['enabled']:
        _write_result(runtime, identity, int(request_file.get('seq') or 0),
                      'disabled', None, 0, None)
        return 'disabled', None, 0, None
    if cfg.get('agents'):
        os.environ['HANJUKU_PERIODIC_AGENTS'] = cfg['agents']
    else:
        os.environ.pop('HANJUKU_PERIODIC_AGENTS', None)
    try:
        request = checked_request(runtime, identity, now)
        current(g, game, runtime, request, now=now)
    except periodic.PeriodicError as exc:
        _write_result(runtime, identity, int(request_file.get('seq') or 0), exc.reason,
                      None, 0, None)
        return exc.reason, None, 0, None
    facts = periodic.flatten(request['situation'])
    recent = list(request['recent_texts'])
    avoid = []
    attempts = 0
    status = 'invalid_output'
    chosen = None
    role = None
    for _attempt in range(cfg['max_attempts']):
        remaining = min(cfg['generation_timeout_s'],
                        request['expires_at'] - time.time() - 2)
        if remaining < 1:
            status = 'stale'
            break
        prompt = periodic.build_prompt(request['situation'], recent, avoid)
        attempts += 1
        try:
            output, result_role = (generate_fn or generate_prompt)(
                g, runtime, prompt, remaining)
        except SceneError as exc:
            status = exc.reason if exc.reason in {
                'disabled', 'stale', 'unavailable', 'timeout',
                'generation_failed', 'worker_error'} else 'generation_failed'
            _log(runtime, {'event': 'generate_failed', 'reason': status,
                           'seq': request['seq'], 'attempt': attempts})
            if status in {'disabled', 'stale'}:
                break
            continue
        role = result_role if result_role in ROLES else None
        try:
            candidates = periodic.parse_candidates(output)
        except periodic.PeriodicError:
            status = 'invalid_output'
            _log(runtime, {'event': 'invalid_output', 'seq': request['seq'],
                           'attempt': attempts})
            continue
        valid = [text for text in candidates if periodic.valid_text(text, facts)]
        _log(runtime, {'event': 'generated', 'seq': request['seq'],
                       'attempt': attempts, 'candidates': len(candidates),
                       'valid': len(valid)})
        if not valid:
            status = 'invalid_output'
            continue
        best = periodic.choose_novel(valid, recent)
        if best is None:
            status = 'duplicate'
            avoid.extend(valid)
            continue
        chosen = best
        status = 'enqueued'
        break
    if chosen is None:
        _write_result(runtime, identity, request['seq'], status, None, attempts, role)
        _log(runtime, {'event': 'skipped', 'reason': status, 'seq': request['seq'],
                       'attempts': attempts})
        return status, None, attempts, role
    try:
        current(g, game, runtime, request)
        if not periodic.config(game, periodic._game_config_path(g))['enabled']:
            raise periodic.PeriodicError('disabled')
    except periodic.PeriodicError as exc:
        _write_result(runtime, identity, request['seq'], exc.reason, None, attempts, role)
        return exc.reason, None, attempts, role
    if deliver_fn is None:
        from .hanjuku_narration import deliver_periodic as deliver_fn
    try:
        delivered = deliver_fn(g, game, runtime, identity, chosen,
                               at=request['at'], max_age=periodic.REQUEST_TTL_S,
                               seq=request['seq'])
    except Exception:
        delivered = 'delivery_failed'
    status = _delivery_status(delivered)
    if status == 'enqueued':
        _write_result(runtime, identity, request['seq'], status, chosen, attempts, role)
        _log(runtime, {'event': 'deliver_enqueued', 'seq': request['seq'],
                       'attempts': attempts, 'chars': len(chosen),
                       **({'role': role} if role else {})})
        return status, chosen, attempts, role
    _write_result(runtime, identity, request['seq'], status, None, attempts, role)
    _log(runtime, {'event': 'deliver_failed', 'reason': status,
                   'seq': request['seq'], 'attempts': attempts})
    return status, None, attempts, role


def _write_result(runtime, identity, seq, status, text, attempts, role):
    value = {'schema': 1, **identity, 'seq': seq, 'at': time.time(), 'status': status,
             'attempts': attempts}
    if isinstance(text, str):
        value['text'] = text
    if role in ROLES:
        value['role'] = role
    try:
        atomic_write_json(Path(runtime) / periodic.RESULT, value)
    except Exception:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--lock-fd', type=int, required=True)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    fd = args.lock_fd
    try:
        g = load_global(repo, args.config)
        runtime = args.runtime
        if (runtime.is_symlink() or runtime.parent.is_symlink()
                or runtime.resolve().parent != (g.state_dir / 'runtimes').resolve()):
            return 2
        expected = (runtime / periodic.LOCK).stat(follow_symlinks=False)
        actual = os.fstat(fd)
        if (not stat.S_ISREG(expected.st_mode) or not stat.S_ISREG(actual.st_mode)
                or (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino)):
            return 2
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def stop(_signal, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        def unavailable(*_args):
            raise SceneError('unavailable')

        try:
            enable_subreaper()
            provider = None
        except SceneProcessUnavailable:
            provider = unavailable
        consume(g, runtime, generate_fn=provider)
        return 0
    except (OSError, ValueError, KeyboardInterrupt):
        return 2
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
