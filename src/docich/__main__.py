"""Allow `python3 -m docich`."""
from __future__ import annotations

import sys

from .cli import main

sys.exit(main())
