"""Dedicated PAPER worker for isolated free-form strategies.

The worker is deliberately separate from the existing PAPER worker: it has its
own enable gate, state database, cadence and health file. Disabling it must not
construct a gateway, touch the filesystem, start gVisor or perform public API
requests. There is no live mode.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
from typing import Callable

from ..exchanges.bitbank_ccxt import BitbankPublicGateway
from .contract import IMAGE_RE, StrategyError
from .service import run_cycle, write_health

DEFAULT_INTERVAL_S = 300
MIN_INTERVAL_S = 300


def probe_host(*, docker: str = "docker") -> dict:
    """Read Docker host capability flags without creating containers or state.

    Only fixed booleans/error codes leave this boundary. Docker stderr, paths,
    versions and daemon configuration are deliberately not exposed.
    """
    info_format = (
        '{"OSType":{{json .OSType}},"Runtimes":{{json .Runtimes}},'
        '"MemoryLimit":{{json .MemoryLimit}},"PidsLimit":{{json .PidsLimit}},'
        '"CPUCfsQuota":{{json .CPUCfsQuota}}}'
    )
    base = {
        "mode": "PAPER",
        "docker_available": False,
        "linux_daemon": False,
        "runsc_registered": False,
        "memory_limit": False,
        "pids_limit": False,
        "cpu_quota": False,
    }
    try:
        result = subprocess.run(
            [docker, "info", "--format", info_format],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {**base, "status": "unavailable", "error_codes": ["docker_unavailable"]}
    if result.returncode != 0 or len(result.stdout) > 64 * 1024:
        return {**base, "status": "unavailable", "error_codes": ["docker_unavailable"]}
    try:
        info = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        return {**base, "status": "unavailable", "error_codes": ["docker_info_invalid"]}
    if not isinstance(info, dict):
        return {**base, "status": "unavailable", "error_codes": ["docker_info_invalid"]}

    runtimes = info.get("Runtimes")
    capabilities = {
        **base,
        "docker_available": True,
        "linux_daemon": info.get("OSType") == "linux",
        "runsc_registered": isinstance(runtimes, dict) and "runsc" in runtimes,
        "memory_limit": info.get("MemoryLimit") is True,
        "pids_limit": info.get("PidsLimit") is True,
        "cpu_quota": info.get("CPUCfsQuota") is True,
    }
    errors = []
    if not capabilities["linux_daemon"]:
        errors.append("linux_docker_required")
    if not capabilities["runsc_registered"]:
        errors.append("gvisor_required")
    if not all(capabilities[key] for key in ("memory_limit", "pids_limit", "cpu_quota")):
        errors.append("resource_limits_unavailable")
    return {
        **capabilities,
        "status": "ready" if not errors else "unavailable",
        "error_codes": errors,
    }


def run_worker(
    trading_dir: Path,
    *,
    image: str,
    enabled: bool = False,
    interval_s: int = DEFAULT_INTERVAL_S,
    gateway_factory=lambda: BitbankPublicGateway(timeout_ms=10_000),
    cycle_fn=run_cycle,
    now_fn: Callable[[], float] = time.time,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> dict:
    """Run the isolated PAPER lab on a 5-minute-or-slower cadence.

    ``enabled=False`` is a strict no-op: callers may safely deploy the code
    without creating state or triggering Docker/public-data work.
    """
    if enabled is not True:
        return {"mode": "PAPER", "status": "disabled", "cycles": 0}
    if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
        raise StrategyError("immutable_image_required")
    if type(interval_s) is not int or interval_s < MIN_INTERVAL_S:
        raise StrategyError("invalid_worker_interval")
    if max_cycles is not None and (type(max_cycles) is not int or max_cycles <= 0):
        raise StrategyError("invalid_worker_cycles")

    directory = Path(trading_dir)
    lab_directory = directory / "free-strategies"
    gateway = None
    cycles = 0
    last = {"mode": "PAPER", "status": "idle", "completed": 0, "error_codes": []}
    while True:
        cycles += 1
        started = float(monotonic_fn())
        try:
            if gateway is None:
                gateway = gateway_factory()
            last = cycle_fn(
                directory,
                image=image,
                gateway=gateway,
                now_fn=now_fn,
            )
            if not isinstance(last, dict) or last.get("mode") != "PAPER":
                raise StrategyError("invalid_cycle_result")
        except StrategyError as exc:
            code = str(exc)
            write_health(lab_directory, now=float(now_fn()), status="degraded", codes=[code])
            last = {"mode": "PAPER", "status": "degraded", "completed": 0, "error_codes": [code]}
            gateway = None
        except Exception:
            # Provider/public API/runtime exception text never leaves this boundary.
            write_health(
                lab_directory,
                now=float(now_fn()),
                status="degraded",
                codes=["free_strategy_worker_failed"],
            )
            last = {
                "mode": "PAPER",
                "status": "degraded",
                "completed": 0,
                "error_codes": ["free_strategy_worker_failed"],
            }
            gateway = None

        if max_cycles is not None and cycles >= max_cycles:
            return {**last, "cycles": cycles}
        elapsed = max(0.0, float(monotonic_fn()) - started)
        sleep_fn(max(0.0, interval_s - elapsed))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-free-strategy-worker")
    parser.add_argument("--trading-dir", metavar="PATH")
    parser.add_argument("--image", default="", metavar="SHA256")
    parser.add_argument("--enabled", action="store_true")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S, metavar="SEC")
    parser.add_argument(
        "--check-host", action="store_true",
        help="read Docker/runsc/resource-control availability without creating state",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.check_host:
            result = probe_host()
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0 if result["status"] == "ready" else 2
        if not args.trading_dir:
            raise StrategyError("trading_dir_required")
        result = run_worker(
            Path(args.trading_dir).expanduser(),
            image=args.image,
            enabled=bool(args.enabled),
            interval_s=args.interval,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0 if result.get("status") != "degraded" else 2
    except StrategyError as exc:
        print(json.dumps(
            {"mode": "PAPER", "status": "error", "error": str(exc)},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ))
        return 2
    except Exception:
        print('{"error":"free_strategy_worker_failed","mode":"PAPER","status":"error"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
