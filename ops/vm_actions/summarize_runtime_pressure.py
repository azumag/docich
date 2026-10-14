#!/usr/bin/env python3
"""Add a fixed, sanitized AI rate-limit pressure signal to runtime summaries.

This helper intentionally leaves the existing runtime severity unchanged.  It
only appends ``ai_rate_limit_pressure=0|1`` using aggregate counters that are
already exposed by the owner-only diagnostics contract.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

MIN_ATTEMPTS = 10
MIN_RATE_LIMITS = 5
MIN_RATE_LIMIT_PERCENT = 30


def _load_base_module():
    script = Path(__file__).with_name("summarize_runtime.py")
    spec = importlib.util.spec_from_file_location("docich_summarize_runtime", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load summarize_runtime")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _non_negative_int(mapping, name):
    value = mapping.get(name, 0) if isinstance(mapping, dict) else 0
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def rate_limit_pressure(ai):
    """Return 1 only for a material 15-minute rate-limit window.

    The threshold is deliberately conservative: at least 10 attempts, at least
    5 rate-limit events, and at least 30% of attempts rate-limited.  This keeps
    isolated 429s from surfacing as pressure while making the repeatedly
    observed production windows visible without changing runtime behavior.
    """
    attempts = _non_negative_int(ai, "attempts_15m")
    rate_limits = _non_negative_int(ai, "rate_limits_15m")
    return int(
        attempts >= MIN_ATTEMPTS
        and rate_limits >= MIN_RATE_LIMITS
        and rate_limits * 100 >= attempts * MIN_RATE_LIMIT_PERCENT
    )


def summarize(data):
    base = _load_base_module()
    severity, summary = base.summarize(data)
    ai = data.get("ai") if isinstance(data, dict) else None
    pressure = rate_limit_pressure(ai)
    return severity, f"{summary},ai_rate_limit_pressure={pressure}"


def main(argv):
    if len(argv) != 2:
        print("usage: summarize_runtime_pressure.py <runtime-diagnostics.json>", file=sys.stderr)
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
