"""Fail-closed A/B pre-flight for live projection writes (#4412).

The docich deploy/reconcile plane writes ``/home/ubuntu/soren`` directly and
never goes through the soviet_now runtime.  Before the first byte of the live
projection changes we run the runtime's own ``strategy/ab_gate_on_merge.sh
check`` from that root, so an active A/B -- or any indeterminate state -- stops
the write instead of silently overwriting the live strategy while an
experiment is running.

There is deliberately no bypass switch on this path: the external root change
is exactly what the gate is meant to catch.  The script receives a fixed,
minimal environment, so ``AB_*`` overrides in the caller cannot redirect the
state file or force a "clear" verdict.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

GATE_SCRIPT_REL = 'strategy/ab_gate_on_merge.sh'
GATE_TIMEOUT_SECONDS = 20
GATE_ENV = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8'}

CLEAR = 'clear'
ACTIVE = 'active'
UNKNOWN = 'unknown'


class GateRefused(RuntimeError):
    """The gate did not positively report 'clear'."""

    def __init__(self, outcome, reason):
        super().__init__(f'ab gate refused: {outcome} ({reason})')
        self.outcome = outcome
        self.reason = reason


def record_refusal(marker, outcome, reason, operation, log=None):
    """Write a bounded, secret-free durable marker and a workflow-log line."""
    if marker is not None:
        payload = {
            'schema_version': 1,
            'outcome': outcome,
            'reason': reason,
            'gate': GATE_SCRIPT_REL,
            'operation': operation,
            'recorded_at': int(time.time()),
        }
        try:
            marker = Path(marker)
            marker.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write('\n')
        except OSError:
            # Evidence must never turn a clean refusal into an unhandled crash;
            # the caller still refuses and the operation log has the verdict.
            pass
    if log is not None:
        line = f'ab-gate: refused outcome={outcome} reason={reason} operation={operation}\n'
        _write_log(log, line)


def _write_log(log, line):
    try:
        if hasattr(log, 'write'):
            try:
                log.write(line.encode('utf-8'))
            except TypeError:
                log.write(line)
            flush = getattr(log, 'flush', None)
            if flush is not None:
                flush()
    except (OSError, ValueError):
        pass


def _refuse(outcome, reason, marker, log, operation):
    record_refusal(marker, outcome, reason, operation, log=log)
    raise GateRefused(outcome, reason)


def _independent_verdict(root):
    """Check the A/B state directly, without trusting the gate's version.

    Older soviet_now gate scripts (df9b8b99, still shippable to the live
    projection) swallow a malformed ``ab_state.json`` and return 0, so a bare
    ``clear`` from the script cannot be trusted. Reproduce the gate's state
    signals here and fail closed on anything indeterminate. Returns
    ``(outcome, reason)`` or None when nothing is active or unreadable.
    """
    state = root / 'tmp' / 'state'
    abort = state / 'ab_abort'
    path = state / 'ab_state.json'
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = None
    except OSError:
        return (UNKNOWN, 'state_unreadable')
    if raw:
        try:
            data = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return (UNKNOWN, 'state_invalid')
        if isinstance(data, dict) and (data.get('a_hash') or data.get('b_hash')) \
                and not abort.exists():
            return (ACTIVE, 'active_ab')
    try:
        env = (root / '.env').read_bytes()[:1 << 20]
    except FileNotFoundError:
        env = b''
    except OSError:
        return (UNKNOWN, 'env_unreadable')
    if re.search(rb'^REGRESSION_DISABLED=(?:1|true|TRUE)', env, re.M):
        return (ACTIVE, 'env_gate_enabled')
    if (state / 'improve_daemon.paused').exists():
        return (ACTIVE, 'improve_paused')
    # An arm selected in the operator's environment is an active signal too;
    # never let env scrubbing of the redirect knobs hide it.
    if os.environ.get('SOREN_AB_ALT_STRATEGY'):
        return (ACTIVE, 'active_ab')
    return None


def check(projection_root, *, marker=None, log=None, operation='n/a'):
    """Return CLEAR, or raise GateRefused.

    ``projection_root`` is the live projection (production: /home/ubuntu/soren).
    Both the gate script and the state it inspects are read from that root.
    """
    root = Path(projection_root)
    if not root.is_dir():
        return _refuse(UNKNOWN, 'root_missing', marker, log, operation)
    verdict = _independent_verdict(root)
    if verdict is not None:
        return _refuse(verdict[0], verdict[1], marker, log, operation)
    script = root / GATE_SCRIPT_REL
    if not script.is_file() or not os.access(script, os.X_OK):
        return _refuse(UNKNOWN, 'gate_missing', marker, log, operation)
    try:
        completed = subprocess.run(
            [str(script), 'check'],
            cwd=str(root),
            env=dict(GATE_ENV),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=GATE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _refuse(UNKNOWN, 'execution_error', marker, log, operation)
    rc = completed.returncode
    if rc == 0:
        return CLEAR
    if rc == 1:
        return _refuse(ACTIVE, 'active_ab', marker, log, operation)
    if rc == 2:
        return _refuse(UNKNOWN, 'unknown_state', marker, log, operation)
    return _refuse(UNKNOWN, f'unexpected_rc_{rc}', marker, log, operation)
