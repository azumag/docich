from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.run(
        [sys.executable, "-m", "docich", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_top_level_free_strategy_status_is_read_only_and_paper_only(tmp_path):
    trading_dir = tmp_path / "trading"
    result = _run("free-strategy", "--trading-dir", str(trading_dir), "status")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"experiments": [], "mode": "PAPER"}
    assert not trading_dir.exists()


def test_top_level_free_strategy_worker_is_strictly_disabled_without_flag(tmp_path):
    trading_dir = tmp_path / "trading"
    result = _run("free-strategy-worker", "--trading-dir", str(trading_dir))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"cycles": 0, "mode": "PAPER", "status": "disabled"}
    assert not trading_dir.exists()


def test_top_level_routes_do_not_add_live_or_promotion_surface(tmp_path):
    trading_dir = tmp_path / "trading"
    for action in ("live", "promote"):
        result = _run("free-strategy", "--trading-dir", str(trading_dir), action)
        assert result.returncode != 0
        assert "invalid choice" in result.stderr
    assert not trading_dir.exists()
