"""CLI for the paper-only docich crypto trading foundation."""
from __future__ import annotations

import json
import math
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from ..config import GlobalConfig
from .arbitrage import ArbitrageDataError, TopOfBook, find_triangle_symbols, scan_triangular_arbitrage
from .depth import DepthRouteSimulation, simulate_route_depth
from .exchanges.bitbank_ccxt import BitbankPublicGateway, CCXTUnavailableError
from .ledger import PaperLedger
from .market_data import MarketFrame, MarketFrameError
from .models import MarketInfo, Opportunity, TradingValidationError, as_decimal
from .notifications import NotificationError, deliver_pending_notifications
from .paper import PaperBroker
from .presentation import PresentationError, read_presentation, write_presentation
from .relative_value import scan_relative_value_opportunities
from .risk import CapitalPolicy, allocate_opportunities
from .strategies import scan_opportunities, select_diversified_opportunities
from .status import build_public_status, write_public_status
from .settlement import MultiLegSettlement, settlement_observation_id, simulate_multileg_settlement


class TradingCliError(RuntimeError):
    """Stable user-facing error for trading CLI commands."""


_SNAPSHOT_KEYS = {
    "mode", "as_of", "capital_reference", "deployed_reference",
    "quote_to_reference", "available_quote", "markets", "prices", "opportunities",
}
_MARKET_KEYS = {"base", "quote", "spot", "active", "amount_step", "min_amount", "min_cost", "taker_fee_rate", "taker_fee_rate_base", "taker_fee_rate_quote", "market_order_enabled"}
_OPPORTUNITY_KEYS = {
    "opportunity_id", "strategy_id", "symbol", "side", "score", "expected_edge_bps",
    "max_notional_fraction", "expires_at", "reason_code",
}
_PUBLIC_STATUS_KEYS = {
    "schema_version", "mode", "worker_state", "last_cycle_at", "market_count",
    "eligible_symbols", "capital_reference", "deployed_reference", "open_positions",
    "recent_fills", "skipped_reason_codes", "signal_summary", "worker_summary",
}
_SIGNAL_SUMMARY_KEYS = {"candidate_count", "selected_count", "rejected_count", "strategy_ids", "candidate_reason_codes"}
_WORKER_SUMMARY_KEYS = {
    "cycle_index", "last_success_at", "next_cycle_at", "frame_error_count",
    "arbitrage_candidate_count", "new_fill_count", "new_settlement_count", "error_codes",
}
_FRAME_KEYS = {"timeframe_seconds", "timestamps", "closes", "volumes"}
_STRATEGY_SNAPSHOT_KEYS = {
    "mode", "as_of", "capital_reference", "deployed_reference",
    "quote_to_reference", "available_quote", "markets", "frames",
}

_FILL_KEYS = {
    "fill_id", "opportunity_id", "strategy_id", "symbol", "side", "quote", "amount",
    "price", "quote_notional", "reference_notional", "reason_code", "filled_at",
}


def configure_parser(parser) -> None:
    parser.add_argument(
        "--state-dir", metavar="PATH",
        help="paper trading state directory (default: run/trading)",
    )
    sub = parser.add_subparsers(dest="trading_command", required=True)
    sub.add_parser("status", help="paper trading public status を表示する")
    sub.add_parser("discover", help="bitbank の公開 market metadata を列挙する")
    history = sub.add_parser("history", help="bitbank の公開 OHLCV を正規化して出力する")
    history.add_argument("--symbols", metavar="CSV", help="対象symbolのカンマ区切り (省略時は全eligible market)")
    history.add_argument("--timeframe", default="5m", metavar="TF", help="CCXT timeframe (既定5m)")
    history.add_argument("--limit", type=int, default=24, metavar="N", help="取得bar数 (既定24)")
    paper = sub.add_parser("paper-cycle", help="安全な snapshot から paper cycle を1回実行する")
    paper.add_argument("--snapshot", required=True, metavar="JSON", help="paper snapshot JSON")
    strategy = sub.add_parser("strategy-cycle", help="履歴snapshotから戦略生成→分散→paper cycleを実行する")
    strategy.add_argument("--snapshot", required=True, metavar="JSON", help="strategy snapshot JSON")
    arbitrage = sub.add_parser("arbitrage-scan", help="公開板からfee-aware三角裁定候補を診断する")
    arbitrage.add_argument("--min-edge-bps", default="10", metavar="BPS", help="最低net edge (既定10bps)")
    arbitrage.add_argument("--book-limit", type=int, default=5, metavar="N", help="板取得depth (既定5)")
    depth = sub.add_parser("arbitrage-depth-scan", help="公開板depthで三角裁定候補のサイズ別slippageを診断する")
    depth.add_argument("--min-edge-bps", default="10", metavar="BPS", help="top-of-book最低net edge (既定10bps)")
    depth.add_argument("--book-limit", type=int, default=20, metavar="N", help="板取得depth (既定20)")
    depth.add_argument("--probe-asset", default="JPY", metavar="ASSET", help="probe開始資産 (既定JPY)")
    depth.add_argument("--probe-amounts", default="1000,3000,10000", metavar="CSV", help="開始資産単位のprobe量")
    depth.add_argument("--record", action="store_true", help="constraint-aware settlementをpaper台帳へ保存する")
    settlement_history = sub.add_parser("settlement-history", help="保存済みmulti-leg paper settlementを表示する")
    settlement_history.add_argument("--limit", type=int, default=20, metavar="N", help="表示件数 (既定20)")
    presentation = sub.add_parser("presentation", help="取引通知のcompact/detailed表示モードを操作する")
    presentation.add_argument("presentation_action", choices=("status", "compact", "detailed"))
    sub.add_parser("notify-once", help="保存済みpaper eventの未配送通知だけを1回処理する")


