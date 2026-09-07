"""CLI for the paper-only docich crypto trading foundation."""
from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .exchanges.bitbank_ccxt import BitbankPublicGateway, CCXTUnavailableError
from .ledger import PaperLedger
from .models import MarketInfo, Opportunity, TradingValidationError, as_decimal
from .paper import PaperBroker
from .risk import CapitalPolicy, allocate_opportunities
from .status import build_public_status, write_public_status


class TradingCliError(RuntimeError):
    """Stable user-facing error for trading CLI commands."""


_SNAPSHOT_KEYS = {
    "mode", "as_of", "capital_reference", "deployed_reference",
    "quote_to_reference", "available_quote", "markets", "prices", "opportunities",
}
_MARKET_KEYS = {"base", "quote", "spot", "active", "amount_step", "min_amount", "min_cost"}
_OPPORTUNITY_KEYS = {
    "opportunity_id", "strategy_id", "symbol", "side", "score", "expected_edge_bps",
    "max_notional_fraction", "expires_at", "reason_code",
}
_PUBLIC_STATUS_KEYS = {
    "schema_version", "mode", "worker_state", "last_cycle_at", "market_count",
    "eligible_symbols", "capital_reference", "deployed_reference", "open_positions",
    "recent_fills", "skipped_reason_codes",
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
    paper = sub.add_parser("paper-cycle", help="安全な snapshot から paper cycle を1回実行する")
    paper.add_argument("--snapshot", required=True, metavar="JSON", help="paper snapshot JSON")


def _state_dir(args, repo_root: Path) -> Path:
    raw = getattr(args, "state_dir", None)
    return Path(raw).expanduser() if raw else repo_root / "run" / "trading"


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
    }


def run_args(args, *, repo_root: Path) -> int:
    command = args.trading_command
    state_dir = _state_dir(args, repo_root)
    if command == "status":
        status_path = state_dir / "status.json"
        _json_print(_absent_status() if not status_path.is_file() else _safe_existing_status(status_path))
        return 0
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
    raise TradingCliError(f"unknown trading command: {command}")
