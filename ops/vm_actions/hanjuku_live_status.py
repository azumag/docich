#!/usr/bin/env python3
"""Render a fixed, secret-free live status for chapter-4 boss sorties.

Input is the already-sanitized ``diagnostics docich production <sha>`` JSON on
stdin.  Output is a GitHub Issue comment body containing only fixed tokens and
bounded numbers; arbitrary diagnostic text is never copied through.
"""
from __future__ import annotations

import json
import os
import re
import sys

STEPS = ("4-B1", "4-B2", "4-B3", "4-B4")
ORDER_STATES = frozenset({"pending", "launched_unconfirmed", "launched", "done", "failed"})
TACTICAL_STATUSES = frozenset({"ok", "unsupported_chapter", "unavailable", "identity_changed", "output_omitted"})
ROTATION_STATUSES = frozenset({
    "ready", "waiting", "starting", "active", "restoring", "preparing",
    "recovery_required", "failed",
})
RECENT_DECISIONS = (
    "boss_roster_reprobe",
    "boss_relaunch_stale_retry",
    "order_start",
    "order_launched",
)
BASE = "https://github.com/azumag/docich"


def _bounded_int(value, low=0, high=10**7):
    return value if type(value) is int and low <= value <= high else None


def _fixed_bool(value):
    return value if type(value) is bool else None


def _bool_token(value):
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def project(payload):
    if not isinstance(payload, dict):
        raise ValueError("invalid_diagnostics")
    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise ValueError("invalid_diagnostics")
    tactical = diagnostics.get("hanjuku_tactical")
    if not isinstance(tactical, dict):
        raise ValueError("hanjuku_tactical_unavailable")

    raw_status = tactical.get("status")
    status = raw_status if raw_status in TACTICAL_STATUSES else "unknown"
    chapter = _bounded_int(tactical.get("chapter"), 1, 12)
    age_sec = _bounded_int(tactical.get("age_sec"), 0, 3600)
    policy = tactical.get("policy") if isinstance(tactical.get("policy"), dict) else {}
    orders = policy.get("orders") if isinstance(policy.get("orders"), dict) else {}

    projected_orders = {}
    for step in STEPS:
        value = orders.get(step)
        projected_orders[step] = value if value in ORDER_STATES else "unknown"

    active = policy.get("active")
    if active not in STEPS:
        active = None

    recent_seen = {name: False for name in RECENT_DECISIONS}
    recent = policy.get("recent_decisions")
    if isinstance(recent, list):
        for row in recent[:32]:
            if not isinstance(row, dict):
                continue
            decision = row.get("decision")
            if decision in recent_seen:
                recent_seen[decision] = True

    # A manual Hanjuku start may be safely queued before the bot process exists.
    # In that state hanjuku_tactical legitimately has no chapter, so expose the
    # existing diagnostics collector's already-sanitized rotation booleans as
    # fixed tokens. This distinguishes "reservation is still waiting" from a
    # missing/active bot without leaking request ids, timestamps or free text.
    corners = diagnostics.get("corners") if isinstance(diagnostics.get("corners"), dict) else {}
    rotation = corners.get("corner_rotation") if isinstance(corners.get("corner_rotation"), dict) else {}
    rotation_status = rotation.get("status")
    if rotation_status not in ROTATION_STATUSES:
        rotation_status = "unknown"

    return {
        "status": status,
        "chapter": chapter,
        "age_sec": age_sec,
        "active": active,
        "orders": projected_orders,
        "recent": recent_seen,
        "rotation": {
            "status": rotation_status,
            "pending": _fixed_bool(rotation.get("pending")),
            "queued_manual": _fixed_bool(rotation.get("queued_manual")),
        },
    }


def reply(projected, env):
    sha = env.get("RUN_SHA", "")
    run = env.get("RUN_ID", "")
    attempt = env.get("RUN_ATTEMPT", "")
    source_comment = env.get("SOURCE_COMMENT_ID", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("invalid_reply_metadata")
    for value in (run, attempt, source_comment):
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
            raise ValueError("invalid_reply_metadata")

    orders = projected["orders"]
    rotation = projected["rotation"]
    lines = [
        "Hanjuku live boss status: success",
        f"SHA: `{sha}`",
        f"Chapter: `{projected['chapter'] if projected['chapter'] is not None else 'unknown'}`",
        f"Diagnostics: `{projected['status']}` / age `{projected['age_sec'] if projected['age_sec'] is not None else 'unknown'}s`",
        "Rotation: " + ", ".join((
            f"`status={rotation['status']}`",
            f"`pending={_bool_token(rotation['pending'])}`",
            f"`queued_manual={_bool_token(rotation['queued_manual'])}`",
        )),
        "Boss orders: " + ", ".join(f"`{step}={orders[step]}`" for step in STEPS),
        f"Active boss order: `{projected['active'] or 'none'}`",
        "Recent fixed signals: " + ", ".join(
            f"`{name}={'yes' if projected['recent'][name] else 'no'}`"
            for name in RECENT_DECISIONS
        ),
        f"Request: {BASE}/issues/1339#issuecomment-{source_comment}",
        f"Run: {BASE}/actions/runs/{run}/attempts/{attempt}",
    ]
    return {"body": "\n\n".join(lines)}


def main():
    try:
        payload = json.load(sys.stdin)
        projected = project(payload)
        print(json.dumps(reply(projected, os.environ), ensure_ascii=False))
    except Exception:
        print("Hanjuku live status projection rejected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())