def _state_dir(
    args, repo_root: Path, global_config: GlobalConfig | None = None
) -> Path:
    raw = getattr(args, "state_dir", None)
    if raw:
        return Path(raw).expanduser()
    if global_config is not None:
        return global_config.state_dir / "trading"
    return repo_root / "run" / "trading"


def _json_print(payload: Mapping[str, Any]) -> None:
    print(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _unknown_keys(data: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise TradingCliError(f"{label} has unknown fields: {', '.join(unknown)}")


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TradingCliError(f"snapshot/status JSON could not be read: {path}") from exc
    if not isinstance(data, dict):
        raise TradingCliError("snapshot/status JSON must be an object")
    return data


def _safe_existing_status(path: Path) -> dict[str, Any]:
    data = _load_json_object(path)
    if data.get("mode") != "paper":
        raise TradingCliError("trading status must be paper mode")
    safe = {key: data.get(key) for key in _PUBLIC_STATUS_KEYS}
    fills = data.get("recent_fills", [])
    if not isinstance(fills, list):
        raise TradingCliError("trading status recent_fills must be a list")
    safe["recent_fills"] = [
        {key: fill.get(key) for key in _FILL_KEYS}
        for fill in fills if isinstance(fill, dict)
    ]
    summary = data.get("signal_summary", {})
    safe["signal_summary"] = (
        {key: summary.get(key) for key in _SIGNAL_SUMMARY_KEYS}
        if isinstance(summary, dict) else {}
    )
    worker_summary = data.get("worker_summary", {})
    safe["worker_summary"] = (
        {key: worker_summary[key] for key in _WORKER_SUMMARY_KEYS if key in worker_summary}
        if isinstance(worker_summary, dict) else {}
    )
    return safe


def _absent_status() -> dict[str, Any]:
    return build_public_status(
        worker_state="absent",
        last_cycle_at=None,
        eligible_symbols=[],
        capital_reference=Decimal("0"),
        deployed_reference=Decimal("0"),
        open_positions={},
        recent_fills=[],
        skipped_reason_codes=[],
    )


def _market_from_snapshot(symbol: str, raw: Any) -> MarketInfo:
    if not isinstance(raw, dict):
        raise TradingCliError(f"market {symbol} must be an object")
    _unknown_keys(raw, _MARKET_KEYS, f"market {symbol}")
    try:
        return MarketInfo(
            symbol=symbol,
            base=raw.get("base", ""),
            quote=raw.get("quote", ""),
            spot=raw.get("spot") is True,
            active=raw.get("active") is True,
            amount_step=None if raw.get("amount_step") is None else as_decimal(raw["amount_step"], "amount_step"),
            min_amount=None if raw.get("min_amount") is None else as_decimal(raw["min_amount"], "min_amount"),
            min_cost=None if raw.get("min_cost") is None else as_decimal(raw["min_cost"], "min_cost"),
            taker_fee_rate=None if raw.get("taker_fee_rate") is None else as_decimal(raw["taker_fee_rate"], "taker_fee_rate"),
            taker_fee_rate_base=None if raw.get("taker_fee_rate_base") is None else as_decimal(raw["taker_fee_rate_base"], "taker_fee_rate_base"),
            taker_fee_rate_quote=None if raw.get("taker_fee_rate_quote") is None else as_decimal(raw["taker_fee_rate_quote"], "taker_fee_rate_quote"),
            market_order_enabled=raw.get("market_order_enabled", True) is True,
        )
    except (TradingValidationError, KeyError) as exc:
        raise TradingCliError(f"market {symbol} is invalid") from exc


def _opportunity_from_snapshot(raw: Any) -> Opportunity:
    if not isinstance(raw, dict):
        raise TradingCliError("opportunity must be an object")
    _unknown_keys(raw, _OPPORTUNITY_KEYS, "opportunity")
    try:
        return Opportunity(
            opportunity_id=raw.get("opportunity_id", ""),
            strategy_id=raw.get("strategy_id", ""),
            symbol=raw.get("symbol", ""),
            side=raw.get("side", ""),
            score=as_decimal(raw.get("score"), "score"),
            expected_edge_bps=as_decimal(raw.get("expected_edge_bps"), "expected_edge_bps"),
            max_notional_fraction=as_decimal(raw.get("max_notional_fraction"), "max_notional_fraction"),
            expires_at=float(raw.get("expires_at")),
            reason_code=raw.get("reason_code", ""),
        )
    except (TradingValidationError, TypeError, ValueError) as exc:
        raise TradingCliError("opportunity is invalid") from exc


def _decimal_mapping(raw: Any, label: str) -> dict[str, Decimal]:
    if not isinstance(raw, dict):
        raise TradingCliError(f"{label} must be an object")
    try:
        return {str(key): as_decimal(value, f"{label}[{key}]") for key, value in raw.items()}
    except TradingValidationError as exc:
        raise TradingCliError(f"{label} contains an invalid value") from exc


def _load_snapshot(path: Path) -> dict[str, Any]:
    data = _load_json_object(path)
    _unknown_keys(data, _SNAPSHOT_KEYS, "snapshot")
    if data.get("mode") != "paper":
        raise TradingCliError("only paper mode is supported; live trading is unavailable")
    missing = sorted(_SNAPSHOT_KEYS - set(data))
    if missing:
        raise TradingCliError(f"snapshot is missing fields: {', '.join(missing)}")
    try:
        as_of = float(data["as_of"])
    except (TypeError, ValueError) as exc:
        raise TradingCliError("snapshot as_of must be finite") from exc
    if not math.isfinite(as_of):
        raise TradingCliError("snapshot as_of must be finite")
    markets_raw = data["markets"]
    opportunities_raw = data["opportunities"]
    if not isinstance(markets_raw, dict) or not isinstance(opportunities_raw, list):
        raise TradingCliError("snapshot markets/opportunities have invalid types")
    try:
        capital_reference = as_decimal(data["capital_reference"], "capital_reference")
        deployed_reference = as_decimal(data["deployed_reference"], "deployed_reference")
    except TradingValidationError as exc:
        raise TradingCliError("snapshot contains an invalid numeric value") from exc
    if capital_reference < 0 or deployed_reference < 0:
        raise TradingCliError("snapshot capital values must be non-negative")

    quote_to_reference = _decimal_mapping(data["quote_to_reference"], "quote_to_reference")
    if any(value <= 0 for value in quote_to_reference.values()):
        raise TradingCliError("snapshot quote_to_reference values must be positive")
    available_quote = _decimal_mapping(data["available_quote"], "available_quote")
    if any(value < 0 for value in available_quote.values()):
        raise TradingCliError("snapshot available_quote values must be non-negative")
    prices = _decimal_mapping(data["prices"], "prices")
    if any(value <= 0 for value in prices.values()):
        raise TradingCliError("snapshot prices must be positive")
    opportunities = [_opportunity_from_snapshot(raw) for raw in opportunities_raw]
    opportunity_ids = [item.opportunity_id for item in opportunities]
    if len(opportunity_ids) != len(set(opportunity_ids)):
        raise TradingCliError("snapshot contains duplicate opportunity_id values")

    return {
        "as_of": as_of,
        "capital_reference": capital_reference,
        "deployed_reference": deployed_reference,
        "quote_to_reference": quote_to_reference,
        "available_quote": available_quote,
        "markets": {symbol: _market_from_snapshot(symbol, raw) for symbol, raw in markets_raw.items()},
        "prices": prices,
        "opportunities": opportunities,
    }


def _frame_from_snapshot(symbol: str, raw: Any, *, as_of: float) -> MarketFrame:
    if not isinstance(raw, dict):
        raise TradingCliError(f"frame {symbol} must be an object")
    _unknown_keys(raw, _FRAME_KEYS, f"frame {symbol}")
    missing = sorted(_FRAME_KEYS - set(raw))
    if missing:
        raise TradingCliError(f"frame {symbol} is missing fields: {', '.join(missing)}")
    timestamps, closes, volumes = raw["timestamps"], raw["closes"], raw["volumes"]
    if not isinstance(timestamps, list) or not isinstance(closes, list) or not isinstance(volumes, list):
        raise TradingCliError(f"frame {symbol} arrays have invalid types")
    if len(timestamps) < 3 or len(timestamps) != len(closes) or len(closes) != len(volumes):
        raise TradingCliError(f"frame {symbol} arrays have invalid lengths")
    try:
        tf = int(raw["timeframe_seconds"])
        ts = tuple(float(value) for value in timestamps)
        close_values = tuple(as_decimal(value, f"frame[{symbol}].close") for value in closes)
        volume_values = tuple(as_decimal(value, f"frame[{symbol}].volume") for value in volumes)
    except (TypeError, ValueError, TradingValidationError) as exc:
        raise TradingCliError(f"frame {symbol} contains invalid values") from exc
    if tf <= 0 or any(not math.isfinite(value) for value in ts):
        raise TradingCliError(f"frame {symbol} contains invalid timing")
    if any(current <= previous for previous, current in zip(ts, ts[1:])):
        raise TradingCliError(f"frame {symbol} timestamps are non-monotonic")
    if any(value <= 0 for value in close_values) or any(value < 0 for value in volume_values):
        raise TradingCliError(f"frame {symbol} contains invalid price/volume")
    if ts[-1] > as_of + 1 or as_of - ts[-1] > tf * 2:
        raise TradingCliError(f"frame {symbol} is stale or from the future")
    return MarketFrame(symbol, tf, ts, close_values, volume_values)


def _load_strategy_snapshot(path: Path) -> dict[str, Any]:
    data = _load_json_object(path)
    _unknown_keys(data, _STRATEGY_SNAPSHOT_KEYS, "strategy snapshot")
    if data.get("mode") != "paper":
        raise TradingCliError("only paper mode is supported; live trading is unavailable")
    missing = sorted(_STRATEGY_SNAPSHOT_KEYS - set(data))
    if missing:
        raise TradingCliError(f"strategy snapshot is missing fields: {', '.join(missing)}")
    try:
        as_of = float(data["as_of"])
        capital = as_decimal(data["capital_reference"], "capital_reference")
        deployed = as_decimal(data["deployed_reference"], "deployed_reference")
    except (TypeError, ValueError, TradingValidationError) as exc:
        raise TradingCliError("strategy snapshot contains invalid numeric values") from exc
    if not math.isfinite(as_of) or capital < 0 or deployed < 0:
        raise TradingCliError("strategy snapshot timing/capital is invalid")
    markets_raw, frames_raw = data["markets"], data["frames"]
    if not isinstance(markets_raw, dict) or not isinstance(frames_raw, dict):
        raise TradingCliError("strategy snapshot markets/frames must be objects")
    markets = {symbol: _market_from_snapshot(symbol, raw) for symbol, raw in markets_raw.items()}
    frames = {symbol: _frame_from_snapshot(symbol, raw, as_of=as_of) for symbol, raw in frames_raw.items()}
    if set(frames) - set(markets):
        raise TradingCliError("strategy snapshot contains frames for unknown markets")
    quote_rates = _decimal_mapping(data["quote_to_reference"], "quote_to_reference")
    available = _decimal_mapping(data["available_quote"], "available_quote")
    if any(value <= 0 for value in quote_rates.values()) or any(value < 0 for value in available.values()):
        raise TradingCliError("strategy snapshot quote values are invalid")
    return {
        "as_of": as_of, "capital_reference": capital, "deployed_reference": deployed,
        "quote_to_reference": quote_rates, "available_quote": available,
        "markets": markets, "frames": frames,
    }


def _frame_payload(frame: MarketFrame) -> dict[str, Any]:
    return {
        "timeframe_seconds": frame.timeframe_seconds,
        "timestamps": [float(value) for value in frame.timestamps],
        "closes": [str(value) for value in frame.closes],
        "volumes": [str(value) for value in frame.volumes],
    }



def _parse_probe_amounts(raw: str) -> tuple[Decimal, ...]:
    parts = [part.strip() for part in str(raw).split(",") if part.strip()]
    if not parts:
        raise TradingCliError("probe amounts must not be empty")
    try:
        amounts = tuple(as_decimal(part, "probe amount") for part in parts)
    except TradingValidationError as exc:
        raise TradingCliError("probe amounts contain an invalid value") from exc
    if any(amount <= 0 for amount in amounts):
        raise TradingCliError("probe amounts must be positive")
    return amounts


def _depth_simulation_payload(simulation: DepthRouteSimulation) -> dict[str, Any]:
    return {
        "start_asset": simulation.start_asset,
        "start_amount": str(simulation.start_amount),
        "complete": simulation.complete,
        "final_amount": None if simulation.final_amount is None else str(simulation.final_amount),
        "net_edge_bps": None if simulation.net_edge_bps is None else str(simulation.net_edge_bps),
        "failed_leg_symbol": simulation.failed_leg_symbol,
        "legs": [{
            "symbol": leg.symbol,
            "from_asset": leg.from_asset,
            "to_asset": leg.to_asset,
            "side": leg.side,
            "input_amount": str(leg.input_amount),
            "filled_input": str(leg.filled_input),
            "output_amount": str(leg.output_amount),
            "traded_base_amount": str(leg.traded_base_amount),
            "fee_paid_base": str(leg.fee_paid_base),
            "fee_paid_quote": str(leg.fee_paid_quote),
            "levels_used": leg.levels_used,
            "complete": leg.complete,
        } for leg in simulation.legs],
    }

def _settlement_payload(result: MultiLegSettlement) -> dict[str, Any]:
    return {
        "route_id": result.route_id,
        "start_asset": result.start_asset,
        "start_amount": str(result.start_amount),
        "complete": result.complete,
        "final_amount": None if result.final_amount is None else str(result.final_amount),
        "net_edge_bps": None if result.net_edge_bps is None else str(result.net_edge_bps),
        "failed_leg_symbol": result.failed_leg_symbol,
        "failure_reason": result.failure_reason,
        "residuals": {asset: str(value) for asset, value in sorted(result.residuals.items())},
        "legs": [{
            "symbol": leg.symbol,
            "side": leg.side,
            "input_amount": str(leg.input_amount),
            "order_base_amount": str(leg.order_base_amount),
            "consumed_input": str(leg.consumed_input),
            "residual_input": str(leg.residual_input),
            "output_amount": str(leg.output_amount),
            "levels_used": leg.levels_used,
            "fee_paid_base": str(leg.fee_paid_base),
            "fee_paid_quote": str(leg.fee_paid_quote),
        } for leg in result.legs],
    }


def _recorded_settlement_payload(item) -> dict[str, Any]:
    return {
        "settlement_id": item.settlement_id,
        "route_id": item.route_id,
        "start_asset": item.start_asset,
        "start_amount": str(item.start_amount),
        "complete": item.complete,
        "final_amount": None if item.final_amount is None else str(item.final_amount),
        "net_edge_bps": None if item.net_edge_bps is None else str(item.net_edge_bps),
        "failed_leg_symbol": item.failed_leg_symbol,
        "failure_reason": item.failure_reason,
        "observed_at": item.observed_at,
        "residuals": {asset: str(value) for asset, value in sorted(item.residuals.items())},
        "legs": [{
            "symbol": leg.symbol, "side": leg.side,
            "input_amount": str(leg.input_amount),
            "order_base_amount": str(leg.order_base_amount),
            "consumed_input": str(leg.consumed_input),
            "residual_input": str(leg.residual_input),
            "output_amount": str(leg.output_amount),
            "levels_used": leg.levels_used,
            "fee_paid_base": str(leg.fee_paid_base),
            "fee_paid_quote": str(leg.fee_paid_quote),
        } for leg in item.legs],
    }


def _market_payload(market: MarketInfo) -> dict[str, Any]:
    return {
        "symbol": market.symbol,
        "base": market.base,
        "quote": market.quote,
        "spot": market.spot,
        "active": market.active,
        "amount_step": None if market.amount_step is None else str(market.amount_step),
        "min_amount": None if market.min_amount is None else str(market.min_amount),
        "min_cost": None if market.min_cost is None else str(market.min_cost),
        "taker_fee_rate": None if market.taker_fee_rate is None else str(market.taker_fee_rate),
        "taker_fee_rate_base": None if market.taker_fee_rate_base is None else str(market.taker_fee_rate_base),
        "taker_fee_rate_quote": None if market.taker_fee_rate_quote is None else str(market.taker_fee_rate_quote),
        "market_order_enabled": bool(market.market_order_enabled),
    }


def run_args(args, *, repo_root: Path, global_config: GlobalConfig | None = None) -> int:
    command = args.trading_command
    state_dir = _state_dir(args, repo_root, global_config)
    if command == "presentation":
        path = state_dir / "presentation.json"
        try:
            if args.presentation_action == "status":
                state = read_presentation(path)
            else:
                state = write_presentation(path, args.presentation_action, now=time.time())
        except PresentationError as exc:
            raise TradingCliError(str(exc)) from exc
        _json_print({"mode": state.mode, "updated_at": state.updated_at})
        return 0
    if command == "notify-once":
        if global_config is None:
            raise TradingCliError("notify-once requires resolved docich global config")
        try:
            result = deliver_pending_notifications(
                global_config, now=time.time(), state_dir=state_dir
            )
        except NotificationError as exc:
            raise TradingCliError(str(exc)) from exc
        _json_print({
            "enabled": result.enabled,
            "bootstrapped": result.bootstrapped,
            "presentation_mode": result.presentation_mode,
            "source_count": result.source_count,
            "overlay_sent": result.overlay_sent,
            "speech_sent": result.speech_sent,
            "overlay_pending": result.overlay_pending,
            "speech_pending": result.speech_pending,
            "error_codes": list(result.error_codes),
        })
        return 0
    if command == "status":
        status_path = state_dir / "status.json"
        _json_print(_absent_status() if not status_path.is_file() else _safe_existing_status(status_path))
        return 0
    if command == "settlement-history":
        if args.limit <= 0:
            raise TradingCliError("settlement history limit must be positive")
        db_path = state_dir / "paper.sqlite3"
        if not db_path.is_file():
            _json_print({"mode": "paper", "count": 0, "settlements": []})
            return 0
        ledger = PaperLedger(db_path)
        try:
            items = ledger.recent_multileg_settlements(limit=args.limit)
            _json_print({
                "mode": "paper", "count": len(items),
                "settlements": [_recorded_settlement_payload(item) for item in items],
            })
            return 0
        finally:
            ledger.close()
    if command == "discover":
        try:
            markets = BitbankPublicGateway().discover_markets()
        except CCXTUnavailableError as exc:
            raise TradingCliError(str(exc)) from exc
        payload = {
            "mode": "paper",
            "exchange": "bitbank",
            "market_count": len(markets),
            "markets": [_market_payload(markets[symbol]) for symbol in sorted(markets)],
        }
        _json_print(payload)
        return 0
    if command == "history":
        try:
            gateway = BitbankPublicGateway()
            markets = gateway.discover_markets()
            requested = [part.strip() for part in (args.symbols or "").split(",") if part.strip()]
            symbols = requested or sorted(markets)
            unknown = sorted(set(symbols) - set(markets))
            if unknown:
                raise TradingCliError(f"history requested unknown/ineligible markets: {', '.join(unknown)}")
            now = time.time()
            frames = gateway.fetch_market_frames(symbols, timeframe=args.timeframe, limit=args.limit, now=now)
        except (CCXTUnavailableError, MarketFrameError) as exc:
            raise TradingCliError(str(exc)) from exc
        _json_print({
            "mode": "paper", "exchange": "bitbank", "as_of": now,
            "market_count": len(frames),
            "frames": {symbol: _frame_payload(frames[symbol]) for symbol in sorted(frames)},
        })
        return 0
    if command == "paper-cycle":
        snapshot = _load_snapshot(Path(args.snapshot))
        state_dir.mkdir(parents=True, exist_ok=True)
        ledger = PaperLedger(state_dir / "paper.sqlite3")
        try:
            ledger_deployed_before = ledger.deployed_reference()
            total_deployed_before = snapshot["deployed_reference"] + ledger_deployed_before
            result = allocate_opportunities(
                snapshot["opportunities"],
                markets=snapshot["markets"],
                prices=snapshot["prices"],
                quote_to_reference=snapshot["quote_to_reference"],
                available_quote=snapshot["available_quote"],
                capital_reference=snapshot["capital_reference"],
                deployed_reference=total_deployed_before,
                policy=CapitalPolicy(),
                now=snapshot["as_of"],
            )
            broker = PaperBroker(ledger)
            for decision in result.decisions:
                broker.fill(decision, timestamp=snapshot["as_of"])
            deployed_after = snapshot["deployed_reference"] + ledger.deployed_reference()
            payload = build_public_status(
                worker_state="paper_cycle_complete",
                last_cycle_at=snapshot["as_of"],
                eligible_symbols=snapshot["markets"].keys(),
                capital_reference=snapshot["capital_reference"],
                deployed_reference=deployed_after,
                open_positions=ledger.positions(),
                recent_fills=ledger.recent_fills(limit=20),
                skipped_reason_codes=[skip.reason_code for skip in result.skipped],
            )
            write_public_status(state_dir / "status.json", payload)
            _json_print(payload)
            return 0
        finally:
            ledger.close()
    if command == "strategy-cycle":
        snapshot = _load_strategy_snapshot(Path(args.snapshot))
        candidates = tuple(scan_opportunities(snapshot["frames"], now=snapshot["as_of"])) + tuple(
            scan_relative_value_opportunities(snapshot["frames"], snapshot["markets"], now=snapshot["as_of"])
        )
        selection = select_diversified_opportunities(candidates, snapshot["frames"])
        prices = {symbol: frame.last_price for symbol, frame in snapshot["frames"].items()}
        state_dir.mkdir(parents=True, exist_ok=True)
        ledger = PaperLedger(state_dir / "paper.sqlite3")
        try:
            deployed_before = snapshot["deployed_reference"] + ledger.deployed_reference()
            allocation = allocate_opportunities(
                selection.selected, markets=snapshot["markets"], prices=prices,
                quote_to_reference=snapshot["quote_to_reference"], available_quote=snapshot["available_quote"],
                capital_reference=snapshot["capital_reference"], deployed_reference=deployed_before,
                policy=CapitalPolicy(), now=snapshot["as_of"],
            )
            broker = PaperBroker(ledger)
            for decision in allocation.decisions:
                broker.fill(decision, timestamp=snapshot["as_of"])
            rejected_codes = [item.reason_code for item in selection.rejected]
            skipped_codes = rejected_codes + [item.reason_code for item in allocation.skipped]
            summary = {
                "candidate_count": len(candidates),
                "selected_count": len(selection.selected),
                "rejected_count": len(selection.rejected),
                "strategy_ids": sorted({item.strategy_id for item in candidates}),
                "candidate_reason_codes": sorted({item.reason_code for item in candidates}),
            }
            payload = build_public_status(
                worker_state="strategy_cycle_complete", last_cycle_at=snapshot["as_of"],
                eligible_symbols=snapshot["markets"].keys(), capital_reference=snapshot["capital_reference"],
                deployed_reference=snapshot["deployed_reference"] + ledger.deployed_reference(),
                open_positions=ledger.positions(), recent_fills=ledger.recent_fills(limit=20),
                skipped_reason_codes=skipped_codes, signal_summary=summary,
            )
            write_public_status(state_dir / "status.json", payload)
            _json_print(payload)
            return 0
        finally:
            ledger.close()
    if command == "arbitrage-scan":
        try:
            gateway = BitbankPublicGateway()
            markets = gateway.discover_markets()
            triangle_symbols = find_triangle_symbols(markets)
            now = time.time()
            books = (
                gateway.fetch_top_books(triangle_symbols, now=now, limit=args.book_limit)
                if triangle_symbols else {}
            )
            min_edge = as_decimal(args.min_edge_bps, "min_edge_bps")
            routes = scan_triangular_arbitrage(markets, books, now=now, min_net_edge_bps=min_edge)
        except (CCXTUnavailableError, ArbitrageDataError, TradingValidationError) as exc:
            raise TradingCliError(str(exc)) from exc
        candidates = []
        for route in routes:
            candidates.append({
                "route_id": route.route_id,
                "start_asset": route.start_asset,
                "net_edge_bps": str(route.net_edge_bps),
                "max_start_amount": str(route.max_start_amount),
                "legs": [{
                    "symbol": leg.symbol, "from_asset": leg.from_asset, "to_asset": leg.to_asset,
                    "side": leg.side, "price": str(leg.price),
                    "fee_rate": str(leg.fee_rate_quote),
                    "fee_rate_base": str(leg.fee_rate_base),
                    "fee_rate_quote": str(leg.fee_rate_quote),
                } for leg in route.legs],
            })
        _json_print({
            "mode": "paper", "exchange": "bitbank", "as_of": now,
            "triangle_market_count": len(triangle_symbols),
            "book_market_count": len(books),
            "candidate_count": len(candidates),
            "candidates": candidates,
        })
        return 0
    if command == "arbitrage-depth-scan":
        probe_asset = str(args.probe_asset).strip().upper()
        if not probe_asset:
            raise TradingCliError("probe asset must not be empty")
        probe_amounts = _parse_probe_amounts(args.probe_amounts)
        try:
            min_edge = as_decimal(args.min_edge_bps, "min_edge_bps")
            gateway = BitbankPublicGateway()
            markets = gateway.discover_markets()
            triangle_symbols = find_triangle_symbols(markets)
            fetch_started_at = time.time()
            circuit_statuses = (
                gateway.fetch_circuit_break_statuses(triangle_symbols)
                if triangle_symbols else {}
            )
            depth_books = (
                gateway.fetch_depth_books(triangle_symbols, now=fetch_started_at, limit=args.book_limit)
                if triangle_symbols else {}
            )
            now = time.time()
            top_books = {
                symbol: TopOfBook(
                    symbol, book.bids[0].price, book.asks[0].price, book.as_of,
                    bid_amount=book.bids[0].amount, ask_amount=book.asks[0].amount,
                )
                for symbol, book in depth_books.items()
            }
            routes = scan_triangular_arbitrage(markets, top_books, now=now, min_net_edge_bps=min_edge)
        except (CCXTUnavailableError, ArbitrageDataError, TradingValidationError) as exc:
            raise TradingCliError(str(exc)) from exc
        candidates = []
        record_ledger = None
        try:
            for route in routes:
                route_assets = {leg.from_asset for leg in route.legs}
                simulations = []
                probe_skip_reason = None
                settlements = []
                if probe_asset in route_assets:
                    simulations = [
                        _depth_simulation_payload(
                            simulate_route_depth(route, depth_books, start_amount=amount, start_asset=probe_asset)
                        )
                        for amount in probe_amounts
                    ]
                    settlement_results = [
                        simulate_multileg_settlement(
                            route, depth_books, markets, circuit_statuses,
                            start_amount=amount, start_asset=probe_asset, now=now,
                        )
                        for amount in probe_amounts
                    ]
                    for result in settlement_results:
                        settlement_id = settlement_observation_id(
                            route, result, depth_books, circuit_statuses, markets
                        )
                        payload = _settlement_payload(result)
                        payload["settlement_id"] = settlement_id
                        if args.record and record_ledger is None:
                            state_dir.mkdir(parents=True, exist_ok=True)
                            record_ledger = PaperLedger(state_dir / "paper.sqlite3")
                        payload["recorded"] = bool(record_ledger is not None)
                        if record_ledger is not None:
                            record_ledger.record_multileg_settlement(
                                settlement_id, result, observed_at=now
                            )
                        settlements.append(payload)
                else:
                    probe_skip_reason = "probe_asset_not_in_route"
                candidates.append({
                    "route_id": route.route_id,
                    "top_start_asset": route.start_asset,
                    "top_net_edge_bps": str(route.net_edge_bps),
                    "top_max_start_amount": str(route.max_start_amount),
                    "probe_asset": probe_asset,
                    "probe_skip_reason": probe_skip_reason,
                    "depth_simulations": simulations,
                    "settlements": settlements,
                    "circuit_modes": {symbol: status.mode for symbol, status in sorted(circuit_statuses.items())},
                    "legs": [{
                        "symbol": leg.symbol, "from_asset": leg.from_asset, "to_asset": leg.to_asset,
                        "side": leg.side, "price": str(leg.price),
                        "fee_rate": str(leg.fee_rate_quote),
                        "fee_rate_base": str(leg.fee_rate_base),
                        "fee_rate_quote": str(leg.fee_rate_quote),
                    } for leg in route.legs],
                })
        finally:
            if record_ledger is not None:
                record_ledger.close()
        _json_print({
            "mode": "paper", "exchange": "bitbank", "as_of": now,
            "triangle_market_count": len(triangle_symbols),
            "depth_market_count": len(depth_books),
            "candidate_count": len(candidates),
            "probe_asset": probe_asset,
            "probe_amounts": [str(amount) for amount in probe_amounts],
            "candidates": candidates,
        })
        return 0
    raise TradingCliError(f"unknown trading command: {command}")
