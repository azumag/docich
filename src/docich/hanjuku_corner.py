"""Fixed owner-operated Hanjuku start through the common corner coordinator."""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_global


def start():
    root = Path(__file__).resolve().parents[2]
    config = load_global(root, root / 'config/docich.soren-live.toml')
    from .corner_rotation import CornerRotationManager
    # Durable next-slot priority: the timer finishes the current reservation
    # and uses the normal execution/program-slot/game-boundary gates.
    return CornerRotationManager(config).queue_manual('hanjuku-hero')


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    result = start()
    print(result)
    return 0 if result['status'] in {'completed', 'queued', 'waiting'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
