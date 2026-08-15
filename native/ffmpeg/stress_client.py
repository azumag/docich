#!/usr/bin/env python3
"""Exercise repeated caption lifecycles against a running docichcc filter."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from docich.captions import CaptionSocketClient  # noqa: E402


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


def timed(operation) -> float:
    started = time.monotonic()
    operation()
    return (time.monotonic() - started) * 1000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--max-p95-ms", type=float, default=500)
    args = parser.parse_args()
    if not 1 <= args.count <= 32:
        parser.error("--count must be between 1 and 32")

    client = CaptionSocketClient(args.socket, timeout=3)
    samples: dict[str, list[float]] = {
        "prepare": [],
        "commit": [],
        "clear": [],
    }
    for index in range(args.count):
        execution_id = f"stress-{index:02d}"
        text = f"Caption cycle {index + 1:02d} synchronized."
        samples["prepare"].append(
            timed(lambda: client.prepare(execution_id, 0, text))
        )
        samples["commit"].append(
            timed(lambda: client.commit(execution_id, 0))
        )
        samples["clear"].append(timed(lambda: client.clear(execution_id)))

    p95 = {key: round(percentile(values, 0.95), 3) for key, values in samples.items()}
    verified = all(value <= args.max_p95_ms for value in p95.values())
    print(
        json.dumps(
            {
                "cycles": args.count,
                "p95Ms": p95,
                "maximumP95Ms": args.max_p95_ms,
                "verified": verified,
            },
            separators=(",", ":"),
        )
    )
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
