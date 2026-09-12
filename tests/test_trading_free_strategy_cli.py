from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.free_strategy.cli import build_parser, main, run_args  # noqa: E402

IMAGE = "sha256:" + "a" * 64
SOURCE = "def decide(context):\n    return {'schema_version': 1, 'target_positions': [], 'state': {}, 'reason': 'wait'}\n"


def _args(trading_dir: Path, *rest: str):
    return build_parser().parse_args(["--trading-dir", str(trading_dir), *rest])


def test_register_status_evaluate_pause_and_resume_are_paper_only(tmp_path):
    trading_dir = tmp_path / "trading"
    source = tmp_path / "candidate.py"
    source.write_text(SOURCE, encoding="utf-8")

    registered = run_args(_args(
        trading_dir,
        "register",
        "--source", str(source),
        "--image", IMAGE,
        "--name", "manual",
        "--family", "cli-contract",
        "--thesis", "manual PAPER candidate",
        "--symbols", "BTC/JPY",
        "--days", "1",
    ), now_fn=lambda: 1_000.0)
    assert registered["mode"] == "PAPER"
    assert registered["status"] == "registered"
    identity = registered["experiment"]

    status = run_args(_args(trading_dir, "status"), now_fn=lambda: 1_100.0)
    assert status["mode"] == "PAPER"
    assert len(status["experiments"]) == 1
    assert status["experiments"][0]["id"] == identity
    assert status["experiments"][0]["live_eligible"] is False
    assert "source" not in status["experiments"][0]

    report = run_args(_args(trading_dir, "evaluate", identity), now_fn=lambda: 1_100.0)
    assert report["live_eligible"] is False
    assert "live_broker_not_implemented" in report["blockers"]

    paused = run_args(_args(trading_dir, "pause", identity), now_fn=lambda: 1_101.0)
    assert paused == {"mode": "PAPER", "status": "paused", "experiment": identity}

    resumed = run_args(_args(trading_dir, "resume", identity), now_fn=lambda: 1_102.0)
    assert resumed == {"mode": "PAPER", "status": "paper_validating", "experiment": identity}


def test_generate_uses_text_only_provider_and_daily_budget(tmp_path):
    trading_dir = tmp_path / "trading"
    calls = []

    def text_fn(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({
            "source": SOURCE,
            "name": "generated",
            "family": "cli-generated",
            "thesis": "generated only for PAPER",
        })

    argv = _args(
        trading_dir,
        "generate",
        "--image", IMAGE,
        "--symbols", "BTC/JPY",
        "--brief", "try a different hypothesis",
        "--days", "1",
    )
    first = run_args(argv, now_fn=lambda: 2_000.0, text_fn=text_fn)
    second = run_args(argv, now_fn=lambda: 2_001.0, text_fn=text_fn)
    assert first["mode"] == "PAPER" and first["status"] == "registered"
    assert second == {"status": "skipped", "reason": "generation_daily_budget"}
    assert len(calls) == 1


def test_cycle_delegates_to_isolated_paper_service(tmp_path):
    trading_dir = tmp_path / "trading"
    gateway = object()
    seen = {}

    def gateway_factory():
        return gateway

    def cycle_fn(path, *, image, gateway, now_fn):
        seen.update(path=path, image=image, gateway=gateway, now=now_fn())
        return {"mode": "PAPER", "status": "idle", "completed": 0, "error_codes": []}

    result = run_args(
        _args(trading_dir, "cycle", "--image", IMAGE),
        now_fn=lambda: 3_000.0,
        gateway_factory=gateway_factory,
        cycle_fn=cycle_fn,
    )
    assert result["mode"] == "PAPER"
    assert seen == {"path": trading_dir, "image": IMAGE, "gateway": gateway, "now": 3_000.0}


def test_main_redacts_unexpected_exception_text(tmp_path, capsys):
    secret = "provider-secret-should-not-leak"

    def broken_gateway_factory():
        raise RuntimeError(secret)

    code = main(
        ["--trading-dir", str(tmp_path / "trading"), "cycle", "--image", IMAGE],
        gateway_factory=broken_gateway_factory,
    )
    output = capsys.readouterr().out
    assert code == 2
    assert secret not in output
    assert json.loads(output) == {
        "error": "free_strategy_cli_failed", "mode": "PAPER", "status": "error"
    }
