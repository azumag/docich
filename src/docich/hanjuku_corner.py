"""Fixed owner-operated Hanjuku start through the common corner coordinator."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_global


def clear_clock_quarantine():
    root = Path(__file__).resolve().parents[2]
    config = load_global(root, root / 'config/docich.soren-live.toml')
    from .corner_rotation import CornerRotationManager
    return CornerRotationManager(config).clear_manual_clock_quarantine('hanjuku-hero')


def start(*, resume_latest=False):
    root = Path(__file__).resolve().parents[2]
    config = load_global(root, root / 'config/docich.soren-live.toml')
    from .corner_rotation import CornerRotationManager
    # Explicit checkpoint resume must mirror the WebUI path: before creating a
    # new manual reservation, recover an already-claimed failed-before-AI
    # transfer when the reviewed recovery proves it is still the same logical
    # checkpoint.  Skipping this gate makes a detached claim look like a generic
    # queue rejection and strands an otherwise recoverable save.
    if resume_latest:
        from .hanjuku_resume_recovery import queue_failed_start
        recovered = queue_failed_start(config)
        if recovered is not None:
            return recovered
    # Durable next-slot priority: the timer finishes the current reservation
    # and uses the normal execution/program-slot/game-boundary gates.
    manager = CornerRotationManager(config)
    return (manager.queue_manual('hanjuku-hero', resume_latest=True) if resume_latest
            else manager.queue_manual('hanjuku-hero'))


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

# Failed-before-AI checkpoint recovery already raises only fixed reason tokens.
# Give those tokens dedicated process exits so the owner-only workflow can
# report which invariant refused the retry without exposing request ids, paths,
# checkpoint bytes or arbitrary exception text. Existing rotation reasons keep
# their established codes.
RESUME_RECOVERY_FAILURE_EXIT = {
    'expected_reservation_required': 90,
    'reservation_not_in_scope': 91,
    'invalid_evidence': 92,
    'reservation_changed': 93,
    'context_changed': 94,
    'gameplay_started': 95,
    'startup_rollback_unverified': 96,
    'switch_not_stable': 97,
    'newer_progress_unverified': 98,
    'target_paused_or_unavailable': 99,
    'checkpoint_changed': 100,
    'owner_not_terminal': 101,
    'retry_exhausted': 102,
    'unsafe_state': 103,
    'missing_evidence': 104,
    'unreadable_evidence': 105,
    'unsafe_lock': 106,
    'busy': 107,
    'lock_unavailable': 108,
    'runtime_resources_present': 109,
}
CANCEL_REFUSAL_EXIT = {
    **RESUME_RECOVERY_FAILURE_EXIT,
    'clock_regressed': ROTATION_FAILURE_EXIT['clock_regressed'],
    'manual_queue_conflict': ROTATION_FAILURE_EXIT['manual_queue_conflict'],
}
EXIT_FAILURE_REASON = {
    **{value: key for key, value in ROTATION_FAILURE_EXIT.items()},
    **{value: key for key, value in RESUME_RECOVERY_FAILURE_EXIT.items()},
}

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
    from .hanjuku_manual_cancel import CancelRefused

    if isinstance(exc, RotationError):
        reason = getattr(exc, 'reason_code', None)
        if reason is not None:
            return ROTATION_FAILURE_EXIT.get(reason, 77)
        kind_reason = ROTATION_KIND_FAILURE_REASON.get(getattr(exc, 'kind', None))
        return ROTATION_FAILURE_EXIT.get(kind_reason, 77)
    if isinstance(exc, CancelRefused):
        # CancelRefused is shared by the resume-recovery verifier. Its callers
        # use fixed reason tokens, but still fail closed if an unknown token is
        # ever introduced: never print or forward arbitrary exception text.
        return CANCEL_REFUSAL_EXIT.get(str(exc), ROTATION_FAILURE_EXIT['queue_rejected'])
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
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--resume-latest', action='store_true')
    group.add_argument('--clear-clock-quarantine', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = (clear_clock_quarantine() if args.clear_clock_quarantine
                  else start(resume_latest=True) if args.resume_latest else start())
    except Exception as exc:
        exit_code = _fixed_failure_exit(exc)
        # stderr is retained only in the owner-only VM operation log. Keep it
        # fixed and secret-free; never include the exception text or path.
        print(f'hanjuku_queue_failure={EXIT_FAILURE_REASON[exit_code]}', file=sys.stderr)
        return exit_code
    print(result)
    if args.clear_clock_quarantine:
        return 0 if result['status'] == 'cleared' else 77
    return 0 if result['status'] in {'completed', 'queued', 'waiting'} else 77


if __name__ == '__main__':
    raise SystemExit(main())
