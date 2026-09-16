"""Host-side hardened container launcher for the P5g canary worker (P5h).

The P5g orchestrator invokes this module as its external worker command.  Only
one episode directory and, for the candidate arm, one manifest file are mounted
into the container.  The repository and production NetHack playground are not
mounted.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

DEFAULT_IMAGE = "docich-nethack-canary:5.0.0-p5h"
DEFAULT_INNER_TIMEOUT_S = 840.0
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
_INTERNAL_ROOT = Path("/canary/episode")
_INTERNAL_ARENA = {
    "episode_root": "/canary/episode",
    "playground_dir": "/canary/episode/playground",
    "save_dir": "/canary/episode/playground/save",
    "xlogfile": "/canary/episode/playground/xlogfile",
    "dump_dir": "/canary/episode/playground/dumps",
}


class CanaryContainerError(RuntimeError):
    pass


def _under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _load_request(text: str) -> dict[str, object]:
    if len(text.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise CanaryContainerError("canary request exceeds size limit")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CanaryContainerError("canary request is not valid JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise CanaryContainerError("canary request schema is invalid")
    if raw.get("arm") not in {"baseline", "candidate"}:
        raise CanaryContainerError("canary arm is invalid")
    arena = raw.get("arena")
    if not isinstance(arena, dict) or set(arena) != set(_INTERNAL_ARENA):
        raise CanaryContainerError("canary host arena shape is invalid")
    return raw


def _host_arena(request: dict[str, object]) -> dict[str, Path]:
    arena = request.get("arena")
    assert isinstance(arena, dict)
    result: dict[str, Path] = {}
    for key in _INTERNAL_ARENA:
        raw = arena.get(key)
        if not isinstance(raw, str) or not raw:
            raise CanaryContainerError(f"canary arena.{key} is invalid")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise CanaryContainerError(f"canary arena.{key} must be absolute")
        result[key] = path.resolve()
    root = result["episode_root"]
    if not root.is_dir():
        raise CanaryContainerError("canary episode root does not exist")
    for key, path in result.items():
        if key == "episode_root":
            continue
        if not _under(path, root):
            raise CanaryContainerError(f"canary arena.{key} escapes episode root")
    return result


def _candidate_manifest(request: dict[str, object]) -> Path | None:
    controller = request.get("controller")
    if not isinstance(controller, dict):
        raise CanaryContainerError("canary controller is invalid")
    arm = request.get("arm")
    if arm == "baseline":
        if controller.get("kind") != "baseline_p3b":
            raise CanaryContainerError("baseline controller kind is invalid")
        return None
    if controller.get("kind") != "candidate_strategist":
        raise CanaryContainerError("candidate controller kind is invalid")
    raw = controller.get("manifest_path")
    if not isinstance(raw, str) or not raw:
        raise CanaryContainerError("candidate manifest path is missing")
    path = Path(raw).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise CanaryContainerError("candidate manifest must be an existing absolute file")
    return path.resolve()


def _runtime_name(explicit: str | None = None) -> str:
    value = explicit or os.environ.get("DOCICH_CANARY_RUNTIME", "").strip()
    if value:
        if value not in {"podman", "docker"}:
            raise CanaryContainerError("DOCICH_CANARY_RUNTIME must be podman or docker")
        if shutil.which(value) is None:
            raise CanaryContainerError(f"container runtime not found: {value}")
        return value
    if shutil.which("podman") is not None:
        return "podman"
    if shutil.which("docker") is not None:
        return "docker"
    raise CanaryContainerError("podman/docker is not available")


def _inner_timeout_s() -> float:
    raw = os.environ.get("DOCICH_CANARY_INNER_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_INNER_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError as exc:
        raise CanaryContainerError("DOCICH_CANARY_INNER_TIMEOUT_S must be numeric") from exc
    if not 10.0 <= value <= 7100.0:
        raise CanaryContainerError("DOCICH_CANARY_INNER_TIMEOUT_S must be 10-7100")
    return value


def _internal_request(
    request: dict[str, object], manifest: Path | None
) -> dict[str, object]:
    rewritten = json.loads(json.dumps(request))
    assert isinstance(rewritten, dict)
    rewritten["arena"] = dict(_INTERNAL_ARENA)
    if "wall_timeout_s" not in rewritten:
        rewritten["wall_timeout_s"] = _inner_timeout_s()
    requirements = rewritten.get("requirements")
    if not isinstance(requirements, dict):
        raise CanaryContainerError("canary requirements are invalid")
    if requirements.get("isolation_mode") != "container":
        raise CanaryContainerError("P5h launcher only supports container isolation")
    if requirements.get("production_state_must_remain_untouched") is not True:
        raise CanaryContainerError("production isolation requirement is missing")
    if requirements.get("wizard_mode") is not False or requirements.get("explore_mode") is not False:
        raise CanaryContainerError("wizard/explore canary is forbidden")
    if manifest is not None:
        controller = rewritten.get("controller")
        assert isinstance(controller, dict)
        controller["manifest_path"] = "/canary/candidate.json"
    return rewritten


def build_container_argv(
    request: dict[str, object],
    *,
    runtime: str,
    image: str,
    host_arena: dict[str, Path],
    manifest: Path | None,
) -> list[str]:
    root = host_arena["episode_root"]
    args = [
        runtime,
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--pids-limit=256",
        "--memory=1024m",
        "--cpus=1.0",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=268435456,mode=1777",
        "--env",
        "HOME=/tmp/home",
        "--env",
        "TERM=xterm-256color",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--mount",
        f"type=bind,src={root},dst={_INTERNAL_ROOT},rw",
    ]
    if runtime == "podman":
        args.extend(["--security-opt=no-new-privileges", "--userns=keep-id"])
    else:
        args.extend(
            [
                "--security-opt=no-new-privileges:true",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
            ]
        )
    if manifest is not None:
        args.extend(
            [
                "--mount",
                f"type=bind,src={manifest},dst=/canary/candidate.json,readonly",
            ]
        )
    args.append(image)
    return args


def _rewrite_result_arena(
    raw: dict[str, object], host_arena: dict[str, Path]
) -> dict[str, object]:
    if raw.get("worker_status") in {"timeout", "error"}:
        return raw
    arena = raw.get("arena")
    if arena != _INTERNAL_ARENA:
        raise CanaryContainerError("container worker arena attestation mismatch")
    if raw.get("isolation_mode") != "container":
        raise CanaryContainerError("container worker isolation attestation mismatch")
    if raw.get("production_state_touched") is not False:
        raise CanaryContainerError("container worker reported production access")
    result = dict(raw)
    result["arena"] = {key: str(host_arena[key]) for key in _INTERNAL_ARENA}
    return result


def run_container_worker(
    request_text: str,
    *,
    runtime: str | None = None,
    image: str | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    request = _load_request(request_text)
    arena = _host_arena(request)
    manifest = _candidate_manifest(request)
    selected_runtime = _runtime_name(runtime)
    selected_image = image or os.environ.get("DOCICH_NETHACK_CANARY_IMAGE", DEFAULT_IMAGE)
    if not selected_image or len(selected_image) > 256:
        raise CanaryContainerError("canary image name is invalid")
    internal = _internal_request(request, manifest)
    argv = build_container_argv(
        request,
        runtime=selected_runtime,
        image=selected_image,
        host_arena=arena,
        manifest=manifest,
    )
    payload = json.dumps(internal, ensure_ascii=False, separators=(",", ":"))
    inner_timeout = float(internal.get("wall_timeout_s", DEFAULT_INNER_TIMEOUT_S))
    try:
        completed = runner(
            argv,
            input=payload,
            text=True,
            capture_output=True,
            timeout=min(7200.0, inner_timeout + 30.0),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"schema_version": 1, "worker_status": "timeout", "error": "container launcher timeout"}
    except OSError as exc:
        return {
            "schema_version": 1,
            "worker_status": "error",
            "error": f"container launch failed: {str(exc)[:180]}",
        }
    returncode = getattr(completed, "returncode", None)
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    if returncode != 0:
        return {
            "schema_version": 1,
            "worker_status": "error",
            "error": f"container exit {returncode}: {str(stderr).strip()[:180]}",
        }
    if not isinstance(stdout, str) or len(stdout.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise CanaryContainerError("container worker response exceeds size limit")
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CanaryContainerError("container worker returned invalid JSON") from exc
    if not isinstance(raw, dict):
        raise CanaryContainerError("container worker result must be object")
    return _rewrite_result_arena(raw, arena)


def main() -> int:
    try:
        result = run_container_worker(sys.stdin.read())
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except CanaryContainerError as exc:
        print(
            json.dumps(
                {"schema_version": 1, "worker_status": "error", "error": str(exc)[:240]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
