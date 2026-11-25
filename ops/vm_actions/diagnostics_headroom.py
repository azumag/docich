"""Safe, bounded summary of VM diagnostics section byte accounting.

Only fixed-shape section names and non-negative byte counts are returned.
No section values or arbitrary diagnostics text cross into GitHub issue bodies.
"""
import json
import re

_SECTION_NAME = re.compile(r"[A-Za-z0-9_]{1,64}\Z")
_MAX_REPORTED_BYTES = 10_000_000
_MAX_TOP_SECTIONS = 10


def _safe_bytes(value):
    if type(value) is int and 0 <= value <= _MAX_REPORTED_BYTES:
        return value
    return 0


def summarize_section_bytes(document):
    """Return the ten largest gateway-visible sections, without their values.

    Recompute sizes from the sanitized document returned by the gateway rather
    than relying on the collector's bounded top-five accounting map. This makes
    the alert's "others" bucket useful when several medium-sized sections
    collectively dominate the envelope. Each size includes the JSON key and
    value; the total excludes only outer braces and commas.
    """
    diagnostics = document.get("diagnostics") if isinstance(document, dict) else None
    if not isinstance(diagnostics, dict) or not diagnostics:
        return "unavailable", ["- section_bytes: unavailable"]

    byte_sizes = {}
    for name, value in diagnostics.items():
        try:
            entry = json.dumps(name, ensure_ascii=True) + ":" + json.dumps(
                value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
            size = len(entry.encode("utf-8"))
        except (TypeError, ValueError):
            size = 0
        byte_sizes[name] = _safe_bytes(size)

    ranked = sorted(
        byte_sizes.items(),
        key=lambda item: _safe_bytes(item[1]),
        reverse=True,
    )[:_MAX_TOP_SECTIONS]
    total = min(_MAX_REPORTED_BYTES, sum(byte_sizes.values()))
    top_total = sum(_safe_bytes(size) for _, size in ranked)
    others = max(0, total - top_total)

    compact_parts = []
    lines = ["- section_bytes:"]
    for name, raw_size in ranked:
        safe_name = name if isinstance(name, str) and _SECTION_NAME.fullmatch(name) else "?"
        size = _safe_bytes(raw_size)
        percent = (size * 100) // total if total > 0 else 0
        compact_parts.append(f"{safe_name}={size}B ({percent}%)")
        lines.append(f"  - {safe_name}: {size} B ({percent}%)")
    compact_parts.extend((f"others={others}B", f"total={total}B"))
    lines.extend((f"  - others: {others} B", f"  - total: {total} B"))
    return "; ".join(compact_parts), lines
