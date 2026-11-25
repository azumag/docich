"""Safe, bounded summary of VM diagnostics section byte accounting.

Only fixed-shape section names and non-negative byte counts are returned.
No section values or arbitrary diagnostics text cross into GitHub issue bodies.
"""
import re

_SECTION_NAME = re.compile(r"[A-Za-z0-9_]{1,64}\Z")
_MAX_REPORTED_BYTES = 10_000_000
_MAX_TOP_SECTIONS = 5


def _safe_bytes(value):
    if type(value) is int and 0 <= value <= _MAX_REPORTED_BYTES:
        return value
    return 0


def summarize_section_bytes(document):
    """Return a one-line issue-safe summary and Markdown summary lines."""
    diagnostics = document.get("diagnostics") if isinstance(document, dict) else None
    meta = diagnostics.get("meta") if isinstance(diagnostics, dict) else None
    accounting = meta.get("section_bytes") if isinstance(meta, dict) else None
    top = accounting.get("top") if isinstance(accounting, dict) else None
    if not isinstance(top, dict) or not top:
        return "unavailable", ["- section_bytes: unavailable"]

    total = _safe_bytes(accounting.get("total"))
    others = _safe_bytes(accounting.get("others"))
    ranked = sorted(
        top.items(),
        key=lambda item: _safe_bytes(item[1]),
        reverse=True,
    )[:_MAX_TOP_SECTIONS]

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
