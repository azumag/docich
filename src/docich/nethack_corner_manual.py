"""One-off NetHack corner runner for operator tests.

Manual runs intentionally use state/lock files separate from the scheduled
corner so a smoke test cannot consume the day's scheduled slot.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .config import ConfigError, load_global
from .nethack_corner import (
    NethackCornerError,
    NethackCornerManager,
    _repo_root,
    load_nethack_corner_config,
)
from .retro_corner import CornerResult, RetroCornerError, _safe_detail

MANUAL_STATE_FILE = "nethack_corner_manual.json"
MANUAL_LOCK_FILE = "locks/nethack-corner-manual.lock"
MANUAL_TICK_GUARD_FILE = "locks/nethack-corner-manual-tick.lock"

# A stop that cannot even reach the coordinator switch-back is recorded with one
# of these fixed tokens (never raw exception text in the raised message) so the
# operator can classify it without parsing free-form stderr.
STOP_BLOCKED_PREFIX = "stop_blocked:"
STOP_BLOCKED_RUNTIME = "runtime_unavailable"
STOP_BLOCKED_SWITCH_UNSTABLE = "switch_not_stable"
STOP_BLOCKED_SWITCH_UNREADABLE = "switch_state_unreadable"
STOP_BLOCKED_CATEGORIES = (
    STOP_BLOCKED_RUNTIME,
    STOP_BLOCKED_SWITCH_UNSTABLE,
    STOP_BLOCKED_SWITCH_UNREADABLE,
)


class ManualNethackCornerManager(NethackCornerManager):
    def __init__(self, g, *, duration_minutes: int = 5, config=None, **kwargs):
        if type(duration_minutes) is not int or not 1 <= duration_minutes <= 120:
            raise NethackCornerError("manual duration_minutes は1-120の整数である必要があります")
        base = config or load_nethack_corner_config(g)
        manual = replace(base, enabled=True, duration_minutes=duration_minutes)
        super().__init__(g, config=manual, **kwargs)
        self.state_path = Path(g.state_dir) / MANUAL_STATE_FILE
        self.lock_path = Path(g.state_dir) / MANUAL_LOCK_FILE
        self.tick_guard_path = Path(g.state_dir) / MANUAL_TICK_GUARD_FILE

    def stop(self) -> CornerResult:
        """End an active manual corner; never leave a blocked stop ``active``.

        The inherited ``stop`` runs ``_ensure_runtime`` and the canonical-game
        read *outside* the ``failed`` bookkeeping of ``_finish_locked``.  When
        either raises (runtime unavailable, or the canonical phase is not
        ``ready``/``idle`` -- e.g. a stranded ``draining``), the manual state
        stayed ``active`` and every later ``stop`` hit the same wall.  Record
        those pre-switch failures as ``failed`` so the state is terminal and
        recoverable; the switch-back itself still goes through the unchanged
        ``_finish_locked`` (save boundary, previous-game restore).
        """
        with self._locked():
            state = self._read_state()
            if state.get("status") != "active":
                return CornerResult("noop", detail="not-active")
            blocked: tuple[str, BaseException] | None = None
            try:
                self._ensure_runtime()
            except Exception as exc:
                blocked = (STOP_BLOCKED_RUNTIME, exc)
            else:
                try:
                    self._active_game_reader()
                except RetroCornerError as exc:
                    blocked = (STOP_BLOCKED_SWITCH_UNSTABLE, exc)
                except Exception as exc:
                    blocked = (STOP_BLOCKED_SWITCH_UNREADABLE, exc)
            if blocked is not None:
                self._record_blocked_stop_locked(state, *blocked)
            return self._finish_locked(state, self._local_now())

    def _record_blocked_stop_locked(
        self, state: dict[str, object], category: str, cause: BaseException
    ) -> None:
        token = f"{STOP_BLOCKED_PREFIX}{category}"
        state.update(
            status="failed",
            completed_at=self._local_now().isoformat(),
            last_error=f"{token}: {_safe_detail(cause)}",
        )
        try:
            self._write_state(state)
        except Exception:
            # The state file is the thing that is broken; the categorised error
            # below is still the most useful signal we can return.
            pass
        raise NethackCornerError(f"NetHack stop blocked [{token}]") from cause


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich nethack-corner-manual")
    parser.add_argument("--config", metavar="PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--duration-minutes", type=int, default=5)
    sub.add_parser("stop")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    sub.add_parser("recover")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config_path = Path(args.config) if args.config else None
    try:
        g = load_global(_repo_root(), config_path)
        duration = args.duration_minutes if args.command == "start" else 5
        manager = ManualNethackCornerManager(g, duration_minutes=duration)
        if args.command == "status":
            state = manager.status()
            if args.json:
                print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
            else:
                print(
                    "nethack-corner-manual: "
                    f"status={state.get('status')} game={state.get('game')} "
                    f"previous={state.get('previous_game')} ends_at={state.get('ends_at')}"
                )
            return 0
        if args.command == "recover":
            result = manager.coordinator.recover()
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "operation": result.operation,
                        "error_code": result.error_code,
                        "detail": result.detail,
                        "cleanup_pending": result.cleanup_pending,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            return 0
        result = getattr(manager, args.command)()
        print(
            json.dumps(
                {
                    "status": result.status,
                    "game": result.game,
                    "previous_game": result.previous_game,
                    "detail": result.detail,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    except (ConfigError, RetroCornerError, RuntimeError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
