"""Lazy adapter registry: resolve game.adapter -> Adapter subclass by import path."""
from __future__ import annotations

import importlib

from .base import Adapter, AdapterContext, AdapterError, Observation
from ..config import load_game
from ..game_switch import RuntimeSpec
from ..state import State
from ..tmux import Tmux
from ..xkit import XKit

_REGISTRY = {
    "retroarch": "docich.adapters.retroarch:RetroArchAdapter",
    "cli": "docich.adapters.cli_game:CliGameAdapter",
    "browser": "docich.adapters.browser:BrowserAdapter",
}

# P2: runtime-aware adapters for the GameSwitchCoordinator (design v2 §4).
_COORDINATOR_REGISTRY = {
    "cli": "docich.adapters.cli_game:CliCoordinatorAdapter",
    "retroarch": "docich.adapters.retroarch:RetroArchCoordinatorAdapter",
    "browser": "docich.adapters.browser:BrowserCoordinatorAdapter",
}


def make_adapter(g, game, *, state=None, tmux=None, xkit=None, fence=None) -> Adapter:
    spec = _REGISTRY.get(game.adapter)
    if spec is None:
        raise AdapterError(
            f"未知のアダプタです: {game.adapter} (使用可能: {', '.join(sorted(_REGISTRY))})"
        )
    module_name, _, class_name = spec.partition(":")

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise AdapterError(
            f"アダプタ '{game.adapter}' は未実装です ({module_name} の import に失敗: {exc})"
        ) from exc

    try:
        adapter_cls = getattr(module, class_name)
    except AttributeError as exc:
        raise AdapterError(
            f"アダプタ '{game.adapter}' の実装が不正です ({module_name} に {class_name} がありません)"
        ) from exc

    if state is None:
        state = State(g)
    if tmux is None:
        tmux = Tmux()
    if xkit is None:
        xkit = XKit(g.display.name)

    ctx = AdapterContext(g=g, game=game, state=state, tmux=tmux, xkit=xkit, fence=fence)
    return adapter_cls(ctx)


def make_coordinator_adapter(g, spec: RuntimeSpec):
    """Resolve a game's runtime-aware adapter bound to ``spec``.

    The game definition is loaded from ``spec.game`` and the adapter kind is
    taken from ``[game].adapter``.  An unknown or not-yet-runtime-aware kind
    fails closed with AdapterError.
    """
    game = load_game(g, spec.game)
    spec_name = _COORDINATOR_REGISTRY.get(game.adapter)
    if spec_name is None:
        raise AdapterError(
            f"adapter '{game.adapter}' はまだ runtime-aware 化されていません "
            f"(対応: {', '.join(sorted(_COORDINATOR_REGISTRY))})"
        )
    module_name, _, class_name = spec_name.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise AdapterError(
            f"アダプタ '{game.adapter}' は未実装です ({module_name} の import に失敗: {exc})"
        ) from exc
    try:
        adapter_cls = getattr(module, class_name)
    except AttributeError as exc:
        raise AdapterError(
            f"アダプタ '{game.adapter}' の実装が不正です ({module_name} に {class_name} がありません)"
        ) from exc
    return adapter_cls(g, game, spec)


__all__ = [
    "Adapter",
    "AdapterContext",
    "AdapterError",
    "Observation",
    "make_adapter",
    "make_coordinator_adapter",
]
