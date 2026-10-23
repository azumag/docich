"""Bounded message-row head capture for the NetHack diagnostics pane (#2271).

The tail-only pane capture (last 14 non-blank rows) structurally misses the
top screen row, which is exactly where NetHack draws its message line and
``--More--``. Three consecutive stalled runs could therefore not be
distinguished between "a pager the brain never answered" and "a decision loop
returning no action". These tests pin the bounded head capture, its blank-row
position semantics and the redaction/length bounds.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_DIAG_PATH = (
    Path(__file__).resolve().parents[1] / "ops" / "vm_actions" / "collect_diagnostics.py"
)
_spec = importlib.util.spec_from_file_location("collect_diagnostics_pane_head", _DIAG_PATH)
collect_diagnostics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect_diagnostics)

CAPTURE = collect_diagnostics._capture_tmux_pane_head
HEAD_LINES = collect_diagnostics.NETHACK_PANE_HEAD_LINES


def fake_capture(monkeypatch, output, *, returncode=0, error=None):
    """Replace the fixed tmux argv with a recorded fake subprocess."""
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if error is not None:
            raise error
        return SimpleNamespace(returncode=returncode, stdout=output)

    monkeypatch.setattr(collect_diagnostics.subprocess, "run", run)
    return calls


def pane(*lines):
    return "\n".join(lines) + "\n"


def test_head_keeps_the_message_row_and_the_next_row(monkeypatch):
    calls = fake_capture(monkeypatch, pane(
        "Welcome back to the dungeon, page 7. --More--",
        "You see here a scroll.",
        " |..@....|",
    ))
    assert CAPTURE("docich:nethack") == [
        "Welcome back to the dungeon, page 7. --More--",
        "You see here a scroll.",
    ]
    assert HEAD_LINES == 2
    argv, kwargs = calls[0]
    assert argv == ["tmux", "capture-pane", "-p", "-t", "docich:nethack"]
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 5


def test_blank_message_row_keeps_its_position(monkeypatch):
    """An empty row 0 is evidence that no message/pager line is displayed."""
    fake_capture(monkeypatch, pane("", "", " |..@....|"))
    assert CAPTURE("docich:nethack") == ["", ""]


def test_head_line_length_is_bounded_and_redacted(monkeypatch):
    secret = "key=" + "A" * 300
    fake_capture(monkeypatch, pane(secret, "second"))
    head = CAPTURE("docich:nethack")
    assert len(head[0]) <= collect_diagnostics.NETHACK_PANE_LINE_LIMIT
    assert "A" * 300 not in head[0]


def test_head_rejects_unsafe_targets_without_running_tmux(monkeypatch):
    calls = fake_capture(monkeypatch, pane("x"))
    assert CAPTURE("docich:nethack; rm -rf /") == []
    assert CAPTURE(None) == []
    assert calls == []


def test_head_failure_returns_no_rows(monkeypatch):
    fake_capture(monkeypatch, pane("x"), returncode=1)
    assert CAPTURE("docich:nethack") == []
    fake_capture(monkeypatch, pane("x"), error=OSError("tmux missing"))
    assert CAPTURE("docich:nethack") == []


def test_collect_nethack_panes_publishes_the_game_head(monkeypatch, tmp_path):
    """The committed runtime's game pane head is part of the bounded output."""
    import json

    (tmp_path / "game_switch.json").write_text(json.dumps({
        "phase": "ready",
        "active": {
            "game": "nethack",
            "adapter_session": "docich",
            "game_window": "nethack",
            "generation": 914,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(
        collect_diagnostics, "_list_window_names",
        # The TTY lives in the runtime's birth/process window, not the
        # presentation window named by ``game_window``.
        lambda session: ["nethack-console", "nethack", "agent-g914"],
    )
    monkeypatch.setattr(
        collect_diagnostics, "_capture_tmux_pane",
        lambda target, **kwargs: [f"tail:{target}"],
    )
    monkeypatch.setattr(
        collect_diagnostics, "_capture_tmux_pane_head",
        lambda target, **kwargs: [f"head:{target}", ""],
    )
    result = collect_diagnostics._collect_nethack_panes(tmp_path, 0)
    assert result["active_game"] == "nethack"
    assert result["game"] == ["tail:docich:nethack-console"]
    assert result["game_head"] == ["head:docich:nethack-console", ""]
    assert result["agent"] == ["tail:docich:agent-g914"]


def test_collect_nethack_panes_defaults_to_an_empty_head(tmp_path):
    import json

    (tmp_path / "game_switch.json").write_text(json.dumps({
        "phase": "ready",
        "active": {"game": "sorengame", "adapter_session": "docich",
                   "game_window": "sorengame", "generation": 914},
    }), encoding="utf-8")
    result = collect_diagnostics._collect_nethack_panes(tmp_path, 0)
    assert result["game_head"] == []
    assert result["game"] == []
