"""Equity day-trading and resident FX paper programs; isolated from crypto.

Point-in-time replay, price baseline, and leakage verification for News PAPER.
"""
from .replay import (
    PointInTimeReplay,
    PriceBaseline,
    ReplayConfig,
    ReplayResult,
    verify_no_future_leakage,
)
