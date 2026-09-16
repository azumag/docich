#!/usr/bin/env python3
"""Production-only one-shot NetHack canary smoke (P5i).

This process must run only from the reviewed system unit that grants the
process-scoped docker supplementary group. The normal VM gateway remains
unprivileged and communicates through a bounded request/result spool.
"""
from __future__ import annotations

import grp
import io
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SOURCE_ROOT = Path(__file__).resolve().parents[2]
SRC = SOURCE_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from docich.config import load_global
from docich.nethack_canary_container import IMAGE_RE, run_container_worker
from docich.nethack_run import load_nethack_persistence_settings

REQUEST_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
REQUEST_DIR = Path("/home/ubuntu/.local/state/docich/nethack-canary-smoke")
REQUEST_PATH = REQUEST_DIR / "request.json"
RESULT_DIR = REQUEST_DIR / "results"
CONFIG_PATH = SOURCE_ROOT / "config" / "docich.soren-live.toml"
USER = "ubuntu"
MAX_REQUEST_BYTES = 4096
MAX_TURNS = 1000
INNER_TIMEOUT_S = "180"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
ALLOWED_RESULT_CATEGORIES = frozenset({"terminal", "policy_stall", "turn_limit", "other_timeout"})

FAILURE_CATEGORIES = frozenset(
    {
        "wrong_user",
        "persistent_docker_membership",
        "docker_scope_missing",
        "repo_head_mismatch",
        "reviewed_checkout_drift",
        "production_state_invalid",
        "production_active",
        "persistence_disabled",
        "canary_path_overlap",
        "canary_container_busy",
        "image_build_failed",
        "image_build_timeout",
        "image_id_invalid",
        "worker_error",
        "worker_timeout",
        "worker_result_invalid",
        "production_state_changed",
        "container_cleanup_failed",
        "unexpected_failure",
    }
)


class SmokeError(RuntimeError):
    def __init__(self, category: str):
        if category not in FAILURE_CATEGORIES:
            category = "unexpected_failure"
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class SmokeRequest:
    request_id: str
    sha: str


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=".nethack-smoke-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            out.write("\n")
            out.flush()
            os.fchmod(out.fileno(), 0o600)
            os.fsync(out.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def parse_request_payload(raw: object) -> SmokeRequest:
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "request_id", "sha"}:
        raise ValueError("invalid request shape")
    if raw.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise ValueError("invalid request schema")
    request_id = raw.get("request_id")
    sha = raw.get("sha")
    if not isinstance(request_id, str) or REQUEST_ID_RE.fullmatch(request_id) is None:
        raise ValueError("invalid request id")
    if not isinstance(sha, str) or SHA_RE.fullmatch(sha) is None:
        raise ValueError("invalid sha")
    return SmokeRequest(request_id=request_id, sha=sha)


