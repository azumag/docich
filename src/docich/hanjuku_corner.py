"""Fixed owner-operated Hanjuku start through the common corner coordinator."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_global


def start():
    root = Path(__file__).resolve().parents[2]
    config = load_global(root, root / 'config/docich.soren-live.toml')
    from .corner_rotation import CornerRotationManager
    # Durable next-slot priority: the timer finishes the current reservation
    # and uses the normal execution/program-slot/game-boundary gates.
    return CornerRotationManager(config).queue_manual('hanjuku-hero')


ROTATION_FAILURE_EXIT = {
    'rotation_disabled': 70,
    'recovery_required': 71,
    'clock_regressed': 72,
    'hanjuku_not_eligible': 73,
    'manual_queue_conflict': 74,
    'state_unavailable': 75,
    'config_invalid': 76,
    'queue_rejected': 77,
}
EXIT_FAILURE_REASON = {value: key for key, value in ROTATION_FAILURE_EXIT.items()}


def _fixed_failure_exit(exc):
    """Return only a stable class; never expose exception text or runtime paths."""
    from .corner_rotation import RotationError

    if isinstance(exc, RotationError):
        reason = getattr(exc, 'reason_code', None)
        return ROTATION_FAILURE_EXIT.get(reason, 77)
    if isinstance(exc, OSError):
        return 75
    if isinstance(exc, ConfigError):
        return 76
    return 77


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        result = start()
    except Exception as exc:
        exit_code = _fixed_failure_exit(exc)
        # stderr is retained only in the owner-only VM operation log. Keep it
        # fixed and secret-free; never include the exception text or path.
        print(f'hanjuku_queue_failure={EXIT_FAILURE_REASON[exit_code]}', file=sys.stderr)
        return exit_code
    print(result)
    return 0 if result['status'] in {'completed', 'queued', 'waiting'} else 77


if __name__ == '__main__':
    raise SystemExit(main())
