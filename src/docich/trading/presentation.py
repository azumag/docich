"""Persistent presentation mode and deterministic PAPER notification wording."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Mapping

from ..overlay_queue import validate_event

MODES = {"compact", "detailed"}
_REASON_TEXT = {
    "momentum_breakout": "短期モメンタムの上振れを検出",
    "mean_reversion_discount": "平均からの下方乖離を検出",
    "relative_value_lag": "同一建値グループ内の相対的な出遅れを検出",
}
_FAILURE_TEXT = {
    "insufficient_depth": "板不足",
    "below_min_amount": "最低注文数量未満",
    "below_min_cost": "最低注文金額未満",
    "market_constraints_missing": "市場制約不足",
    "market_order_disabled": "成行注文停止",
    "circuit_status_stale": "サーキット状態が古い",
    "depth_stale": "板情報が古い",
}


class PresentationError(ValueError):
    """Raised for corrupt/unsafe presentation state or event payloads."""


@dataclass(frozen=True)
class PresentationState:
    mode: str
    updated_at: float | None


@dataclass(frozen=True)
class RenderedNotification:
    overlay_event: dict[str, object]
    speech_text: str


def _validate_mode(mode: str) -> str:
    value = str(mode or "").strip().lower()
    if value not in MODES:
        raise PresentationError("presentation mode must be compact or detailed")
    return value


def read_presentation(path: Path) -> PresentationState:
    target = Path(path)
    if not target.is_file():
        return PresentationState("compact", None)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PresentationError("presentation state is corrupt") from exc
    if not isinstance(raw, dict) or set(raw) - {"schema_version", "mode", "updated_at"}:
        raise PresentationError("presentation state schema is invalid")
    if raw.get("schema_version") != 1:
        raise PresentationError("presentation state schema is invalid")
    mode = _validate_mode(raw.get("mode"))
    updated = raw.get("updated_at")
    if updated is None:
        updated_at = None
    else:
        try:
            updated_at = float(updated)
        except (TypeError, ValueError) as exc:
            raise PresentationError("presentation updated_at is invalid") from exc
        if not math.isfinite(updated_at):
            raise PresentationError("presentation updated_at is invalid")
    return PresentationState(mode, updated_at)


def write_presentation(path: Path, mode: str, *, now: float) -> PresentationState:
    target = Path(path)
    value = _validate_mode(mode)
    timestamp = float(now)
    if not math.isfinite(timestamp):
        raise PresentationError("presentation updated_at is invalid")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "mode": value, "updated_at": timestamp}, handle,
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, target)
        try: os.chmod(target, 0o600)
        except OSError: pass
    finally:
        tmp.unlink(missing_ok=True)
    return PresentationState(value, timestamp)


def _decimal(value, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PresentationError(f"{label} is invalid") from exc
    if not result.is_finite():
        raise PresentationError(f"{label} is invalid")
    return result


def _money(value) -> str:
    amount = _decimal(value, "money")
    if amount == amount.to_integral_value():
        return f"{int(amount):,}"
    text = f"{amount:,.4f}".rstrip("0").rstrip(".")
    return text


def _safe_code(value: object, fallback: str = "unknown") -> str:
    text = str(value or fallback).strip()
    if not text:
        return fallback
    return "".join(ch for ch in text if ch.isalnum() or ch in "._:-/")[:80] or fallback


def _status_context(status: Mapping[str, object] | None) -> str:
    if not isinstance(status, Mapping):
        return ""
    try:
        deployed = _money(status.get("deployed_reference"))
        capital = _money(status.get("capital_reference"))
    except PresentationError:
        return ""
    return f"現在のペーパー投入額は{deployed}円、設定ペーパー資金は{capital}円です。"


def _fill(event: Mapping[str, object], mode: str, status: Mapping[str, object] | None) -> RenderedNotification:
    symbol = _safe_code(event.get("symbol"))
    side = "買い" if str(event.get("side")) == "buy" else "売り"
    notional = _money(event.get("reference_notional"))
    strategy = _safe_code(event.get("strategy_id"))
    reason_code = _safe_code(event.get("reason_code"))
    title = "暗号資産 PAPER 約定"
    body = f"{symbol} {side} / 模擬投入 {notional}円 / {strategy}"
    speech = f"ペーパートレード速報。{symbol}を{side}。模擬投入額は約{notional}円です。"
    if mode == "detailed":
        reason = _REASON_TEXT.get(reason_code, f"理由コード {reason_code}")
        body += f" / {reason}"
        speech += f" 判断理由は、{reason}。{_status_context(status)}"
    overlay = validate_event({"ts": int(float(event.get("occurred_at"))), "category": "worker",
                              "title": title, "body": body[:500], "level": "info"})
    return RenderedNotification(overlay, speech[:1000])


def _settlement(event: Mapping[str, object], mode: str, status: Mapping[str, object] | None) -> RenderedNotification:
    route = str(event.get("route_id") or "unknown").replace("\n", " ").strip()[:220]
    start_asset = _safe_code(event.get("start_asset"))
    start_amount = _money(event.get("start_amount"))
    complete = bool(event.get("complete"))
    title = "暗号資産 PAPER 裁定観測"
    if complete:
        edge = _money(event.get("net_edge_bps"))
        body = f"{route} / {start_amount} {start_asset} / 模擬edge {edge} bps"
        speech = f"ペーパー裁定観測。{start_amount}{start_asset}の模擬経路が成立し、模擬エッジは{edge}ベーシスポイントでした。"
        level = "info"
    else:
        code = _safe_code(event.get("failure_reason"))
        reason = _FAILURE_TEXT.get(code, f"失敗理由 {code}")
        body = f"{route} / {start_amount} {start_asset} / 模擬不成立: {reason}"
        speech = f"ペーパー裁定観測。{start_amount}{start_asset}の模擬経路は成立しませんでした。理由は{reason}です。"
        level = "warn"
    if mode == "detailed":
        speech += _status_context(status)
    overlay = validate_event({"ts": int(float(event.get("occurred_at"))), "category": "worker",
                              "title": title, "body": body[:500], "level": level})
    return RenderedNotification(overlay, speech[:1000])


def render_notification(
    event: Mapping[str, object], *, mode: str, status: Mapping[str, object] | None = None
) -> RenderedNotification:
    selected = _validate_mode(mode)
    if not isinstance(event, Mapping):
        raise PresentationError("notification event must be an object")
    event_type = str(event.get("event_type") or "")
    try:
        occurred_at = float(event.get("occurred_at"))
    except (TypeError, ValueError) as exc:
        raise PresentationError("event occurred_at is invalid") from exc
    if not math.isfinite(occurred_at):
        raise PresentationError("event occurred_at is invalid")
    if event_type == "paper_fill":
        return _fill(event, selected, status)
    if event_type == "multileg_settlement":
        return _settlement(event, selected, status)
    raise PresentationError("unsupported notification event type")
