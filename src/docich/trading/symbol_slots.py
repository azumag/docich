"""Per-symbol PAPER strategy slots: layout and state-file locations only.

A *slot* is where one strategy experiment lives. The common slot is the
existing ``trading/`` directory and keeps serving every symbol that has no
slot of its own, so an installation with no dedicated slot behaves exactly as
before. A dedicated slot is ``trading/slots/<BASE>_<QUOTE>/`` and holds the
same four files (active experiment, pending candidate, control, evaluation)
for one symbol. The ledger stays shared in ``trading/``.

This module is not wired into the worker or the improvement job yet
(Issue #3623 §2.9, stage 2 PR-1). It only answers: which symbols have a slot,
where a slot's files are, which symbols an evaluation must count, and which
experiment ids are already taken.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Mapping

from .strategy_lab import (
    StrategyExperiment, _atomic_json, load_pending_experiment, load_strategy_experiment,
)

SLOTS_DIRNAME = "slots"
SLOT_META_FILENAME = "slot.json"
COMMON_SLOT = "common"
_SYMBOL = re.compile(r"[A-Z0-9]{1,16}/[A-Z0-9]{1,16}")


class SymbolSlotError(ValueError):
    """The symbol or the on-disk slot layout cannot be trusted."""


@dataclass(frozen=True)
class SlotLayout:
    """Dedicated slots found on disk. ``invalid`` entries are never usable."""
    dedicated: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.invalid

    def slot_of(self, symbol: str) -> str:
        """The slot that owns ``symbol``: the symbol itself or the common slot."""
        return symbol if symbol in self.dedicated else COMMON_SLOT


def validate_symbol(symbol: object) -> str:
    if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
        raise SymbolSlotError("unsupported slot symbol")
    return symbol


def slot_dirname(symbol: str) -> str:
    """``BTC/JPY`` -> ``BTC_JPY``; reversible because neither part has ``_``."""
    return validate_symbol(symbol).replace("/", "_")


def slot_dir(trading_dir, slot: str = COMMON_SLOT) -> Path:
    """Directory holding a slot's state files (the common slot is trading_dir)."""
    target = Path(trading_dir)
    if slot == COMMON_SLOT:
        return target
    return target / SLOTS_DIRNAME / slot_dirname(slot)


def _read_meta(directory: Path) -> str | None:
    """The symbol a slot directory declares, when it matches its own name."""
    try:
        if directory.is_symlink() or not directory.is_dir():
            return None
        data = json.loads((directory / SLOT_META_FILENAME).read_text(encoding="utf-8"))
        if not isinstance(data, Mapping) or data.get("schema_version") != 1:
            return None
        symbol = validate_symbol(data.get("symbol"))
        stamp = data.get("created_at")
        if (type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0
                or slot_dirname(symbol) != directory.name):
            return None
        return symbol
    except (OSError, ValueError):
        return None


def read_slot_layout(trading_dir) -> SlotLayout:
    """List dedicated slots. Anything unreadable is reported, never guessed.

    A caller must not treat a symbol as common-slot inventory while
    ``layout.ok`` is false: an unreadable slot directory may own it.
    """
    root = Path(trading_dir) / SLOTS_DIRNAME
    try:
        if root.is_symlink():
            return SlotLayout(invalid=(SLOTS_DIRNAME,))
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except FileNotFoundError:
        return SlotLayout()
    except OSError:
        return SlotLayout(invalid=(SLOTS_DIRNAME,))
    dedicated, invalid = [], []
    for entry in entries:
        symbol = _read_meta(entry)
        if symbol is None:
            invalid.append(entry.name)
        else:
            dedicated.append(symbol)
    return SlotLayout(tuple(sorted(dedicated)), tuple(invalid))


def create_slot(trading_dir, symbol: str, *, now: float) -> Path:
    """Create (idempotently) the dedicated slot directory for ``symbol``."""
    if isinstance(now, bool) or not math.isfinite(float(now)) or float(now) < 0:
        raise SymbolSlotError("invalid slot clock")
    directory = slot_dir(trading_dir, validate_symbol(symbol))
    if directory.exists():
        if _read_meta(directory) != symbol:
            raise SymbolSlotError("slot directory exists but is not a valid slot")
        return directory
    directory.mkdir(parents=True, mode=0o700)
    _atomic_json(directory / SLOT_META_FILENAME,
                 {"schema_version": 1, "symbol": symbol, "created_at": float(now)})
    return directory


def evaluation_scope(layout: SlotLayout, slot: str = COMMON_SLOT) -> dict[str, object]:
    """Keyword arguments restricting ``evaluate_strategy_experiment`` to a slot.

    The common slot counts every symbol without a dedicated slot; with no
    dedicated slot that is the unscoped evaluation (empty kwargs).
    """
    if not layout.ok:
        raise SymbolSlotError("slot layout is invalid")
    if slot == COMMON_SLOT:
        return {"exclude_symbols": frozenset(layout.dedicated)} if layout.dedicated else {}
    if slot not in layout.dedicated:
        raise SymbolSlotError("unknown slot")
    return {"symbols": frozenset({slot})}


def experiment_ids_in_use(trading_dir, layout: SlotLayout | None = None) -> dict[str, tuple[str, ...]]:
    """Experiment id -> slots whose active or pending experiment uses it.

    Fill ``strategy_id`` values carry only the experiment id, so one id in two
    slots would mix their evaluations. A candidate must be rejected when its
    id already maps to a different slot.
    """
    layout = read_slot_layout(trading_dir) if layout is None else layout
    if not layout.ok:
        raise SymbolSlotError("slot layout is invalid")
    found: dict[str, set[str]] = {}
    for slot in (COMMON_SLOT, *layout.dedicated):
        directory = slot_dir(trading_dir, slot)
        for spec in (load_strategy_experiment(directory), load_pending_experiment(directory)):
            if isinstance(spec, StrategyExperiment):
                found.setdefault(spec.experiment_id, set()).add(slot)
    return {key: tuple(sorted(value)) for key, value in sorted(found.items())}
