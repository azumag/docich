"""One-off operator runner for immediate retro-corner tests.

Manual runs intentionally use a separate state/lock from the scheduled daily
corner so an ad-hoc test never consumes the day's 20:00 slot.  The normal
RetroCornerManager lifecycle is reused for transactional switch/restore.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ConfigError, GlobalConfig, load_game, load_global
from .naming import NameValidationError, validate_game_name
from .retro_corner import (
    CornerResult,
    RetroCornerConfig,
    RetroCornerError,
    RetroCornerManager,
    load_retro_corner_config,
)

MANUAL_STATE_FILE = "retro_corner_manual.json"
MANUAL_LOCK_FILE = "locks/retro-corner-manual.lock"


class ManualRetroCornerManager(RetroCornerManager):
    """Run one explicitly chosen CLI game without affecting the daily slot."""

    def __init__(
        self,
        g: GlobalConfig,
        *,
        game: str,
        duration_minutes: int = 5,
        coordinator=None,
        now=None,
        sleep=None,
        active_game_reader=None,
        ensure_runtime=None,
    ):
        try:
            game = validate_game_name(game)
        except NameValidationError as exc:
            raise RetroCornerError(f"手動retro cornerのゲーム名が不正です: {exc}") from exc
        if type(duration_minutes) is not int or not 1 <= duration_minutes <= 720:
            raise RetroCornerError("duration_minutes は1-720の整数である必要があります")

        daily = load_retro_corner_config(g)
        cfg = RetroCornerConfig(
            enabled=True,
            start_hour=daily.start_hour,
            duration_minutes=duration_minutes,
            timezone=daily.timezone,
            games=[game],
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
                raise RetroCornerError(f"手動retro corner対象を読み込めません ({name}): {exc}") from exc
            if game.adapter != "cli":
                raise RetroCornerError(f"手動retro corner対象はCLIゲームに限定されます: {name}")

            raw = game.raw.get("retro_corner", {})
            if raw is None:
                raw = {}
            if not isinstance(raw, dict):
                raise RetroCornerError(f"{name} の [retro_corner] はtableである必要があります")
            unattended = raw.get("unattended", False)
            if type(unattended) is not bool:
                raise RetroCornerError(f"{name} の retro_corner.unattended はtrue/falseが必要です")
            if game.agent.enabled is not True and unattended is not True:
                raise RetroCornerError(
                    f"手動retro corner対象はagent.enabled=trueまたは"
                    f"retro_corner.unattended=trueが必要です: {name}"
                )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-retro-corner-manual")
    parser.add_argument("--config", metavar="PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--game", required=True)
    start.add_argument("--duration-minutes", type=int, default=5)
    stop = sub.add_parser("stop")
    stop.add_argument("--game", required=True)
    status = sub.add_parser("status")
    status.add_argument("--game", required=True)
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
        manager = ManualRetroCornerManager(
            g,
            game=args.game,
            duration_minutes=getattr(args, "duration_minutes", 5),
        )
        if args.command == "status":
            state = manager.status()
            if args.json:
                print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
            else:
                print(
                    f"manual-retro-corner: status={state.get('status')} "
                    f"game={state.get('game')} previous={state.get('previous_game')} "
                    f"ends_at={state.get('ends_at')}"
                )
            return 0
        result = manager.start() if args.command == "start" else manager.stop()
        print(_result_json(result))
        return 0
    except (ConfigError, RetroCornerError) as exc:
        print(f"docich: エラー: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
