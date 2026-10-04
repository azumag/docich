#!/usr/bin/env python3
"""Fixed production entry; execution requires a separate owner confirmation."""
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/docich")
if len(sys.argv) != 1 or Path.cwd().resolve() != ROOT:
    raise SystemExit("fixed production root and no arguments required")
sys.path.insert(0, str(ROOT / "src"))

from docich.soren_round_recovery import OwnedRoundRecovery, RecoveryRefused
from docich.soren_recovery_process import LinuxRecoveryEffects

state = ROOT / "run-soren-live"
soren = Path("/home/ubuntu/soren")
try:
    result = OwnedRoundRecovery(state, soren, LinuxRecoveryEffects(soren, state)).run()
    print("owned round interrupted; fresh game ready; rotation remains held; "
          f"common workers changed: {result.get('common_workers_changed')}")
except (RecoveryRefused, OSError, ValueError, RuntimeError) as exc:
    # No process argv, source files or live identifiers are returned publicly.
    print("owned Soren recovery refused or incomplete; retain hold", file=sys.stderr)
    raise SystemExit(1) from None
