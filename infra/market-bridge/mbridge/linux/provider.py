"""Linux fail-closed provider: validate -> lease -> atomic publish.

A missing/hanging Windows bridge makes this provider *alive but unready*: the
process stays up for monitoring and never stops the worker/encoder by itself.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..common.contract import (EXPECTED_SOURCE, Envelope, Snapshot,
                               decode_snapshot, is_tradeable, safe_reason,
                               validate_envelope, validate_snapshot)
from ..common.publish import load_published, publish_atomic
from .client import BridgeClient, BridgeError

DEFAULT_SNAPSHOT_NAME = "market-snapshot.json"


def _host_epoch() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return "unknown-host"


class MarketBridgeProvider:
    def __init__(self, *, market: str, client: BridgeClient, source_epoch: str,
                 state_dir: Path | str, publisher: str,
                 generation: int = 1, host_epoch: str | None = None,
                 expected_source: str | None = None, lease_ttl_s: float = 5.0,
                 max_source_age_s: float = 15.0, required_symbols: tuple[str, ...] = (),
                 fetch_scanner: bool = False, snapshot_name: str = DEFAULT_SNAPSHOT_NAME,
                 clock=time.time, monotonic=time.monotonic) -> None:
        if market not in ("stocks", "fx"):
            raise ValueError("invalid_market")
        self.market = market
        self.client = client
        self.expected_source = expected_source or EXPECTED_SOURCE[market]
        self.source_epoch = source_epoch
        self.state_dir = Path(state_dir)
        self.publisher = publisher
        self.generation = generation
        self.host_epoch = host_epoch or _host_epoch()
        self.lease_ttl_s = lease_ttl_s
        self.max_source_age_s = max_source_age_s
        self.required_symbols = tuple(required_symbols)
        self.fetch_scanner = fetch_scanner
        self.snapshot_name = snapshot_name
        self.clock = clock
        self.monotonic = monotonic

        self._last_envelope: Envelope | None = None
        self._last_ok_mon: float | None = None
        self._last_bridge_sequence = 0
        self._published_sequence = 0
        self._last_reason = "provider_unavailable"

    @property
    def alive(self) -> bool:
        """Process liveness only; never used as readiness evidence."""
        return True

    @property
    def path(self) -> Path:
        return self.state_dir / self.snapshot_name

    def read_published(self) -> Envelope | None:
        return load_published(self.path)

    def poll(self) -> Envelope:
        """One bounded fetch+validate+publish cycle.  Always returns an envelope."""
        now = self.clock()
        mon = self.monotonic()
        ok = False
        reason = "provider_unavailable"
        snap: Snapshot | None = None
        try:
            snap = decode_snapshot(self.client.get("/v1/snapshot"))
            if snap.generation > self.generation:
                # A new bridge instance starts a new generation; regressing to
                # an older one is rejected below as generation_conflict.
                self.generation = snap.generation
                self._last_bridge_sequence = 0
                self._published_sequence = 0
            ok, reason = validate_snapshot(
                snap, expected_market=self.market, expected_source=self.expected_source,
                source_epoch=self.source_epoch, generation=self.generation, now=now,
                max_source_age_s=self.max_source_age_s,
                required_symbols=self.required_symbols)
            if ok and self.fetch_scanner:
                ok, reason = self._validate_scanner(snap, now)
            if ok and snap.sequence <= self._last_bridge_sequence:
                ok, reason = False, "sequence_regressed"
        except BridgeError as exc:
            ok, reason = False, exc.reason
        except (ValueError, TypeError):
            ok, reason = False, "invalid_schema"
        except Exception:
            ok, reason = False, "upstream_error"

        if ok and snap is not None:
            published = self._publish(snap, now)
            self._last_ok_mon = mon
            self._last_bridge_sequence = snap.sequence
            self._published_sequence = snap.sequence
            self._last_reason = "ok" if snap.status == "ready" else snap.reason
        else:
            published = self._publish_unready(reason or "provider_unavailable", now)
            self._last_ok_mon = None
            self._last_reason = reason or "provider_unavailable"
        self._last_envelope = published
        return published

    def _validate_scanner(self, snap: Snapshot, now: float) -> tuple[bool, str]:
        try:
            payload = self.client.get("/v1/scanner")
        except BridgeError as exc:
            return False, exc.reason
        if not isinstance(payload, dict) or int(payload.get("generation", -1)) != self.generation:
            return False, "generation_conflict"
        from ..common.contract import decode_candidate  # local import keeps namespace small
        raw = payload.get("candidates", [])
        if not isinstance(raw, list):
            return False, "invalid_schema"
        try:
            candidates = tuple(decode_candidate(c) for c in raw[:100])
        except (ValueError, TypeError):
            return False, "invalid_schema"
        merged = Snapshot(
            market=snap.market, source=snap.source, source_epoch=snap.source_epoch,
            generation=snap.generation, sequence=snap.sequence, status=snap.status,
            reason=snap.reason, market_state=snap.market_state,
            source_ts=snap.source_ts, received_ts=snap.received_ts,
            quotes=snap.quotes, candidates=candidates)
        return validate_snapshot(
            merged, expected_market=self.market, expected_source=self.expected_source,
            source_epoch=self.source_epoch, generation=self.generation, now=now,
            max_source_age_s=self.max_source_age_s,
            required_symbols=self.required_symbols)

    def _publish(self, snap: Snapshot, now: float) -> Envelope:
        envelope = Envelope(
            schema_version=1, snapshot=snap, published_at=now,
            lease_deadline=now + self.lease_ttl_s, host_epoch=self.host_epoch,
            publisher=self.publisher, generation=snap.generation)
        publish_atomic(self.state_dir, self.snapshot_name, envelope)
        return envelope

    def _publish_unready(self, reason: str, now: float) -> Envelope:
        self._published_sequence += 1
        snap = Snapshot(
            market=self.market, source=self.expected_source,
            source_epoch=self.source_epoch, generation=self.generation,
            sequence=self._published_sequence, status="unavailable", reason=safe_reason(reason),
            market_state="unknown", source_ts=0.0, received_ts=now)
        return self._publish(snap, now)

    def readiness(self, now=None, state=None) -> tuple[bool, str]:
        """Contract used by MarketCorner: (ready, fixed_reason)."""
        now = self.clock() if now is None else float(now)
        mon = self.monotonic()
        if self._last_envelope is None:
            return False, "provider_unavailable"
        if self._last_ok_mon is None:
            # Last poll failed: keep its fixed reason (auth/adapter/stale/...).
            return False, self._last_envelope.snapshot.reason
        if mon - self._last_ok_mon > self.lease_ttl_s:
            return False, "lease_expired"
        kwargs = dict(expected_market=self.market, expected_source=self.expected_source,
                      source_epoch=self.source_epoch, generation=self.generation,
                      host_epoch=self.host_epoch, now=now,
                      max_source_age_s=self.max_source_age_s,
                      max_lease_age_s=self.lease_ttl_s,
                      required_symbols=self.required_symbols)
        ok, reason = validate_envelope(self._last_envelope, **kwargs)
        if not ok:
            return False, reason
        ok, reason = is_tradeable(self._last_envelope, **kwargs)
        return (True, "ok") if ok else (False, reason)

    def diagnostics(self) -> dict[str, Any]:
        """Fixed allowlist, no prices/accounts/secrets."""
        ready, reason = self.readiness()
        return {
            "market": self.market, "alive": self.alive, "ready": ready,
            "reason": reason, "generation": self.generation,
            "source": self.expected_source, "publisher": self.publisher,
            "host_epoch": self.host_epoch,
        }
