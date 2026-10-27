"""Bounded LawSim narration for the shared overlay and audio queues.

The corner manager owns *when* this runs; this module only formats facts that
the deterministic engine already verified (frames / turns / metrics / events)
and hands them to the reviewed shared sinks (overlay append_event and
enqueue_audio_text when the catalog row has audio_enabled = true).

Convention chosen (per #2812 review criteria): narration-module style (a), the
same shape as yoso / puyo / trpg / doushi / tsuitate / mystery: overlay event
injection plus a shared audio worker enqueue (catalog audio_enabled opt-in).
VM-side playback with view status sync remains the default route for the view;
this module supplies the bounded utterance text for that route.

Both sinks are best-effort: failures are fixed counters and never fail the
corner or the game switch.
"""
from __future__ import annotations

import json
from pathlib import Path

from .overlay_queue import OVERLAY_BODY_LIMIT, append_event
from .trading.soren_output import enqueue_audio_text

TITLE = "共産主義法律シミュレーター"
CONTEXT = "lawsim:session"
MAX_SPEECH = 280


def _session_text(data):
    if not isinstance(data, dict):
        return ""
    title = data.get("title") or "共産主義法律シミュレーター"
    republic = data.get("republic") or ""
    final_status = data.get("finalStatusText") or data.get("finalStatus") or ""
    frames = data.get("frames") if isinstance(data.get("frames"), list) else []
    turns = data.get("turns") if isinstance(data.get("turns"), list) else []
    parts = [f"{title}。{republic}。" if republic else f"{title}。"]
    if frames and isinstance(frames[0], dict):
        parts.append(str(frames[0].get("text") or frames[0].get("heading") or ""))
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        name = turn.get("lawName") or turn.get("heading")
        deltas = turn.get("deltas") if isinstance(turn.get("deltas"), dict) else {}
        delta_bits = []
        for key, value in deltas.items():
            try:
                if value:
                    delta_bits.append(f"{key}{value:+d}" if isinstance(value, int) else f"{key}{value}")
            except (TypeError, ValueError):
                continue
        if name:
            parts.append(f"法案{name}。" + (" ".join(delta_bits) if delta_bits else ""))
        events = turn.get("events")
        if isinstance(events, list):
            for ev in events:
                text = ev.get("text") if isinstance(ev, dict) else str(ev)
                if text:
                    parts.append(f"警告: {text}。")
    if final_status:
        parts.append(str(final_status) + "。")
    text = " ".join("".join(parts).split())
    if len(text) > MAX_SPEECH:
        text = text[:MAX_SPEECH]
    return text


def narration_text(data, *, limit=MAX_SPEECH):
    if isinstance(data, (list, tuple)):
        data = {"frames": list(data)}
    text = _session_text(data if isinstance(data, dict) else {})
    if len(text) > limit:
        text = text[:limit]
    return text


def speech(g, text, *, context=CONTEXT, speaker=None, audio_enabled=True):
    if not audio_enabled or not text:
        return "skipped"
    try:
        enqueue_audio_text(
            g,
            text,
            context=context,
            speaker=speaker or "",
            dedup_scope=CONTEXT,
        )
        return "sent"
    except Exception:
        return "failed"


def announce(g, payload, *, request_id=None, audio_enabled=False, overlay=None, **kwargs):
    result = {"overlay": "skipped", "speech": "skipped"}
    text = narration_text(payload)
    if overlay is not None:
        try:
            append_event(g, title=TITLE, body=text[:OVERLAY_BODY_LIMIT],
                        category="prediction", source_id=request_id)
            result["overlay"] = "sent"
        except Exception:
            result["overlay"] = "failed"
    if audio_enabled and text:
        result["speech"] = speech(g, text, context=CONTEXT, audio_enabled=True)
    return result


def announcement(g, *, audio_enabled=False, **kwargs):
    try:
        from .lawsim_corner import STATE_FILENAME, RUNTIME_IDENTITY_KEY
        from .naming import runtime_directory
        state = json.loads(
            (Path(g.state_dir) / STATE_FILENAME).read_text(encoding="utf-8")
        )
        identity = state.get(RUNTIME_IDENTITY_KEY)
        runtime_id = identity.get("runtime_id") if isinstance(identity, dict) else None
        if not isinstance(runtime_id, str) or not runtime_id:
            return {"overlay": "skipped", "speech": "skipped"}
        for name in ("lawsim_session.json", "session.json"):
            path = runtime_directory(Path(g.state_dir), runtime_id) / name
            if path.exists():
                stored = json.loads(path.read_text(encoding="utf-8"))
                payload = stored.get("payload", stored) if isinstance(stored, dict) else stored
                return announce(g, payload, request_id=state.get("rotation_request_id"),
                                audio_enabled=audio_enabled, **kwargs)
    except Exception:
        pass
    return {"overlay": "skipped", "speech": "skipped"}
