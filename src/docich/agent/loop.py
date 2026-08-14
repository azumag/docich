"""Observe -> brain -> act loop (architecture.md §6)."""
from __future__ import annotations

import time

from . import brains
from ..adapters import make_adapter
from ..config import GlobalConfig, load_game


def _run_iteration(adapter, brain, interval_ms: int) -> int:
    """Run a single observe -> decide -> act cycle, returning the action count.

    Exceptions raised by observe()/decide()/act() are intentionally left to
    propagate to the caller (run_agent), which owns the catch-log-continue
    policy so a single bad cycle never kills the loop.
    """
    obs = adapter.observe()
    acts = brain.decide(obs)
    for action in acts:
        if action.type == "wait":
            time.sleep(max(action.ms, 0) / 1000)
        else:
            adapter.act(action)
    return len(acts)


def run_agent(g: GlobalConfig, name: str) -> None:
    game = load_game(g, name)
    adapter = make_adapter(g, game)
    interval_ms = game.agent.interval_ms or g.agent.default_interval_ms
    brain = brains.build_brain(g, game)

    while True:
        t0 = time.monotonic()
        try:
            n = _run_iteration(adapter, brain, interval_ms)
            print(
                f"[agent] {game.name}: {n} 件のアクションを実行しました (brain={game.agent.brain})",
                flush=True,
            )
        except Exception as exc:
            # observe/act/brain のいずれの例外もここで一括して握りつぶし、次のイテレーションへ進む。
            print(f"[agent] 警告: {game.name} のループでエラーが発生しました: {exc}", flush=True)

        elapsed_s = time.monotonic() - t0
        remaining_s = max(interval_ms / 1000 - elapsed_s, 0)
        time.sleep(remaining_s)
