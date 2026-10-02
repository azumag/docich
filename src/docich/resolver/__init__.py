"""Deterministic, token-free resolver policies for CLI games.

A resolver parses the adapter's Observation text and computes the next keys
locally: no LLM call and no subprocess per move.  Strategy weights live in a
JSON file under ``<state_dir>/resolver/`` (see strategy_path) and are
hot-reloaded by the brain, so docich.resolver.improve can promote new
parameters without restarting the agent loop.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..adapters import AdapterError

from . import robots
from .robots import DEFAULT_STRATEGY  # noqa: F401  (re-exported for convenience)

_POLICIES = {
    "robots": robots.decide,
}


def resolver_policy(game_name: str):
    """Return ``decide(text, strategy) -> list[str]`` for a known game."""
    try:
        return _POLICIES[game_name]
    except KeyError:
        raise AdapterError(
            f"resolver brain は game '{game_name}' に対応していません "
            f"(対応済み: {', '.join(sorted(_POLICIES))})"
        ) from None


def strategy_path(state_dir, game_name: str) -> Path:
    """Where a game's strategy JSON lives (hot-reloaded by the brain)."""
    return Path(state_dir) / "resolver" / f"{game_name}_strategy.json"


def strategy_history_dir(state_dir, game_name: str) -> Path:
    """Where one game's promoted strategy snapshots are archived."""
    return Path(state_dir) / "resolver" / "history" / game_name


def latest_strategy_snapshot(state_dir, game_name: str) -> dict | None:
    """Return the newest usable snapshot for this game, if one exists.

    Older releases stored every game's snapshots in the same directory with
    no game identity. Those files cannot be attributed safely and are ignored.
    """
    try:
        snapshots = sorted(
            strategy_history_dir(state_dir, game_name).glob("*.json"),
            reverse=True,
        )
    except OSError:
        return None
    for snapshot in snapshots:
        try:
            data = json.loads(snapshot.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data:
            return data
    return None


def read_strategy(path: Path) -> dict:
    """Current strategy merged over the defaults (missing file = defaults)."""
    st = dict(DEFAULT_STRATEGY)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return st
    if isinstance(data, dict):
        st.update({k: v for k, v in data.items() if k in st})
    return st
