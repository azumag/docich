"""Switch-aware status collection (design v2 section 8, P4).

collect_status() separates the four layers the completion criteria
require callers to distinguish:

- canonical: run/game_switch.json (phase/operation/active/candidate/
  previous/retiring/last_result/last_error), or its absence/corruption
- mirror: the current_game compat file and whether it agrees with
  the canonical active game
- actual: live tmux windows/sessions with ownership verification
  against the canonical identity
- agent_fence: the canonical activation tuple plus agent window presence

Read-only and fail-closed: a corrupt canonical state is reported, never
raised through.  All tmux probes are best-effort; an unreadable target is
reported as unreadable, never guessed.
"""
from __future__ import annotations

import shlex
from typing import Mapping

from .config import GlobalConfig
from .game_switch import GameSwitchError, GameSwitchStore
from .state import State
from .stream import (
    StreamKeyError,
    caption_socket_ready,
    resolve_runtime,
)
from .tmux import OwnershipMismatchError, Tmux
from .xkit import XKit

STATUS_SCHEMA_VERSION = 1
STATUS_WINDOWS = ("display", "audio", "stream", "game", "agent", "watchdog")


def _probe_panes(tmux: Tmux, target: str) -> str:
    """Return pane liveness: alive (all panes live), dead (any pane dead),
    or unreadable (probe failed).  Mirrors the adapter readiness contract
    so status never reports a dead-pane runtime as alive."""
    try:
        states = tmux.pane_states_checked(target)
    except Exception:
        return "unreadable"
    if not states:
        return "unreadable"
    return "dead" if any(getattr(pane, "dead", False) for pane in states) else "alive"


def _check_window(tmux: Tmux, window: str, runtime: Mapping[str, object], role: str) -> dict:
    """Probe one generation window: existence, ownership, and pane liveness."""
    target = f"docich:{window}"
    try:
        exists = tmux.window_target_exists(target, strict=True)
    except Exception:
        return {"name": window, "exists": None, "ownership": "unreadable", "panes": "unreadable"}
    if not exists:
        return {"name": window, "exists": False, "ownership": "absent", "panes": "absent"}
    try:
        actual = tmux.read_window_ownership(target)
        matched = (
            actual.runtime_id == runtime.get("runtime_id")
            and actual.generation == runtime.get("generation")
            and actual.role == role
        )
    except OwnershipMismatchError:
        return {"name": window, "exists": True, "ownership": "mismatched", "panes": _probe_panes(tmux, target)}
    except Exception:
        return {"name": window, "exists": True, "ownership": "unreadable", "panes": _probe_panes(tmux, target)}
    return {
        "name": window,
        "exists": True,
        "ownership": "matched" if matched else "mismatched",
        "panes": _probe_panes(tmux, target),
    }


def _check_session(tmux: Tmux, session: str, runtime: Mapping[str, object]) -> dict:
    """Probe one generation session (CLI adapters only)."""
    try:
        exists = tmux.session_target_exists(session, strict=True)
    except Exception:
        return {"name": session, "exists": None, "ownership": "unreadable", "panes": "unreadable"}
    if not exists:
        return {"name": session, "exists": False, "ownership": "absent", "panes": "absent"}
    try:
        actual = tmux.read_session_ownership(session)
        matched = (
            actual.runtime_id == runtime.get("runtime_id")
            and actual.generation == runtime.get("generation")
            and actual.role == "adapter"
        )
    except OwnershipMismatchError:
        return {"name": session, "exists": True, "ownership": "mismatched", "panes": _probe_panes(tmux, session)}
    except Exception:
        return {"name": session, "exists": True, "ownership": "unreadable", "panes": _probe_panes(tmux, session)}
    return {
        "name": session,
        "exists": True,
        "ownership": "matched" if matched else "mismatched",
        "panes": _probe_panes(tmux, session),
    }


def _check_runtime(tmux: Tmux, runtime: Mapping[str, object] | None) -> dict | None:
    if not isinstance(runtime, dict):
        return None
    game_window = str(runtime.get("game_window") or "")
    agent_window = str(runtime.get("agent_window") or "")
    session = str(runtime.get("adapter_session") or "")
    result: dict = {
        "game": runtime.get("game"),
        "adapter": runtime.get("adapter"),
        "generation": runtime.get("generation"),
        "runtime_id": runtime.get("runtime_id"),
        "game_window": _check_window(tmux, game_window, runtime, "game"),
        "agent_window": _check_window(tmux, agent_window, runtime, "agent"),
    }
    if runtime.get("adapter") == "cli" and session:
        result["adapter_session"] = _check_session(tmux, session, runtime)
    else:
        result["adapter_session"] = {"name": session, "applicable": False}
    return result


