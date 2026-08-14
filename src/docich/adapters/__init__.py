"""Lazy adapter registry: resolve game.adapter -> Adapter subclass by import path."""
from __future__ import annotations

import importlib

from .base import Adapter, AdapterContext, AdapterError, Observation
from ..state import State
from ..tmux import Tmux
from ..xkit import XKit

_REGISTRY = {
    "retroarch": "docich.adapters.retroarch:RetroArchAdapter",
    "cli": "docich.adapters.cli_game:CliGameAdapter",
    "browser": "docich.adapters.browser:BrowserAdapter",
}


def make_adapter(g, game, *, state=None, tmux=None, xkit=None) -> Adapter:
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

    ctx = AdapterContext(g=g, game=game, state=state, tmux=tmux, xkit=xkit)
    return adapter_cls(ctx)


__all__ = ["Adapter", "AdapterContext", "AdapterError", "Observation", "make_adapter"]
