#!/usr/bin/env python3
"""Fixed production entry; execution requires a separate owner confirmation.

Releases only the ``tmp/state/soren_loop.paused`` marker that a consumed,
succeeded switch owns (docich #2061).  The guard and every refusal reason live
in ``docich.soren_loop_pause_recovery``; this entry only pins the production
root and publishes the fixed result.  No arbitrary command, unit, argument or
path is accepted, and shared display/audio/stream services are never touched.
"""
import json
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/docich")
if len(sys.argv) != 1 or Path.cwd().resolve() != ROOT:
    raise SystemExit("fixed production root and no arguments required")
sys.path.insert(0, str(ROOT / "src"))

from docich.soren_loop_pause_recovery import (  # noqa: E402
    OrphanedLoopPauseRelease,
    exit_code,
    incomplete_result,
)

STATE = ROOT / "run-soren-live"
SOREN = Path("/home/ubuntu/soren")

try:
    result = OrphanedLoopPauseRelease(STATE, SOREN).run()
except (OSError, ValueError, RuntimeError):
    # Other errors may carry paths or identifiers; keep them out of the output.
    result = incomplete_result("unexpected_failure")
print(json.dumps(result, sort_keys=True))
# One fixed exit code per refusal reason, so the owner-only workflow can report
# the reason verbatim without publishing VM-private output.
raise SystemExit(exit_code(result))
