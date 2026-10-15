"""Shared synthetic TwiCa 同志カード payload (design §6 shape) for doushi tests.

Kept outside any ``test_*`` module so ranking, view, corner and narration tests
can build the same validated payload without importing each other.
"""
from __future__ import annotations

import datetime as dt
import json


def make_payload(*, generated_at: str | None = None, period: str = "all",
                 limit: int = 10) -> dict:
    """A fully valid design-§6 payload with sequential ranks."""
    if generated_at is None:
        generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "generated_at": generated_at,
        "period": period,
        "metric_labels": {
            "by_count": "排出枚数",
            "by_points": "使用チャネルポイント",
            "by_unique": "蒐集カード数",
            "popular_cards": "人気カード",
        },
        "note": "配信者本人とBOTは集計から除いています。",
        "card_type_total": 128,
        "by_count": [
            {"rank": index + 1, "username": f"alice{index}", "redemption_count": 50 - index}
            for index in range(min(3, limit))
        ],
        "by_points": [
            {"rank": index + 1, "username": f"alice{index}", "total_points": 5000 - index,
             "redemption_count": 50 - index}
            for index in range(min(3, limit))
        ],
        "by_unique": [
            {"rank": index + 1, "username": f"alice{index}", "unique_count": 40 - index,
             "total_cards": 128}
            for index in range(min(3, limit))
        ],
        "popular_cards": [
            {"rank": index + 1, "card_name": f"カード{index}", "actual_count": 30 - index,
             "drawer_count": 20 - index}
            for index in range(min(3, limit))
        ],
    }


def api_body(payload: dict | None = None, *, period: str = "all") -> bytes:
    """The public card-ranking API response: the validated payload, as-is."""
    return json.dumps(
        payload if payload is not None else make_payload(period=period),
        ensure_ascii=False,
    ).encode("utf-8")


def stored_body(payload: dict | None = None, *, period: str = "all",
                limit: int = 10) -> bytes:
    """The transient ``doushi_ranking.json`` byte stream the adapter writes."""
    return json.dumps(
        {"period": period, "limit": limit, "payload": payload if payload is not None
         else make_payload(period=period, limit=limit)},
        ensure_ascii=False,
    ).encode("utf-8")
