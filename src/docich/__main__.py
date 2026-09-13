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


def _direct_paper_argv(command: str, argv: list[str]) -> list[str] | None:
    """Route isolated PAPER tools without teaching the legacy CLI about them.

    These commands intentionally require their own explicit ``--trading-dir``
    instead of inheriting the global config parser. That keeps deployment of
    the code from enabling either worker and avoids any live-trading surface.
    """
    if argv and argv[0] == command:
        return argv[1:]
    return None


free_strategy_argv = _direct_paper_argv("free-strategy", sys.argv[1:])
if free_strategy_argv is not None:
    from .trading.free_strategy.cli import main as free_strategy_main

    sys.exit(free_strategy_main(free_strategy_argv))

free_worker_argv = _direct_paper_argv("free-strategy-worker", sys.argv[1:])
if free_worker_argv is not None:
    from .trading.free_strategy.worker import main as free_worker_main

    sys.exit(free_worker_main(free_worker_argv))

from .paper_improve_retry import is_paper_improve_invocation

if is_paper_improve_invocation(sys.argv[1:]):
    from .paper_improve_retry import main as paper_improve_main

    sys.exit(paper_improve_main(sys.argv[1:]))

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


def _soren91_corner_argv(argv: list[str]) -> list[str] | None:
    if argv and argv[0] == "soren91-corner":
        return argv[1:]
    if len(argv) >= 3 and argv[0] == "--config" and argv[2] == "soren91-corner":
        return ["--config", argv[1], *argv[3:]]
    if len(argv) >= 2 and argv[0].startswith("--config=") and argv[1] == "soren91-corner":
        return [argv[0], *argv[2:]]
    return None


soren91_argv = _soren91_corner_argv(sys.argv[1:])
if soren91_argv is not None:
    from .soren91_corner import main as soren91_corner_main

    sys.exit(soren91_corner_main(soren91_argv))


def _soren91_corner_manual_argv(argv: list[str]) -> list[str] | None:
    if argv and argv[0] == "soren91-corner-manual":
        return argv[1:]
    if len(argv) >= 3 and argv[0] == "--config" and argv[2] == "soren91-corner-manual":
        return ["--config", argv[1], *argv[3:]]
    if len(argv) >= 2 and argv[0].startswith("--config=") and argv[1] == "soren91-corner-manual":
        return [argv[0], *argv[2:]]
    return None


soren91_manual_argv = _soren91_corner_manual_argv(sys.argv[1:])
if soren91_manual_argv is not None:
    from .soren91_corner_manual import main as soren91_corner_manual_main

    sys.exit(soren91_corner_manual_main(soren91_manual_argv))

from .cli import main

sys.exit(main())
