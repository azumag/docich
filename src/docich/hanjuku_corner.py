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
    'rotation_adapter_state': 78,
    'rotation_adapter_timestamp': 79,
    'rotation_catalog_mismatch': 80,
    'rotation_execution_error': 81,
    'rotation_execution_unverified': 82,
    'rotation_invalid_state': 83,
    'rotation_unexpected': 84,
    'invalid_value': 85,
    'missing_key': 86,
    'type_mismatch': 87,
    'dependency_unavailable': 88,
    'adapter_failure': 89,
}
EXIT_FAILURE_REASON = {value: key for key, value in ROTATION_FAILURE_EXIT.items()}

ROTATION_KIND_FAILURE_REASON = {
    'adapter-state': 'rotation_adapter_state',
    'adapter-timestamp': 'rotation_adapter_timestamp',
    'catalog-mismatch': 'rotation_catalog_mismatch',
    'execution-error': 'rotation_execution_error',
    'execution-unverified': 'rotation_execution_unverified',
    'invalid-state': 'rotation_invalid_state',
    'unexpected': 'rotation_unexpected',
}

# Only exception classes defined by the reviewed corner adapter stack enter the
# adapter bucket. Do not classify every docich.* exception as an adapter failure.
ADAPTER_FAILURE_MODULES = frozenset({
    'docich.corner_adapters',
    'docich.retro_corner',
    'docich.nethack_corner',
    'docich.soren91_corner',
    'docich.paper_corner',
    'docich.paper_corner_fast',
    'docich.weather_corner',
    'docich.weather_view',
})


def _fixed_failure_exit(exc):
    """Return only a stable class; never expose exception text or runtime paths."""
    from .corner_rotation import RotationError

    if isinstance(exc, RotationError):
        reason = getattr(exc, 'reason_code', None)
        if reason is not None:
            return ROTATION_FAILURE_EXIT.get(reason, 77)
        kind_reason = ROTATION_KIND_FAILURE_REASON.get(getattr(exc, 'kind', None))
        return ROTATION_FAILURE_EXIT.get(kind_reason, 77)
    if isinstance(exc, ConfigError):
        return ROTATION_FAILURE_EXIT['config_invalid']
    if isinstance(exc, OSError):
        return ROTATION_FAILURE_EXIT['state_unavailable']

    module = type(exc).__module__ or ''
    if module in ADAPTER_FAILURE_MODULES:
        return ROTATION_FAILURE_EXIT['adapter_failure']
    if isinstance(exc, ImportError):
        return ROTATION_FAILURE_EXIT['dependency_unavailable']
    if isinstance(exc, KeyError):
        return ROTATION_FAILURE_EXIT['missing_key']
    if isinstance(exc, TypeError):
        return ROTATION_FAILURE_EXIT['type_mismatch']
    if isinstance(exc, ValueError):
        return ROTATION_FAILURE_EXIT['invalid_value']
    return ROTATION_FAILURE_EXIT['queue_rejected']


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
