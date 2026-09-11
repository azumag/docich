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
    "momentum_breakout": "短期モメンタムの上振れ",
    "mean_reversion_discount": "平均からの下方乖離",
    "relative_value_lag": "同一建値グループ内の相対的な出遅れ",
    "take_profit": "利確条件",
    "stop_loss": "損切り条件",
    "max_hold": "保有期限",
    "paper_lab_entry": "実験戦略の買い条件",
    "paper_lab_exit": "実験戦略の売り条件",
}
_FAILURE_TEXT = {
    "insufficient_depth": "板不足",
    "below_min_amount": "最低注文数量未満",
    "below_min_cost": "最低注文金額未満",
    "no_inventory": "保有なし",
    "take_profit": "利確",
    "stop_loss": "損切",
    "max_hold": "保有期限",
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
    return f"{amount:,.4f}".rstrip("0").rstrip(".")


def _signed_money(value) -> tuple[str, str]:
    amount = _decimal(value, "pnl")
    if amount > 0:
        return f"+{_money(amount)}", "プラス"
    if amount < 0:
        return f"-{_money(-amount)}", "マイナス"
    return _money(amount), "プラスマイナスゼロ"


def _safe_code(value: object, fallback: str = "unknown") -> str:
    text = str(value or fallback).strip()
    if not text:
        return fallback
    return "".join(ch for ch in text if ch.isalnum() or ch in "._:-/")[:80] or fallback


def _plain_number(value: object, digits: int = 2) -> str:
    number = _decimal(value, "reason")
    quantum = Decimal("1").scaleb(-digits)
    text = f"{number.quantize(quantum):f}".rstrip("0").rstrip(".")
    return text or "0"


def _pct_from_bps(value: object) -> Decimal:
    return _decimal(value, "bps") / Decimal("100")


def _duration(minutes: object) -> str:
    total = max(0, int(_decimal(minutes, "minutes")))
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours}時間{mins}分"
    if hours:
        return f"{hours}時間"
    return f"{mins}分"


def _threshold_phrase(item: Mapping[str, object]) -> str:
    feature = str(item.get("feature") or "")
    op = str(item.get("op") or "")
    threshold = item.get("threshold")
    if threshold is None:
        return ""
    op_ja = {">=": "以上", "<=": "以下", ">": "超", "<": "未満"}.get(op, op)
    if feature.endswith("_bps") or feature == "pnl_bps":
        value = _pct_from_bps(threshold)
        return f"{_plain_number(abs(value))}%{op_ja}"
    if feature == "hold_minutes":
        return f"{_duration(threshold)}{op_ja}"
    if feature == "rsi":
        return f"RSI{_plain_number(threshold)}{op_ja}"
    if feature == "zscore":
        return f"Zスコア{_plain_number(threshold)}{op_ja}"
    return f"{_plain_number(threshold)}{op_ja}"


def _condition_reason(item: Mapping[str, object], *, reason_code: str = "") -> str:
    feature = str(item.get("feature") or "")
    observed = item.get("observed")
    lookback = item.get("lookback")
    if observed is None:
        return ""
    threshold = _threshold_phrase(item)
    if feature == "pnl_bps":
        pct = _pct_from_bps(observed)
        direction = "上昇" if pct >= 0 else "下落"
        if reason_code == "take_profit":
            return f"平均取得価格から{_plain_number(abs(pct))}%{direction}、利確基準{threshold}"
        if reason_code == "stop_loss":
            return f"平均取得価格から{_plain_number(abs(pct))}%{direction}、損切り基準{_plain_number(abs(_pct_from_bps(item.get('threshold'))))}%以上"
        return f"平均取得価格から{_plain_number(abs(pct))}%{direction}、条件{threshold}"
    if feature == "hold_minutes":
        return f"保有{_duration(observed)}、最大保有{_duration(item.get('threshold'))}を超過したこと"
    if feature == "return_bps":
        pct = _pct_from_bps(observed)
        direction = "上昇" if pct >= 0 else "下落"
        return f"直近{lookback}本で{_plain_number(abs(pct))}%{direction}、基準{threshold}"
    if feature == "zscore":
        return f"直近{lookback}本のZスコア{_plain_number(observed)}、基準{threshold}"
    if feature == "rsi":
        return f"RSI{lookback}が{_plain_number(observed)}、基準{threshold}"
    if feature == "sma_gap_bps":
        pct = _pct_from_bps(observed)
        side = "上" if pct >= 0 else "下"
        return f"{lookback}本移動平均の{_plain_number(abs(pct))}%{side}、基準{threshold}"
    if feature == "volatility_bps":
        pct = _pct_from_bps(observed)
        return f"直近{lookback}本のボラティリティ{_plain_number(abs(pct))}%、基準{threshold}"
    if feature == "breakout_bps":
        pct = _pct_from_bps(observed)
        return f"直近{lookback}本高値を{_plain_number(pct)}%上抜け、基準{threshold}"
    if feature == "drawdown_bps":
        pct = _pct_from_bps(observed)
        return f"直近{lookback}本高値から{_plain_number(abs(pct))}%下落、基準{threshold}"
    return f"{feature}={_plain_number(observed)}、基準{threshold}"


