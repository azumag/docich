#!/usr/bin/env python3
"""Render the public runtime-health summary from sanitized VM diagnostics.

The diagnostics payload is already bounded and redacted on the VM. This helper
runs on the GitHub Actions runner and deliberately emits only counts, booleans,
and fixed category names. It never emits provider/model identifiers, paths,
prompt text, or error previews.
"""
from collections import Counter
import json
import re
import sys

CAUSES = (
    "rate_limit",
    "timeout",
    "auth_policy",
    "provider_server",
    "model_unavailable",
    "invalid_output",
    "other",
)


def _integer(mapping, name):
    value = mapping.get(name, 0) if isinstance(mapping, dict) else 0
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _nlist(mapping, name):
    value = mapping.get(name) if isinstance(mapping, dict) else None
    return len(value) if isinstance(value, list) else 0


def _failure_cause(event):
    """Map one already-redacted recent failure event to a fixed enum."""
    if not isinstance(event, dict) or event.get("event") != "fail":
        return None
    rc = str(event.get("rc") or "").lower()
    preview = str(event.get("error_preview") or "").lower()
    text = f"{rc} {preview}"

    if rc == "79" or re.search(r"\b429\b|rate[ _-]?limit|quota", text):
        return "rate_limit"
    if re.search(r"timed? out|timeout|deadline exceeded", text):
        return "timeout"
    if re.search(r"\b401\b|\b403\b|unauthori[sz]ed|forbidden|permission denied|restricted.?model", text):
        return "auth_policy"
    if re.search(r"\b50[0-4]\b|server error|internal server|bad gateway|service unavailable|gateway timeout", text):
        return "provider_server"
    if re.search(r"\b404\b|model not found|unknown model|model unavailable|model is not available", text):
        return "model_unavailable"
    if re.search(r"validation|validator|empty output|output empty|too short|provider error text|invalid output", text):
        return "invalid_output"
    return "other"


def summarize(data):
    if not isinstance(data, dict):
        raise ValueError("diagnostics must be an object")
    severity = data.get("status")
    if severity not in {"ok", "warn", "critical"}:
        raise ValueError("invalid diagnostics severity")

    workers = data.get("workers") or {}
    queues = data.get("queues") or {}
    ai = data.get("ai") or {}
    improvement = data.get("improvement") or {}

    recent = ai.get("recent_events")
    counts = Counter()
    if isinstance(recent, list):
        for event in recent:
            cause = _failure_cause(event)
            if cause is not None:
                counts[cause] += 1

    sampled = sum(counts.values())
    parts = [
        f"required_down={_nlist(workers, 'required_down')}",
        f"required_stale={_nlist(workers, 'required_stale')}",
        f"paused={_nlist(workers, 'paused')}",
        f"duplicates={_nlist(workers, 'duplicates')}",
        f"zombies={_nlist(workers, 'zombies')}",
        f"stale_pid_files={_nlist(workers, 'stale_pid_files')}",
        f"unregistered={_nlist(workers, 'unregistered')}",
        f"stale_locks={_integer(queues, 'stale_locks')}",
        f"queue_giveups_15m={_integer(queues, 'queue_giveups_15m')}",
        f"ai_attempts_15m={_integer(ai, 'attempts_15m')}",
        f"ai_successes_15m={_integer(ai, 'successes')}",
        f"ai_failures_15m={_integer(ai, 'failures_15m')}",
        f"ai_rate_limits_15m={_integer(ai, 'rate_limits_15m')}",
        f"ai_fallbacks_15m={_integer(ai, 'fallbacks_15m')}",
        f"ai_all_failed_15m={_integer(ai, 'all_failed_15m')}",
        f"ai_recent_fail_sampled={sampled}",
    ]
    parts.extend(f"ai_recent_fail_{cause}={counts[cause]}" for cause in CAUSES)
    parts.extend(
        [
            f"improvement_stale={int(improvement.get('stale') is True)}",
            f"retry_pending={int(improvement.get('retry_pending') is True)}",
        ]
    )
    return severity, ",".join(parts)


def main(argv):
    if len(argv) != 2:
        print("usage: summarize_runtime.py <runtime-diagnostics.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        wrapper = json.load(handle)
    if wrapper.get("status") != "diagnosed" or not isinstance(wrapper.get("diagnostics"), dict):
        raise SystemExit("invalid diagnostics envelope")
    try:
        severity, summary = summarize(wrapper["diagnostics"])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"severity={severity}")
    print(f"summary={summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
