"""Fake market-data provider for Step 2 readiness / skip / cancel tests.

No network, no credentials, no deploy. Only the ``readiness(now, state)``
contract is used by ``MarketCorner``: it must return ``(bool, reason)``.
"""
from __future__ import annotations


class FakeMarketDataProvider:
    def __init__(self, available: bool = True, reason: str = "ok"):
        self.available = bool(available)
        self.reason = reason if self.available else (reason or "provider_unavailable")
        self.calls = []

    def fail(self, reason: str = "provider_unavailable"):
        self.available = False
        self.reason = reason
        return self

    def recover(self, reason: str = "ok"):
        self.available = True
        self.reason = reason
        return self

    def readiness(self, now=None, state=None):
        self.calls.append(now)
        if self.available:
            return True, self.reason
        return False, self.reason
