"""Narrow adapters from paper notifications to existing Soren viewer queues."""
from __future__ import annotations

from pathlib import Path

from ..config import ConfigError, GlobalConfig, load_game
from ..overlay_queue import append_event, regenerate_overlay


class SorenOutputError(RuntimeError):
    """Raised when an existing Soren viewer-output queue cannot accept output."""


def resolve_soren_root(g: GlobalConfig) -> Path:
    raw = (g.webui.soren_root or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = g.repo_root / candidate
        return candidate.resolve()
    try:
        game = load_game(g, "sorengame")
        soren_raw = game.raw.get("soren", {}) if isinstance(game.raw, dict) else {}
        runtime_raw = soren_raw.get("root", "") if isinstance(soren_raw, dict) else ""
        if isinstance(runtime_raw, str) and runtime_raw.strip():
            runtime = Path(runtime_raw.strip()).expanduser()
            if not runtime.is_absolute():
                runtime = g.repo_root / runtime
            runtime = runtime.resolve()
            if runtime.is_dir():
                return runtime
    except ConfigError:
        pass
    candidate = g.repo_root / "games" / "soviet_now"
    if (candidate / "eloop_lib.sh").is_file():
        return candidate.resolve()
    cwd = Path.cwd()
    if (cwd / "eloop_lib.sh").is_file():
        return cwd.resolve()
    return candidate.resolve()


def send_overlay(g: GlobalConfig, payload: dict[str, object]) -> None:
    root = resolve_soren_root(g)
    try:
        # Keep queue mutation and HTML regeneration separately observable. If a
        # process dies after the queue write, exact-event dedupe makes retry safe.
        append_event(root, payload, strict=True, regenerate=False)
        if not regenerate_overlay(root):
            raise SorenOutputError("Soren overlay regeneration failed")
    except SorenOutputError:
        raise
    except Exception as exc:
        raise SorenOutputError("Soren overlay queue delivery failed") from exc


def enqueue_speech(g: GlobalConfig, text: str, *, event_id: str = "") -> None:
    # Reuse the same production Soren comment-audio queue used by Web UI.  The
    # paper event id is a durable sink-side dedupe key so a crash after enqueue
    # but before notification ACK cannot cause a later replay.
    try:
        from .. import webui
        result = webui._enqueue_audio_text(
            resolve_soren_root(g), text, "crypto_paper", delivery_key=event_id
        )
    except Exception as exc:
        raise SorenOutputError("Soren audio queue delivery failed") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise SorenOutputError("Soren audio queue rejected notification")
