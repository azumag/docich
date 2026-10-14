"""Operator CLI for the isolated free-strategy PAPER lab.

This module intentionally has no live mode or promotion command. It can be
invoked directly with ``python -m docich.trading.free_strategy.cli``; routing
through the main ``docich trading`` CLI can remain a thin follow-up change.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Callable

from ..exchanges.bitbank_ccxt import BitbankPublicGateway
from .contract import Artifact, StrategyError, decode, read_source
from .evaluation import evaluate, public_summary
from .generation import generate, request_text
from .service import run_cycle
from .store import LabStore


def _symbols(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise StrategyError("invalid_symbols")
    return values


def _object(raw: str) -> dict:
    value = decode(raw.encode("utf-8"), limit=16 * 1024)
    if not isinstance(value, dict):
        raise StrategyError("invalid_state")
    return value


def _json_print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--trading-dir", required=True, metavar="PATH")
    sub = parser.add_subparsers(dest="action", required=True)

    register = sub.add_parser("register", help="固定sourceをPAPER候補として登録する")
    register.add_argument("--source", required=True, metavar="PY")
    register.add_argument("--image", required=True, metavar="SHA256")
    register.add_argument("--name", required=True)
    register.add_argument("--family", required=True)
    register.add_argument("--thesis", required=True)
    register.add_argument("--symbols", required=True, metavar="CSV")
    register.add_argument("--parameters-json", default="{}", metavar="JSON")
    register.add_argument("--initial-state-json", default="{}", metavar="JSON")
    register.add_argument("--capital", default="10000")
    register.add_argument("--days", type=int, default=30)

    generated = sub.add_parser("generate", help="text-only providerからPAPER候補を1件生成する")
    generated.add_argument("--image", required=True, metavar="SHA256")
    generated.add_argument("--symbols", required=True, metavar="CSV")
    generated.add_argument("--brief", required=True)
    generated.add_argument("--capital", default="10000")
    generated.add_argument("--days", type=int, default=30)

    cycle = sub.add_parser("cycle", help="隔離PAPER cycleを1回実行する")
    cycle.add_argument("--image", required=True, metavar="SHA256")

    sub.add_parser("status", help="read-onlyのPAPER研究サマリを表示する")

    evaluated = sub.add_parser("evaluate", help="指定experimentのPAPER評価を更新する")
    evaluated.add_argument("experiment")

    paused = sub.add_parser("pause", help="指定experimentを停止する")
    paused.add_argument("experiment")

    resumed = sub.add_parser("resume", help="手動停止したexperimentを再開する")
    resumed.add_argument("experiment")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-free-strategy")
    configure_parser(parser)
    return parser


def run_args(
    args,
    *,
    now_fn: Callable[[], float] = time.time,
    text_fn=request_text,
    gateway_factory=lambda: BitbankPublicGateway(timeout_ms=10_000),
    cycle_fn=run_cycle,
) -> dict:
    trading_dir = Path(args.trading_dir).expanduser()
    lab_path = trading_dir / "free-strategies" / "lab.sqlite3"
    action = args.action

    if action == "status":
        return public_summary(lab_path, now=float(now_fn()))

    if action == "generate":
        return generate(
            trading_dir,
            image=args.image,
            symbols=_symbols(args.symbols),
            brief=args.brief,
            capital=args.capital,
            days=args.days,
            now_fn=now_fn,
            text_fn=text_fn,
        )

    if action == "cycle":
        return cycle_fn(
            trading_dir,
            image=args.image,
            gateway=gateway_factory(),
            now_fn=now_fn,
        )

    store = LabStore(lab_path)
    try:
        if action == "register":
            artifact = Artifact.create(
                source=read_source(Path(args.source).expanduser()),
                image=args.image,
                name=args.name,
                family=args.family,
                thesis=args.thesis,
                symbols=_symbols(args.symbols),
                parameters=_object(args.parameters_json),
                initial_state=_object(args.initial_state_json),
            )
            digest = store.register(artifact)
            identity = store.create(
                digest,
                now=float(now_fn()),
                capital=args.capital,
                days=args.days,
            )
            return {"mode": "PAPER", "status": "registered", "artifact": digest, "experiment": identity}

        if action == "evaluate":
            return evaluate(store, args.experiment, now=float(now_fn()))

        if action == "pause":
            store.pause(args.experiment)
            return {"mode": "PAPER", "status": "paused", "experiment": args.experiment}

        if action == "resume":
            store.resume(args.experiment, now=float(now_fn()))
            return {"mode": "PAPER", "status": "paper_validating", "experiment": args.experiment}

        raise StrategyError("invalid_cli_action")
    finally:
        store.close()


def main(
    argv: list[str] | None = None,
    *,
    now_fn: Callable[[], float] = time.time,
    text_fn=request_text,
    gateway_factory=lambda: BitbankPublicGateway(timeout_ms=10_000),
    cycle_fn=run_cycle,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = run_args(
            args,
            now_fn=now_fn,
            text_fn=text_fn,
            gateway_factory=gateway_factory,
            cycle_fn=cycle_fn,
        )
        _json_print(payload)
        return 0
    except StrategyError as exc:
        _json_print({"mode": "PAPER", "status": "error", "error": str(exc)})
        return 2
    except Exception:
        # Never expose provider/public-API/source exception details through CLI output.
        _json_print({"mode": "PAPER", "status": "error", "error": "free_strategy_cli_failed"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
