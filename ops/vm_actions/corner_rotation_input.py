"""Read the private dispatch reservation without workflow expression expansion.

CLI stdout is for a quoted shell capture only, never a step output or env file.
"""
import json
import os
from pathlib import Path
import re
import sys


def read_expected(env):
    try:
        path = env["GITHUB_EVENT_PATH"]
        with Path(path).open("rb") as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("oversized event")
        event = json.loads(raw)
        expected = event["inputs"].get("expected_reservation", "")
        if not isinstance(expected, str):
            raise ValueError("invalid input type")
        return expected
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise ValueError("reservation input unavailable") from None


def main():
    try:
        expected = read_expected(os.environ)
        if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("invalid reservation")
    except ValueError:
        print("reservation input unavailable", file=sys.stderr)
        return 1
    print(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
