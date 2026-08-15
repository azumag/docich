#!/usr/bin/env python3
"""Verify that encoded H.264 contains ATSC A/53 registered user data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ATSC_A53_SIGNATURE = b"\xb5\x00\x31GA94\x03"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--minimum", type=int, default=1)
    args = parser.parse_args()

    payload = args.input.read_bytes()
    count = payload.count(ATSC_A53_SIGNATURE)
    result = {
        "input": str(args.input),
        "bytes": len(payload),
        "a53Payloads": count,
        "verified": count >= args.minimum,
    }
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
