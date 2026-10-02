"""Production entrypoint for the eight-slot PAPER corner.

The manager lives in :mod:`docich.paper_corner`. This entrypoint prewarms its
deterministic fallback while the corner waits for the program boundary.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_global
from .paper_corner import PaperCornerManager, ensure_trading_window


class FastPaperCornerManager(PaperCornerManager):
    """Scheduled PAPER corner with an offline fallback for every slot."""

    def _prewarm_script(self, state) -> None:
        # Waiting-phase hook (see base): installing the deterministic fallback
        # is local and immediate, so the corner can speak right after the
        # switch even if the first AI segment is slow.
        if state.get('status') == 'waiting':
            self._ensure_fallback_script(state)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("command", choices=["tick", "status"])
    args = parser.parse_args(argv)

    manager = FastPaperCornerManager(
        load_global(Path(__file__).resolve().parents[2], args.config)
    )
    if args.command == "status":
        print(manager.path.read_text() if manager.path.exists() else "{}")
    else:
        print(manager.tick())
        guard = ensure_trading_window(manager.g)
        if guard in ("created", "recreated"):
            print(f"trading window {guard}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
