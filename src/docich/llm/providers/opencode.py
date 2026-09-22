"""Opencode-family provider adapter (#829 PR-1).

Mirrors ``_ai_call_opencode_unqueued``: fixed ``opencode run`` argv with the
legacy model mapping, ``--agent`` selection for vercel/amd chains, the
429-guard path for the muse-spark free models, one same-model abort retry
(except vercel / budget-disabled), the rotation gate (shared lock, fail
closed), per-model run locks, and the XDG/auth fixed setup.  The prompt is
passed as a single argv positional, exactly like the dispatch path (the
improve path's stdin variant is out of scope).
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import time
from typing import Iterator

from .base import (
    ProviderResult,
    clean_text,
    provider_error_detected,
    rate_limit_detected,
    resolve_binary,
)
from ..contracts import RC_FAILED, RC_TIMEOUT

_SPARK_GUARD_RE = re.compile(r"^opencode/muse-spark-1\.[23]-contributor-free$")


def model_from_agent(agent: str) -> str:
    """Legacy opencode model mapping (ai_generate.sh model-mapping block)."""
    if agent.startswith("openrouter:"):
        return "openrouter/" + agent.split(":", 1)[1]
    if agent.startswith("vercel:"):
        return "vercel/" + agent.split(":", 1)[1]
    if agent.startswith("amd:"):
        return "amd-token-factory/" + agent.split(":", 1)[1]
    if agent.startswith("opencode-go/"):
        return agent
    if agent.startswith("opencode-go:"):
        return "opencode-go/" + agent.split(":", 1)[1]
    if agent.startswith("opencode/"):
        return agent
    if agent.startswith("opencode:"):
        return "opencode/" + agent.split(":", 1)[1]
    return agent


def agent_args(agent: str, label: str) -> list[str]:
    """Extra ``--agent`` args, only for vercel/amd chains."""
    if agent.startswith("vercel:") or agent.startswith("amd:"):
        if "prepass" in (label or "") or "RESEARCH" in (label or ""):
            return ["--agent", "soren-research"]
        return ["--agent", "soren-lite"]
    return []


def _sanitize(value: str) -> str:
    return "".join(
        ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value
    )[:128] or "default"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def prepare_xdg(state_dir: Path) -> dict[str, str]:
    """Own the legacy opencode XDG/auth fixed setup under our state dir.

    Creates the XDG state/data homes, syncs ``auth.json`` from the canonical
    source when the destination is missing or differs, and cleans internal
    ``*.lock`` files older than the stale threshold.  Returns env overrides
    scoped to the child process (the parent environment is never mutated).
    """
    base = Path(state_dir)
    xdg_state = base / "xdg_state"
    xdg_data = base / "xdg_data"
    locks = xdg_state / "opencode" / "locks"
    try:
        locks.mkdir(parents=True, exist_ok=True)
        (xdg_data / "opencode").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    home = Path(os.environ.get("HOME", str(Path.home())))
    src = Path(
        os.environ.get(
            "OPENCODE_AUTH_SOURCE",
            str(home / ".local/share/opencode/auth.json"),
        )
    )
    dst = xdg_data / "opencode" / "auth.json"
    try:
        if src.is_file() and src.stat().st_size > 0:
            blob = src.read_bytes()
            if not dst.is_file() or dst.read_bytes() != blob:
                dst.write_bytes(blob)
    except OSError:
        pass
    try:
        stale = max(
            10, int(os.environ.get("OPENCODE_INTERNAL_LOCK_STALE_SEC", "60"))
        )
    except ValueError:
        stale = 60
    now = time.time()
    try:
        for entry in locks.glob("*.lock"):
            try:
                if now - entry.stat().st_mtime > stale:
                    entry.unlink()
            except OSError:
                continue
    except OSError:
        pass
    return {
        "XDG_STATE_HOME": str(xdg_state),
        "XDG_DATA_HOME": str(xdg_data),
    }


@contextmanager
def rotation_gate(state_dir: Path, settings) -> Iterator[bool]:
    """Hold the shared rotation-gate lock for the run; fail closed.

    Yields True when the gate is held, False when contention exceeds the
    wait budget (caller maps to rc 124 without running the command).
    """
    if not settings.rotation_gate_enabled:
        yield True
        return
    path = Path(state_dir) / ".opencode_rotation_gate.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+b")
    except OSError:
        yield False
        return
    deadline = time.monotonic() + max(0, settings.rotation_gate_wait_sec)
    held = False
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                held = True
                break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        yield held
    finally:
        try:
            if held:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass


@contextmanager
def run_lock(
    state_dir: Path, model: str, settings
) -> Iterator[bool]:
    """Hold the per-model run lock; reap stale/dead owners on the way in.

    Yields True when held, False when the max-wait budget expires (caller
    treats it as a generic provider failure, mirroring the legacy 124 from
    ``_opencode_run_lock_enter``).
    """
    path = Path(state_dir) / "opencode_run_locks" / _sanitize(model)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield False
        return
    wait_sec = max(1, settings.opencode_run_lock_wait_sec)
    stale_sec = max(60, settings.opencode_run_lock_stale_sec)
    max_wait = settings.opencode_run_lock_max_wait_sec
    deadline = None if max_wait <= 0 else time.monotonic() + max_wait
    pid = os.getpid()
    while True:
        try:
            handle = open(path, "a+", encoding="utf-8")
        except OSError:
            yield False
            return
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                if deadline is not None and time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(wait_sec)
                continue
            try:
                handle.seek(0)
                try:
                    owner = json.loads(handle.read() or "{}")
                except ValueError:
                    owner = {}
                owner_pid = int(owner.get("pid") or 0)
                started = float(owner.get("started_at") or 0)
                if owner and (
                    _pid_alive(owner_pid)
                    or (time.time() - started) < stale_sec
                ):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    handle.close()
                    if deadline is not None and time.monotonic() >= deadline:
                        yield False
                        return
                    time.sleep(wait_sec)
                    continue
                handle.seek(0)
                handle.truncate()
                handle.write(json.dumps({"pid": pid, "started_at": time.time()}))
                handle.flush()
                try:
                    yield True
                finally:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                    try:
                        handle.close()
                    except OSError:
                        pass
                return
            except GeneratorExit:
                raise
            except Exception:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    handle.close()
                except OSError:
                    pass
                yield False
                return
        except OSError:
            yield False
            return


def _stream_run(
    argv: list[str],
    timeout: int,
    env: dict[str, str],
    watch_429: bool,
) -> ProviderResult:
    """Run argv, streaming stderr for early 429 termination.

    When ``watch_429`` is set (the muse-spark guard path), rate-limit text
    in stderr kills the process group immediately and maps to rc 79 instead
    of burning the wall budget on hidden CLI retries.
    """
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return ProviderResult(rc=RC_FAILED, stderr="spawn_failed")
    assert proc.stdout is not None and proc.stderr is not None
    for stream in (proc.stdout, proc.stderr):
        flags = fcntl.fcntl(stream.fileno(), fcntl.F_GETFL)
        fcntl.fcntl(stream.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    deadline = time.monotonic() + max(1, timeout)
    rate_hit = False
    timed_out = False
    try:
        while True:
            if proc.poll() is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                ready, _, _ = select.select(
                    [proc.stdout, proc.stderr], [], [], min(0.2, remaining)
                )
            except (OSError, ValueError):
                break
            for stream in ready:
                try:
                    chunk = stream.read(65536) or b""
                except OSError:
                    chunk = b""
                if not chunk:
                    continue
                if stream is proc.stderr:
                    err_chunks.append(chunk)
                    if watch_429 and rate_limit_detected(
                        b"".join(err_chunks).decode("utf-8", "replace")[-4096:]
                    ):
                        rate_hit = True
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            pass
                        break
                else:
                    out_chunks.append(chunk)
            if rate_hit:
                break
        try:
            tail_out, tail_err = proc.communicate(timeout=10)
        except Exception:
            tail_out, tail_err = b"", b""
        out_chunks.append(tail_out or b"")
        err_chunks.append(tail_err or b"")
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
    stdout = b"".join(out_chunks).decode("utf-8", "replace")
    stderr = b"".join(err_chunks).decode("utf-8", "replace")
    if rate_hit or rate_limit_detected(stderr):
        return ProviderResult(rc=79, stdout=stdout, stderr=stderr)
    if timed_out:
        return ProviderResult(
            rc=RC_TIMEOUT, stdout=stdout, stderr=stderr, timed_out=True
        )
    if proc.returncode != 0:
        return ProviderResult(rc=proc.returncode, stdout=stdout, stderr=stderr)
    return ProviderResult(rc=0, stdout=stdout, stderr=stderr)


def run(
    agent: str,
    prompt_text: str,
    timeout: int,
    settings,
    label: str = "",
    state_dir: Path | None = None,
    abort_retry_allowed: bool = True,
) -> ProviderResult:
    """Run one opencode-family candidate with golden argv and guards."""
    model = model_from_agent(agent)
    binary = resolve_binary(settings.opencode_bin, settings.opencode_bin_fallback)
    if binary is None:
        return ProviderResult(rc=RC_FAILED, stderr="opencode_not_found")
    root = Path(state_dir) if state_dir is not None else Path.cwd()
    env = dict(os.environ)
    env.update(prepare_xdg(root))
    argv = [binary, "run", *agent_args(agent, label)]
    if _SPARK_GUARD_RE.match(agent):
        argv.append("--print-logs")
    argv += ["--model", model, prompt_text]

    with rotation_gate(root, settings) as gated:
        if not gated:
            return ProviderResult(rc=RC_TIMEOUT, stderr="rotation_gate_contention")
        with run_lock(root, model, settings) as locked:
            if not locked:
                return ProviderResult(rc=RC_TIMEOUT, stderr="run_lock_contention")
            watch = bool(_SPARK_GUARD_RE.match(agent))
            result = _stream_run(argv, timeout, env, watch_429=watch)
            if (
                settings.opencode_abort_retry
                and abort_retry_allowed
                and not agent.startswith("vercel:")
                and (result.rc != 0 or not result.stdout.strip())
                and result.rc != 79
            ):
                time.sleep(max(0, settings.opencode_abort_retry_wait_sec))
                result = _stream_run(argv, timeout, env, watch_429=watch)
    if result.rc != 0:
        return result
    text = clean_text(result.stdout)
    # Rate-limit wins over provider-error (see codex provider note).
    if rate_limit_detected(text):
        return ProviderResult(rc=79, stderr="rate_limited")
    if not text or provider_error_detected(text):
        return ProviderResult(rc=RC_FAILED, stderr="invalid_output")
    return ProviderResult(rc=0, stdout=text, resolved_model=model)
