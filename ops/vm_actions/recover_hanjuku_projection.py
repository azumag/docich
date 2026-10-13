#!/usr/bin/env python3
"""Fixed owner-only recovery of the active Hanjuku mirror; native game kept."""
import json
import sys
from pathlib import Path

ROOT = Path('/home/ubuntu/docich')
if len(sys.argv) != 1 or Path.cwd().resolve() != ROOT:
    raise SystemExit('fixed production root and no arguments required')
sys.path.insert(0, str(ROOT/'src'))
from docich.presentation_recovery import adopt, LinuxEffects

try:
    result = adopt(LinuxEffects(ROOT))
except Exception:
    print(json.dumps({'status': 'refused', 'game_restarted': False}))
    raise SystemExit(1) from None
print(json.dumps(result, sort_keys=True))
if result.get('status') not in {'recovered', 'already_recovered'}:
    raise SystemExit(75)
