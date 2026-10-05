#!/usr/bin/env python3
"""Fixed production entry; execution requires a separate owner confirmation."""
import json
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/docich")
if len(sys.argv) != 1 or Path.cwd().resolve() != ROOT:
    raise SystemExit("fixed production root and no arguments required")
sys.path.insert(0, str(ROOT / "src"))

from docich.soren_round_recovery import OwnedRoundRecovery, RecoveryRefused, incomplete_result
from docich.soren_recovery_process import LinuxRecoveryEffects

state = ROOT / "run-soren-live"
soren = Path("/home/ubuntu/soren")
try:
    result = OwnedRoundRecovery(state, soren, LinuxRecoveryEffects(soren, state)).run()
    print(json.dumps(result, sort_keys=True))
    if result.get("status") != "completed":
        raise SystemExit(1)
except RecoveryRefused:
    # Publish only fixed diagnostics, never the exception payload.
    print(json.dumps(incomplete_result(reason="recovery_refused"), sort_keys=True))
    print("owned Soren recovery refused or incomplete; no recovery hold", file=sys.stderr)
    raise SystemExit(1) from None
except (OSError, ValueError, RuntimeError):
    # Other errors may carry paths or identifiers; keep them out of the output.
    print(json.dumps(incomplete_result(reason="unexpected_failure"), sort_keys=True))
    print("owned Soren recovery failed unexpectedly; no recovery hold", file=sys.stderr)
    raise SystemExit(1) from None
