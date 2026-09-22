"""Fixed owner-operated Hanjuku start through the common corner coordinator."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from .config import load_global
from .retro_corner import RetroCornerManager, load_retro_corner_config


def start():
    root = Path(__file__).resolve().parents[2]
    config = load_global(root, root / 'config/docich.soren-live.toml')
    manager = RetroCornerManager(
        config,
        config=replace(load_retro_corner_config(config), games=['hanjuku-hero']),
    )
    # start() retains the common reservation, program-slot lock, eligibility,
    # pause, cooldown and current-game boundary checks. Never call _start_direct.
    return manager.start()


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    result = start()
    print(result)
    return 0 if result.status in {'completed', 'queued', 'waiting'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
