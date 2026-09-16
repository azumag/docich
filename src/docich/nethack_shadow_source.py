"""Bounded producer for NetHack structured shadow snapshots (P4b).

An external source command may observe its own structured environment and emit
one P4a public-only shadow snapshot.  Docich validates it strictly before
atomically publishing ``latest.json``.  This sidecar never reads or writes the
game tmux session and never participates in policy/action selection.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from . import procs
from .config import ConfigError, GameConfig, GlobalConfig, load_game, load_global
from .game_switch import atomic_write_json
from .nethack_shadow import (
    NethackShadowController,
    ShadowPublicSnapshot,
    load_shadow_config,
    parse_shadow_snapshot,
)


CaptureStatus = Literal["snapshot", "error"]
RunStatus = Literal["disabled", "published", "error"]


@dataclass(frozen=True)
class ShadowSourceConfig:
    enabled: bool = False
    command: str | tuple[str, ...] = ""
    interval_s: float = 1.0
    timeout_s: float = 2.0
    max_response_bytes: int = 131_072


@dataclass(frozen=True)
class SourceCaptureResult:
    status: CaptureStatus
    snapshot: ShadowPublicSnapshot | None = None
    error: str | None = None


@dataclass(frozen=True)
class SourceRunResult:
    status: RunStatus
    source: str | None = None
    error: str | None = None


def load_shadow_source_config(game: GameConfig) -> ShadowSourceConfig:
    raw = game.raw if isinstance(game.raw, dict) else {}
    nethack = raw.get("nethack", {})
    section = nethack.get("shadow_source", {}) if isinstance(nethack, dict) else {}
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ValueError("[nethack.shadow_source] must be a table")

    enabled = section.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("shadow source enabled must be boolean")

    command_raw = section.get("command", "")
    if isinstance(command_raw, str):
        command: str | tuple[str, ...] = command_raw.strip()
    elif isinstance(command_raw, list) and all(isinstance(part, str) for part in command_raw):
        command = tuple(part for part in command_raw if part)
    else:
        raise ValueError("shadow source command must be a string or string array")
    if enabled and not command:
        raise ValueError("enabled shadow source requires command")

    interval_s = section.get("interval_s", 1.0)
    timeout_s = section.get("timeout_s", 2.0)
    for name, value, low, high in (
        ("interval_s", interval_s, 0.2, 60.0),
        ("timeout_s", timeout_s, 0.1, 30.0),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= float(value) <= high:
            raise ValueError(f"shadow source {name} must be between {low} and {high}")

    max_response_bytes = section.get("max_response_bytes", 131_072)
    if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1_048_576:
        raise ValueError("shadow source max_response_bytes is out of range")

    return ShadowSourceConfig(
        enabled=enabled,
        command=command,
        interval_s=float(interval_s),
        timeout_s=float(timeout_s),
        max_response_bytes=max_response_bytes,
    )


def snapshot_to_dict(snapshot: ShadowPublicSnapshot) -> dict[str, object]:
    v = snapshot.vitals
    return {
        "schema_version": 1,
        "source": snapshot.source,
        "captured_at": snapshot.captured_at,
        "public": {
            "message": snapshot.message,
            "map_rows": list(snapshot.map_rows),
            "player": list(snapshot.player) if snapshot.player is not None else None,
            "vitals": {
                "hp": v.hp,
                "hp_max": v.hp_max,
                "power": v.power,
                "power_max": v.power_max,
                "ac": v.ac,
                "experience_level": v.experience_level,
                "dungeon_level": v.dungeon_level,
                "gold": v.gold,
                "turn": v.turn,
            },
            "conditions": list(snapshot.conditions),
            "prompt": snapshot.prompt,
        },
    }


class CommandShadowSource:
    """Execute one bounded external structured-source command."""

    def __init__(
        self,
        command: str | tuple[str, ...] | list[str],
        *,
        timeout_s: float = 2.0,
        max_response_bytes: int = 131_072,
        cwd: Path | None = None,
        runner: Callable[..., object] = procs.run,
    ) -> None:
        if isinstance(command, str):
            argv = shlex.split(command)
        else:
            argv = [str(part) for part in command]
        if not argv:
            raise ValueError("shadow source command must not be empty")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0.1 <= float(timeout_s) <= 30:
            raise ValueError("shadow source timeout_s must be between 0.1 and 30")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1_048_576:
            raise ValueError("shadow source max_response_bytes is out of range")
        self.command = argv
        self.timeout_s = float(timeout_s)
        self.max_response_bytes = max_response_bytes
        self.cwd = cwd
        self.runner = runner

    @staticmethod
    def _detail(exc: BaseException) -> str:
        return str(exc).replace("\n", " ")[:240]

    def capture(self) -> SourceCaptureResult:
        request = json.dumps(
            {
                "schema_version": 1,
                "request": "public_shadow_snapshot",
                "game": "nethack",
                "constraints": [
                    "public-visible fields only",
                    "no hidden map",
                    "no true unidentified item identity",
                    "no monster internal identity/peacefulness",
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            result = self.runner(
                self.command,
                timeout=self.timeout_s,
                input=request,
                cwd=str(self.cwd) if self.cwd is not None else None,
            )
        except subprocess.TimeoutExpired:
            return SourceCaptureResult(status="error", error="shadow source timeout")
        except OSError as exc:
            return SourceCaptureResult(status="error", error=f"shadow source launch failed: {self._detail(exc)}")

        returncode = getattr(result, "returncode", None)
        stdout = getattr(result, "stdout", "")
        stderr = getattr(result, "stderr", "")
        if returncode != 0:
            detail = str(stderr or "").replace("\n", " ").strip()[:160]
            return SourceCaptureResult(
                status="error",
                error=f"shadow source exited with code {returncode}: {detail}".rstrip(": "),
            )
        if not isinstance(stdout, str):
            return SourceCaptureResult(status="error", error="shadow source stdout is not text")
        if len(stdout.encode("utf-8")) > self.max_response_bytes:
            return SourceCaptureResult(status="error", error="shadow source response exceeds size limit")
        try:
            snapshot = parse_shadow_snapshot(stdout)
        except (UnicodeError, ValueError) as exc:
            return SourceCaptureResult(status="error", error=f"invalid shadow source snapshot: {self._detail(exc)}")
        return SourceCaptureResult(status="snapshot", snapshot=snapshot)


class ShadowSourceWriter:
    """Publish only schema-valid snapshots to the P4a shadow input path."""

    def __init__(
        self,
        g: GlobalConfig,
        game: GameConfig,
        *,
        config: ShadowSourceConfig | None = None,
        source: object | None = None,
        sleep: Callable[[float], None] = time.sleep,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.g = g
        self.game = game
        self.config = config or load_shadow_source_config(game)
        self._sleep = sleep
        self._wall_time = wall_time
        shadow_controller = NethackShadowController(
            g,
            game,
            config=load_shadow_config(game),
        )
        self.output_path = shadow_controller.snapshot_path
        self.status_path = Path(getattr(g, "state_dir", ".")) / "nethack" / "shadow" / "source-status.json"

        if source is not None:
            self.source = source
        elif self.config.enabled:
            command = list(self.config.command) if isinstance(self.config.command, tuple) else self.config.command
            self.source = CommandShadowSource(
                command,
                timeout_s=self.config.timeout_s,
                max_response_bytes=self.config.max_response_bytes,
                cwd=Path(getattr(g, "repo_root", ".")),
            )
        else:
            self.source = None

    def _status(self, result: SourceRunResult) -> None:
        payload: dict[str, object] = {
            "schema_version": 1,
            "status": result.status,
            "updated_at": self._wall_time(),
        }
        if result.source:
            payload["source"] = result.source
        if result.error:
            payload["error"] = result.error
        try:
            atomic_write_json(self.status_path, payload)
        except OSError:
            pass

    def capture_once(self) -> SourceRunResult:
        if not self.config.enabled or self.source is None:
            result = SourceRunResult(status="disabled")
            self._status(result)
            return result
        try:
            capture = self.source.capture()
        except Exception as exc:
            capture = SourceCaptureResult(status="error", error=str(exc).replace("\n", " ")[:240])
        if capture.status != "snapshot" or capture.snapshot is None:
            result = SourceRunResult(status="error", error=capture.error or "shadow source failed")
            self._status(result)
            return result

        try:
            # Re-serialize from the validated dataclass instead of publishing
            # the source's raw JSON, so rejected/unknown fields can never leak.
            atomic_write_json(self.output_path, snapshot_to_dict(capture.snapshot))
        except OSError as exc:
            result = SourceRunResult(status="error", error=f"publish failed: {str(exc)[:200]}")
            self._status(result)
            return result

        result = SourceRunResult(status="published", source=capture.snapshot.source)
        self._status(result)
        return result

    def run_forever(self) -> None:
        while True:
            self.capture_once()
            self._sleep(self.config.interval_s)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-shadow-source")
    parser.add_argument("--config", metavar="PATH")
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        g = load_global(_repo_root(), Path(args.config) if args.config else None)
        game = load_game(g, "nethack")
        writer = ShadowSourceWriter(g, game)
        if args.once:
            result = writer.capture_once()
            return 0 if result.status in {"disabled", "published"} else 1
        if not writer.config.enabled:
            print("docich: NetHack shadow source は無効です", file=sys.stderr)
            return 0
        writer.run_forever()
        return 0
    except (ConfigError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
