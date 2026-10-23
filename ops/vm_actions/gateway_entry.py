#!/usr/bin/env python3
"""Installed dispatcher for fixed evidence and read-only VM gateway operations."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

READ_ONLY_OPS = {"status", "diagnostics", "github_auth_diagnostics"}


def _read_only_main(gateway, config_path: str, command: str):
    """Run fixed read-only operations under a shared non-blocking VM lock.

    Writers keep the legacy gateway's blocking exclusive lock. A read arriving
    while a writer owns that lock must fail closed with a fixed public reason
    rather than occupying an Actions runner for minutes. Holding the shared
    lock for the complete read keeps the snapshot mutually exclusive with a
    deployment while allowing independent readers to coexist.
    """
    parts = command.split()
    if not parts or parts[0] not in READ_ONLY_OPS:
        return None

    # Preserve the legacy gateway's own config/missing-file rejection path.
    # The installed path always exists; this mainly keeps bootstrap/test error
    # handling identical instead of inventing a second missing-config contract.
    config = Path(config_path)
    if not config.is_file():
        return None

    cfg = None
    try:
        cfg = gateway.load_config(config)
        op, repo, target, sha = gateway.parse_command(cfg)
        if op not in READ_ONLY_OPS:
            return None
        lock = gateway.state_root(cfg) / "vm-operations.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                gateway.die("VM operation rejected: gateway_busy")
            if op == "diagnostics":
                result = gateway.diagnostics_result(cfg, repo, target, sha)
            elif op == "github_auth_diagnostics":
                result = gateway.github_auth_diagnostics(cfg, repo, target, sha)
            else:
                result = gateway.status_result(cfg, repo, target, sha)
    except ValueError as exc:
        gateway.die("VM operation rejected: %s" % gateway.reason_code(exc))
    except Exception:
        gateway._log_unexpected_traceback(cfg)
        gateway.die("VM operation rejected: operation_rejected")

    print(json.dumps(result, separators=(",", ":")))
    if result.get("exit_code", 0):
        raise SystemExit(result["exit_code"])
    return 0


def main():
    import gateway
    if len(sys.argv) != 2:
        gateway.die()
    command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if command.split()[:1] in (["hanjuku_evidence"], ["hanjuku_evidence_query"]):
        import hanjuku_evidence_gateway
        return hanjuku_evidence_gateway.main(gateway, sys.argv[1])
    read_only = _read_only_main(gateway, sys.argv[1], command)
    if read_only is not None:
        return read_only
    # Mutating operations retain the legacy parser, blocking exclusive lock,
    # allowlist, stdin handling, and output/error behavior unchanged.
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
