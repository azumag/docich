#!/usr/bin/env python3
"""Wait for the fixed local Moomoo OpenD endpoint to speak the quote protocol.

No prices, account identifiers, provider errors or credentials are printed.  The
caller receives only exit status: 0 when the existing sanitized quote probe can
reach OpenD, 1 on timeout, 2 for an invalid timeout.
"""
from __future__ import annotations

import argparse
import time

from docich.trading.markets.provider_probe import probe_moomoo


def wait_ready(timeout_s: int) -> bool:
    if type(timeout_s) is not int or not 1 <= timeout_s <= 60:
        raise ValueError("timeout out of bounds")
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            result = probe_moomoo(["JP.7203"], host="127.0.0.1", port=11111, now=time.time())
            if result.get("opend_reachable") is True:
                return True
        except Exception:
            # Startup is intentionally fail-closed.  Do not expose provider
            # exception text through systemd/GitHub logs.
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(1.0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        return 0 if wait_ready(args.timeout) else 1
    except ValueError:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
