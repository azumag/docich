from __future__ import annotations

import json

from docich.paper_improve_retry import (
    _read_validation_failure,
    build_validation_repair_prompt,
    is_paper_improve_invocation,
)


def test_routes_only_paper_improve_command():
    assert is_paper_improve_invocation([
        "--config", "config/docich.soren-live.toml",
        "trading", "paper-improve", "--date", "2026-09-12",
    ])
    assert is_paper_improve_invocation([
        "--config=config/docich.soren-live.toml",
        "trading", "paper-improve",
    ])
    assert is_paper_improve_invocation([
        "trading", "--state-dir", "run/trading", "paper-improve",
    ])
    assert is_paper_improve_invocation([
        "trading", "--state-dir=run/trading", "paper-improve",
    ])
    assert not is_paper_improve_invocation(["trading", "status"])
    assert not is_paper_improve_invocation(["--config", "trading", "status"])


def test_repair_prompt_uses_reason_without_raw_candidate():
    prompt = build_validation_repair_prompt("facts=trusted", "bad\nschema")
    assert "facts=trusted" in prompt
    assert "bad schema" in prompt
    assert "bad\nschema" not in prompt
    assert "前回の出力そのものは再利用せず" in prompt
    assert "有効なJSONオブジェクト1つだけ" in prompt


def test_validation_failure_is_the_only_retryable_phase(tmp_path):
    path = tmp_path / "paper_improve_status.json"
    path.write_text(json.dumps({
        "status": "failed",
        "phase": "validate",
        "detail": "unsupported feature: price",
    }), encoding="utf-8")
    assert _read_validation_failure(path) == "unsupported feature: price"

    path.write_text(json.dumps({
        "status": "failed",
        "phase": "save",
        "detail": "save:permission denied",
    }), encoding="utf-8")
    assert _read_validation_failure(path) is None

    path.write_text(json.dumps({
        "status": "failed",
        "phase": "generate",
        "detail": "ai-error",
    }), encoding="utf-8")
    assert _read_validation_failure(path) is None
