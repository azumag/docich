#!/usr/bin/env python3
"""Project a fixed, secret-free shared-overlay renderer memory alert (#5112).

Input is the owner-only sanitized diagnostics envelope. Only the fixed owner
label and a finite renderer RSS number are accepted; missing or incomplete
process scans are unknown and never interpreted as recovery.
"""
from __future__ import annotations

import json
import math
import sys

WARN_RENDERER_RSS_MB = 4 * 1024
CRITICAL_RENDERER_RSS_MB = 8 * 1024


def assess(wrapper):
    if not isinstance(wrapper, dict) or wrapper.get("status") != "diagnosed":
        return {"available": 0, "severity": "unknown", "renderer_rss_mb": None}
    diagnostics = wrapper.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return {"available": 0, "severity": "unknown", "renderer_rss_mb": None}
    browsers = diagnostics.get("browser_processes")
    if (not isinstance(browsers, dict)
            or browsers.get("status") != "ok"
            or type(browsers.get("scan_complete")) is not int
            or browsers.get("scan_complete") != 1):
        return {"available": 0, "severity": "unknown", "renderer_rss_mb": None}
    owners = browsers.get("by_owner")
    shared = owners.get("soren-shared-overlay") if isinstance(owners, dict) else None
    if not isinstance(shared, dict):
        return {"available": 0, "severity": "unknown", "renderer_rss_mb": None}
    value = shared.get("renderer_rss_mb")
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        return {"available": 0, "severity": "unknown", "renderer_rss_mb": None}
    severity = (
        "critical" if value >= CRITICAL_RENDERER_RSS_MB
        else "warn" if value >= WARN_RENDERER_RSS_MB
        else "ok"
    )
    return {
        "available": 1,
        "severity": severity,
        "renderer_rss_mb": round(float(value), 1),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: shared_overlay_memory_alert.py <runtime-diagnostics.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as handle:
            wrapper = json.load(handle)
    except (OSError, ValueError, UnicodeError):
        wrapper = None
    result = assess(wrapper)
    print(f"available={result['available']}")
    print(f"severity={result['severity']}")
    if result["renderer_rss_mb"] is not None:
        print(f"renderer_rss_mb={result['renderer_rss_mb']}")
    print(f"warn_threshold_mb={WARN_RENDERER_RSS_MB}")
    print(f"critical_threshold_mb={CRITICAL_RENDERER_RSS_MB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
