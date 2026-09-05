"""Headless match runner for external command brains (token-free).

Drives any CommandBrain-protocol script (stdin Observation JSON -> stdout
actions JSON) through real matches inside a throwaway tmux session, so a
bot sees exactly what the live agent loop sees.  Nothing here touches
game-switch state, the production session, or the stream.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path


def _tmux(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def _run_bot_once(
    bot_cmd: list[str],
    cwd: str,
    obs: dict,
    env: dict | None,
    timeout_s: float,
) -> list[dict]:
    """Run the bot subprocess for one observation; return raw action dicts."""
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)
    try:
        proc = subprocess.run(
            bot_cmd,
            input=json.dumps(obs, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=cwd,
            env=proc_env,
        )
    except subprocess.TimeoutExpired:
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout[proc.stdout.index("{") :])
    except (ValueError, IndexError):
        return []
    if isinstance(data, dict) and "actions" in data:
        data = data["actions"]
    if isinstance(data, dict):
        return [data]
    return data if isinstance(data, list) else []


def _send_actions(session: str, actions: list[dict]) -> None:
    for item in actions:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "key":
            keys = item.get("keys") or []
            if keys:
                _tmux(["send-keys", "-t", session, *[str(k) for k in keys]])
        elif kind == "text":
            text = item.get("text") or ""
            if text:
                _tmux(["send-keys", "-t", session, "-l", str(text)])
        elif kind == "wait":
            try:
                time.sleep(max(int(item.get("ms", 0)), 0) / 1000)
            except (TypeError, ValueError):
                pass
        # pad/special/mouse are not drivable on a tmux text pane: ignore.


def run_bot_matches(
    *,
    label: str,
    binary: list[str],
    bot_cmd: list[str],
    cwd: str,
    cols: int,
    rows: int,
    boot_sleep_s: float = 2.0,
    start_keys: list[str] | None = None,
    retry_keys: list[str] | None = None,
    game_over_res: list[str],
    score_res: list[str],
    matches: int = 3,
    interval_s: float = 0.7,
    max_turns: int = 3000,
    bot_timeout_s: float = 10.0,
    env: dict | None = None,
) -> dict:
    """Play ``matches`` matches with a command brain; report scores."""
    over = [re.compile(p) for p in game_over_res]
    score_pats = [re.compile(p) for p in score_res]
    session = f"evalr-{os.getpid()}-{int(time.time() * 1000) % 1000000}"
    _tmux(["kill-session", "-t", session])
    created = _tmux(
        ["new-session", "-d", "-x", str(cols), "-y", str(rows), "-s", session,
         shlex.join(binary)]
    )
    if created.returncode != 0:
        raise RuntimeError(f"評価用セッションの起動に失敗しました: {created.stderr.strip()}")

    def capture() -> str:
        got = _tmux(["capture-pane", "-p", "-t", session])
        return got.stdout if got.returncode == 0 else ""

    def is_over(text: str) -> bool:
        return any(p.search(text) for p in over)

    def parse_score(text: str) -> int | None:
        best = None
        for pat in score_pats:
            for m in pat.finditer(text):
                try:
                    value = int(m.group(1).replace(",", ""))
                except (ValueError, IndexError):
                    continue
                if best is None or value > best:
                    best = value
        return best

    def send_key(key: str) -> None:
        if key in ("Enter", "Space", "Up", "Down", "Left", "Right", "Escape", "Tab", "BSpace"):
            _tmux(["send-keys", "-t", session, key])
        elif len(key) == 1:
            _tmux(["send-keys", "-t", session, "-l", key])
        else:
            _tmux(["send-keys", "-t", session, key])

    results = []
    try:
        time.sleep(boot_sleep_s)
        for key in start_keys or []:
            send_key(key)
            time.sleep(interval_s)
        for _ in range(matches):
            turns = 0
            score = None
            last_score = None
            while turns < max_turns:
                time.sleep(interval_s)
                text = capture()
                if not text and _tmux(["list-panes", "-t", session]).returncode != 0:
                    break  # session gone (process exited on its own)
                if is_over(text):
                    score = parse_score(text)
                    break
                seen = parse_score(text)
                if seen is not None:
                    last_score = seen
                actions = _run_bot_once(
                    bot_cmd, cwd, {"game": label, "text": text}, env, bot_timeout_s
                )
                if actions:
                    _send_actions(session, actions)
                turns += 1
            else:
                score = parse_score(capture())
            if score is None:
                # cutoff/transition frames may hide the status line; fall
                # back to the last score actually seen alive.
                score = last_score
            results.append({"score": score, "turns": turns, "maxed": turns >= max_turns})
            if _ >= matches - 1:
                break
            for key in retry_keys or []:
                send_key(key)
                time.sleep(interval_s * 2)
                if not is_over(capture()):
                    break
    finally:
        _tmux(["kill-session", "-t", session])
    scores = [r["score"] for r in results if isinstance(r.get("score"), int)]
    return {
        "game": label,
        "matches": results,
        "mean_score": (sum(scores) / len(scores)) if scores else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="docich.resolver.bot_eval", description="外部botのヘッドレス実戦評価 (トークン不要)"
    )
    ap.add_argument("game")
    ap.add_argument("--config", default="config/docich.toml")
    ap.add_argument("--matches", type=int, default=3)
    ap.add_argument("--interval-ms", type=int, default=700)
    ap.add_argument("--max-turns", type=int, default=3000)
    ap.add_argument("--bot-timeout-s", type=float, default=10.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]
    from ..adapters.cli_game import cli_cols, cli_command_list, cli_rows
    from ..config import load_game, load_global

    g = load_global(repo_root, Path(args.config))
    game = load_game(g, args.game)
    presets = _bot_presets()
    if args.game not in presets:
        raise SystemExit(f"bot preset がありません: {args.game} (対応: {sorted(presets)})")
    preset = presets[args.game]
    cmd = preset["command"](game)
    summary = run_bot_matches(
        label=args.game,
        binary=cmd,
        bot_cmd=preset["bot_cmd"],
        cwd=str(repo_root),
        cols=cli_cols(game),
        rows=cli_rows(game),
        matches=args.matches,
        interval_s=args.interval_ms / 1000,
        max_turns=args.max_turns,
        bot_timeout_s=args.bot_timeout_s,
        **preset["run_kwargs"],
    )
    print(json.dumps(summary, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


def _bot_presets() -> dict:
    import sys

    exe = sys.executable
    return {
        "nsnake": {
            "command": lambda game: _resolve_binary(game),
            "bot_cmd": [exe, "brains/nsnake/brain.py"],
            "run_kwargs": {
                "boot_sleep_s": 2.0,
                "start_keys": ["Enter"],
                "retry_keys": ["Enter"],
                "game_over_res": [r"Game Over"],
                "score_res": [r"Score\s+([0-9]+)"],
            },
        },
    }


def _resolve_binary(game) -> list[str]:
    from .runner import resolve_command
    from ..adapters.cli_game import cli_command_list

    return resolve_command(cli_command_list(game))


if __name__ == "__main__":
    sys.exit(main())