def consume_request(path: Path = REQUEST_PATH) -> SmokeRequest:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("request must be mode 0600 regular file")
        if info.st_uid != os.geteuid() or info.st_size <= 0 or info.st_size > MAX_REQUEST_BYTES:
            raise ValueError("request ownership/size invalid")
        data = os.read(fd, MAX_REQUEST_BYTES + 1)
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
    finally:
        os.close(fd)
    try:
        raw = json.loads(data.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("request JSON invalid") from exc
    request = parse_request_payload(raw)
    path.unlink()
    return request


def verify_docker_scope(
    *,
    user: str,
    primary_gid: int,
    docker_gid: int,
    persistent_members: tuple[str, ...],
    effective_gids: tuple[int, ...],
) -> None:
    if user != USER:
        raise SmokeError("wrong_user")
    if primary_gid == docker_gid or user in persistent_members:
        raise SmokeError("persistent_docker_membership")
    if docker_gid not in effective_gids:
        raise SmokeError("docker_scope_missing")


def _verify_process_scope() -> None:
    account = pwd.getpwuid(os.geteuid())
    try:
        docker = grp.getgrnam("docker")
    except KeyError as exc:
        raise SmokeError("docker_scope_missing") from exc
    verify_docker_scope(
        user=account.pw_name,
        primary_gid=account.pw_gid,
        docker_gid=docker.gr_gid,
        persistent_members=tuple(docker.gr_mem),
        effective_gids=tuple({os.getegid(), *os.getgroups()}),
    )


def _git_head(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise SmokeError("repo_head_mismatch") from exc


# The canary image must contain exactly the reviewed commit's build inputs. The
# production checkout is long-lived and carries legitimate untracked runtime
# state (improve-daemon outputs, backups, ``._*`` metadata), so the Docker build
# context is materialized from the commit object instead of the working tree.
# Only paths tracked at the requested SHA can ever reach the image.
REVIEWED_BUILD_PATHS = ("src", "brains", "containers/nethack-canary")
DOCKERFILE_REL = Path("containers") / "nethack-canary" / "Dockerfile"


def _export_reviewed_build_context(root: Path, sha: str, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, mode=0o700)
    try:
        os.chmod(dest, 0o700)
    except OSError as exc:
        raise SmokeError("reviewed_checkout_drift") from exc
    try:
        proc = subprocess.run(
            [
                "git", "-C", str(root), "-c", "core.hooksPath=/dev/null",
                "archive", "--format=tar", sha, *REVIEWED_BUILD_PATHS,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeError("reviewed_checkout_drift") from exc
    if proc.returncode or not proc.stdout:
        raise SmokeError("reviewed_checkout_drift")
    try:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as tar:
            try:
                tar.extractall(path=dest, filter="data")
            except TypeError:  # Python < 3.11.4 has no extraction filter.
                tar.extractall(path=dest)
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise SmokeError("reviewed_checkout_drift") from exc
    if not (dest / "src").is_dir() or not (dest / "brains").is_dir():
        raise SmokeError("reviewed_checkout_drift")
    if not (dest / DOCKERFILE_REL).is_file():
        raise SmokeError("reviewed_checkout_drift")
    return dest


def _load_json_strict(path: Path) -> dict[str, object] | None:
    try:
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise SmokeError("production_state_invalid")
        value = json.loads(path.read_text(encoding="utf-8"))
    except SmokeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SmokeError("production_state_invalid") from exc
    if not isinstance(value, dict):
        raise SmokeError("production_state_invalid")
    return value


def _assert_production_inactive(state_dir: Path) -> None:
    switch = _load_json_strict(state_dir / "game_switch.json")
    if switch is not None:
        active = switch.get("active")
        if switch.get("phase") == "ready" and isinstance(active, dict) and active.get("game") == "nethack":
            raise SmokeError("production_active")

    root = state_dir / "nethack"
    current = _load_json_strict(root / "current.json")
    if current is None:
        return
    run_id = current.get("run_id")
    if run_id is None:
        return
    if not isinstance(run_id, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", run_id
    ):
        raise SmokeError("production_state_invalid")
    run = _load_json_strict(root / "runs" / f"{run_id}.json")
    if run is None or run.get("run_id") != run_id:
        raise SmokeError("production_state_invalid")
    if run.get("status") == "active":
        raise SmokeError("production_active")


def _path_fingerprint(path: Path) -> object:
    if not path.exists():
        return {"exists": False}
    try:
        if path.is_symlink():
            raise SmokeError("production_state_invalid")
        if path.is_file():
            info = path.stat()
            return {"exists": True, "kind": "file", "size": info.st_size, "mtime_ns": info.st_mtime_ns}
        if path.is_dir():
            entries: list[tuple[str, int, int]] = []
            for item in sorted(path.iterdir(), key=lambda p: p.name):
                if item.is_symlink():
                    raise SmokeError("production_state_invalid")
                info = item.stat()
                entries.append((item.name, info.st_size, info.st_mtime_ns))
            return {"exists": True, "kind": "dir", "entries": entries}
    except SmokeError:
        raise
    except OSError as exc:
        raise SmokeError("production_state_invalid") from exc
    raise SmokeError("production_state_invalid")


def _production_fingerprint(settings) -> dict[str, object]:
    return {
        "save_dir": _path_fingerprint(settings.save_dir),
        "xlogfile": _path_fingerprint(settings.xlogfile),
        "dump_dir": _path_fingerprint(settings.dump_dir),
    }


def _under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _assert_disjoint(canary_root: Path, settings) -> None:
    for production in (settings.save_dir, settings.xlogfile, settings.dump_dir):
        if _under(canary_root, production) or _under(production, canary_root):
            raise SmokeError("canary_path_overlap")


def _canary_container_ids(docker: str) -> tuple[str, ...]:
    try:
        proc = subprocess.run(
            [docker, "ps", "-aq", "--filter", "label=org.docich.nethack-canary=1"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeError("canary_container_busy") from exc
    if proc.returncode:
        raise SmokeError("canary_container_busy")
    ids = tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())
    if any(re.fullmatch(r"[0-9a-f]{12,64}", item) is None for item in ids):
        raise SmokeError("canary_container_busy")
    return ids


def _build_image(context: Path, setup: Path, *, docker: str) -> str:
    setup.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(setup, 0o700)
    iid = setup / "image-id"
    try:
        iid.unlink()
    except FileNotFoundError:
        pass
    try:
        proc = subprocess.run(
            [
                docker,
                "build",
                # The reviewed host state intentionally leaves containers without
                # egress (iptables=false / ip-forward=false), while building the
                # pinned NetHack image needs apt/curl/fetch-lua. Use the host
                # network for this build step only; the canary episode itself
                # always runs with --network=none.
                "--network=host",
                "--file",
                str(context / DOCKERFILE_REL),
                "--iidfile",
                str(iid),
                ".",
            ],
            cwd=context,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeError("image_build_timeout") from exc
    except OSError as exc:
        raise SmokeError("image_build_failed") from exc
    if proc.returncode:
        raise SmokeError("image_build_failed")
    try:
        image = iid.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise SmokeError("image_id_invalid") from exc
    if IMAGE_RE.fullmatch(image) is None:
        raise SmokeError("image_id_invalid")
    return image


def classify_worker_result(result: object) -> str:
    if not isinstance(result, dict):
        raise SmokeError("worker_result_invalid")
    status = result.get("worker_status")
    if status == "error":
        raise SmokeError("worker_error")
    if status == "timeout":
        raise SmokeError("worker_timeout")
    if status != "completed":
        raise SmokeError("worker_result_invalid")
    if result.get("arm") != "baseline" or result.get("isolation_mode") != "container":
        raise SmokeError("worker_result_invalid")
    if result.get("production_state_touched") is not False:
        raise SmokeError("worker_result_invalid")
    if result.get("candidate_action_source") != "baseline_p3b":
        raise SmokeError("worker_result_invalid")
    terminal = result.get("terminal_status")
    if terminal not in {"dead", "ascended", "ended", "ended_unknown", "timeout"}:
        raise SmokeError("worker_result_invalid")
    if terminal != "timeout":
        return "terminal"
    reason = result.get("exit_reason")
    if not isinstance(reason, str) or not reason or len(reason) > 240:
        raise SmokeError("worker_result_invalid")
    if reason.startswith("policy_stall:"):
        return "policy_stall"
    if reason in {"max_turns", "turn_limit"} or reason.startswith("max_turns:"):
        return "turn_limit"
    return "other_timeout"


def _arena(root: Path) -> dict[str, Path]:
    playground = root / "playground"
    save = playground / "save"
    dumps = playground / "dumps"
    for path in (root, playground, save, dumps):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    return {
        "episode_root": root.resolve(),
        "playground_dir": playground.resolve(),
        "save_dir": save.resolve(),
        "xlogfile": (playground / "xlogfile").resolve(),
        "dump_dir": dumps.resolve(),
    }


def _worker_request(request: SmokeRequest, arena: dict[str, Path]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": f"p5i-{request.request_id[:16]}",
        "episode_id": "000",
        "arm": "baseline",
        "arena": {key: str(value) for key, value in arena.items()},
        "player_name": f"canary_b_{request.request_id[:12]}",
        "max_turns": MAX_TURNS,
        "seed": None,
        "controller": {"kind": "baseline_p3b"},
        "requirements": {
            "isolation_mode": "container",
            "production_state_must_remain_untouched": True,
            "wizard_mode": False,
            "explore_mode": False,
        },
    }


def run_smoke(
    request: SmokeRequest,
    *,
    root: Path = SOURCE_ROOT,
    docker: str = "/usr/bin/docker",
    build_image: Callable[..., str] = _build_image,
    worker: Callable[..., dict[str, object]] = run_container_worker,
) -> dict[str, object]:
    _verify_process_scope()
    if _git_head(root) != request.sha:
        raise SmokeError("repo_head_mismatch")

    g = load_global(root, root / "config" / "docich.soren-live.toml")
    state_dir = Path(g.state_dir).resolve()
    settings = load_nethack_persistence_settings(g)
    if settings is None:
        raise SmokeError("persistence_disabled")
    _assert_production_inactive(state_dir)

    smoke_root = state_dir / "nethack" / "canary" / "p5i-smoke" / request.request_id
    _assert_disjoint(smoke_root, settings)
    before = _production_fingerprint(settings)

    if _canary_container_ids(docker):
        raise SmokeError("canary_container_busy")

    setup = state_dir / "nethack" / "canary" / "p5i-smoke" / "_images" / request.sha
    context = setup / "context"
    result: dict[str, object] | None = None
    category: str | None = None
    failure: Exception | None = None
    old_timeout = os.environ.get("DOCICH_CANARY_INNER_TIMEOUT_S")
    try:
        _export_reviewed_build_context(root, request.sha, context)
        image = build_image(context, setup, docker=docker)
        arena = _arena(smoke_root / "baseline" / "episode-000")
        payload = _worker_request(request, arena)
        os.environ["DOCICH_CANARY_INNER_TIMEOUT_S"] = INNER_TIMEOUT_S
        result = worker(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            docker=docker,
            image=image,
        )
        category = classify_worker_result(result)
    except Exception as exc:
        failure = exc
    finally:
        if old_timeout is None:
            os.environ.pop("DOCICH_CANARY_INNER_TIMEOUT_S", None)
        else:
            os.environ["DOCICH_CANARY_INNER_TIMEOUT_S"] = old_timeout

    after = _production_fingerprint(settings)
    if before != after:
        raise SmokeError("production_state_changed")
    if _canary_container_ids(docker):
        raise SmokeError("container_cleanup_failed")
    if failure is not None:
        raise failure
    assert result is not None and category is not None

    terminal = result.get("terminal_status")
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "passed",
        "category": category,
        "request_id": request.request_id,
        "sha": request.sha,
        "worker_status": "completed",
        "terminal_status": terminal,
        "exit_reason": result.get("exit_reason") if isinstance(result.get("exit_reason"), str) else None,
        "turns": result.get("turns") if type(result.get("turns")) is int else None,
        "max_depth": result.get("max_depth") if type(result.get("max_depth")) is int else None,
        "score": result.get("score") if type(result.get("score")) is int else None,
        "production_fingerprint_unchanged": True,
        "production_config_changed": False,
        "isolation_mode": "container",
        "seed_applied": result.get("seed_applied") is True,
    }
    if report["category"] not in ALLOWED_RESULT_CATEGORIES:
        raise SmokeError("worker_result_invalid")
    _atomic_json(smoke_root / "report.json", report)
    return report


def _failure_report(request: SmokeRequest, category: str) -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "failed",
        "category": category if category in FAILURE_CATEGORIES else "unexpected_failure",
        "request_id": request.request_id,
        "sha": request.sha,
        "production_config_changed": False,
    }


def main() -> int:
    try:
        request = consume_request()
    except (OSError, ValueError):
        return 2

    result_path = RESULT_DIR / f"{request.request_id}.json"
    if result_path.exists():
        return 0

    try:
        report = run_smoke(request)
    except SmokeError as exc:
        report = _failure_report(request, exc.category)
    except Exception:
        report = _failure_report(request, "unexpected_failure")
    _atomic_json(result_path, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
