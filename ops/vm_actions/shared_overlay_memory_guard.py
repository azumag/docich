"""Reload the shared overlay browser when its memory exceeds a limit.

docich#5112: the shared overlay renders a long-lived Playwright page. Its
renderer accumulated 14.7 GiB of RSS over 7.3 days and exhausted host swap;
the unit had no ceiling and no scheduled restart. ``reload_shared_overlay``
already restarts only that unit while proving the encoder, the audio worker and
the active game are unchanged, so the missing piece is a bounded trigger.

This guard decides *when* to reload and enforces a cooldown, so a leak that
regrows is restarted at most once per cooldown instead of in a loop. It changes
no overlay content, no unit file and no other service.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

UNIT = 'soren-shared-overlay.service'
DEFAULT_LIMIT_BYTES = 4 * 1024 ** 3
DEFAULT_COOLDOWN_SECONDS = 6 * 3600
STATE_PATH = Path('/home/ubuntu/soren/tmp/state/shared_overlay_memory_guard.json')

EXIT_OK = 0
EXIT_ERROR = 1


def parse_memory_current(text):
    """Bytes from `systemctl show -p MemoryCurrent`, or None when unmeasured.

    systemd prints `[not set]` for a stopped unit and `infinity` when the
    accounting is unavailable. Neither is a measurement, so neither may trigger
    a reload.
    """
    value = (text or '').strip()
    if not value.isdigit():
        return None
    return int(value)


def memory_current(unit=UNIT, *, run=subprocess.run):
    completed = run(['systemctl', 'show', unit, '-p', 'MemoryCurrent', '--value'],
                    capture_output=True, text=True, timeout=10, check=False)
    return parse_memory_current(getattr(completed, 'stdout', ''))


def decide(memory_bytes, *, limit=DEFAULT_LIMIT_BYTES, cooldown_remaining=0.0):
    if memory_bytes is None:
        return 'unmeasured'
    if memory_bytes < limit:
        return 'ok'
    if cooldown_remaining > 0:
        return 'cooldown'
    return 'reload'


def _load_state(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _store_state(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, separators=(',', ':')) + '\n')
    os.replace(temporary, path)


def run_guard(*, limit=DEFAULT_LIMIT_BYTES, cooldown=DEFAULT_COOLDOWN_SECONDS,
              memory=memory_current, reload=None, now=time.time, state_path=STATE_PATH):
    if reload is None:
        # Imported as a package in tests, executed as a sibling script by the
        # systemd wrapper, so accept both import roots.
        try:
            from .reload_shared_overlay import reload_shared_overlay as reload
        except ImportError:
            from reload_shared_overlay import reload_shared_overlay as reload
    used = memory()
    state = _load_state(state_path)
    last = state.get('reloaded_at')
    remaining = 0.0
    if isinstance(last, (int, float)):
        remaining = max(0.0, float(cooldown) - (float(now()) - float(last)))
    decision = decide(used, limit=limit, cooldown_remaining=remaining)
    if decision != 'reload':
        return {'decision': decision, 'memory_current': used,
                'cooldown_remaining': int(remaining)}
    receipt = reload()
    _store_state(state_path, {'reloaded_at': now(), 'memory_current': used,
                              'limit': int(limit)})
    return {'decision': 'reloaded', 'memory_current': used, 'receipt': receipt}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    limit = DEFAULT_LIMIT_BYTES
    for index, item in enumerate(argv):
        if item == '--limit-mb' and index + 1 < len(argv):
            limit = int(argv[index + 1]) * 1024 * 1024
    try:
        result = run_guard(limit=limit)
    except Exception:
        print('{"decision":"error"}')
        return EXIT_ERROR
    print(json.dumps(result, separators=(',', ':')))
    return EXIT_OK


if __name__ == '__main__':
    raise SystemExit(main())
