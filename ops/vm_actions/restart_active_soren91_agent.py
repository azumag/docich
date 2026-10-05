#!/usr/bin/env python3
"""Restart only the active Soren91 gameplay agent in its owned runtime window.

This is a narrow production operation: it does not change canonical game state,
viewer/renderer ownership, the program slot, or the corner schedule. The
generation-owned Soren91 adapter performs the graceful stop and recreates the
same agent window with the currently deployed bot code.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

ROOT = Path("/home/ubuntu/docich")
CONFIG = ROOT / "config/docich.soren-live.toml"
VERIFY_SECONDS = 18.0
VERIFY_INTERVAL_SECONDS = 0.25


class RestartRefused(RuntimeError):
    pass


def restart_adapter(adapter, *, now=time.monotonic, sleep=time.sleep,
                    verify_seconds=VERIFY_SECONDS,
                    verify_interval=VERIFY_INTERVAL_SECONDS):
    """Gracefully recycle one already-bound Soren91 adapter agent."""
    initial_alive = bool(adapter.agent_alive(now() + 5.0, None))

    # Soren91GracefulCoordinatorAdapter requests the bot's own cleanup first,
    # then uses the existing bounded owned-window fallback if necessary.
    adapter.stop_agent(now() + 12.0, None)
    adapter.start_agent(now() + 20.0, None)

    deadline = now() + verify_seconds
    while now() < deadline:
        if adapter.agent_alive(min(deadline, now() + 5.0), None):
            return {
                "status": "completed",
                "previous_alive": initial_alive,
                "agent_alive": True,
            }
        sleep(min(verify_interval, max(0.0, deadline - now())))
    raise RestartRefused("agent_not_live_after_restart")


def _active_adapter():
    if Path.cwd().resolve() != ROOT:
        raise RestartRefused("fixed_production_root_required")

    sys.path.insert(0, str(ROOT / "src"))
    from docich.adapters import make_coordinator_adapter
    from docich.config import load_global
    from docich.game_switch import GameSwitchStore, RuntimeSpec

    g = load_global(ROOT, CONFIG)
    canonical, _needs_write = GameSwitchStore(g.state_dir).canonical.load()
    if canonical.get("phase") != "ready":
        return None, "canonical_not_ready"
    active = canonical.get("active")
    if not isinstance(active, dict) or active.get("game") != "soren91":
        return None, "soren91_not_active"

    spec = RuntimeSpec.from_runtime(g.state_dir, active)
    adapter = make_coordinator_adapter(g, spec)
    if not getattr(adapter, "agent_enabled", False):
        return None, "soren91_agent_disabled"
    return adapter, None


def main() -> int:
    adapter, reason = _active_adapter()
    if adapter is None:
        print(json.dumps({"status": "noop", "reason": reason}, sort_keys=True))
        return 0
    result = restart_adapter(adapter)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RestartRefused as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True))
        raise SystemExit(1) from None
    except Exception:
        # Keep runtime identifiers, paths, host details and credentials out of
        # workflow output. The operation is fail-closed and diagnostics remain
        # available through the existing private VM/evidence paths.
        print(json.dumps({"status": "failed", "reason": "unexpected_failure"}, sort_keys=True))
        raise SystemExit(1) from None
