"""Headless match runner for resolver evaluation (token-free).

Plays real matches of a CLI game inside throwaway tmux sessions: tmux keeps
the curses rendering intact, so the resolver parses exactly what the live
agent loop sees.  Nothing here touches game-switch state, the production
session, or the stream.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..adapters.cli_game import cli_cols, cli_command_list, cli_rows
from ..config import load_game, load_global
from . import read_strategy, resolver_policy, strategy_path
from . import robots as _robots


def _tmux(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def resolve_command(cmd: list[str]) -> list[str]:
    """Resolve a [cli] command the same way the adapter does (/usr/games too)."""
    path = shutil.which(cmd[0])
    if path is None:
        for d in ("/usr/games", "/usr/local/games"):
            cand = Path(d) / cmd[0]
            if cand.exists():
                path = str(cand)
                break
    if path is None:
        raise RuntimeError(f"コマンドが見つかりません: {cmd[0]} (PATH を確認してください)")
    return [path, *cmd[1:]]


def run_match(
    command: list[str],
    cols: int,
    rows: int,
    decide,
    *,
    interval_s: float = 0.06,
    max_turns: int = 4000,
) -> dict:
    """Play one match to its death and report score/turns.

    ``decide(text) -> list[str]`` returns the keys to send for a pane capture.
    """
    session = f"docich-eval-{os.getpid()}-{int(time.time() * 1000) % 1000000}"
    _tmux(["kill-session", "-t", session])
    created = _tmux(
        ["new-session", "-d", "-x", str(cols), "-y", str(rows), "-s", session, shlex.join(command)]
    )
    if created.returncode != 0:
        raise RuntimeError(f"評価用セッションの起動に失敗しました: {created.stderr.strip()}")
    turns = 0
    score = None
    cause = None
    noparse = 0
    try:
        while turns < max_turns:
            time.sleep(interval_s)
            captured = _tmux(["capture-pane", "-p", "-t", session])
            if captured.returncode != 0:
                return {"score": None, "turns": turns, "error": "セッションが予期なく終了しました"}
            text = captured.stdout
            if _robots.game_over(text):
                score = _robots.score_from_text(text)
                cause = next(
                    (
                        line.strip()
                        for line in text.splitlines()
                        if "got you" in line.lower() or "quitter" in line.lower()
                    ),
                    None,
                )
                break
            keys = decide(text)
            if not keys:
                noparse += 1
                if noparse >= 60:
                    return {
                        "score": _robots.score_from_text(text),
                        "turns": turns,
                        "error": "盤面を連続して解析できません (演出フレームの可能性)",
                    }
                continue
            noparse = 0
            _tmux(["send-keys", "-t", session, "-l", "".join(keys)])
            turns += 1
        else:
            score = _robots.score_from_text(_tmux(["capture-pane", "-p", "-t", session]).stdout)
    finally:
        _tmux(["kill-session", "-t", session])
    return {"score": score, "turns": turns, "maxed": turns >= max_turns, "cause": cause}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="docich.resolver.runner", description="リゾルバのヘッドレス実戦評価 (トークン不要)"
    )
    ap.add_argument("game")
    ap.add_argument("--config", default="config/docich.toml")
    ap.add_argument("--matches", type=int, default=3)
    ap.add_argument("--interval-ms", type=int, default=60)
    ap.add_argument("--max-turns", type=int, default=4000)
    ap.add_argument("--strategy", default=None, help="戦略JSON (既定: state_dir の resolver ファイル)")
    ap.add_argument("--out", default=None, help="結果JSONの出力先")
    args = ap.parse_args(argv)
    # src/docich/resolver/runner.py -> parents[3] = repo root (cli_game.py と同じ)
    repo_root = Path(__file__).resolve().parents[3]
    g = load_global(repo_root, Path(args.config))
    game = load_game(g, args.game)
    policy = resolver_policy(args.game)
    st_file = Path(args.strategy) if args.strategy else strategy_path(g.state_dir, args.game)
    strategy = read_strategy(st_file)
    cmd = resolve_command(cli_command_list(game))
    matches = []
    for _ in range(args.matches):
        try:
            matches.append(
                run_match(
                    cmd,
                    cli_cols(game),
                    cli_rows(game),
                    lambda text: policy(text, strategy),
                    interval_s=args.interval_ms / 1000,
                    max_turns=args.max_turns,
                )
            )
        except Exception as exc:  # 一試合の失敗で全体を落とさない
            matches.append({"score": None, "turns": None, "error": str(exc)})
    scores = [m["score"] for m in matches if isinstance(m.get("score"), int)]
    summary = {
        "game": args.game,
        "strategy_file": str(st_file),
        "matches": matches,
        "mean_score": (sum(scores) / len(scores)) if scores else None,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
