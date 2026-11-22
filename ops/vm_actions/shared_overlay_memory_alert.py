#!/usr/bin/env python3
"""Project a fixed, secret-free shared-overlay renderer memory alert (#5112).

Input is the owner-only sanitized diagnostics envelope. Only the fixed owner
label and a finite renderer RSS number are accepted; missing or incomplete
process scans are unknown and never interpreted as recovery.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
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



def _gh(args, *, run=subprocess.run):
    completed = run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return getattr(completed, "stdout", "")


def update_alert(result, *, repo, workflow_url, run=subprocess.run):
    """Create, refresh or close one deduplicated owner-visible alert."""
    if result.get("available") != 1:
        return "unknown"
    alerts = json.loads(_gh(
        ["issue", "list", "--repo", repo, "--state", "open", "--limit", "100",
         "--json", "number,title"],
        run=run,
    ))
    if not isinstance(alerts, list):
        raise ValueError("GitHub issue list is not an array")
    known = [
        item for item in alerts
        if isinstance(item, dict)
        and type(item.get("number")) is int
        and item.get("title") in {
            "[VM shared overlay] WARN renderer RSS alert",
            "[VM shared overlay] CRITICAL renderer RSS alert",
        }
    ]
    severity = result["severity"]
    if severity == "ok":
        for item in known:
            _gh(
                ["issue", "close", str(item["number"]), "--repo", repo,
                 "--comment", "Shared overlay renderer RSS recovered below 4096 MiB."],
                run=run,
            )
        return "closed" if known else "ok"
    if severity not in {"warn", "critical"}:
        return "unknown"

    upper = severity.upper()
    title = f"[VM shared overlay] {upper} renderer RSS alert"
    rss = result["renderer_rss_mb"]
    body = (
        "Shared overlay Chromium renderer memory crossed the fixed read-only monitoring threshold.\\n\\n"
        f"- Severity: {upper}\\n"
        f"- Renderer RSS: {rss:.1f} MiB\\n"
        f"- Warning threshold: {WARN_RENDERER_RSS_MB} MiB\\n"
        f"- Critical threshold: {CRITICAL_RENDERER_RSS_MB} MiB\\n"
        f"- Workflow: {workflow_url}\\n\\n"
        "The measurement is a complete /proc scan aggregated under the fixed owner label "
        "soren-shared-overlay. Only the renderer RSS total is published; PIDs, command lines, "
        "URLs, paths, process details, raw diagnostics and command output are never published. "
        "This monitor is read-only and does not restart services."
    )
    matching = [item for item in known if item["title"] == title]
    for item in known:
        if matching and item["number"] == matching[0]["number"]:
            continue
        _gh(
            ["issue", "close", str(item["number"]), "--repo", repo,
             "--comment", f"Shared overlay renderer RSS severity changed to {upper}."],
            run=run,
        )
    if matching:
        _gh(
            ["issue", "edit", str(matching[0]["number"]), "--repo", repo, "--body", body],
            run=run,
        )
        return "updated"
    _gh(["issue", "create", "--repo", repo, "--title", title, "--body", body], run=run)
    return "created"

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    manage = "--manage" in argv
    argv = [item for item in argv if item != "--manage"]
    if len(argv) != 1:
        print("usage: shared_overlay_memory_alert.py <runtime-diagnostics.json> [--manage]", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as handle:
            wrapper = json.load(handle)
    except (OSError, ValueError, UnicodeError):
        wrapper = None
    result = assess(wrapper)
    if manage:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not repo or result["available"] != 1:
            print("Shared overlay renderer memory is unknown; existing alerts are left unchanged.")
            return 0
        workflow_url = (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
        )
        action = update_alert(result, repo=repo, workflow_url=workflow_url)
        print(f"Shared overlay renderer RSS: {result['severity']} ({action}).")
        return 0
    print(f"available={result['available']}")
    print(f"severity={result['severity']}")
    if result["renderer_rss_mb"] is not None:
        print(f"renderer_rss_mb={result['renderer_rss_mb']}")
    print(f"warn_threshold_mb={WARN_RENDERER_RSS_MB}")
    print(f"critical_threshold_mb={CRITICAL_RENDERER_RSS_MB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
