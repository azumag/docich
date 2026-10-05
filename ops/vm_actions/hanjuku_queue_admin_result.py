"""Only generic outcome from a matching withheld gateway envelope is public."""
import json
import re
import sys


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError()
        out[key] = value
    return out


def public_result(raw, mode, sha, gateway_rc):
    try:
        envelope = json.loads(raw, object_pairs_hook=_unique)
        if (mode not in {"check", "execute"} or not isinstance(sha, str)
                or not re.fullmatch(r"[0-9a-f]{40}", sha) or not isinstance(envelope, dict)
                or envelope.get("status") != "executed" or envelope.get("sha") != sha
                or envelope.get("output") != "withheld" or type(envelope.get("exit_code")) is not int
                or type(gateway_rc) is not int or envelope["exit_code"] != gateway_rc
                or not 0 <= gateway_rc <= 254):
            raise ValueError()
        if gateway_rc == 0:
            return {"status": "completed", "operation": mode}, 0
    except (ValueError, TypeError, AttributeError, RecursionError):
        pass
    return {"status": "failed", "operation": mode if mode in {"check", "execute"} else "unknown"}, 1


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        raw, mode, sha, status = args
        rc = int(status)
    except (ValueError, TypeError):
        raw, mode, sha, rc = "", "", "", -1
    result, code = public_result(raw, mode, sha, rc)
    print(json.dumps(result, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
