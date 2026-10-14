"""Paper-only crypto trading foundation for docich.

The initial slice intentionally exposes no live-order or credential APIs.
"""

from .models import AllocationDecision, AllocationResult, MarketInfo, Opportunity, SkipDecision
from .risk import CapitalPolicy, allocate_opportunities

__all__ = [
    "AllocationDecision",
    "AllocationResult",
    "CapitalPolicy",
    "MarketInfo",
    "Opportunity",
    "SkipDecision",
    "allocate_opportunities",
]
