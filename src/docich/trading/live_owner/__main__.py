"""``python -m docich.trading.live_owner``: run the owner-only CLI and exit with its code."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
