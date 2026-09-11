"""Process-local bridge between persisted PAPER experiments and one worker cycle."""
from __future__ import annotations

from typing import Mapping

_active_experiment = None
_reason_contexts: dict[str, dict[str, object]] = {}


def set_active_experiment(spec) -> None:
    global _active_experiment
    _active_experiment = spec
    _reason_contexts.clear()


def get_active_experiment():
    return _active_experiment


def register_reason_context(opportunity_id: str, context: Mapping[str, object] | None) -> None:
    if context:
        _reason_contexts[str(opportunity_id)] = dict(context)


def register_reason_contexts(contexts: Mapping[str, Mapping[str, object]]) -> None:
    for opportunity_id, context in contexts.items():
        register_reason_context(str(opportunity_id), context)


def pop_reason_context(opportunity_id: str) -> dict[str, object] | None:
    return _reason_contexts.pop(str(opportunity_id), None)
