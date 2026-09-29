#!/usr/bin/env python3
"""Installed dispatcher: one fixed evidence operation; legacy gateway unchanged."""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    import gateway
    if len(sys.argv) != 2:
        gateway.die()
    command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if command.split()[:1] in (["hanjuku_evidence"], ["hanjuku_evidence_query"]):
        import hanjuku_evidence_gateway
        return hanjuku_evidence_gateway.main(gateway, sys.argv[1])
    # Same argv, parser, lock, command allowlist and output/error behavior.
    gateway.main()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        print("VM operation rejected", file=sys.stderr)
        raise SystemExit(1)
