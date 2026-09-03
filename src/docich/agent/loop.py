"""Observe -> brain -> act loop (architecture.md §6)."""
from __future__ import annotations

import time

from . import brains
from ..adapters import make_adapter
from ..config import GlobalConfig, load_game
from .fence import (
    AgentFence,
    FenceLost,
    active_fence,
    check_fence,
    read_canonical,
    resolve_fence,
    shared_section,
)


def _check_loop_fence(fence: AgentFence, state_dir) -> None:
    check_fence(fence, active_fence(state_dir))


def _run_iteration(adapter, brain, interval_ms: int, *, fence=None, state_dir=None) -> int:
    """Run a single observe -> decide -> act cycle, returning the action count.

    When a fence is bound, the canonical state is resolved first: a worker
    matching canonical active runs the cycle with the per-step fence checks
    (observe and each non-wait action execute under the shared game-switch
    lock); a worker matching only the not-yet-active candidate/previous awaits
    activation without observing or acting; anything else raises terminal
    FenceLost.  Brain inference and wait actions stay outside the shared lock.
    FenceLost must not be swallowed by the caller's catch-log-continue policy.
    All other exceptions raised by observe()/decide()/act() propagate to the
    caller (run_agent), which owns the catch-log-continue policy so a single bad
    cycle never kills the loop.
    """
    if fence is not None:
        if resolve_fence(fence, read_canonical(state_dir)) != "run":
            time.sleep(max(interval_ms, 0) / 1000)
            return 0

        def _observe():
            # Re-check only after the shared lock is held so a coordinator
            # transition cannot begin between the fence check and observation.
            _check_loop_fence(fence, state_dir)
            return adapter.observe()

        obs = shared_section(state_dir, _observe)
    else:
        obs = adapter.observe()

    acts = brain.decide(obs)
    if fence is not None:
        _check_loop_fence(fence, state_dir)

    for action in acts:
        if action.type == "wait":
            time.sleep(max(action.ms, 0) / 1000)
            continue

        if fence is None:
            adapter.act(action)
            continue

        def _act(single=action):
            # Same TOCTOU guard as observe: validate the lease only after
            # acquiring the shared lock and keep it held through one action.
            _check_loop_fence(fence, state_dir)
            adapter.act(single)

        shared_section(state_dir, _act)
    return len(acts)


def run_agent(
    g: GlobalConfig,
    name: str,
    *,
    runtime_id: str | None = None,
    generation: int | None = None,
    lease_id: str | None = None,
) -> None:
    game = load_game(g, name)
    fence = None
    if runtime_id is not None or generation is not None or lease_id is not None:
        if runtime_id is None or generation is None or lease_id is None:
            raise ValueError(
                "runtime_id / generation / lease_id はすべて指定してください"
            )
        fence = AgentFence(
            game=name, runtime_id=runtime_id, generation=generation, lease_id=lease_id
        )
    adapter = make_adapter(g, game, fence=fence)
    interval_ms = game.agent.interval_ms or g.agent.default_interval_ms
    brain = brains.build_brain(g, game)

    while True:
        t0 = time.monotonic()
        try:
            n = _run_iteration(adapter, brain, interval_ms, fence=fence, state_dir=g.state_dir)
            print(
                f"[agent] {game.name}: {n} 件のアクションを実行しました (brain={game.agent.brain})",
                flush=True,
            )
        except FenceLost as exc:
            # Terminal: this worker's activation is gone.  Exit with the
            # distinct code instead of re-entering the supervise restart loop.
            print(f"[agent] fence を失いました。終了します: {exc.detail}", flush=True)
            raise
        except Exception as exc:
            # observe/act/brain のいずれの例外もここで一括して握りつぶし、次のイテレーションへ進む。
            print(f"[agent] 警告: {game.name} のループでエラーが発生しました: {exc}", flush=True)

        elapsed_s = time.monotonic() - t0
        remaining_s = max(interval_ms / 1000 - elapsed_s, 0)
        time.sleep(remaining_s)