def _reason_text(code: str, context: object = None) -> str:
    if isinstance(context, Mapping):
        raw = context.get("conditions")
        if isinstance(raw, list):
            parts = [
                _condition_reason(item, reason_code=code)
                for item in raw[:2] if isinstance(item, Mapping)
            ]
            parts = [part for part in parts if part]
            if parts:
                joiner = " または " if str(context.get("combine")) == "any" else " かつ "
                suffix = "など" if len(raw) > 2 else ""
                return joiner.join(parts) + suffix
    return _REASON_TEXT.get(code, f"取引条件 {code}" if code != "unknown" else "取引条件")


def _fill(
    event: Mapping[str, object], mode: str, status: Mapping[str, object] | None, *, display_at: float
) -> RenderedNotification:
    del mode, status
    symbol = _safe_code(event.get("symbol"))
    is_sell = str(event.get("side")) == "sell"
    side = "売り" if is_sell else "買い"
    strategy = _safe_code(event.get("strategy_id"))
    reason_code = _safe_code(event.get("reason_code"))
    reason = _reason_text(reason_code, event.get("reason_context"))
    title = "暗号資産 PAPER 約定"
    brief = f"{reason}を検出：{symbol}を{side}"
    body = f"{brief} / {strategy}"
    speech = brief
    if is_sell:
        if event.get("realized_pnl_reference") is None:
            body += " / 実現損益 取得失敗"
            speech += "、損益は確認できませんでした。"
        else:
            try:
                pnl_text, spoken_sign = _signed_money(event.get("realized_pnl_reference"))
                pnl_value = _decimal(event.get("realized_pnl_reference"), "pnl")
            except PresentationError:
                body += " / 実現損益 取得失敗"
                speech += "、損益は確認できませんでした。"
            else:
                body += f" / 実現損益 {pnl_text}円"
                if pnl_value == 0:
                    speech += "、損益プラスマイナスゼロです。"
                else:
                    speech += f"、損益{spoken_sign}{_money(abs(pnl_value))}円です。"
    else:
        speech += "。"
    overlay = validate_event(
        {
            "ts": int(display_at), "category": "worker", "title": title, "body": body[:500],
            "level": "info", "source_id": str(event.get("event_id") or ""),
        },
        now=int(display_at),
    )
    return RenderedNotification(overlay, speech[:1000])


def _settlement(
    event: Mapping[str, object], mode: str, status: Mapping[str, object] | None, *, display_at: float
) -> RenderedNotification:
    del mode, status
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
    overlay = validate_event(
        {
            "ts": int(display_at), "category": "worker", "title": title, "body": body[:500],
            "level": level, "source_id": str(event.get("event_id") or ""),
        },
        now=int(display_at),
    )
    return RenderedNotification(overlay, speech[:1000])


def render_notification(
    event: Mapping[str, object], *, mode: str, status: Mapping[str, object] | None = None,
    display_at: float | None = None,
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
    shown_at = occurred_at if display_at is None else float(display_at)
    if not math.isfinite(shown_at):
        raise PresentationError("notification display_at is invalid")
    if event_type == "paper_fill":
        return _fill(event, selected, status, display_at=shown_at)
    if event_type == "multileg_settlement":
        return _settlement(event, selected, status, display_at=shown_at)
    raise PresentationError("unsupported notification event type")
