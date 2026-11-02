"""Regression tests for #2793 comment translation failure logging."""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.comment import translation  # noqa: E402


def _capture_stderr(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    return buf


def test_exception_failure_is_logged_and_returns_original(monkeypatch):
    buf = _capture_stderr(monkeypatch)
    result = translation.translate_reply(
        "hello", translator=lambda text: (_ for _ in ()).throw(ValueError("boom"))
    )
    assert result == "hello"
    assert "[COMMENT_TRANSLATION]" in buf.getvalue()
    assert "failed: exception" in buf.getvalue()
    assert "ValueError: boom" in buf.getvalue()


def test_invalid_type_failure_is_logged(monkeypatch):
    buf = _capture_stderr(monkeypatch)
    result = translation.translate_reply("hello", translator=lambda text: 42)
    assert result == "hello"
    assert "failed: invalid_type" in buf.getvalue()
    assert "int" in buf.getvalue()


def test_empty_result_failure_is_logged(monkeypatch):
    buf = _capture_stderr(monkeypatch)
    result = translation.translate_reply("hello", translator=lambda text: "   ")
    assert result == "hello"
    assert "failed: empty_result" in buf.getvalue()


def test_never_raises_on_translator_error(monkeypatch):
    _capture_stderr(monkeypatch)

    def bad(text):
        raise RuntimeError("provider down")

    # Must not raise; original text is adopted (fail-soft).
    assert translation.translate_reply("hello", translator=bad) == "hello"


def test_success_returns_translation(monkeypatch):
    _capture_stderr(monkeypatch)
    assert translation.translate_reply("hello", translator=lambda text: "hi") == "hi"


def test_empty_input_returns_empty_without_logging(monkeypatch):
    buf = _capture_stderr(monkeypatch)
    assert translation.translate_reply("") == ""
    assert buf.getvalue() == ""
