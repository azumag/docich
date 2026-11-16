"""Cancel one queued Hanjuku manual reservation before dispatch; never runs recovery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ConfigError, load_global
from .corner_rotation import CornerRotationManager, RotationError

# Fixed reason -> exit mapping; arbitrary exception text never leaves the VM.
EXIT = {
    'rotation_disabled': 70,
    'recovery_required': 71,
    'clock_regressed': 72,
    'manual_queue_conflict': 74,
    'state_unavailable': 75,
    'queue_rejected': 77,
    'expected_reservation_required': 90,
    'reservation_changed': 93,
    'busy': 107,
    'queued_request_missing': 110,
    'already_dispatched': 111,
    'artifact_unverified': 112,
    'evidence_unverified': 113,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('check', 'apply'))
    parser.add_argument('--expected', default=None)
    args = parser.parse_args(argv)
    try:
        root = Path(__file__).resolve().parents[2]
        g = load_global(root, root / 'config/docich.soren-live.toml')
        result = CornerRotationManager(g).cancel_queued_manual(
            'hanjuku-hero', expected=args.expected,
            apply=args.operation == 'apply')
    except RotationError as exc:
        result = {'status': 'refused',
                  'reason': getattr(exc, 'reason_code', None) or 'queue_rejected'}
    except ConfigError:
        result = {'status': 'refused', 'reason': 'state_unavailable'}
    except Exception:
        result = {'status': 'refused', 'reason': 'evidence_unverified'}
    if result.get('reason') not in EXIT:
        result = {'status': 'refused', 'reason': 'queue_rejected'}
    print(json.dumps(result, separators=(',', ':')))
    if result['status'] in {'eligible', 'cancelled'}:
        return 0
    return EXIT[result['reason']]


if __name__ == '__main__':
    raise SystemExit(main())
