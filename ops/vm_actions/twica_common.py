#!/usr/bin/env python3
"""Fixed owner operation helper. Stream interruption needs its own explicit flag.

The GitHub workflow pins reviewed main and checks tracked-clean before calling.
No arbitrary argv, shell body, environment dump or upstream URL is accepted.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from docich.twica_state import (control, fresh, legacy_clients, read_json, state_directory, status)
from docich.twica_operator import NotReady, prepare, transfer
from docich.stream_identity import is_docich_runner_args

UNIT = 'docich-twica-common.service'
STREAM_UNIT = 'docich-stream.service'
SOREN_ROOT = Path('/home/ubuntu/soren')


def checked(argv, timeout=60, env=None):
    # stdout/stderr remain private even when this helper is used interactively.
    completed = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               check=False, timeout=timeout, env=env)
    if completed.returncode:
        raise NotReady('operation_failed')


def user_bus_environment():
    # Reuse the existing user's bus; never start a session or change permissions.
    runtime = os.environ.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path={runtime}/bus')


def wait_renderer_ready(timeout_sec=20):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        renderer = read_json(state_directory() / 'renderer.json')
        if fresh(renderer) and renderer.get('ready') is True:
            return
        time.sleep(0.2)
    raise NotReady('renderer_not_ready')


def install():
    if ROOT != Path.home() / 'docich':
        raise NotReady('unexpected_install_root')
    venv = ROOT / 'run/twica-env'
    if not venv.exists():
        checked(['python3', '-m', 'venv', str(venv)])
    checked([str(venv/'bin/python'), '-m', 'pip', 'install', '-r',
             str(ROOT/'requirements-twica-overlay.txt')], timeout=300)
    checked([str(venv/'bin/python'), '-m', 'playwright', 'install', 'chromium'], timeout=300)
    prepare(state_directory())
    units = Path.home() / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'deploy/twica-common' / UNIT
    target = units / UNIT
    if target.is_symlink():
        raise NotReady('foreign_unit')
    if target.exists() and target.read_bytes() != source.read_bytes():
        raise NotReady('unit_drift')
    shutil.copyfile(source, target)
    user_bus_environment()
    checked(['systemctl', '--user', 'daemon-reload'])
    checked(['systemctl', '--user', 'enable', '--now', UNIT])
    wait_renderer_ready()


def _owned_process(pid, name):
    if type(pid) is not int or pid < 2:
        return False
    proc = Path(f'/proc/{pid}')
    try:
        args = proc.joinpath('cmdline').read_bytes().split(b'\0')
        return (proc.stat().st_uid == os.getuid() and proc.joinpath('cwd').resolve() == SOREN_ROOT
                and any(Path(os.fsdecode(arg)).name == name for arg in args if arg))
    except OSError:
        return False


def _docich_runner(pid):
    """True only for the live `python3 -m docich.stream_supervisor [--root ROOT] run` of this user/tree."""
    if type(pid) is not int or pid < 2:
        return False
    proc = Path(f'/proc/{pid}')
    try:
        args = [os.fsdecode(arg) for arg in proc.joinpath('cmdline').read_bytes().split(b'\0') if arg]
        return (proc.stat().st_uid == os.getuid() and proc.joinpath('cwd').resolve() == SOREN_ROOT
                and is_docich_runner_args(args, SOREN_ROOT))
    except OSError:
        return False


def _unit_properties(unit, names, *, run=subprocess.run):
    """Read-only `systemctl show` (no sudo, no state change)."""
    try:
        done = run(['systemctl', 'show', unit, *[f'--property={name}' for name in names]],
                   capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise NotReady('stream_supervisor_unverified') from None
    if done.returncode:
        raise NotReady('stream_supervisor_unverified')
    values = dict(line.split('=', 1) for line in done.stdout.splitlines() if '=' in line)
    return {name: values.get(name) for name in names}


def verify_docich_supervision(runner_pid, *, show=_unit_properties):
    """The exact live runner must be the main process of an active, auto-restarting unit.

    Without Restart=always a stop would leave the stream off; the legacy check
    ("descendant of the start_all.sh supervisor") has the same purpose.
    """
    props = show(STREAM_UNIT, ('ActiveState', 'MainPID', 'Restart'))
    if (props.get('ActiveState') != 'active' or props.get('Restart') != 'always'
            or props.get('MainPID') != str(runner_pid)):
        raise NotReady('stream_supervisor_unverified')


def _stop_docich_stream():
    environment = dict(os.environ, PYTHONPATH=str(ROOT / 'src') + (
        os.pathsep + os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else ''))
    checked([sys.executable, '-m', 'docich.stream_supervisor', '--root', str(SOREN_ROOT), 'stop'],
            timeout=40, env=environment)


def arm_stream(confirm_restart: bool, *, shared_only: bool = False):
    directory = state_directory()
    if status(directory)['pipeline_ready']:
        return
    if not confirm_restart:
        raise NotReady('stream_restart_confirmation_required')
    policy = control(directory)
    if policy.get('invalid') or policy.get('pipeline_enabled') is not True:
        raise NotReady('prepare_required')
    # Refuse BEFORE interrupting the encoder if already-running browsers
    # have not loaded the ownership guard. Deployment alone is not readiness.
    renderer = read_json(directory / 'renderer.json')
    clients = legacy_clients(directory)
    required = {'shared'} if shared_only else {'game', 'shared'}
    if not fresh(renderer) or renderer.get('ready') is not True:
        raise NotReady('renderer_not_ready')
    roles = [c.get('role') for c in clients]
    if (not required.issubset(set(roles)) or any(not fresh(c) for c in clients)
            or any(roles.count(role) > 1 for role in set(roles))):
        raise NotReady('legacy_guards_not_ready')
    # Never stop an unsupervised process or override an intentional pause.
    for marker in ['tmp/stop', 'tmp/state/direct_stream.paused', 'tmp/state/docich_stream.paused']:
        if (SOREN_ROOT / marker).exists():
            raise NotReady('stream_intentionally_stopped')
    try:
        stream = json.loads((SOREN_ROOT/'tmp/state/direct_stream/status.json').read_text())
    except (OSError, ValueError):
        raise NotReady('stream_supervisor_unverified') from None
    if _docich_runner(stream.get('pid')):
        # docich-stream.service owns the encoder: restart through its own graceful
        # stop; `Restart=always` brings it back with the TwiCa wrapper armed.
        verify_docich_supervision(stream['pid'])
        stop_command = _stop_docich_stream
    else:
        try:
            supervisor = int((SOREN_ROOT/'tmp/state/start_all.pid').read_text().strip())
        except (OSError, ValueError):
            raise NotReady('stream_supervisor_unverified') from None
        if not _owned_process(supervisor, 'start_all.sh') or not _owned_process(stream.get('pid'), 'direct_stream.py'):
            raise NotReady('stream_supervisor_unverified')
        # Verify that the exact live runner is a descendant of this supervisor.
        ancestor = stream['pid']
        for _ in range(16):
            if ancestor == supervisor:
                break
            try:
                text = Path(f'/proc/{ancestor}/stat').read_text()
                ancestor = int(text[text.rindex(')') + 2:].split()[1])
            except (OSError, ValueError, IndexError):
                raise NotReady('stream_supervisor_unverified') from None
        else:
            raise NotReady('stream_supervisor_unverified')
        stop_command = lambda: checked(['/bin/bash', str(SOREN_ROOT/'direct_stream.sh'), 'stop'], timeout=30)
    stop_command()
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if status(directory)['pipeline_ready']:
            return
        time.sleep(0.5)
    raise NotReady('stream_input_not_ready_after_restart')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['status', 'prepare', 'arm', 'activate', 'rollback', 'enable', 'restart-renderer'])
    p.add_argument('--confirm-production', action='store_true')
    p.add_argument('--confirm-stream-restart', action='store_true')
    p.add_argument('--confirm-idle', action='store_true')
    p.add_argument('--shared-only', action='store_true')
    p.add_argument('--legacy-role', choices=['shared', 'game'], default='game')
    args = p.parse_args(argv)
    if args.operation != 'status' and not args.confirm_production:
        return 3
    try:
        user_bus_environment()
        if args.operation in {'prepare', 'enable'}:
            install()
        if args.operation in {'arm', 'enable'}:
            arm_stream(args.confirm_stream_restart, shared_only=args.shared_only)
        if args.operation in {'activate', 'rollback', 'enable'}:
            transfer(state_directory(), 'legacy' if args.operation == 'rollback' else 'common',
                     idle_confirmed=args.confirm_idle, shared_only=args.shared_only,
                     legacy_role=args.legacy_role)
        if args.operation == 'restart-renderer':
            # Does not change owner, reload Xvfb, or touch encoder/game services.
            checked(['systemctl', '--user', 'restart', UNIT])
            wait_renderer_ready()
        data = status(state_directory())
        print(json.dumps(data, sort_keys=True))
        # No optimistic green status when common is selected but not visible.
        if data['owner'] == 'common' and not (data['pipeline_ready'] and data['renderer_alive']
              and data['renderer_state'] == 'active' and data['frame_state'] == 'fresh'
              and data['legacy_subscribers'] == 0 and data['legacy_healthy']):
            return 3
        return 0
    except NotReady as exc:
        print(json.dumps({'status': 'not_ready', 'reason': str(exc)}))
        return 3
    except Exception:
        print(json.dumps({'status': 'unavailable'}))
        return 4


if __name__ == '__main__':
    raise SystemExit(main())
