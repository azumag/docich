"""Read-only overview of dedicated per-symbol PAPER slots for display.

The dashboard and the PAPER corner narration both need to say which
strategy actually trades a symbol once symbols move into dedicated slots
(Issue #3623 §2.9, stage 2 PR-4). This module only reads the files the worker
and the improvement job already write; it never writes, never takes the
experiment lock and never raises on bad input. Every value is bounded.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping

from .experiment_control import (
    CONTROL_FILENAME,
    CONTROL_REASONS,
    CONTROL_STATUSES,
    SLOT_MIN_EXPERIMENT_AGE_S,
    SLOT_MIN_EXPERIMENT_CLOSED_SELLS,
)
from .strategy_lab import EVALUATION_FILENAME, load_strategy_experiment
from .symbol_slots import read_slot_layout, slot_dir

MAX_LISTED_SLOTS = 8
MAX_THESIS_CHARS = 160
_EVALUATION_STATUSES = frozenset({"ok", "partial", "unavailable", "invalid"})


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _enum(value: object, allowed: frozenset) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _closed_sells(directory: Path, control: Mapping[str, object]) -> int | None:
    """Closed sells of the *current* activation only.

    The evaluation and control files are written separately; an evaluation
    from another activation must not be shown as this experiment's progress.
    """
    evaluation = _read_json(directory / EVALUATION_FILENAME)
    if (evaluation.get("status") not in {"ok", "partial"}
            or not isinstance(control.get("experiment_id"), str)
            or evaluation.get("experiment_id") != control.get("experiment_id")
            or _finite(evaluation.get("activated_at")) is None
            or evaluation.get("activated_at") != control.get("activated_at")):
        return None
    return _count(evaluation.get("closed_sells"))


def _clip_sentence(text: str, limit: int = MAX_THESIS_CHARS) -> str:
    """At most ``limit`` chars, ending at a sentence break when there is one."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = head.rfind("。")
    return head[:cut + 1] if cut > 0 else head


def _slot_entry(trading_dir: Path, symbol: str, *, now: float) -> dict[str, object]:
    directory = slot_dir(trading_dir, symbol)
    control = _read_json(directory / CONTROL_FILENAME)
    experiment = load_strategy_experiment(directory)
    activated_at = _finite(control.get("activated_at"))
    if experiment is not None and experiment.experiment_id != control.get("experiment_id"):
        # Mid-activation: never pair one experiment's text with another's state.
        experiment = None
    thesis = "" if experiment is None else _clip_sentence(" ".join(experiment.thesis.split()))
    return {
        "symbol": symbol,
        "experiment_id": None if experiment is None else experiment.experiment_id,
        "name": None if experiment is None else experiment.name[:60],
        "thesis": thesis,
        "status": _enum(control.get("status"), CONTROL_STATUSES) or "unknown",
        "reason_code": _enum(control.get("reason_code"), CONTROL_REASONS),
        "entries_allowed": control.get("entries_allowed") is True,
        "evaluation_status": _enum(control.get("evaluation_status"), _EVALUATION_STATUSES),
        "open_position_count": _count(control.get("open_position_count")),
        "closed_sells": _closed_sells(directory, control),
        "age_days": (None if activated_at is None
                     else round(max(0.0, now - activated_at) / 86400, 1)),
    }


def build_slot_overview(trading_dir, *, now: float, universe: Iterable[str] = (),
                        focus_symbols: Iterable[str] = (),
                        limit: int = MAX_LISTED_SLOTS) -> dict[str, object]:
    """Counts for every dedicated slot plus a short list for display.

    ``focus_symbols`` (e.g. symbols of recent fills) are listed first; the
    rest follow by closed sells, so the slots nearest rotation are visible.
    """
    root = Path(trading_dir)
    try:
        layout = read_slot_layout(root)
    except Exception:
        return {"slot_count": 0, "layout_ok": False, "slots": [], "status_counts": {},
                "entries_allowed_count": 0, "closed_sells_total": 0,
                "common_owned_symbol_count": None,
                "rotation_closed_sells": SLOT_MIN_EXPERIMENT_CLOSED_SELLS,
                "rotation_days": SLOT_MIN_EXPERIMENT_AGE_S // 86400}
    entries = []
    for symbol in sorted(layout.dedicated):
        try:
            entries.append(_slot_entry(root, symbol, now=float(now)))
        except Exception:
            entries.append({"symbol": symbol, "experiment_id": None, "name": None,
                            "thesis": "", "status": "unknown", "reason_code": None,
                            "entries_allowed": False, "evaluation_status": None,
                            "open_position_count": None, "closed_sells": None,
                            "age_days": None})
    focus = [symbol for symbol in dict.fromkeys(focus_symbols) if symbol in layout.dedicated]
    rank = {symbol: index for index, symbol in enumerate(focus)}
    ordered = sorted(entries, key=lambda item: (
        rank.get(item["symbol"], len(rank)),
        -(item["closed_sells"] or 0),
        item["symbol"],
    ))
    universe_set = {str(symbol) for symbol in universe}
    return {
        "slot_count": len(entries),
        "layout_ok": layout.ok,
        "status_counts": dict(sorted(Counter(item["status"] for item in entries).items())),
        "entries_allowed_count": sum(1 for item in entries if item["entries_allowed"]),
        "closed_sells_total": sum(item["closed_sells"] or 0 for item in entries),
        "common_owned_symbol_count": (len(universe_set - set(layout.dedicated))
                                      if universe_set else None),
        "rotation_closed_sells": SLOT_MIN_EXPERIMENT_CLOSED_SELLS,
        "rotation_days": SLOT_MIN_EXPERIMENT_AGE_S // 86400,
        "slots": ordered[:max(0, int(limit))],
    }
