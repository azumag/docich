"""Allow `python3 -m docich`."""
from __future__ import annotations

import sys


def _retro_corner_argv(argv: list[str]) -> list[str] | None:
    if argv and argv[0] == "retro-corner":
        return argv[1:]
    if len(argv) >= 3 and argv[0] == "--config" and argv[2] == "retro-corner":
        return ["--config", argv[1], *argv[3:]]
    if len(argv) >= 2 and argv[0].startswith("--config=") and argv[1] == "retro-corner":
        return [argv[0], *argv[2:]]
    return None


paper_args = [arg.replace("paper-corner", "retro-corner") if arg == "paper-corner" else arg for arg in sys.argv[1:]]
if "paper-corner" in sys.argv[1:]:
    paper_argv = _retro_corner_argv(paper_args)
    if paper_argv is not None:
        from .paper_corner import main as paper_main
        sys.exit(paper_main(paper_argv))

retro_argv = _retro_corner_argv(sys.argv[1:])
if retro_argv is not None:
    from .retro_corner import main as retro_corner_main

    sys.exit(retro_corner_main(retro_argv))

from .cli import main

sys.exit(main())
