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


def _speech_quality_argv(argv: list[str]) -> list[str] | None:
    """Route the stdin-only text validator before loading the config CLI."""
    if argv and argv[0] == "speech-quality":
        return argv[1:]
    return None


speech_quality_argv = _speech_quality_argv(sys.argv[1:])
if speech_quality_argv is not None:
    from .spoken_text_quality import main as speech_quality_main

    sys.exit(speech_quality_main(speech_quality_argv))

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


def _nethack_sidecar_argv(command: str, argv: list[str]) -> list[str] | None:
    if argv and argv[0] == command:
        return argv[1:]
    if len(argv) >= 3 and argv[0] == "--config" and argv[2] == command:
        return ["--config", argv[1], *argv[3:]]
    if len(argv) >= 2 and argv[0].startswith("--config=") and argv[1] == command:
        return [argv[0], *argv[2:]]
    return None


nethack_spectator_argv = _nethack_sidecar_argv("nethack-spectator-live", sys.argv[1:])
if nethack_spectator_argv is not None:
    from .nethack_spectator_live import main as nethack_spectator_main

    sys.exit(nethack_spectator_main(nethack_spectator_argv))

nethack_shadow_source_argv = _nethack_sidecar_argv("nethack-shadow-source", sys.argv[1:])
if nethack_shadow_source_argv is not None:
    from .nethack_shadow_source import main as nethack_shadow_source_main

    sys.exit(nethack_shadow_source_main(nethack_shadow_source_argv))

nethack_shadow_eval_argv = _nethack_sidecar_argv("nethack-shadow-evaluate", sys.argv[1:])
if nethack_shadow_eval_argv is not None:
    from .nethack_shadow_eval import main as nethack_shadow_eval_main

    sys.exit(nethack_shadow_eval_main(nethack_shadow_eval_argv))

nethack_retrospective_argv = _nethack_sidecar_argv("nethack-retrospective", sys.argv[1:])
if nethack_retrospective_argv is not None:
    from .nethack_retrospective import main as nethack_retrospective_main

    sys.exit(nethack_retrospective_main(nethack_retrospective_argv))

nethack_regression_argv = _nethack_sidecar_argv("nethack-regression", sys.argv[1:])
if nethack_regression_argv is not None:
    from .nethack_regression import main as nethack_regression_main

    sys.exit(nethack_regression_main(nethack_regression_argv))

nethack_candidate_argv = _nethack_sidecar_argv("nethack-candidate-evaluate", sys.argv[1:])
if nethack_candidate_argv is not None:
    from .nethack_candidate_eval import main as nethack_candidate_main

    sys.exit(nethack_candidate_main(nethack_candidate_argv))

nethack_candidate_shadow_eval_argv = _nethack_sidecar_argv(
    "nethack-candidate-shadow-evaluate", sys.argv[1:]
)
if nethack_candidate_shadow_eval_argv is not None:
    from .nethack_candidate_shadow_eval import main as nethack_candidate_shadow_eval_main

    sys.exit(nethack_candidate_shadow_eval_main(nethack_candidate_shadow_eval_argv))

from .cli import main

sys.exit(main())
