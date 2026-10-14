"""Subprocess execution helpers shared by all docich modules."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

EXTRA_PATH_DIRS = ("/usr/games", "/usr/local/games")


class OutputLimitExceeded(RuntimeError):
    """A bounded child process produced more stdout than its caller permits."""


def user_bus_env(env: dict | None = None) -> dict:
    """Return ``env`` with an address for the systemd user manager.

    Corner ticks run as user units and submit their follow-up jobs with
    ``systemd-run --user``. Timer-launched user services do not reliably
    inherit ``XDG_RUNTIME_DIR``, and without it the client fails with
    ``Failed to connect to bus: No medium found`` even though the user
    manager is running (#947). Derive the runtime directory from the uid
    instead of depending on the unit template that started the tick, and set
    the session bus address only when that socket actually exists.
    """
    merged = dict(os.environ if env is None else env)
    runtime = merged.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    merged["XDG_RUNTIME_DIR"] = runtime
    if not merged.get("DBUS_SESSION_BUS_ADDRESS"):
        socket = Path(runtime) / "bus"
        try:
            socket_exists = socket.exists()
        except OSError:
            socket_exists = False
        if socket_exists:
            merged["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={socket}"
    return merged


def _build_env(env_extra: dict | None, strip_tmux: bool) -> dict:
    env = os.environ.copy()
    extra = os.pathsep.join(EXTRA_PATH_DIRS)
    path = env.get("PATH", "")
    env["PATH"] = f"{path}{os.pathsep}{extra}" if path else extra
    if strip_tmux:
        # ネストした tmux から呼ばれても新しい tmux/シェルが拒否されないように剥がす
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    return env


def run(
    cmd: list[str],
    *,
    check: bool = False,
    timeout: float | None = None,
    env_extra: dict | None = None,
    capture: bool = True,
    strip_tmux: bool = False,
    input: str | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    env = _build_env(env_extra, strip_tmux)
    kwargs: dict = {}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if input is not None:
        kwargs["input"] = input
    return subprocess.run(
        cmd,
        env=env,
        timeout=timeout,
        text=True,
        check=check,
        cwd=cwd,
        **kwargs,
    )


def run_bounded_output(
    cmd: list[str],
    *,
    max_output_bytes: int,
    timeout: float,
    env_extra: dict | None = None,
    strip_tmux: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command while bounding stdout in memory and wall-clock time.

    Stderr is discarded because this helper is used for small, fixed-format
    probes whose public error messages must not contain child-process output.
    The process is killed as soon as it exceeds the byte cap or deadline.
    """
    if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 1_048_576:
        raise ValueError("max_output_bytes must be between 1 and 1048576")
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise ValueError("timeout must be greater than 0 and at most 30 seconds")

    process = subprocess.Popen(
        cmd,
        env=_build_env(env_extra, strip_tmux),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    output = bytearray()
    output_exceeded = threading.Event()

    def drain_stdout() -> None:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                return
            if len(output) + len(chunk) > max_output_bytes:
                output_exceeded.set()
                try:
                    process.kill()
                except OSError:
                    pass
                return
            output.extend(chunk)

    reader = threading.Thread(target=drain_stdout, name="bounded-command-output", daemon=True)
    try:
        reader.start()
        try:
            process.wait(timeout=float(timeout))
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            reader.join(timeout=1)
            raise subprocess.TimeoutExpired(cmd, timeout, output=bytes(output)) from exc
        reader.join(timeout=1)
        if reader.is_alive():
            process.kill()
            process.wait()
            raise subprocess.TimeoutExpired(cmd, timeout, output=bytes(output))
        if output_exceeded.is_set():
            raise OutputLimitExceeded(
                f"command output exceeded {max_output_bytes} bytes"
            )
        return subprocess.CompletedProcess(
            cmd, process.wait(), stdout=bytes(output), stderr=b""
        )
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()
        if reader.ident is not None:
            reader.join(timeout=1)


def which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    search_path = os.environ.get("PATH", "") + os.pathsep + os.pathsep.join(EXTRA_PATH_DIRS)
    return shutil.which(name, path=search_path)


def spawn(
    cmd: list[str],
    *,
    env_extra: dict | None = None,
    strip_tmux: bool = False,
    log_fh=None,
) -> subprocess.Popen:
    env = _build_env(env_extra, strip_tmux)
    stdout = log_fh if log_fh is not None else None
    stderr = log_fh if log_fh is not None else None
    return subprocess.Popen(cmd, env=env, stdout=stdout, stderr=stderr)
