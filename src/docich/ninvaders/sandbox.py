"""Isolation for LLM-authored nInvaders policies (stdlib only).

A *policy* is a Python source file that defines::

    def decide(obs, state) -> list[str]     # keys: "Left" / "Right" / "Space"

It is never imported into the docich process.  Three independent layers:

1. ``validate_policy_source``: static AST gate (import whitelist, no dunder
   access, no reflection/IO builtins, size/shape limits).
2. the worker (``python -I sandbox.py --worker POLICY``): separate process,
   scrubbed environment (no secrets), restricted ``__builtins__`` with a
   guarded ``__import__``, rlimits (CPU, files, address space on Linux).
3. ``PolicyProcess``: the parent side.  Frames go in as JSON lines, keys come
   out as JSON lines; a per-tick wall-clock timeout kills a hung worker.

The static gate is a filter for honest mistakes and casual abuse, not a
security proof; the process boundary, empty environment and rlimits are what
bound the blast radius.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VALID_KEYS = ("Left", "Right", "Space")
MAX_SOURCE_BYTES = 24_000
MAX_AST_NODES = 6_000
ALLOWED_IMPORTS = frozenset({"math"})
FORBIDDEN_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "input", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "dir",
    "help", "exit", "quit", "memoryview", "id", "type", "object", "super",
})
FORBIDDEN_ATTRS = frozenset({
    "mro", "gi_frame", "gi_code", "f_globals", "f_locals", "f_back",
    "f_builtins", "tb_frame", "cr_frame", "ag_frame", "co_code", "func_globals",
})
SAFE_BUILTINS = (
    "abs all any bool dict divmod enumerate filter float frozenset int "
    "isinstance issubclass iter len list map max min next pow range "
    "repr reversed round set slice sorted str sum tuple zip chr ord hash "
    "Exception ValueError KeyError IndexError TypeError ZeroDivisionError "
    "ArithmeticError StopIteration AttributeError NotImplemented"
).split()


class PolicyRejected(ValueError):
    """The policy source failed the static gate."""


def policy_sha(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]


def validate_policy_source(source: str) -> ast.Module:
    if not isinstance(source, str) or not source.strip():
        raise PolicyRejected("空のポリシーです")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise PolicyRejected(f"ポリシーが大きすぎます (>{MAX_SOURCE_BYTES}B)")
    try:
        tree = ast.parse(source, "<policy>")
    except SyntaxError as exc:
        raise PolicyRejected(f"構文エラー: {exc.msg} (line {exc.lineno})") from exc
    count = 0
    decide_ok = False
    for node in ast.walk(tree):
        count += 1
        if count > MAX_AST_NODES:
            raise PolicyRejected("ポリシーが複雑すぎます")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS or "." in alias.name:
                    raise PolicyRejected(f"許可されないimport: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or (node.module or "").split(".")[0] not in ALLOWED_IMPORTS \
                    or "." in (node.module or ""):
                raise PolicyRejected(f"許可されないimport: {node.module}")
            if any(a.name == "*" for a in node.names):
                raise PolicyRejected("from ... import * は禁止です")
            if any(a.name.startswith("_") for a in node.names):
                raise PolicyRejected("private import は禁止です")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                raise PolicyRejected(f"禁止された名前: {node.id}")
            if node.id.startswith("__") and node.id != "__name__":
                raise PolicyRejected(f"dunder名は禁止です: {node.id}")
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr in FORBIDDEN_ATTRS or attr.startswith("__"):
                raise PolicyRejected(f"禁止された属性: {attr}")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            raise PolicyRejected("async構文は禁止です")
        elif isinstance(node, ast.ClassDef):
            raise PolicyRejected("class定義は禁止です")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "decide":
            a = node.args
            if len(a.args) == 2 and not a.vararg and not a.kwarg and not a.kwonlyargs:
                decide_ok = True
    if not decide_ok:
        raise PolicyRejected("トップレベルに decide(obs, state) が必要です")
    return tree


def sanitize_keys(keys) -> list[str]:
    """Only Left/Right/Space, deduplicated, at most 3; Left+Right cancel."""
    if not isinstance(keys, (list, tuple)):
        return []
    out: list[str] = []
    for k in keys:
        if k in VALID_KEYS and k not in out:
            out.append(k)
    if "Left" in out and "Right" in out:
        out = [k for k in out if k not in ("Left", "Right")]
    return out[:3]


# ---------------------------------------------------------------- worker side

def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    requested = tuple(fromlist or ())
    if (level != 0 or name.split(".")[0] not in ALLOWED_IMPORTS or "." in name
            or any(part == "*" or str(part).startswith("_") for part in requested)):
        raise ImportError(f"import not allowed: {name}")
    return __import__(name, globals, locals, fromlist, level)


def _worker_builtins() -> dict:
    import builtins

    table = {n: getattr(builtins, n) for n in SAFE_BUILTINS if hasattr(builtins, n)}
    table.update({
        "True": True, "False": False, "None": None,
        "__import__": _guarded_import,
        "__name__": "policy",
        "print": lambda *a, **k: None,
    })
    return table


def _apply_limits(cpu_seconds: int) -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    if sys.platform.startswith("linux"):
        # The policy only needs a pane-sized observation and simple math.
        # Bound a single worker to 256 MiB so parallel arena runs remain small.
        resource.setrlimit(resource.RLIMIT_AS, (256 << 20, 256 << 20))


def _worker_main(path: str, cpu_seconds: int) -> int:
    source = Path(path).read_text(encoding="utf-8")
    tree = validate_policy_source(source)  # defence in depth: parent also validates
    code = compile(tree, path, "exec")
    _apply_limits(cpu_seconds)
    namespace = {"__builtins__": _worker_builtins(), "__name__": "policy"}
    exec(code, namespace)  # noqa: S102 - the sandbox exists precisely to run this
    decide = namespace["decide"]
    state: dict = {}
    for line in sys.stdin:
        try:
            request = json.loads(line)
            reply = {"keys": sanitize_keys(decide(request["obs"], state))}
        except BaseException as exc:  # noqa: BLE001 - includes RecursionError
            reply = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
    return 0


# ---------------------------------------------------------------- parent side

class PolicyProcess:
    """One worker per match.  ``tick`` never raises and never blocks past its timeout."""

    def __init__(self, policy_path, *, tick_timeout_s: float = 0.4,
                 cpu_seconds: int = 600, max_restarts: int = 3):
        self.path = str(Path(policy_path).resolve())  # the worker runs with cwd=/
        self.tick_timeout_s = float(tick_timeout_s)
        self.cpu_seconds = int(cpu_seconds)
        self.max_restarts = int(max_restarts)
        self.ticks = self.timeouts = self.errors = self.restarts = 0
        self.last_error: str | None = None
        self.dead = False
        self._proc: subprocess.Popen | None = None
        self._err = None
        self._buf = b""
        # Reject bad source before any process is spawned.
        validate_policy_source(Path(self.path).read_text(encoding="utf-8"))
        self._spawn()

    def _spawn(self) -> None:
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8",
               "PYTHONHASHSEED": "0"}
        self._buf = b""
        if self._err is not None:
            self._err.close()
        self._err = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).resolve()), "--worker",
             self.path, str(self.cpu_seconds)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err,
            env=env, cwd="/", close_fds=True, start_new_session=True,
        )

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    def _readline(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        fd = self._proc.stdout.fileno()
        while b"\n" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                return None
            chunk = os.read(fd, 65536)
            if not chunk:
                return b""
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line

    def _stderr_tail(self) -> str:
        try:
            self._err.seek(0)
            data = self._err.read()[-240:].decode("utf-8", "replace").strip().replace("\n", " | ")
            return data
        except (OSError, ValueError, AttributeError):
            return ""

    def _restart_or_die(self, reason: str) -> None:
        tail = self._stderr_tail()
        self.last_error = f"{reason}: {tail}" if tail else reason
        self._kill()
        if self.restarts >= self.max_restarts:
            self.dead = True
            return
        self.restarts += 1
        try:
            self._spawn()
        except OSError as exc:
            self.last_error = f"spawn failed: {exc}"
            self.dead = True

    def tick(self, obs: dict, timeout_s: float | None = None) -> list[str]:
        if self.dead or self._proc is None:
            return []
        self.ticks += 1
        try:
            self._proc.stdin.write((json.dumps({"obs": obs}) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
            raw = self._readline(timeout_s if timeout_s is not None else self.tick_timeout_s)
        except (BrokenPipeError, OSError, ValueError):
            self.errors += 1
            self._restart_or_die("worker pipe closed")
            return []
        if raw is None:
            self.timeouts += 1
            self._restart_or_die("tick timeout")
            return []
        if raw == b"":
            self.errors += 1
            self._restart_or_die("worker exited")
            return []
        try:
            reply = json.loads(raw)
        except ValueError:
            self.errors += 1
            self.last_error = "bad reply"
            return []
        if "error" in reply:
            self.errors += 1
            self.last_error = str(reply["error"])
            return []
        return sanitize_keys(reply.get("keys"))

    def stats(self) -> dict:
        return {"ticks": self.ticks, "timeouts": self.timeouts, "errors": self.errors,
                "restarts": self.restarts, "dead": self.dead, "last_error": self.last_error}

    def close(self) -> None:
        self._kill()
        if self._err is not None:
            self._err.close()
            self._err = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        raise SystemExit(_worker_main(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 600))
    print("usage: sandbox.py --worker POLICY [CPU_SECONDS]", file=sys.stderr)
    raise SystemExit(2)
