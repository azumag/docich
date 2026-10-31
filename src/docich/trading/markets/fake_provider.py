"""Fake market-data provider for Step 2 readiness/skip/cancel/guard/recovery tests."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


def _now(clock=None):
    if clock is not None:
        try:
            return clock()
        except Exception:
            pass
    return time.time()


def _normalize_available(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "ready", "ok", "available", "healthy", "up")
    return bool(value)


@dataclass
class FakeMarketDataProvider:
    available: bool = True
    ready: bool | None = None
    status: str | None = None
    reason: str | None = None
    name: str = "fake"
    market: str | None = None
    clock: object = None
    calls: list = field(default_factory=list)
    history: list = field(default_factory=list)

    def __post_init__(self):
        if self.ready is not None:
            self.available = _normalize_available(self.ready)
        if self.status is not None:
            lowered = str(self.status).strip().lower()
            if lowered in ("ready", "ok", "available", "true", "healthy", "up"):
                self.available = True
            elif lowered in ("unavailable", "not_ready", "down", "false", "unhealthy", "provider_unavailable"):
                self.available = False
            self.reason = self.reason or str(self.status)
        if self.reason is None:
            self.reason = "ok" if self.available else "provider_unavailable"
        self.ready = self.available
        if self.status is None:
            self.status = "ready" if self.available else "unavailable"

    def set_available(self, available, reason=None):
        self.available = _normalize_available(available)
        self.ready = self.available
        self.status = "ready" if self.available else "unavailable"
        if reason is not None:
            self.reason = str(reason)
        elif self.reason is None:
            self.reason = "ok" if self.available else "provider_unavailable"
        return self

    def set_ready(self, ready, reason=None):
        return self.set_available(ready, reason)

    def set_status(self, status, reason=None):
        self.status = str(status)
        lowered = str(status).strip().lower()
        self.available = lowered in ("ready", "ok", "available", "true", "healthy", "up")
        self.ready = self.available
        if reason is not None:
            self.reason = str(reason)
        elif self.reason is None:
            self.reason = "ok" if self.available else "provider_unavailable"
        return self

    def fail(self, reason="provider_unavailable"):
        return self.set_available(False, reason)

    def recover(self, reason="ok"):
        return self.set_available(True, reason)

    def toggle(self):
        return self.set_available(not self.available)

    def readiness(self, now=None, state=None):
        ts = now if now is not None else _now(self.clock)
        self.calls.append((ts, state))
        self.history.append((ts, self.available, self.reason))
        reason = self.reason if self.reason is not None else ("ok" if self.available else "provider_unavailable")
        if not self.available:
            reason = reason or "provider_unavailable"
        return bool(self.available), str(reason)

    def is_ready(self, now=None, state=None):
        ready, _ = self.readiness(now, state)
        return ready

    def is_available(self, now=None, state=None):
        return self.is_ready(now, state)

    def check(self, now=None, state=None):
        ready, reason = self.readiness(now, state)
        return {"ready": ready, "available": ready, "ok": ready,
                "status": "ready" if ready else "unavailable", "reason": reason,
                "market": self.market, "name": self.name}

    def health(self, now=None, state=None):
        return self.check(now, state)

    def get_readiness(self, now=None, state=None):
        return self.readiness(now, state)

    def __bool__(self):
        return bool(self.available)

    def __call__(self, *args, **kwargs):
        now = kwargs.get("now", args[0] if len(args) > 0 else None)
        state = kwargs.get("state", args[1] if len(args) > 1 else None)
        return self.readiness(now, state)


FakeProvider = FakeMarketDataProvider
FakeMarketData = FakeMarketDataProvider
FakeReadinessProvider = FakeMarketDataProvider
MarketFakeProvider = FakeMarketDataProvider
FakeMarketDataProviderAlias = FakeMarketDataProvider


class MarketGuard:
    def __init__(self, provider=None, clock=None, state_path=None):
        self.provider = provider
        self.clock = clock
        self.state_path = state_path
        self.aborted = False
        self.reason = None

    def request_abort(self, reason="provider_unavailable"):
        self.aborted = True
        self.reason = reason
        return True

    def clear_abort(self):
        self.aborted = False
        self.reason = None
        return True

    def check(self, now=None, state=None):
        if self.provider is not None:
            from .program import _normalize_provider_readiness
            ready, reason = _normalize_provider_readiness(self.provider, now, state)
        else:
            ready, reason = True, "ok"
        if self.aborted:
            return False, self.reason or "guard_aborted"
        return ready, reason

    def recover(self, now=None, state=None):
        self.clear_abort()
        if self.provider is not None:
            from .program import _normalize_provider_readiness
            return _normalize_provider_readiness(self.provider, now, state)
        return True, "ok"


def make_fake_provider(*args, **kwargs):
    return FakeMarketDataProvider(*args, **kwargs)


def fake_provider(*args, **kwargs):
    return FakeMarketDataProvider(*args, **kwargs)