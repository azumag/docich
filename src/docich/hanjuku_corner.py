"""Fixed owner-operated Hanjuku start through the common corner coordinator."""
from __future__ import annotations

import argparse
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
    'common corner rotation is disabled': 70,
    'corner recovery required before manual reservation': 71,
    'clock regressed': 72,
    'no unique eligible manual corner': 73,
    'another manual corner is already queued': 74,
    'invalid manual queue inbox': 75,
    'conflicting manual queue ownership': 74,
}


def _fixed_failure_exit(exc):
    """Return only a stable class; never expose exception text or runtime paths."""
    from .corner_rotation import RotationError

    if isinstance(exc, RotationError):
        return ROTATION_FAILURE_EXIT.get(str(exc), 77)
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
        return _fixed_failure_exit(exc)
    print(result)
    return 0 if result['status'] in {'completed', 'queued', 'waiting'} else 77


if __name__ == '__main__':
    raise SystemExit(main())