def collect_status(g: GlobalConfig, *, tmux: Tmux | None = None, xkit: XKit | None = None) -> dict:
    """Collect the full switch-aware status as a stable-schema dict."""
    tmux = tmux or Tmux()
    xkit = xkit or XKit(g.display.name)
    state = State(g)
    store = GameSwitchStore(g.state_dir)

    session_alive = tmux.has_session()
    windows = {
        w: (tmux.has_window(w) if session_alive else False) for w in STATUS_WINDOWS
    }

    canonical: dict = {"present": False, "corrupt": False, "error": None}
    loaded = None
    needs_write = False
    try:
        loaded, needs_write = store.canonical.load()
    except GameSwitchError as exc:
        canonical.update({"present": True, "corrupt": True, "error": str(exc)})
    if loaded is not None:
        canonical["present"] = not needs_write
        if needs_write:
            canonical.update(
                {
                    "phase": "idle",
                    "operation": None,
                    "request_id": None,
                    "next_generation": 1,
                    "active": None,
                    "candidate": None,
                    "previous": None,
                    "retiring": [],
                    "last_result": None,
                    "last_error": None,
                }
            )
        else:
            for key in (
                "phase", "operation", "request_id", "next_generation",
                "active", "candidate", "previous", "retiring",
                "last_result", "last_error",
            ):
                canonical[key] = loaded.get(key)

    mirror_game = state.current_game()
    canonical_game = None
    active = (loaded or {}).get("active") if loaded is not None else None
    if isinstance(active, dict):
        canonical_game = active.get("game")
    mirror = {
        "game": mirror_game,
        "matches_canonical": (mirror_game == canonical_game)
        if (loaded is not None and not needs_write)
        else None,
    }

    actual = {"active": None, "candidate": None}
    candidate = (loaded or {}).get("candidate") if loaded is not None else None
    if isinstance(active, dict):
        actual["active"] = _check_runtime(tmux, active)
    if isinstance(candidate, dict):
        actual["candidate"] = _check_runtime(tmux, candidate)

    fence_tuple = None
    if isinstance(active, dict):
        fence_tuple = {
            "game": active.get("game"),
            "runtime_id": active.get("runtime_id"),
            "generation": active.get("generation"),
            "lease_id": active.get("lease_id"),
        }
    agent_window_exists = None
    if actual["active"] is not None:
        agent_window_exists = actual["active"]["agent_window"].get("exists")
    agent_fence = {
        "tuple": fence_tuple,
        # true/false/null: null keeps probe failure distinguishable from
        # confirmed absence (fail-closed, never guessed).
        "agent_window_present": agent_window_exists,
    }

    retiring = (loaded or {}).get("retiring") if loaded is not None else None
    cleanup_pending = bool(isinstance(retiring, list) and len(retiring) > 0)

    stream: dict = {"mode": g.stream.mode}
    if g.stream.mode == "null":
        stream.update({"ffmpeg": None, "captions_active": False, "captions_detail": "配信しない設定です"})
    else:
        try:
            runtime = resolve_runtime(g, mask_key=True)
            detail = runtime.caption_detail
            captions_active = False
            if runtime.captions_active and not windows["stream"]:
                detail = f"{detail}; stream windowは停止中です"
            elif runtime.captions_active:
                captions_active, socket_detail = caption_socket_ready(g.captions.socket_path)
                detail = f"{detail}; {socket_detail}"
            stream.update(
                {
                    "ffmpeg": shlex.join(runtime.command),
                    "captions_active": captions_active,
                    "captions_detail": detail,
                }
            )
        except StreamKeyError as exc:
            stream.update(
                {"ffmpeg": None, "captions_active": False, "captions_detail": f"構築できません ({exc})"}
            )

    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "session_alive": session_alive,
        "windows": windows,
        "display_ready": bool(xkit.display_ready()),
        "canonical": canonical,
        "mirror": mirror,
        "actual": actual,
        "agent_fence": agent_fence,
        "cleanup_pending": cleanup_pending,
        "stream": stream,
    }
