"""Fixture-only news collectors (N02, #778). No network module is imported here."""
from .fixture_feed import (
    COLLECTOR_VERSION,
    CollectedItem,
    FetchOutcome,
    FixtureCollector,
    Quarantine,
    Response,
    Resolver,
    Transport,
    TransportFailure,
    TransportTimeout,
    is_public_ip,
    run_cycle,
)

__all__ = [
    "COLLECTOR_VERSION", "CollectedItem", "FetchOutcome", "FixtureCollector",
    "Quarantine", "Response", "Resolver", "Transport", "TransportFailure",
    "TransportTimeout", "is_public_ip", "run_cycle",
]
