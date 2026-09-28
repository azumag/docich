"""Fixed owner operations. No arbitrary VM commands, URLs or shell payloads."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from .twica_config import load_common_config
from .twica_state import OwnerControl, TransitionError, diagnostics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'activate', 'rollback'))
    parser.add_argument('--soren-root', type=Path, default=Path('/home/ubuntu/soren'))
    args = parser.parse_args(argv)
    try:
        config = load_common_config(args.soren_root)
        if args.action == 'status':
            result = diagnostics(config.state)
        elif not config.enabled:
            raise TransitionError('integration_not_enabled')
        else:
            controller = OwnerControl(config.state, proxy_ports=config.proxy_ports)
            result = getattr(controller, args.action)()
        print(json.dumps(result, sort_keys=True))
        return 0
    except TransitionError as error:
        print(json.dumps({'status': 'blocked', 'reason': str(error)}))
        return 2
    except (OSError, ValueError):
        print(json.dumps({'status': 'blocked', 'reason': 'configuration_or_state_invalid'}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
