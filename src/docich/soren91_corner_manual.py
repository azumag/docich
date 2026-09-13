"""One-off operator runner for immediate Soren91 corner tests.

Manual runs intentionally use a separate state/lock from any scheduled corner
so an ad-hoc test never consumes another slot.  The normal
RetroCornerManager lifecycle is reused for transactional switch/restore
through GameSwitchCoordinator.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ConfigError, GlobalConfig, load_game, load_global
from .retro_corner import (
    CornerResult,
    RetroCornerConfig,
    RetroCornerError,
    RetroCornerManager,
    load_retro_corner_config,
)

MANUAL_STATE_FILE = "soren91_corner_manual.json"
MANUAL_LOCK_FILE = "locks/soren91-corner-manual.lock"

GAME_NAME = "soren91"


class ManualSoren91CornerManager(RetroCornerManager):
    """Run the Soren91 corner immediately without affecting other slots."""

    def __init__(
        self,
        g: GlobalConfig,
        *,
        duration_minutes: int = 5,
        coordinator=None,
        now=None,
        sleep=None,
        active_game_reader=None,
        ensure_runtime=None,
    ):
        if type(duration_minutes) is not int or not 1 <= duration_minutes <= 720:
            raise RetroCornerError("duration_minutes は1-720の整数である必要があります")

        daily = load_retro_corner_config(g)
        cfg = RetroCornerConfig(
            enabled=True,
            start_hour=daily.start_hour,
            duration_minutes=duration_minutes,
            timezone=daily.timezone,
            games=[GAME_NAME],
        )
        kwargs = {
            "config": cfg,
            "coordinator": coordinator,
            "active_game_reader": active_game_reader,
            "ensure_runtime": ensure_runtime,
        }
        if now is not None:
            kwargs["now"] = now
        if sleep is not None:
            kwargs["sleep"] = sleep
        super().__init__(g, **kwargs)
        self.state_path = Path(g.state_dir) / MANUAL_STATE_FILE
        self.lock_path = Path(g.state_dir) / MANUAL_LOCK_FILE

    def _validate_games(self) -> None:
        for name in self.config.games:
            try:
                game = load_game(self.g, name)
            except Exception as exc:
                raise RetroCornerError(f"手動soren91 corner対象を読み込めません ({name}): {exc}") from exc
            if game.adapter != "soren91":
                raise RetroCornerError(f"手動soren91 corner対象はsoren91ゲームに限定されます: {name}")

    def _announce_start_locked(self, state: dict[str, object]) -> None:
        if state.get("announced"):
            return
        game = state.get("game")
        if not isinstance(game, str) or not game:
            return
        text = "ソ連ゲーム91、メリケンAIのコーナーです。"
        try:
            self._chat(text)
        except Exception as exc:
            from .retro_corner import _safe_detail

            state["announce_error"] = _safe_detail(exc)
            return
        state["announced"] = True
        state.pop("announce_error", None)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-soren91-corner-manual")
    parser.add_argument("--config", metavar="PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--duration-minutes", type=int, default=5)
    sub.add_parser("stop")
    sub.add_parser("recover")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    return parser


def _result_json(result: CornerResult) -> str:
    return json.dumps(
        {
            "status": result.status,
            "game": result.game,
            "previous_game": result.previous_game,
            "detail": result.detail,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = Path(args.config) if args.config else None
    try:
        g = load_global(_repo_root(), config_path)
        manager = ManualSoren91CornerManager(
            g,
            duration_minutes=getattr(args, "duration_minutes", 5),
        )
        if args.command == "status":
            state = manager.status()
            if args.json:
                print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
            else:
                print(
                    f"manual-soren91-corner: status={state.get('status')} "
                    f"game={state.get('game')} previous={state.get('previous_game')} "
                    f"ends_at={state.get('ends_at')}"
                )
            return 0
        if args.command == "recover":
            # Reset a stuck coordinator (e.g. phase=failed after a rolled-back
            # test) so the next start is accepted. Same store/scope as the
            # manual run; production state is untouched when --config points
            # at an isolated rehearsal config.
            result = manager.coordinator.recover()
            print(_recover_json(result))
            return 0
        result = manager.start() if args.command == "start" else manager.stop()
        print(_result_json(result))
        return 0
    except (ConfigError, RetroCornerError) as exc:
        print(f"docich: エラー: {exc}")
        return 2


def _recover_json(result) -> str:
    return json.dumps(
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


if __name__ == "__main__":
    raise SystemExit(main())
