from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.free_strategy.contract import StrategyError  # noqa: E402
from docich.trading.free_strategy.worker import main, probe_host, run_worker  # noqa: E402

IMAGE = "sha256:" + "a" * 64


def test_disabled_worker_is_strict_noop(tmp_path):
    calls = []

    def gateway_factory():
        calls.append("gateway")
        raise AssertionError("disabled worker must not construct gateway")

    result = run_worker(
        tmp_path / "trading",
        image="",
        enabled=False,
        gateway_factory=gateway_factory,
        cycle_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not cycle")),
    )
    assert result == {"mode": "PAPER", "status": "disabled", "cycles": 0}
    assert calls == []
    assert not (tmp_path / "trading").exists()


def test_enabled_worker_runs_separate_paper_cycle_on_five_minute_cadence(tmp_path):
    sleeps = []
    calls = []
    mono = iter([10.0, 12.0, 20.0, 23.5])

    class Gateway:
        pass

    gateway = Gateway()

    def cycle(trading_dir, *, image, gateway, now_fn):
        calls.append((Path(trading_dir), image, gateway, now_fn()))
        return {"mode": "PAPER", "status": "idle", "completed": 1, "error_codes": []}

    result = run_worker(
        tmp_path / "trading",
        image=IMAGE,
        enabled=True,
        interval_s=300,
        gateway_factory=lambda: gateway,
        cycle_fn=cycle,
        now_fn=lambda: 1000.0 + len(calls),
        monotonic_fn=lambda: next(mono),
        sleep_fn=sleeps.append,
        max_cycles=2,
    )
    assert result == {"mode": "PAPER", "status": "idle", "completed": 1, "error_codes": [], "cycles": 2}
    assert len(calls) == 2
    assert all(call[1] == IMAGE and call[2] is gateway for call in calls)
    assert sleeps == [298.0]


def test_worker_rejects_sub_five_minute_cadence_before_gateway(tmp_path):
    with pytest.raises(StrategyError, match="invalid_worker_interval"):
        run_worker(
            tmp_path,
            image=IMAGE,
            enabled=True,
            interval_s=299,
            gateway_factory=lambda: (_ for _ in ()).throw(AssertionError("must not construct gateway")),
            max_cycles=1,
        )


def test_unexpected_cycle_error_is_redacted_and_health_is_generic(tmp_path):
    marker = "private-provider-token-value"

    def broken(*args, **kwargs):
        raise RuntimeError(marker)

    result = run_worker(
        tmp_path / "trading",
        image=IMAGE,
        enabled=True,
        gateway_factory=lambda: object(),
        cycle_fn=broken,
        now_fn=lambda: 2000.0,
        monotonic_fn=lambda: 1.0,
        max_cycles=1,
    )
    assert result == {
        "mode": "PAPER",
        "status": "degraded",
        "completed": 0,
        "error_codes": ["free_strategy_worker_failed"],
        "cycles": 1,
    }
    raw = (tmp_path / "trading" / "free-strategies" / "health.json").read_text()
    assert marker not in raw
    assert json.loads(raw)["error_codes"] == ["free_strategy_worker_failed"]


def test_worker_cli_stays_disabled_without_explicit_enable(tmp_path, capsys):
    code = main(["--trading-dir", str(tmp_path / "trading")])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"cycles": 0, "mode": "PAPER", "status": "disabled"}
    assert not (tmp_path / "trading").exists()


def test_host_probe_reports_only_fixed_ready_capabilities(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps({
                "OSType": "linux",
                "Runtimes": {"runc": {}, "runsc": {}},
                "MemoryLimit": True,
                "PidsLimit": True,
                "CPUCfsQuota": True,
            }).encode(),
            b"private-daemon-marker",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = probe_host()
    assert result == {
        "mode": "PAPER",
        "status": "ready",
        "docker_available": True,
        "linux_daemon": True,
        "runsc_registered": True,
        "memory_limit": True,
        "pids_limit": True,
        "cpu_quota": True,
        "error_codes": [],
    }
    assert calls and calls[0][0][:3] == ["docker", "info", "--format"]
    assert "private-daemon-marker" not in json.dumps(result)


def test_host_probe_fails_closed_with_fixed_codes(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps({
                "OSType": "linux",
                "Runtimes": {"runc": {}},
                "MemoryLimit": True,
                "PidsLimit": False,
                "CPUCfsQuota": False,
            }).encode(),
            b"secret error details",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = probe_host()
    assert result["status"] == "unavailable"
    assert result["docker_available"] is True
    assert result["runsc_registered"] is False
    assert result["memory_limit"] is True
    assert result["pids_limit"] is False
    assert result["cpu_quota"] is False
    assert result["error_codes"] == ["gvisor_required", "resource_limits_unavailable"]
    assert "secret" not in json.dumps(result)


def test_host_probe_does_not_create_state_and_cli_needs_no_trading_dir(tmp_path, monkeypatch, capsys):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            b"",
            b"daemon private diagnostic",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    before = list(tmp_path.iterdir())
    result = probe_host(docker="missing-docker")
    assert result["status"] == "unavailable"
    assert result["error_codes"] == ["docker_unavailable"]
    assert list(tmp_path.iterdir()) == before

    code = main(["--check-host"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "unavailable"
    assert payload["error_codes"] == ["docker_unavailable"]
