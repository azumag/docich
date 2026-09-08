"""Bounded, public-safe JSONL events for paper trading narration consumers."""
from __future__ import annotations

from decimal import Decimal
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Mapping

from .ledger import RecordedMultiLegSettlement
from .models import PaperFill


class PublicEventError(ValueError):
    """Raised when a public event journal or payload is unsafe/corrupt."""


_BASE_KEYS = {"schema_version", "event_id", "event_type", "occurred_at"}
_TYPE_KEYS = {
    "paper_fill": {
        "symbol", "strategy_id", "side", "amount", "price",
        "reference_notional", "reason_code",
    },
    "multileg_settlement": {
        "route_id", "start_asset", "start_amount", "complete", "final_amount",
        "net_edge_bps", "failed_leg_symbol", "failure_reason",
    },
}


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def build_fill_event(fill: PaperFill) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": f"fill:{fill.fill_id}",
        "event_type": "paper_fill",
        "occurred_at": float(fill.filled_at),
        "symbol": fill.symbol,
        "strategy_id": fill.strategy_id,
        "side": fill.side,
        "amount": str(fill.amount),
        "price": str(fill.price),
        "reference_notional": str(fill.reference_notional),
        "reason_code": fill.reason_code,
    }


def build_settlement_event(item: RecordedMultiLegSettlement) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": f"settlement:{item.settlement_id}",
        "event_type": "multileg_settlement",
        "occurred_at": float(item.observed_at),
        "route_id": item.route_id,
        "start_asset": item.start_asset,
        "start_amount": str(item.start_amount),
        "complete": bool(item.complete),
        "final_amount": _text(item.final_amount),
        "net_edge_bps": _text(item.net_edge_bps),
        "failed_leg_symbol": item.failed_leg_symbol,
        "failure_reason": item.failure_reason,
    }


def _validated_event(event: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(event, Mapping):
        raise PublicEventError("public event must be an object")
    event_type = str(event.get("event_type") or "")
    allowed_extra = _TYPE_KEYS.get(event_type)
    if allowed_extra is None:
        raise PublicEventError("public event type is invalid")
    unknown = sorted(set(event) - (_BASE_KEYS | allowed_extra))
    if unknown:
        raise PublicEventError("public event contains unknown fields: " + ", ".join(unknown))
    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        raise PublicEventError("public event id must not be empty")
    if event.get("schema_version") != 1:
        raise PublicEventError("public event schema_version must be 1")
    try:
        occurred_at = float(event.get("occurred_at"))
    except (TypeError, ValueError) as exc:
        raise PublicEventError("public event occurred_at must be finite") from exc
    if not math.isfinite(occurred_at):
        raise PublicEventError("public event occurred_at must be finite")
    result = dict(event)
    result["event_id"] = event_id
    result["occurred_at"] = occurred_at
    return result


def _load_existing(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    records: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PublicEventError("public event journal could not be read") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublicEventError("public event journal is corrupt") from exc
        if not isinstance(raw, dict):
            raise PublicEventError("public event journal is corrupt")
        records.append(_validated_event(raw))
    return records



def read_public_events(path: Path) -> list[dict[str, object]]:
    """Read and strictly validate the bounded public trading event journal."""
    return _load_existing(Path(path))

def append_public_event(
    path: Path,
    event: Mapping[str, object],
    *,
    max_events: int = 500,
) -> bool:
    """Append one allowlisted event, deduplicating by id and bounding history."""
    if type(max_events) is not int or max_events <= 0:
        raise PublicEventError("max_events must be a positive integer")
    target = Path(path)
    record = _validated_event(event)
    existing = _load_existing(target)
    if any(str(item.get("event_id")) == record["event_id"] for item in existing):
        return False
    records = (existing + [record])[-max_events:]

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
            for item in records:
                handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
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
    return True
