"""Read only public plan handle and explicit acknowledgement from dispatch JSON."""
import json
import os
from pathlib import Path
import re
import sys

HANDLE = re.compile(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,3}\Z")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def read_plan(env):
    try:
        with Path(env["GITHUB_EVENT_PATH"]).open("rb") as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        event = json.loads(raw, object_pairs_hook=_unique)
        values = event["inputs"]
        handle = values.get("plan_handle", "")
        acknowledgement = values.get("unknown_resources", "not-acknowledged")
        if (not isinstance(handle, str) or handle and not HANDLE.fullmatch(handle)
                or not isinstance(acknowledgement, str)
                or acknowledgement not in {"not-acknowledged", "acknowledged"}):
            raise ValueError()
        return handle, acknowledgement
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise ValueError("queue admin input unavailable") from None


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        handle, acknowledgement = read_plan(os.environ)
        if args == ["handle"]:
            print(handle)
        elif args == ["acknowledgement"]:
            print(acknowledgement)
        else:
            raise ValueError()
    except ValueError:
        print("queue admin input unavailable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
