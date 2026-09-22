import json
import signal
from types import SimpleNamespace

import pytest

from docich.trading import paper_improve


class _FatalImproveExit(BaseException):
    pass


def _g(tmp_path):
    return SimpleNamespace(state_dir=tmp_path / "state")


def test_abnormal_baseexception_terminalizes_active_status(tmp_path, monkeypatch):
    trading = tmp_path / "state" / "trading"

    def crash(_g, **kwargs):
        paper_improve._write_improve_status(
            trading,
            status="running",
            phase="generate",
            progress=35,
            started_at=100.0,
            updated_at=101.0,
            detail="working",
        )
        raise _FatalImproveExit("must-not-leak")

    monkeypatch.setattr(paper_improve, "_run_paper_improve", crash)

    with pytest.raises(_FatalImproveExit):
        paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=123.0
        )

    status = json.loads((trading / paper_improve.STATUS_FILENAME).read_text())
    assert status["status"] == "failed"
    assert status["phase"] == "interrupted"
    assert status["progress"] == 35
    assert status["started_at"] == 100.0
    assert status["updated_at"] == 123.0
    assert status["completed_at"] == 123.0
    assert status["detail"] == "abnormal-exit"
    assert "must-not-leak" not in json.dumps(status)


def test_sigterm_exception_uses_fixed_terminal_detail(tmp_path, monkeypatch):
    trading = tmp_path / "state" / "trading"

    def terminate(_g, **kwargs):
        paper_improve._write_improve_status(
            trading,
            status="running",
            phase="generate",
            progress=35,
            started_at=100.0,
            updated_at=101.0,
            detail="working",
        )
        raise paper_improve._PaperImproveTermination()

    monkeypatch.setattr(paper_improve, "_run_paper_improve", terminate)

    with pytest.raises(paper_improve._PaperImproveTermination):
        paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=123.0
        )

    status = json.loads((trading / paper_improve.STATUS_FILENAME).read_text())
    assert status["status"] == "failed"
    assert status["phase"] == "interrupted"
    assert status["progress"] == 35
    assert status["detail"] == "terminated"
    assert status["completed_at"] == 123.0


def test_abnormal_wrapper_never_overwrites_terminal_status(tmp_path, monkeypatch):
    trading = tmp_path / "state" / "trading"

    def fail_after_terminal(_g, **kwargs):
        paper_improve._write_improve_status(
            trading,
            status="failed",
            phase="validate",
            progress=75,
            started_at=100.0,
            updated_at=110.0,
            detail="expected-terminal",
        )
        raise _FatalImproveExit("later")

    monkeypatch.setattr(paper_improve, "_run_paper_improve", fail_after_terminal)

    with pytest.raises(_FatalImproveExit):
        paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=123.0
        )

    status = json.loads((trading / paper_improve.STATUS_FILENAME).read_text())
    assert status["status"] == "failed"
    assert status["phase"] == "validate"
    assert status["progress"] == 75
    assert status["detail"] == "expected-terminal"
    assert status["completed_at"] == 110.0


def test_sigterm_default_handler_becomes_bounded_exception(monkeypatch):
    current = {signal.SIGTERM: signal.SIG_DFL}

    def fake_getsignal(sig):
        return current[sig]

    def fake_signal(sig, handler):
        previous = current[sig]
        current[sig] = handler
        return previous

    monkeypatch.setattr(paper_improve.signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(paper_improve.signal, "signal", fake_signal)

    with pytest.raises(paper_improve._PaperImproveTermination):
        with paper_improve._sigterm_as_exception():
            handler = current[signal.SIGTERM]
            assert handler is not signal.SIG_DFL
            handler(signal.SIGTERM, None)

    assert current[signal.SIGTERM] is signal.SIG_DFL


def test_sigterm_custom_handler_is_respected(monkeypatch):
    custom = object()
    current = {signal.SIGTERM: custom}
    calls = []

    monkeypatch.setattr(paper_improve.signal, "getsignal", lambda sig: current[sig])
    monkeypatch.setattr(
        paper_improve.signal,
        "signal",
        lambda sig, handler: calls.append((sig, handler)),
    )

    with paper_improve._sigterm_as_exception():
        assert current[signal.SIGTERM] is custom

    assert calls == []


def test_ai_timeout_terminalizes_as_failed_generate(tmp_path, monkeypatch):
    """Issue #724: AI timeout must land in a durable terminal state."""
    from docich.trading.ai_text import AiTextError

    trading = tmp_path / "state" / "trading"
    trading.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        paper_improve,
        "build_facts",
        lambda target, now=None, policy=None: {"capital_jpy": "1000000"},
    )

    def timeout_llm(prompt_text):
        raise AiTextError("AI呼び出しがタイムアウトしました", kind="timeout")

    summary = paper_improve.run_paper_improve(
        _g(tmp_path), trading_dir=trading, agents="x", now=2000.0, llm=timeout_llm
    )
    assert summary["status"] == "failed"
    assert summary["reason"] == "ai-error"
    status = json.loads((trading / paper_improve.STATUS_FILENAME).read_text())
    assert status["status"] == "failed"
    assert status["phase"] == "generate"
    assert status["completed_at"] == 2000.0
    assert "decision" not in status


def test_improve_path_sends_no_process_kill():
    """Issue #724: stale detection must never broaden into killing workers.

    The improve path owns no process handles: no pkill, no os.kill, no
    SIGKILL, no signal delivery. Recovery is metadata-only (stale flag,
    terminal states, next-run single-flight).
    """
    from pathlib import Path as _Path

    repo_root = _Path(__file__).resolve().parents[1]
    roots = [
        repo_root / "src/docich/trading/paper_improve.py",
        repo_root / "src/docich/trading/strategy_store.py",
        repo_root / "src/docich/trading/strategies.py",
    ]
    forbidden = ("os.kill", "pkill", "SIGKILL", "send_signal", "kill -9", "kill(")
    for root in roots:
        text = root.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, f"{root}: forbidden process-kill marker {marker!r}"
