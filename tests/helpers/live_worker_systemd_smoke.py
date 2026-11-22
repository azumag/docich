"""Run the documented LIVE worker user unit with an empty state on hosted CI."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/operations/live-worker-systemd.md"
PROPERTIES = ("ActiveState", "SubState", "ExecMainStatus", "NRestarts")


def run(*args, allow_failure=False):
    env = {**os.environ, "LC_ALL": "C", "SYSTEMD_COLORS": "0"}
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, env=env)
    if result.returncode and not allow_failure:
        raise RuntimeError(f"{args[0]} exited {result.returncode}")
    return result.stdout


def exec_arg(path):
    value = str(path).replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("%", "%%").replace("$", "$$")
    return '"' + value + '"'


def unit_text(state):
    (template,) = re.findall(r"```ini[ \t]*\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    src = ROOT / "src"
    if any("\r" in str(path) or "\n" in str(path) for path in (src, sys.executable, state)):
        raise RuntimeError("unit paths cannot contain line breaks")
    replacements = {
        "<リポジトリの checkout>/src": str(src).replace("%", "%%"),
        "<venv の python>": exec_arg(sys.executable),
        "<state の絶対パス>": exec_arg(state),
    }
    for original, replacement in replacements.items():
        if template.count(original) != 1:
            raise RuntimeError("unit template placeholder changed")
        template = template.replace(original, replacement)
    return template


def verify_exit(unit, status_path):
    deadline = time.monotonic() + 20
    expected = dict(zip(PROPERTIES, ("failed", "failed", "2", "0")))
    while time.monotonic() < deadline:
        args = [f"--property={k}" for k in PROPERTIES]
        text = run("systemctl", "--user", "show", *args, unit)
        props = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        if props == expected and status_path.is_file():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if (status.get("phase"), status.get("exit_code"), status.get("reason")) != (
                    "exited", 2, "config"):
                raise RuntimeError("worker status did not record the missing-key config exit")
            return
        time.sleep(0.2)
    raise RuntimeError(f"unexpected unit state: {props}")


def cleanup(unit, path):
    try:
        run("systemctl", "--user", "stop", unit, allow_failure=True)
        run("systemctl", "--user", "reset-failed", unit, allow_failure=True)
    finally:
        path.unlink(missing_ok=True)
        run("systemctl", "--user", "daemon-reload")


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("requires a GitHub-hosted Actions runner")
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    if not runtime.is_absolute() or runtime.stat().st_uid != os.getuid():
        raise RuntimeError("XDG_RUNTIME_DIR must belong to the runner user")
    unit_dir = runtime / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = "docich-live-worker-smoke-" + uuid.uuid4().hex + ".service"
    path = unit_dir / unit
    with tempfile.TemporaryDirectory(prefix="docich-live-systemd-") as temp:
        state = Path(temp) / "state"
        with path.open("x", encoding="utf-8") as config:
            config.write(unit_text(state))
        try:
            # network-online.target belongs to the system manager. Check this user unit
            # without propagating verify failures from its dependency units.
            run("systemd-analyze", "--user", "--man=no", "--recursive-errors=no", "verify", str(path))
            run("systemctl", "--user", "daemon-reload")
            run("systemctl", "--user", "start", unit, allow_failure=True)
            verify_exit(unit, state / "trading-live/live_status.json")
        finally:
            cleanup(unit, path)
    print("LIVE worker systemd smoke passed: no-key exit 2, no restart")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"LIVE worker systemd smoke failed: {exc if isinstance(exc, RuntimeError) else type(exc).__name__}",
              file=sys.stderr)
        sys.exit(1)
