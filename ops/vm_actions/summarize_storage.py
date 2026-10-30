#!/usr/bin/env python3
"""Render fixed-category storage metrics from sanitized production diagnostics."""
import json
import sys

DB_FAMILIES = ("opencode_default", "opencode_worker")
OPENCODE_CALLER_BUCKETS = (
    "radio_prepass",
    "radio_main",
    "comment",
    "improvement",
    "soren91",
    "probe",
    "other",
)
OPENCODE_ATTRIBUTION_METRICS = (
    "sessions",
    "messages",
    "message_data_chars",
    "message_max_chars",
    "parts",
    "part_data_chars",
    "part_max_chars",
    "events",
    "event_data_chars",
    "event_max_chars",
)
OPENCODE_COVERAGE_METRICS = (
    "all_sessions",
    "attributed_sessions",
    "unattributed_sessions",
    "unattributed_latest_age_sec",
    "unattributed_15m_sessions",
    "unattributed_1h_sessions",
    "unattributed_2h_sessions",
    "unattributed_6h_sessions",
)

TREE_CATEGORIES = (
    "opencode_default_total",
    "opencode_worker_total",
    "soren_logs",
    "soren_tmp",
    "say_queue",
    "browser_profile",
    "strategy_archive",
    "docich_git",
    "soren_live_git",
    "soren_persist_git",
    "voicevox_root",
    "voicevox_archive",
)

# Version 2 fixed roots (azumag/docich#2583): the share of a decline window
# that version 1 could not attribute. Same key shape as TREE_CATEGORIES, same
# overlap rule (system_log contains system_journal, docich_git contains the
# object subcategories). A version 1 payload (old collector) simply does not
# carry them, so nothing is invented and the shared incomplete flag stays
# untouched until the reviewed collector is deployed.
V2_TREE_CATEGORIES = (
    "gateway_state",
    "alt_checkout_tmp_sync",
    "alt_checkout_soren_src",
    "system_journal",
    "system_log",
    "swapfile",
    "sys_tmp",
    "docich_git_objects",
    "docich_git_objects_pack",
)

# Subtrees of gateway_state (azumag/docich#365). Optional: emitted only when
# the collector publishes them, and never charged to the shared incomplete
# flag, so a collector deployed before this split cannot look incomplete.
GATEWAY_SUBTREE_CATEGORIES = (
    "gateway_bundles",
    "gateway_releases",
)

SUPPORTED_BREAKDOWN_VERSIONS = (1, 2)

# Fixed on-disk formats of the strategy permanent archive. The split is what
# makes "growth stays bounded" checkable without reading any strategy content.
STRATEGY_ARCHIVE_FORMATS = ("plain", "compressed")


