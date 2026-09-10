"""One-off operator runner for immediate PAPER corner tests.

Manual runs use a separate state/lock from the scheduled daily corner so an
ad-hoc test never consumes the day's 22:00 slot. The normal
:class:`PaperCornerManager` switch/restore lifecycle is reused.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import ConfigError, GlobalConfig, load_global
from .paper_corner import PaperCornerError, PaperCornerManager

MANUAL_STATE_FILE = "paper_corner_manual.json"
MANUAL_LOCK_FILE = "locks/paper-corner-manual.lock"


class ManualPaperCornerManager(PaperCornerManager):
    """Run the PAPER view immediately without touching the daily slot."""

    def __init__(self, g: GlobalConfig, *, duration_minutes: int = 5, **kwargs):
        if type(duration_minutes) is not int or not 1 <= duration_minutes <= 720:
            raise PaperCornerError("duration_minutes は1-720の整数である必要があります")
        super().__init__(g, **kwargs)
        self.minutes = duration_minutes
        self.path = Path(g.state_dir) / MANUAL_STATE_FILE
        self.tick_guard_path = Path(g.state_dir) / MANUAL_LOCK_FILE


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-paper-corner-manual")
    parser.add_argument("--config", metavar="PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--duration-minutes", type=int, default=5)
    sub.add_parser("stop")
    sub.add_parser("status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = Path(args.config) if args.config else None
    try:
        g = load_global(_repo_root(), config_path)
        manager = ManualPaperCornerManager(
            g, duration_minutes=getattr(args, "duration_minutes", 5)
        )
        if args.command == "status":
            print(manager.path.read_text() if manager.path.exists() else "{}")
            return 0
        result = manager.start() if args.command == "start" else manager.stop()
        print(result)
        return 0
    except (ConfigError, PaperCornerError, ValueError) as exc:
        print(f"docich: エラー: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
