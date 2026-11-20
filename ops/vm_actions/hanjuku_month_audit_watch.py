#!/usr/bin/env python3
"""One filing pass for the Hanjuku month/boss audit on the bot host.

The production VM runs this from a systemd user timer. It reads the same
state_dir the corner runtime uses, scans the sealed audit logs and files new
incidents through the local gh CLI. It never starts a game, sends input or
restarts a runtime.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import tomllib

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "docich.soren-live.toml"
DEFAULT_REPO = "azumag/docich"


def resolve_state_dir(root: Path, config_path: Path) -> Path:
    """Resolve the configured state_dir, constrained to the checkout root."""
    root = Path(root).resolve()
    raw = Path(config_path).read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    rel = data.get("paths", {}).get("state_dir") if isinstance(data, dict) else None
    if not isinstance(rel, str) or not rel:
        raise ValueError("state_dir_missing")
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("state_dir_outside_root")
    return candidate


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hanjuku_month_audit_watch.py")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--state-dir")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.state_dir:
            state_dir = Path(args.state_dir).resolve()
        else:
            state_dir = resolve_state_dir(ROOT, Path(args.config))
        if not state_dir.is_dir():
            raise ValueError("state_dir_not_found")
        sys.path.insert(0, str(ROOT / "src"))
        from docich.hanjuku import month_audit
        results = month_audit.watch_once(
            state_dir, repo=args.repo, dry_run=args.dry_run, now=time.time())
        for result in results:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 1 if any(row.get("status") == "error" for row in results) else 0
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)},
                         ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