def _uint(mapping, key):
    value = mapping.get(key) if isinstance(mapping, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def render(data):
    if not isinstance(data, dict):
        return 0, 1, "storage_breakdown_unavailable=1"
    breakdown = data.get("storage_breakdown")
    if not isinstance(breakdown, dict) or breakdown.get("version") not in SUPPORTED_BREAKDOWN_VERSIONS:
        return 0, 1, "storage_breakdown_unavailable=1"

    parts = ["overlap=1"]
    incomplete = False
    for family in DB_FAMILIES:
        item = breakdown.get(family)
        complete = isinstance(item, dict) and item.get("scan_complete") is True
        incomplete = incomplete or not complete
        parts.append(f"{family}_complete={int(complete)}")
        for suffix in ("db", "wal", "shm"):
            leaf = item.get(suffix) if isinstance(item, dict) else None
            leaf_complete = isinstance(leaf, dict) and leaf.get("scan_complete") is True
            incomplete = incomplete or not leaf_complete
            parts.append(f"{family}_{suffix}_bytes={_uint(leaf, 'allocated_bytes')}")
        # The db/wal/shm family sum. It must not flatten to
        # `{family}_total_bytes`: the `<family>_total` tree category below
        # already publishes that key, so the two different byte totals would
        # collide in the flat `key=value` context and make category-wise
        # attribution ambiguous (azumag/docich#866).
        parts.append(f"{family}_family_bytes={_uint(item, 'allocated_bytes')}")

    for category in TREE_CATEGORIES:
        item = breakdown.get(category)
        complete = isinstance(item, dict) and item.get("scan_complete") is True
        incomplete = incomplete or not complete
        parts.append(f"{category}_complete={int(complete)}")
        parts.append(f"{category}_bytes={_uint(item, 'allocated_bytes')}")
        parts.append(f"{category}_count={_uint(item, 'count')}")

    # Version 2 fixed roots (azumag/docich#2583). Version 1 payloads never
    # reach this block, so an old collector cannot look incomplete here.
    if breakdown.get("version") == 2:
        for category in V2_TREE_CATEGORIES:
            item = breakdown.get(category)
            complete = isinstance(item, dict) and item.get("scan_complete") is True
            incomplete = incomplete or not complete
            parts.append(f"{category}_complete={int(complete)}")
            parts.append(f"{category}_bytes={_uint(item, 'allocated_bytes')}")
            parts.append(f"{category}_count={_uint(item, 'count')}")

    if breakdown.get("version") == 2:
        for category in GATEWAY_SUBTREE_CATEGORIES:
            item = breakdown.get(category)
            if not isinstance(item, dict):
                continue
            parts.append(f"{category}_complete={int(item.get('scan_complete') is True)}")
            parts.append(f"{category}_bytes={_uint(item, 'allocated_bytes')}")
            parts.append(f"{category}_count={_uint(item, 'count')}")

    formats = breakdown.get("strategy_archive_formats")
    formats = formats if isinstance(formats, dict) else {}
    formats_complete = formats.get("scan_complete") is True
    incomplete = incomplete or not formats_complete
    parts.append(f"strategy_archive_formats_complete={int(formats_complete)}")
    buckets = formats.get("formats") if formats_complete else None
    buckets = buckets if isinstance(buckets, dict) else {}
    for name in STRATEGY_ARCHIVE_FORMATS:
        bucket = buckets.get(name)
        bucket = bucket if isinstance(bucket, dict) else {}
        parts.append(
            f"strategy_archive_{name}_count="
            f"{_uint(bucket, 'count') if formats_complete else 0}"
        )
        parts.append(
            f"strategy_archive_{name}_bytes="
            f"{_uint(bucket, 'allocated_bytes') if formats_complete else 0}"
        )
    if formats_complete:
        plain_bytes = _uint(buckets.get("plain"), "allocated_bytes")
        compressed_bytes = _uint(buckets.get("compressed"), "allocated_bytes")
        total_bytes = plain_bytes + compressed_bytes
        share = (compressed_bytes * 100) // total_bytes if total_bytes else 0
    else:
        share = 0
    parts.append(f"strategy_archive_compressed_share_pct={share}")

    attribution = data.get("opencode_session_attribution")
    attribution_complete = (
        isinstance(attribution, dict)
        and attribution.get("version") == 1
        and attribution.get("scan_complete") is True
        and attribution.get("schema_supported") is True
        and isinstance(attribution.get("buckets"), dict)
    )
    parts.append(f"opencode_attr_complete={int(attribution_complete)}")
    parts.append(
        f"opencode_attr_window_sec={_uint(attribution, 'window_sec') if attribution_complete else 0}"
    )
    buckets = attribution.get("buckets") if attribution_complete else {}
    for bucket in OPENCODE_CALLER_BUCKETS:
        item = buckets.get(bucket) if isinstance(buckets, dict) else None
        item = item if isinstance(item, dict) else {}
        for metric in OPENCODE_ATTRIBUTION_METRICS:
            parts.append(
                f"opencode_attr_{bucket}_{metric}="
                f"{_uint(item, metric) if attribution_complete else 0}"
            )

    coverage = attribution.get("coverage") if attribution_complete else None
    coverage_complete = (
        isinstance(coverage, dict)
        and coverage.get("scan_complete") is True
    )
    parts.append(f"opencode_attr_coverage_complete={int(coverage_complete)}")
    for metric in OPENCODE_COVERAGE_METRICS:
        parts.append(
            f"opencode_attr_{metric}="
            f"{_uint(coverage, metric) if coverage_complete else 0}"
        )

    retention = data.get("opencode_retention") or {}
    for label in ("attempt", "default", "worker"):
        item = retention.get(label) or {}
        state = item.get("status")
        if state not in {"running", "completed", "gate_timeout", "disabled", "deferred", "failed"}:
            state = "unknown"
        parts.append(f"retention_{label}={state}")
    return 1, int(incomplete), ",".join(parts)


def main(argv):
    if len(argv) != 2:
        print("usage: summarize_storage.py <runtime-diagnostics.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as handle:
            wrapper = json.load(handle)
    except (OSError, ValueError):
        print("storage_breakdown_available=0")
        print("storage_breakdown_incomplete=1")
        print("storage_context=storage_breakdown_unavailable=1")
        return 0

    diagnostics = wrapper.get("diagnostics") if isinstance(wrapper, dict) else None
    available, incomplete, context = render(diagnostics)
    print(f"storage_breakdown_available={available}")
    print(f"storage_breakdown_incomplete={incomplete}")
    print(f"storage_context={context}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
