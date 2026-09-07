"""Allowlisted public status for the paper trading subsystem."""
from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable, Mapping, Sequence

from .models import PaperFill, as_decimal


def _decimal_text(value: Decimal | str | int | float) -> str:
    return str(as_decimal(value, "status decimal"))


def _fill_payload(fill: PaperFill) -> dict[str, object]:
    return {
        "fill_id": fill.fill_id,
        "opportunity_id": fill.opportunity_id,
        "strategy_id": fill.strategy_id,
        "symbol": fill.symbol,
        "side": fill.side,
        "quote": fill.quote,
        "amount": _decimal_text(fill.amount),
        "price": _decimal_text(fill.price),
        "quote_notional": _decimal_text(fill.quote_notional),
        "reference_notional": _decimal_text(fill.reference_notional),
        "reason_code": fill.reason_code,
        "filled_at": float(fill.filled_at),
    }


def build_public_status(
    *,
    worker_state: str,
    last_cycle_at: float | None,
    eligible_symbols: Iterable[str],
    capital_reference: Decimal,
    deployed_reference: Decimal,
    open_positions: Mapping[str, Decimal],
    recent_fills: Sequence[PaperFill],
    skipped_reason_codes: Sequence[str],
) -> dict[str, object]:
    symbols = sorted({str(symbol).strip() for symbol in eligible_symbols if str(symbol).strip()})
    positions = {
        str(symbol): _decimal_text(amount)
        for symbol, amount in sorted(open_positions.items())
        if as_decimal(amount, f"position[{symbol}]") != 0
    }
    return {
        "schema_version": 1,
        "mode": "paper",
        "worker_state": str(worker_state),
        "last_cycle_at": None if last_cycle_at is None else float(last_cycle_at),
        "market_count": len(symbols),
        "eligible_symbols": symbols,
        "capital_reference": _decimal_text(capital_reference),
        "deployed_reference": _decimal_text(deployed_reference),
        "open_positions": positions,
        "recent_fills": [_fill_payload(fill) for fill in recent_fills],
        "skipped_reason_codes": [str(code) for code in skipped_reason_codes],
    }


def write_public_status(path: Path, payload: Mapping[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp_path.unlink(missing_ok=True)
        raise
