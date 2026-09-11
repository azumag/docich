"""Launch a bounded manual PAPER corner as a detached reviewed operation."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .config import ConfigError, load_global
from .paper_corner import PaperCornerError

MIN_DURATION = 1
MAX_DURATION = 60
STARTUP_GRACE_SECONDS = 1.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-paper-corner-operator")
    parser.add_argument("--config", metavar="PATH", required=True)
    parser.add_argument("--duration-minutes", type=int, required=True)
    return parser


def launch(config_path: Path, duration_minutes: int) -> dict[str, object]:
    if type(duration_minutes) is not int or not MIN_DURATION <= duration_minutes <= MAX_DURATION:
        raise PaperCornerError(f"duration_minutes は{MIN_DURATION}-{MAX_DURATION}の整数である必要があります")
    g = load_global(_repo_root(), config_path)
    log_dir = Path(g.state_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    operation_id = f"paper-manual-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    log_path = log_dir / f"{operation_id}.log"
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    argv = [
        sys.executable,
        "-m",
        "docich.paper_corner_manual",
        "--config",
        str(g.config_path),
        "start",
        "--duration-minutes",
        str(duration_minutes),
    ]
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(Path(g.repo_root).parent),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(Path(g.repo_root) / "src"),
    }
    try:
        with os.fdopen(fd, "ab", closefd=True) as log:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(g.repo_root),
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        time.sleep(STARTUP_GRACE_SECONDS)
        if proc.poll() is not None:
            raise PaperCornerError("PAPER manual runner exited during startup")
    except Exception:
        try:
            log_path.unlink()
        except OSError:
            pass
        raise
    return {
        "status": "started",
        "operation_id": operation_id,
        "duration_minutes": duration_minutes,
        "pid": proc.pid,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = launch(Path(args.config), args.duration_minutes)
    except (ConfigError, PaperCornerError, OSError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
