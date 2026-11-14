#!/usr/bin/env python3
"""Render a fixed, secret-free live status for Hanjuku boss/field progress.

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
# Fixed tokens the diagnostics budget keeps beside a reduced tactical section.
TACTICAL_DETAIL_STATUSES = frozenset({"ok", "stale", "unavailable", "unsupported_chapter", "identity_changed"})
TACTICAL_DETAIL_REASONS = frozenset({
    "record_not_ready", "record_unbound", "identity_mismatch", "terminal",
    "unsupported_policy", "record_stale",
})
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
MARCH_STATES = frozenset({"active", "idle", "overdue", "parked"})
BOSS_TARGETS = frozenset({
    "ボス", "けっかい",
    "四季王城(北西)", "四季王城(北東)", "四季王城(南西)", "四季王城(南東)",
})
BOSS_SORTIE_STATES = frozenset({
    "pending", "launched_unconfirmed", "launched", "en_route", "overdue",
})
CHAPTER_BOSS_STEP = re.compile(r"([1-9]|1[0-2])-B[1-9][0-9]*\Z")
MONTH_VALUE = re.compile(r"\d{1,3}-\d{1,2}\Z")
PHASE_STATES = frozenset({
    "title", "transition", "battle", "name", "shop", "concert",
    "month_menu", "dialogue", "field_menu", "battle_intro", "field",
    "title_or_intro", "event",
})
FIELD_TOKENS = (
    "captured", "lost", "march", "overdue", "exhausted", "oldest_age_ticks",
    "boss_wins", "boss_sorties", "boss_sorties_overdue", "active_boss",
    "uncertain", "month", "phase", "unknown_generals", "generals_home",
    "castles_unread",
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


def _field_token(value):
    if value is None:
        return "unknown"
    if type(value) is bool:
        return "yes" if value else "no"
    if type(value) is int:
        return str(value)
    if isinstance(value, str) and (value in MARCH_STATES or value in PHASE_STATES
                                  or value == "unknown" or MONTH_VALUE.fullmatch(value)):
        return value
    return "unknown"


def _region(policy, key):
    """A declared state region's dict, read from the flat key or ``world``.

    The policy's declared keys (``sorties``, ``garrison``, ``boss_victories``)
    moved into the ``world`` region (docich#3193), so a reader that only looks
    at the flat key silently sees nothing: g1163 2026-11-09 15:50 the live
    status lost every sortie row this way and the 出撃チャート card emptied.
    """
    value = policy.get(key)
    if isinstance(value, dict):
        return value
    world = policy.get("world") if isinstance(policy.get("world"), dict) else {}
    value = world.get(key)
    return value if isinstance(value, dict) else {}


def _compact_field(policy, chapter, active_step):
    def bounded(value, high=64):
        return _bounded_int(value, 0, high)

    march = _region(policy, "march")
    march_state = march.get("state")
    if march_state not in MARCH_STATES:
        march_state = "unknown"
    overdue = march.get("overdue") if isinstance(march.get("overdue"), list) else []
    exhausted = march.get("exhausted") if isinstance(march.get("exhausted"), list) else []
    victories = _region(policy, "boss_victories")
    sorties = _region(policy, "sorties")
    boss_rows = [row for row in sorties.values()
                 if isinstance(row, dict)
                 and row.get("target") in BOSS_TARGETS
                 and row.get("status") in BOSS_SORTIE_STATES]
    active_boss = False
    if isinstance(active_step, str):
        active_boss = (
            active_step in STEPS
            or (chapter is not None and CHAPTER_BOSS_STEP.fullmatch(active_step)
                and active_step.startswith(f"{chapter}-B"))
            or (isinstance(sorties.get(active_step), dict)
                and sorties[active_step].get("target") in BOSS_TARGETS)
        )
    month = policy.get("month")
    if not (isinstance(month, str) and MONTH_VALUE.fullmatch(month)):
        month = None
    phase = policy.get("phase")
    if phase not in PHASE_STATES:
        phase = None
    garrison = _region(policy, "garrison")
    garrison_counts = [value for value in garrison.values() if type(value) is int]
    generals_home = (_bounded_int(sum(garrison_counts), 0, 10_000_000)
                     if garrison_counts else None)
    castles_unread = bounded(sum(1 for value in garrison.values() if value is None))
    return {
        "captured": bounded(policy.get("captured_count")),
        "lost": bounded(policy.get("castle_loss_count")),
        "month": month,
        "phase": phase,
        "unknown_generals": bounded(policy.get("unknown_generals")),
        "generals_home": generals_home,
        "castles_unread": castles_unread,
        "march": march_state,
        "overdue": bounded(len(overdue)),
        "exhausted": bounded(len(exhausted)),
        "oldest_age_ticks": _bounded_int(march.get("oldest_age_ticks"), 0, 10_000_000),
        "boss_wins": _bounded_int(victories.get("count"), 0, 64),
        "boss_sorties": bounded(len(boss_rows)),
        "boss_sorties_overdue": bounded(sum(1 for row in boss_rows
                                            if row.get("status") == "overdue")),
        "active_boss": _fixed_bool(active_boss),
        "uncertain": _fixed_bool(policy.get("uncertain")),
    }


def _fixed_hanjuku_pending(rotation):
    pending = _fixed_bool(rotation.get("manual_pending"))
    if pending is False:
        return False
    if pending is not True:
        return None
    corner = rotation.get("manual_pending_corner")
    if corner == "hanjuku-hero":
        return True
    # Only return a negative answer for a collector-projected fixed string.
    # Missing/non-string data stays unknown rather than becoming permission.
    return False if isinstance(corner, str) else None


def _fixed_pending_hanjuku(rotation):
    pending = _fixed_bool(rotation.get("pending"))
    if pending is False:
        return False
    corner = rotation.get("pending_corner")
    if corner == "hanjuku-hero":
        return True
    return False if isinstance(corner, str) else None


def _fixed_active_hanjuku(corners):
    game_switch = corners.get("game_switch")
    if not isinstance(game_switch, dict):
        return None
    active_game = game_switch.get("active_game")
    if active_game == "hanjuku-hero":
        return True
    return False if isinstance(active_game, str) else None


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
    raw_detail = tactical.get("detail_status")
    detail_status = raw_detail if raw_detail in TACTICAL_DETAIL_STATUSES else None
    raw_reason = tactical.get("detail_reason")
    detail_reason = raw_reason if raw_reason in TACTICAL_DETAIL_REASONS else None
    policy = tactical.get("policy") if isinstance(tactical.get("policy"), dict) else {}
    orders = policy.get("orders") if isinstance(policy.get("orders"), dict) else {}

    projected_orders = {}
    for step in STEPS:
        value = orders.get(step)
        projected_orders[step] = value if value in ORDER_STATES else "unknown"

    raw_active = policy.get("active")
    active = raw_active if raw_active in STEPS else None
    if (active is None and isinstance(raw_active, str) and chapter is not None
            and CHAPTER_BOSS_STEP.fullmatch(raw_active)
            and raw_active.startswith(f"{chapter}-B")):
        active = raw_active

    recent_seen = {name: False for name in RECENT_DECISIONS}
    recent = policy.get("recent_decisions")
    if isinstance(recent, list):
        for row in recent[:32]:
            if not isinstance(row, dict):
                continue
            decision = row.get("decision")
            if decision in recent_seen:
                recent_seen[decision] = True
    field = _compact_field(policy, chapter, raw_active)

    # A manual Hanjuku start may be safely queued before the bot process exists.
    # In that state hanjuku_tactical legitimately has no chapter, so expose the
    # existing diagnostics collector's already-sanitized fixed booleans.  The
    # manual_pending bit is distinct from pending/queued_manual: omitting it made
    # a stuck reservation look as if it had left the queue and started (#3279
    # follow-up).  active_hanjuku is derived as a boolean only; the active game
    # name itself never crosses this owner-only projection.
    corners = diagnostics.get("corners") if isinstance(diagnostics.get("corners"), dict) else {}
    rotation = corners.get("corner_rotation") if isinstance(corners.get("corner_rotation"), dict) else {}
    rotation_status = rotation.get("status")
    if rotation_status not in ROTATION_STATUSES:
        rotation_status = "unknown"
    manual_pending = _fixed_bool(rotation.get("manual_pending"))

    return {
        "status": status,
        "chapter": chapter,
        "age_sec": age_sec,
        "detail_status": detail_status,
        "detail_reason": detail_reason,
        "active": active,
        "orders": projected_orders,
        "field": field,
        "recent": recent_seen,
        "rotation": {
            "status": rotation_status,
            "pending": _fixed_bool(rotation.get("pending")),
            "queued_manual": _fixed_bool(rotation.get("queued_manual")),
            "manual_pending": manual_pending,
            "manual_pending_hanjuku": _fixed_hanjuku_pending(rotation),
            "pending_hanjuku": _fixed_pending_hanjuku(rotation),
            "error_kind": (rotation.get("error_kind")
                           if isinstance(rotation.get("error_kind"), str)
                           else "unknown"),
            "active_hanjuku": _fixed_active_hanjuku(corners),
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
    field = projected["field"]
    rotation = projected["rotation"]
    chapter = "`unknown`" if projected['chapter'] is None else f"`{projected['chapter']}`"
    if projected.get('detail_status') == 'stale' and projected['chapter'] is not None:
        # The last written position is shown for a stalled game; never let it
        # read as the current one.
        chapter += " (last record; stale)"
    lines = [
        "Hanjuku live boss status: success",
        f"SHA: `{sha}`",
        f"Chapter: {chapter}",
        "Diagnostics: " + " ".join((
            f"`{projected['status']}`",
            f"`detail={projected['detail_status']}`" if projected.get('detail_status') else "`detail=none`",
            f"`reason={projected['detail_reason']}`" if projected.get('detail_reason') else "`reason=none`",
            f"/ age `{projected['age_sec'] if projected['age_sec'] is not None else 'unknown'}s`",
        )),
        "Rotation: " + ", ".join((
            f"`status={rotation['status']}`",
            f"`pending={_bool_token(rotation['pending'])}`",
            f"`queued_manual={_bool_token(rotation['queued_manual'])}`",
            f"`manual_pending={_bool_token(rotation['manual_pending'])}`",
            f"`manual_pending_hanjuku={_bool_token(rotation['manual_pending_hanjuku'])}`",
            f"`pending_hanjuku={_bool_token(rotation['pending_hanjuku'])}`",
            f"`error_kind={rotation['error_kind']}`",
            f"`active_hanjuku={_bool_token(rotation['active_hanjuku'])}`",
        )),
        "Boss orders: " + ", ".join(f"`{step}={orders[step]}`" for step in STEPS),
        f"Active boss order: `{projected['active'] or 'none'}`",
        "Field: " + ", ".join(
            f"`{name}={_field_token(field.get(name))}`" for name in FIELD_TOKENS
        ),
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
