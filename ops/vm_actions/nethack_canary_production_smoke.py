#!/usr/bin/env python3
"""One-shot production smoke for the isolated NetHack canary worker (P5i).

This script is intended to run only from the dedicated system-scope oneshot
unit which grants the process Docker socket access with SupplementaryGroups.
The regular VM gateway exec path remains Docker-less.

The smoke deliberately executes only the baseline P3b arm. Its purpose is to
prove the production host can build the reviewed immutable image and run one
real NetHack episode under Docker+gVisor without touching production NetHack
state. Candidate performance remains a separate P5g experiment.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from docich.config import load_global  # noqa: E402
from docich.game_switch import atomic_write_json  # noqa: E402
from docich.nethack_canary import (  # noqa: E402
    _production_fingerprint,
    _production_live,
)
from docich.nethack_canary_container import run_container_worker  # noqa: E402
from docich.nethack_run import load_nethack_persistence_settings  # noqa: E402

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EPOCH_RE = re.compile(r"^[0-9]{1,12}$")
RESULT_SCHEMA_VERSION = 1
EPOCH_FILE = ROOT / "ops" / "vm_actions" / "nethack_canary_smoke_epoch"
CONFIG_FILE = ROOT / "config" / "docich.soren-live.toml"
TRACKED_BUILD_PATHS = ("containers/nethack-canary", "src", "brains")
RELEVANT_RUNTIME_PATHS = (
    "ops/vm_actions/nethack_canary_production_smoke.py",
    "src/docich/nethack_canary_container.py",
    "src/docich/nethack_canary_worker.py",
    "src/docich/nethack_canary_executor.py",
    "src/docich/nethack_canary.py",
    "containers/nethack-canary/Dockerfile",
)


class SmokeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _run_checked(argv: list[str], *, cwd: Path = ROOT, timeout: float = 30.0, stdout=None, stderr=None):
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if stdout is None else stdout,
            stderr=subprocess.PIPE if stderr is None else stderr,
            timeout=timeout,
            check=False,
            text=stdout is None and stderr is None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeError("command_unavailable") from exc
    if result.returncode != 0:
        raise SmokeError("command_failed")
    return result


def _head_sha() -> str:
    result = _run_checked(["git", "rev-parse", "HEAD"])
    sha = (result.stdout or "").strip()
    if SHA_RE.fullmatch(sha) is None:
        raise SmokeError("head_invalid")
    # The service executes working-tree Python, so all security-relevant smoke
    # paths must exactly match the reviewed commit even though the build context
    # itself comes from git archive.
    diff = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *RELEVANT_RUNTIME_PATHS],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if diff.returncode != 0:
        raise SmokeError("reviewed_files_dirty")
    return sha


def _epoch() -> str:
    try:
        value = EPOCH_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SmokeError("epoch_unreadable") from exc
    if EPOCH_RE.fullmatch(value) is None:
        raise SmokeError("epoch_invalid")
    return value


def _safe_extract_git_archive(destination: Path) -> None:
    try:
        proc = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD", *TRACKED_BUILD_PATHS],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeError("git_archive_failed") from exc
    if proc.returncode != 0 or not proc.stdout:
        raise SmokeError("git_archive_failed")
    try:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as archive:
            # Python 3.12's data filter rejects absolute/escaping members.
            archive.extractall(destination, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise SmokeError("git_archive_invalid") from exc


def _build_image(context: Path, iidfile: Path, log_path: Path) -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise SmokeError("docker_missing")
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with log_path.open("wb") as log:
        os.chmod(log_path, 0o600)
        try:
            result = subprocess.run(
                [
                    docker,
                    "build",
                    "--network=host",
                    "--file",
                    str(context / "containers" / "nethack-canary" / "Dockerfile"),
                    "--iidfile",
                    str(iidfile),
                    str(context),
                ],
                cwd=str(context),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=1200,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SmokeError("image_build_unavailable") from exc
    if result.returncode != 0:
        raise SmokeError("image_build_failed")
    try:
        image = iidfile.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SmokeError("image_id_missing") from exc
    if IMAGE_RE.fullmatch(image) is None:
        raise SmokeError("image_id_invalid")
    return image


def _episode_request(arena_root: Path, sha: str, epoch: str) -> dict[str, object]:
    episode = arena_root / "episode-000"
    playground = episode / "playground"
    save = playground / "save"
    dumps = playground / "dumps"
    for path in (episode, playground, save, dumps):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    digest = sha[:8]
    return {
        "schema_version": 1,
        "experiment_id": f"ops-smoke-{digest}-{epoch}"[:80],
        "episode_id": "000",
        "arm": "baseline",
        "arena": {
            "episode_root": str(episode.resolve()),
            "playground_dir": str(playground.resolve()),
            "save_dir": str(save.resolve()),
            "xlogfile": str((playground / "xlogfile").resolve()),
            "dump_dir": str(dumps.resolve()),
        },
        "player_name": f"canary_smoke_{digest}"[:31],
        "max_turns": 250,
        "seed": None,
        "controller": {"kind": "baseline_p3b"},
        "requirements": {
            "isolation_mode": "container",
            "production_state_must_remain_untouched": True,
            "wizard_mode": False,
            "explore_mode": False,
        },
    }


def _validate_smoke_result(result: object, request: dict[str, object]) -> tuple[str, str]:
    if not isinstance(result, dict):
        raise SmokeError("worker_result_invalid")
    if result.get("worker_status") != "completed":
        raise SmokeError("worker_not_completed")
    if result.get("arm") != "baseline" or result.get("episode_id") != "000":
        raise SmokeError("worker_identity_mismatch")
    if result.get("isolation_mode") != "container":
        raise SmokeError("worker_isolation_mismatch")
    if result.get("production_state_touched") is not False:
        raise SmokeError("worker_reports_production_touch")
    if result.get("candidate_action_source") != "baseline_p3b":
        raise SmokeError("worker_action_source_invalid")
    if result.get("arena") != request.get("arena"):
        raise SmokeError("worker_arena_mismatch")
    terminal = result.get("terminal_status")
    if terminal not in {"dead", "ascended", "ended", "ended_unknown", "timeout"}:
        raise SmokeError("worker_terminal_invalid")
    exit_reason = result.get("exit_reason")
    if not isinstance(exit_reason, str) or not exit_reason or len(exit_reason) > 160:
        raise SmokeError("worker_exit_reason_invalid")
    if exit_reason == "process_exited_without_terminal_xlog":
        raise SmokeError("worker_process_exit_without_xlog")
    return str(terminal), exit_reason


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main() -> int:
    started = _utc_now()
    sha = ""
    epoch = ""
    state_dir: Path | None = None
    result_path: Path | None = None
    try:
        sha = _head_sha()
        epoch = _epoch()
        g = load_global(ROOT, CONFIG_FILE)
        state_dir = Path(g.state_dir)
        root = state_dir / "nethack" / "production-smoke"
        result_path = root / "result.json"
        lock_path = root / ".lock"
        settings = load_nethack_persistence_settings(g)
        if settings is None:
            raise SmokeError("nethack_persistence_disabled")

        with _exclusive_lock(lock_path):
            if _production_live(state_dir):
                raise SmokeError("production_nethack_active")
            before = _production_fingerprint(settings)

            run_root = root / "runs" / f"{epoch}-{sha[:12]}"
            if run_root.exists():
                shutil.rmtree(run_root)
            run_root.mkdir(parents=True, exist_ok=False, mode=0o700)
            os.chmod(run_root, 0o700)
            build_log = run_root / "build.log"
            iidfile = run_root / "image-id"
            with tempfile.TemporaryDirectory(prefix="build-", dir=run_root) as tmp:
                context = Path(tmp)
                _safe_extract_git_archive(context)
                image = _build_image(context, iidfile, build_log)

            # A scheduled NetHack corner may have started while the image was
            # building. Never launch the canary in that case.
            if _production_live(state_dir) or _production_fingerprint(settings) != before:
                raise SmokeError("production_changed_during_build")

            request = _episode_request(run_root, sha, epoch)
            old_image = os.environ.get("DOCICH_NETHACK_CANARY_IMAGE")
            old_timeout = os.environ.get("DOCICH_CANARY_INNER_TIMEOUT_S")
            os.environ["DOCICH_NETHACK_CANARY_IMAGE"] = image
            os.environ["DOCICH_CANARY_INNER_TIMEOUT_S"] = "90"
            try:
                worker = run_container_worker(json.dumps(request, ensure_ascii=False))
            finally:
                if old_image is None:
                    os.environ.pop("DOCICH_NETHACK_CANARY_IMAGE", None)
                else:
                    os.environ["DOCICH_NETHACK_CANARY_IMAGE"] = old_image
                if old_timeout is None:
                    os.environ.pop("DOCICH_CANARY_INNER_TIMEOUT_S", None)
                else:
                    os.environ["DOCICH_CANARY_INNER_TIMEOUT_S"] = old_timeout

            terminal, exit_reason = _validate_smoke_result(worker, request)
            after = _production_fingerprint(settings)
            if _production_live(state_dir) or after != before:
                raise SmokeError("production_changed_during_canary")

            payload = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "status": "success",
                "sha": sha,
                "epoch": epoch,
                "completed_at": _utc_now(),
                "started_at": started,
                "image_id": image,
                "runsc_required": True,
                "production_untouched": True,
                "arm": "baseline",
                "worker_status": "completed",
                "terminal_status": terminal,
                "exit_reason": exit_reason,
                "policy_effect": "none",
            }
            atomic_write_json(result_path, payload)
            return 0
    except SmokeError as exc:
        if result_path is not None and sha and epoch:
            atomic_write_json(
                result_path,
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "status": "error",
                    "sha": sha,
                    "epoch": epoch,
                    "completed_at": _utc_now(),
                    "started_at": started,
                    "error_code": exc.code,
                    "production_untouched": exc.code
                    not in {"production_changed_during_build", "production_changed_during_canary"},
                    "policy_effect": "none",
                },
            )
        return 2
    except Exception:
        if result_path is not None and sha and epoch:
            atomic_write_json(
                result_path,
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "status": "error",
                    "sha": sha,
                    "epoch": epoch,
                    "completed_at": _utc_now(),
                    "started_at": started,
                    "error_code": "unexpected_failure",
                    "production_untouched": False,
                    "policy_effect": "none",
                },
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
