"""Immutable LIVE policy snapshots and their digests (Issue #3623 §2.4/§2.6)."""
from __future__ import annotations

import argparse
import json
import os
import stat
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from ..models import TradingValidationError
from ..strategy_lab import (StrategyExperiment, StrategyLabError,
                            experiment_from_mapping, experiment_to_payload)
from ..symbol_slots import validate_symbol
from .canonical import CanonicalError, canonical_json, is_sha256_hex, sha256_hex

INITIAL_SYMBOLS = ("OAS/JPY", "OMG/JPY", "XYM/JPY", "CYBER/JPY", "MONA/JPY", "ATOM/JPY",
                   "KLAY/JPY", "LPT/JPY", "AXS/JPY", "IMX/JPY", "BOBA/JPY")
MAX_SNAPSHOT_BYTES = 64 * 1024
MAX_FRACTION = Decimal("0.30")


class LivePolicyError(ValueError):
    pass


def _payload_without_activation(spec: StrategyExperiment) -> dict:
    payload = experiment_to_payload(spec)
    payload.pop("activated_at", None)
    return payload


def experiment_digest(spec: StrategyExperiment) -> str:
    return sha256_hex(canonical_json(_payload_without_activation(spec)))


def _read_snapshot_file(path: Path) -> dict:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise LivePolicyError(f"snapshot unreadable: {exc.strerror}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise LivePolicyError("snapshot must be a regular file (no symlink)")
    if info.st_size > MAX_SNAPSHOT_BYTES:
        raise LivePolicyError("snapshot too large")
    try:
        data = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise LivePolicyError("snapshot is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise LivePolicyError("snapshot must be a JSON object")
    return data


def _strict_experiment(data: Mapping) -> StrategyExperiment:
    """Build the experiment and require the input to round-trip exactly.

    ``experiment_from_mapping`` silently trims ids, fills defaults and accepts
    float thresholds; LIVE must run exactly what was digested.
    """
    try:
        spec = experiment_from_mapping(data)
    except (StrategyLabError, TradingValidationError, TypeError, ValueError) as exc:
        raise LivePolicyError(f"invalid experiment: {exc}") from exc
    given = {key: value for key, value in data.items() if key != "activated_at"}
    if given != _payload_without_activation(spec):
        raise LivePolicyError("snapshot does not round-trip (unknown keys or lossy fields)")
    return spec


def load_experiment_snapshot(path, *, expected_digest: str) -> StrategyExperiment:
    if not is_sha256_hex(expected_digest):
        raise LivePolicyError("expected digest must be 64 lowercase hex chars")
    spec = _strict_experiment(_read_snapshot_file(Path(path)))
    if experiment_digest(spec) != expected_digest:
        raise LivePolicyError("experiment digest mismatch")
    return spec


@dataclass(frozen=True)
class LivePolicy:
    symbol: str
    experiment: StrategyExperiment
    experiment_digest: str
    max_opportunity_fraction: Decimal
    digest: str

    def payload(self) -> dict:
        return {"symbol": self.symbol, "experiment_digest": self.experiment_digest,
                "experiment": _payload_without_activation(self.experiment),
                "max_opportunity_fraction": format(self.max_opportunity_fraction, "f"),
                "digest": self.digest}


def _policy_digest(symbol: str, exp_digest: str, payload: dict, fraction: Decimal) -> str:
    return sha256_hex(canonical_json({
        "symbol": symbol, "experiment_digest": exp_digest, "experiment": payload,
        "max_opportunity_fraction": format(fraction, "f")}))


def build_policy(spec: StrategyExperiment, symbol: str) -> LivePolicy:
    symbol = validate_symbol(symbol)
    if not symbol.endswith("/JPY"):
        raise LivePolicyError("LIVE policies are JPY pairs only")
    fraction = max(rule.max_notional_fraction for rule in spec.entry_rules)
    if not Decimal("0") < fraction <= MAX_FRACTION:
        raise LivePolicyError("max_opportunity_fraction out of range")
    exp_digest = experiment_digest(spec)
    digest = _policy_digest(symbol, exp_digest, _payload_without_activation(spec), fraction)
    return LivePolicy(symbol, spec, exp_digest, fraction, digest)


def policy_from_payload(payload: Mapping) -> LivePolicy:
    try:
        spec = _strict_experiment(payload["experiment"])
        policy = build_policy(spec, str(payload["symbol"]))
    except (KeyError, TypeError, CanonicalError) as exc:
        raise LivePolicyError("policy payload is malformed") from exc
    if (policy.digest != payload.get("digest")
            or policy.experiment_digest != payload.get("experiment_digest")
            or format(policy.max_opportunity_fraction, "f") != payload.get("max_opportunity_fraction")):
        raise LivePolicyError("policy digest mismatch")
    return policy


@dataclass(frozen=True)
class LivePolicySet:
    policies: Mapping[str, LivePolicy]
    digest: str

    def policy_for(self, symbol: str) -> LivePolicy:
        try:
            return self.policies[symbol]
        except KeyError as exc:
            raise LivePolicyError(f"no policy for {symbol}") from exc

    def payload(self) -> dict:
        return {"symbols": {sym: pol.digest for sym, pol in sorted(self.policies.items())}}


def _set_digest(policies: Mapping[str, LivePolicy]) -> str:
    return sha256_hex(canonical_json({"symbols": {s: p.digest for s, p in sorted(policies.items())}}))


def build_policy_set(spec: StrategyExperiment, symbols=INITIAL_SYMBOLS) -> LivePolicySet:
    names = [validate_symbol(symbol) for symbol in symbols]
    if not names or len(set(names)) != len(names):
        raise LivePolicyError("symbols must be non-empty and unique")
    policies = {name: build_policy(spec, name) for name in names}
    return LivePolicySet(policies, _set_digest(policies))


def policy_set_from_policies(policies: Mapping[str, LivePolicy]) -> LivePolicySet:
    return LivePolicySet(dict(policies), _set_digest(policies))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="print the digest of a LIVE experiment snapshot")
    parser.add_argument("command", choices=["digest"])
    parser.add_argument("file")
    args = parser.parse_args(argv)
    spec = _strict_experiment(_read_snapshot_file(Path(args.file)))
    print(experiment_digest(spec))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
