"""Separate PAPER research loop. It never shares the existing paper account."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import math
import os
from pathlib import Path
import tempfile
import time

from ..market_data import MarketFrame
from .broker import settle_observation
from .contract import StrategyError, decimal_text, encode
from .evaluation import evaluate
from .sandbox import DockerSandbox, SandboxError
from .store import LabStore


def write_health(directory: Path, *, now: float, status: str, codes: list[str], completed: int = 0):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {"schema_version": 1, "mode": "PAPER", "status": status, "heartbeat_at": now,
               "completed_experiments": completed, "error_codes": sorted(set(codes))[:8]}
    fd, filename = tempfile.mkstemp(dir=directory, prefix=".health-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encode(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(filename, directory / "health.json")
    finally:
        Path(filename).unlink(missing_ok=True)


@contextmanager
def cycle_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "cycle.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StrategyError("cycle_busy") from exc
        yield


def build_context(store: LabStore, exp: dict, frames: dict, *, now: float) -> tuple[dict, dict]:
    artifact = store.artifact(exp["artifact"])
    symbols = artifact.payload["symbols"]
    series = {}
    for symbol in symbols:
        frame = frames.get(symbol)
        if (not isinstance(frame, MarketFrame) or frame.symbol != symbol or frame.timeframe_seconds != 300
                or len(frame.timestamps) != len(frame.closes) or len(frame.closes) != len(frame.volumes)
                or len(frame.closes) > 144):
            raise StrategyError("history_unavailable")
        if (any(not math.isfinite(stamp) for stamp in frame.timestamps)
                or any(b <= a for a, b in zip(frame.timestamps, frame.timestamps[1:]))
                or any(not price.is_finite() or price <= 0 for price in frame.closes)
                or any(not volume.is_finite() or volume < 0 for volume in frame.volumes)):
            raise StrategyError("history_invalid")
        # Only completed candles are delivered. No future suffix exists in the guest.
        values = [{"start_at": stamp, "end_at": stamp + 300,
                   "close": decimal_text(price), "volume": decimal_text(volume), "available_at": now}
                  for stamp, price, volume in zip(frame.timestamps, frame.closes, frame.volumes)
                  if stamp + 300 <= now]
        if len(values) < 2 or not 0 <= now - values[-1]["end_at"] <= 600:
            raise StrategyError("history_stale")
        series[symbol] = values
    cutoff = min(values[-1]["end_at"] for values in series.values())
    series = {symbol: [v for v in values if v["end_at"] <= cutoff] for symbol, values in series.items()}
    if any(len(values) < 2 for values in series.values()):
        raise StrategyError("history_unaligned")
    run_id = hashlib.sha256(f"{exp['id']}:{cutoff:.6f}".encode()).hexdigest()
    context = {"schema_version": 1, "run_id": run_id, "logical_time": now, "data_cutoff": cutoff,
        "seed": int(run_id[:8], 16), "market_history": series,
        "balances": {"JPY": exp["account"]["cash_jpy"]}, "positions": exp["account"]["positions"],
        "pending_targets": exp["pending"], "recent_fills": store.recent_fills(exp["id"]),
        "state": exp["strategy_state"], "state_revision": exp["revision"],
        "parameters": artifact.payload["parameters"], "constraints": exp["policy"],
        "allowed_symbols": symbols}
    from decimal import Decimal
    prices = {symbol: Decimal(values[-1]["close"]) for symbol, values in series.items()}
    return context, prices


def run_cycle(trading_dir: Path, *, image: str, gateway, runner=None, now_fn=time.time) -> dict:
    directory = Path(trading_dir) / "free-strategies"
    with cycle_lock(directory):
        store = LabStore(directory / "lab.sqlite3")
        codes, completed = [], 0
        runner = runner or DockerSandbox(image, state_dir=directory)
        write_health(directory, now=now_fn(), status="running", codes=[])
        try:
            # Preflight even without candidates so an unusable deployment is visible.
            runner.preflight()
            ids = store.active_ids()
            markets = gateway.discover_markets() if ids else {}
            for identity in ids:
                exp = store.experiment(identity)
                try:
                    now = float(now_fn())
                    if now >= exp["end_at"]:
                        settle_observation(store, identity, markets={}, books={}, statuses={}, now=now)
                        evaluate(store, identity, now=now)
                        continue
                    artifact = store.artifact(exp["artifact"])
                    if artifact.payload["image"] != image:
                        raise StrategyError("runtime_image_mismatch")
                    symbols = artifact.payload["symbols"]
                    frames = gateway.fetch_market_frames(symbols, timeframe="5m", limit=144, now=now)
                    # Settlement uses a newly fetched book AFTER the preceding decision.
                    statuses = gateway.fetch_circuit_break_statuses(symbols, fetched_at=now_fn())
                    books = gateway.fetch_depth_books(symbols, now=now_fn(), limit=20)
                    observed_at = float(now_fn())
                    exp = settle_observation(store, identity, markets=markets, books=books, statuses=statuses, now=observed_at)
                    evaluate(store, identity, now=observed_at)
                    if exp["phase"] not in {"research", "paper_validating"}:
                        continue
                    context, prices = build_context(store, exp, frames, now=observed_at)
                    if exp["last_bar"] is None or context["data_cutoff"] > exp["last_bar"]:
                        decision = runner.run(artifact, context)
                        store.accept(identity, exp["revision"], bar=context["data_cutoff"],
                                     accepted_at=now_fn(), run_id=context["run_id"], decision=decision, prices=prices)
                    completed += 1
                except StrategyError as exc:
                    code = str(exc)
                    codes.append(code)
                    quarantine = code.startswith(("invalid_", "nonfinite_", "json_", "duplicate_")) or code in {
                        "sandbox_timeout", "sandbox_output_limit", "strategy_execution_failed", "artifact_integrity_failed"}
                    store.error(identity, exp["revision"], code, quarantine=quarantine)
                except Exception:
                    # Neither source nor public-API/provider exception text leaves here.
                    codes.append("public_data_unavailable")
                    store.error(identity, exp["revision"], "public_data_unavailable")
        except (StrategyError, SandboxError) as exc:
            codes.append(str(exc))
        except Exception:
            codes.append("lab_cycle_failed")
        finally:
            store.close()
        status = "degraded" if codes else "idle"
        write_health(directory, now=now_fn(), status=status, codes=codes, completed=completed)
        return {"mode": "PAPER", "status": status, "completed": completed, "error_codes": sorted(set(codes))}