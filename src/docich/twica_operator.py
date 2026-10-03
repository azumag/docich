"""Bounded prepare/status/activate/rollback operations. Never restart the stream.

First arming the FFmpeg input requires one separately approved encoder restart.
Activation/rollback after arming only transfer the TwiCa subscription owner.
"""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path

from .twica_state import (all_legacy_quiescent, atomic_json, control, exclusive,
                         fresh, heartbeat, legacy_clients, new_control,
                         read_json, state_directory, status)


class NotReady(RuntimeError):
    pass


def prepare(directory: Path) -> dict:
    with exclusive(directory, 'operator.lock'):
        path = directory / 'control.json'
        if path.exists() or path.is_symlink():
            if control(directory).get('invalid'):
                raise NotReady('invalid_policy')
        else:
            atomic_json(directory, 'control.json', new_control('legacy', pipeline_enabled=True, preserve_legacy=True))
    return status(directory)


def _wait(predicate, deadline):
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise NotReady('handoff_timeout')


def _renderer_quiet(directory, generation):
    renderer = read_json(directory / 'renderer.json')
    return (fresh(renderer) and renderer.get('generation') == generation
            and renderer.get('subscribed') is False)


def transfer(directory: Path, destination: str, *, timeout_sec: float = 25,
             idle_confirmed: bool = False, shared_only: bool = False, legacy_role: str = 'game') -> dict:
    if destination not in {'common', 'legacy'} or not idle_confirmed or legacy_role not in {'shared', 'game'}:
        raise NotReady('idle_boundary_confirmation_required')
    with exclusive(directory, 'operator.lock'):
        before = control(directory)
        if before.get('invalid'):
            raise NotReady('prepare_required')
        if (before['owner'] == destination and (destination == 'common'
                or (not before.get('preserve_legacy') and before.get('legacy_role') == legacy_role))):
            # Idempotent requests are not proof of active output.
            return status(directory)
        renderer = read_json(directory / 'renderer.json')
        if not fresh(renderer) or renderer.get('ready') is not True:
            raise NotReady('renderer_not_ready')
        pipeline = read_json(directory / 'pipeline.json')
        if destination == 'common' and (not fresh(pipeline) or pipeline.get('ready') is not True):
            raise NotReady('encoder_input_not_armed')
        clients = legacy_clients(directory)
        roles = {c.get('role') for c in clients if fresh(c)}
        required = {'shared'} if shared_only else {'game', 'shared'}
        if (not required.issubset(roles) or any(not fresh(c) for c in clients)
                or (destination == 'legacy' and legacy_role not in roles)
                or any(sum(c.get('role') == role for c in clients) > 1 for role in roles)):
            raise NotReady('legacy_guards_not_ready')
        quiet = new_control('none', pipeline_enabled=True)
        atomic_json(directory, 'control.json', quiet)
        deadline = time.monotonic() + timeout_sec
        try:
            _wait(lambda: all_legacy_quiescent(directory, quiet['generation'])
                  and _renderer_quiet(directory, quiet['generation']), deadline)
            next_policy = new_control(destination, pipeline_enabled=True, legacy_role=legacy_role)
            atomic_json(directory, 'control.json', next_policy)
            if destination == 'common':
                def ready():
                    r = read_json(directory / 'renderer.json')
                    p = read_json(directory / 'pipeline.json')
                    return (fresh(r) and r.get('generation') == next_policy['generation']
                            and r.get('state') == 'active' and fresh(p)
                            and p.get('frame_state') == 'fresh'
                            and all_legacy_quiescent(directory, next_policy['generation']))
                _wait(ready, deadline)
            else:
                def restored():
                    cs = legacy_clients(directory)
                    return (_renderer_quiet(directory, next_policy['generation'])
                            and bool(cs) and all(fresh(c) and c.get('generation') == next_policy['generation']
                                and c.get('subscribed') == (c.get('role') == legacy_role) for c in cs))
                _wait(restored, deadline)
        except NotReady:
            # A partial transfer never guesses which subscription is still live.
            # Remain transparent; an explicit retry/rollback can reconcile it.
            atomic_json(directory, 'control.json', new_control('none', pipeline_enabled=True))
            raise
    return status(directory)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'status', 'activate', 'rollback', 'pipeline-enabled'])
    parser.add_argument('--confirm-idle', action='store_true')
    parser.add_argument('--shared-only', action='store_true',
                        help='Only after verifying sorengame/its browser are stopped')
    parser.add_argument('--legacy-role', choices=['shared', 'game'], default='game')
    args = parser.parse_args(argv)
    directory = state_directory()
    try:
        if args.operation == 'pipeline-enabled':
            c = control(directory)
            return 0 if not c.get('invalid') and c.get('pipeline_enabled') is True else 1
        if args.operation == 'prepare':
            result = prepare(directory)
        elif args.operation == 'status':
            result = status(directory)
        else:
            result = transfer(directory, 'common' if args.operation == 'activate' else 'legacy',
                              idle_confirmed=args.confirm_idle, shared_only=args.shared_only, legacy_role=args.legacy_role)
        print(json.dumps(result, sort_keys=True))
        return 0
    except NotReady as exc:
        print(json.dumps({'status': 'not_ready', 'reason': str(exc)}))
        return 3
    except Exception:
        print(json.dumps({'status': 'unavailable'}))
        return 4


if __name__ == '__main__':
    raise SystemExit(main())
