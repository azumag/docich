"""Print the owner-only, read-only list of finished Tsuitate beta runs.

The list comes from the Worker's Durable Object storage (``beta:run:<runId>``
receipts), so it does not depend on the corner state file being current. The
output is bounded and contains only the public identity of a completed game;
moves, result and fouls still come from the public replay.

Usage (from the docich checkout)::

    PYTHONPATH=src python3 -m docich.tsuitate_records > /tmp/tsuitate-records.json

Exit codes: 0 success, 76 capability environment unusable, 77 control failure.
"""
from __future__ import annotations

import json
import sys

from .tsuitate_background_start import BackgroundStartError, install_control_environment
from .tsuitate_beta_control import ControlError, call_beta_records


def main() -> int:
    try:
        install_control_environment()
    except BackgroundStartError as exc:
        code = str(exc).split(":", 1)[0]
        print(f"tsuitate_records_failure={code}", file=sys.stderr)
        return 76
    try:
        result = call_beta_records()
    except ControlError as exc:
        print(f"tsuitate_records_failure={exc.code}", file=sys.stderr)
        return 77
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
