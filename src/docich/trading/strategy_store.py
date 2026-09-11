"""Durable storage for the paper trading StrategyPolicy (Issue #198, Stage 4).

The worker rebuilds its default ``StrategyPolicy()`` on every process start.
Persisting the effective policy here lets the end-of-corner improvement job
adjust it and lets the next worker cycle observe the change without a code
edit. When a declarative PAPER strategy experiment exists, loading the policy
also activates that experiment for the current worker cycle.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from .models import TradingValidationError, as_decimal
from .strategies import StrategyPolicy


POLICY_FILENAME = "strategy_policy.json"
POLICY_KEYS = (
    "momentum_lookback",
    "momentum_threshold_bps",
    "mean_reversion_lookback",
    "mean_reversion_z",
    "max_notional_fraction",
)


class StrategyStoreError(RuntimeError):
    """Raised when a persisted strategy policy is malformed or invalid."""


def strategy_policy_path(trading_dir) -> Path:
    return Path(trading_dir) / POLICY_FILENAME


def policy_to_payload(policy: StrategyPolicy) -> dict[str, object]:
    """Serialize only the five public policy keys (allowlisted)."""
    return {
        "momentum_lookback": int(policy.momentum_lookback),
        "momentum_threshold_bps": str(policy.momentum_threshold_bps),
        "mean_reversion_lookback": int(policy.mean_reversion_lookback),
        "mean_reversion_z": str(policy.mean_reversion_z),
        "max_notional_fraction": str(policy.max_notional_fraction),
    }


def _lookback(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool):
        raise StrategyStoreError(f"{name} は整数である必要があります")
    if isinstance(value, float):
        if not value.is_integer():
            raise StrategyStoreError(f"{name} は整数である必要があります")
        value = int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise StrategyStoreError(f"{name} は整数である必要があります")
        try:
            value = int(text)
        except ValueError as exc:
            raise StrategyStoreError(f"{name} は整数である必要があります") from exc
    if type(value) is not int:
        raise StrategyStoreError(f"{name} は整数である必要があります")
    if value < minimum:
        raise StrategyStoreError(f"{name} は{minimum}以上である必要があります")
    return value


def policy_from_mapping(data: Mapping[str, object]) -> StrategyPolicy:
    """Build a validated StrategyPolicy from a stored payload."""
    if not isinstance(data, Mapping):
        raise StrategyStoreError("戦略ポリシーはJSONオブジェクトである必要があります")
    missing = [key for key in POLICY_KEYS if key not in data]
    if missing:
        raise StrategyStoreError(f"戦略ポリシーに必要なキーがありません: {', '.join(missing)}")
    try:
        return StrategyPolicy(
            momentum_lookback=_lookback(data["momentum_lookback"], "momentum_lookback", 2),
            momentum_threshold_bps=as_decimal(
                data["momentum_threshold_bps"], "momentum_threshold_bps"
            ),
            mean_reversion_lookback=_lookback(
                data["mean_reversion_lookback"], "mean_reversion_lookback", 3
            ),
            mean_reversion_z=as_decimal(data["mean_reversion_z"], "mean_reversion_z"),
            max_notional_fraction=as_decimal(
                data["max_notional_fraction"], "max_notional_fraction"
            ),
        )
    except TradingValidationError as exc:
        raise StrategyStoreError(str(exc)) from exc


def _activate_experiment(trading_dir) -> None:
    """Refresh process-local experiment state on every policy load."""
    try:
        from .strategy_lab import load_strategy_experiment
        from .strategy_runtime import set_active_experiment

        set_active_experiment(load_strategy_experiment(trading_dir))
    except Exception:
        try:
            from .strategy_runtime import set_active_experiment
            set_active_experiment(None)
        except Exception:
            pass


def load_strategy_policy(
    trading_dir, *, fallback: StrategyPolicy | None = None
) -> StrategyPolicy:
    """Load policy and activate any valid PAPER strategy experiment."""
    default = fallback if fallback is not None else StrategyPolicy()
    _activate_experiment(trading_dir)
    try:
        raw = strategy_policy_path(trading_dir).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return default
    try:
        return policy_from_mapping(data)
    except StrategyStoreError:
        return default


def save_strategy_policy(trading_dir, policy: StrategyPolicy) -> Path:
    """Atomically write the policy as a 0600 JSON document. Returns its path."""
    target = strategy_policy_path(trading_dir)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    payload = policy_to_payload(policy)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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
    return target
