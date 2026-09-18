#!/usr/bin/env python3
"""Output-free probe for the deployed source diagnostics path.

This helper is intentionally narrow: it runs the current checkout's
``gateway.diagnostics_result`` against the fixed production registration and
returns only a bounded exit code.  It never prints the diagnostics payload,
exception text, configuration contents, hashes, paths from evidence, or other
runtime data.  The owner-only evidence workflow maps these exit codes to fixed
public reason strings.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ops.vm_actions import gateway


CONFIG_PATH = Path("/etc/azumag-vm-ops.json")

# Fixed vocabulary for ValueErrors raised by diagnostics_result().  Keep the
# codes below 64 and away from the generic shell/SSH exit statuses we care
# about.  The exception string is used only as an in-process lookup key and is
# never emitted.
_REASON_EXIT = {
    "diagnostics is production-only": 20,
    "diagnostics collector missing": 21,
    "diagnostics collector verification failed": 22,
    "diagnostics collector drift": 23,
    "diagnostics projection missing": 24,
    "diagnostics collector failed": 25,
    "diagnostics output invalid": 26,
    "diagnostics output too deep": 27,
    "diagnostics output has unsupported type": 28,
    "diagnostics output too large": 29,
}
UNKNOWN_VALUE_ERROR_EXIT = 30
INTERNAL_ERROR_EXIT = 31


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not gateway.SHA_RE.fullmatch(argv[1]):
        return 2
    try:
        cfg = gateway.load_config(CONFIG_PATH)
        gateway.diagnostics_result(cfg, "docich", "production", argv[1])
    except ValueError as exc:
        return _REASON_EXIT.get(str(exc), UNKNOWN_VALUE_ERROR_EXIT)
    except Exception:
        return INTERNAL_ERROR_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
