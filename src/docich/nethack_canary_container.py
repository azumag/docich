"""Host-side Docker/runsc launcher for the P5h controlled canary.

Candidate code and NetHack evidence never share a mount namespace. The game
container receives the episode arena plus a Unix-socket IPC directory. A
sibling candidate broker receives only that IPC directory and the read-only
candidate manifest. Public StrategicRequest/StrategicProposal JSON is the only
bridge between them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable

from .nethack_candidate_eval import load_candidate_manifest

DEFAULT_INNER_TIMEOUT_S = 840.0
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CANARY_ABI = "1"
NETHACK_VERSION = "5.0.0"
NETHACK_SOURCE_SHA256 = "2959b7886aac76185b90aea0c9f80d14343f604de0ae96b3dd2a760f7ab3bde9"
_INTERNAL_ROOT = Path("/canary/episode")
_INTERNAL_IPC_DIR = Path("/canary/episode/.candidate-ipc")
_BROKER_IPC_DIR = Path("/canary/ipc")
_BROKER_SOCKET = _INTERNAL_IPC_DIR / "candidate.sock"
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
        if key != "episode_root" and not _under(path, root):
            raise CanaryContainerError(f"canary arena.{key} escapes episode root")
    return result


def _candidate_manifest(request: dict[str, object]) -> Path | None:
    controller = request.get("controller")
    if not isinstance(controller, dict):
        raise CanaryContainerError("canary controller is invalid")
    if request.get("arm") == "baseline":
        if controller != {"kind": "baseline_p3b"}:
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
    path = path.resolve()
    manifest = load_candidate_manifest(path)
    expected = {
        "candidate_id": manifest.candidate_id,
        "candidate_version": manifest.version,
        "candidate_fingerprint": manifest.fingerprint,
        "command_sha256": manifest.command_hash,
    }
    for key, value in expected.items():
        if controller.get(key) != value:
            raise CanaryContainerError(f"candidate manifest identity mismatch: {key}")
    return path


def _docker_binary(explicit: str | None = None) -> str:
    value = explicit or os.environ.get("DOCICH_CANARY_DOCKER", "docker").strip()
    if not value or Path(value).name != "docker":
        raise CanaryContainerError("P5h requires the reviewed Docker/runsc runtime")
    resolved = shutil.which(value)
    if resolved is None:
        raise CanaryContainerError("docker is not available")
    return resolved


def _image_id(explicit: str | None = None) -> str:
    value = explicit or os.environ.get("DOCICH_NETHACK_CANARY_IMAGE", "").strip()
    if not IMAGE_RE.fullmatch(value):
        raise CanaryContainerError(
            "DOCICH_NETHACK_CANARY_IMAGE must be an immutable sha256 image ID"
        )
    return value


def _inner_timeout_s() -> float:
    raw = os.environ.get("DOCICH_CANARY_INNER_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_INNER_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError as exc:
        raise CanaryContainerError("DOCICH_CANARY_INNER_TIMEOUT_S must be numeric") from exc
    if not 10.0 <= value <= 7000.0:
        raise CanaryContainerError("DOCICH_CANARY_INNER_TIMEOUT_S must be 10-7000")
    return value


def _checked(
    runner: Callable[..., object], argv: list[str], *, timeout: float = 10.0
) -> object:
    try:
        result = runner(
            argv,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CanaryContainerError("docker control unavailable") from exc
    if getattr(result, "returncode", 1) != 0:
        raise CanaryContainerError("docker control failed")
    return result


def _preflight(
    docker: str,
    image: str,
    *,
    runner: Callable[..., object],
) -> None:
    info_format = (
        '{"OSType":{{json .OSType}},"Runtimes":{{json .Runtimes}},'
        '"MemoryLimit":{{json .MemoryLimit}},"PidsLimit":{{json .PidsLimit}},'
        '"CPUCfsQuota":{{json .CPUCfsQuota}}}'
    )
    info_result = _checked(runner, [docker, "info", "--format", info_format])
    try:
        info = json.loads(str(getattr(info_result, "stdout", "")))
    except json.JSONDecodeError as exc:
        raise CanaryContainerError("docker capability response is invalid") from exc
    if (
        not isinstance(info, dict)
        or info.get("OSType") != "linux"
        or "runsc" not in (info.get("Runtimes") or {})
    ):
        raise CanaryContainerError("gVisor runsc is required")
    if not info.get("MemoryLimit") or not info.get("PidsLimit") or not info.get("CPUCfsQuota"):
        raise CanaryContainerError("docker resource limits are unavailable")

    inspect_result = _checked(runner, [docker, "image", "inspect", image])
    try:
        inspected = json.loads(str(getattr(inspect_result, "stdout", "")))
    except json.JSONDecodeError as exc:
        raise CanaryContainerError("canary image inspect response is invalid") from exc
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
        raise CanaryContainerError("canary image inspect response is invalid")
    entry = inspected[0]
    if entry.get("Id") != image:
        raise CanaryContainerError("canary image ID does not match immutable image")
    config = entry.get("Config")
    if not isinstance(config, dict) or config.get("Volumes"):
        raise CanaryContainerError("canary image declares unexpected volumes")
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        raise CanaryContainerError("canary image labels are missing")
    if labels.get("org.docich.nethack-canary.abi") != CANARY_ABI:
        raise CanaryContainerError("canary image ABI mismatch")
    if labels.get("org.docich.nethack.version") != NETHACK_VERSION:
        raise CanaryContainerError("canary NetHack version mismatch")
    if labels.get("org.docich.nethack.source-sha256") != NETHACK_SOURCE_SHA256:
        raise CanaryContainerError("canary NetHack source hash mismatch")


def _candidate_timeout(request: dict[str, object], manifest_path: Path) -> float:
    del request
    manifest = load_candidate_manifest(manifest_path)
    return min(130.0, max(0.1, manifest.timeout_s + 5.0))


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
        controller.pop("manifest_path", None)
        controller["broker_socket"] = str(_BROKER_SOCKET)
        controller["broker_timeout_s"] = _candidate_timeout(request, manifest)
    return rewritten


def _security_args(*, name: str, memory: str, cpus: str, pids: int) -> list[str]:
    return [
        "--rm",
        "--name",
        name,
        "--label",
        "org.docich.nethack-canary=1",
        "--runtime=runsc",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        f"--cpus={cpus}",
        f"--memory={memory}",
        f"--memory-swap={memory}",
        f"--pids-limit={pids}",
        "--ipc=none",
        "--ulimit",
        "nofile=128:128",
        "--ulimit",
        "core=0:0",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=268435456,mode=1777",
        "--log-driver=none",
        "--restart=no",
        "--env",
        "HOME=/tmp/home",
        "--env",
        "TERM=xterm-256color",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
    ]


def build_container_argv(
    request: dict[str, object],
    *,
    docker: str,
    image: str,
    name: str,
    host_arena: dict[str, Path],
    manifest: Path | None = None,
) -> list[str]:
    """Build the game-container argv. Candidate manifests are never mounted."""
    del request
    if manifest is not None:
        raise CanaryContainerError("candidate manifest may not be mounted in game container")
    root = host_arena["episode_root"]
    return [
        docker,
        "run",
        # Attach stdin so the worker receives its request JSON over the pipe.
        "-i",
        *_security_args(name=name, memory="1024m", cpus="1.0", pids=256),
        "--mount",
        # `docker run --mount` has no `rw` key; a bind mount is read-write
        # unless `readonly` is given.
        f"type=bind,src={root},dst={_INTERNAL_ROOT}",
        image,
    ]


def build_candidate_broker_argv(
    *,
    docker: str,
    image: str,
    name: str,
    ipc_dir: Path,
    manifest: Path,
) -> list[str]:
    """Build an arena-blind broker container. No episode-root mount is present."""
    return [
        docker,
        "run",
        "--detach",
        *_security_args(name=name, memory="512m", cpus="0.5", pids=128),
        "--mount",
        f"type=bind,src={ipc_dir},dst={_BROKER_IPC_DIR}",
        "--mount",
        f"type=bind,src={manifest},dst=/canary/candidate.json,readonly",
        "--entrypoint",
        "python3",
        image,
        "-m",
        "docich.nethack_canary_candidate_broker",
        "--manifest",
        "/canary/candidate.json",
        "--socket",
        str(_BROKER_IPC_DIR / "candidate.sock"),
    ]


def _remove_container(
    docker: str,
    name: str,
    *,
    runner: Callable[..., object],
) -> None:
    try:
        runner(
            [docker, "rm", "--force", name],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return


def _prepare_ipc_dir(arena: dict[str, Path]) -> tuple[Path, Path]:
    ipc = arena["episode_root"] / ".candidate-ipc"
    ipc.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ipc, 0o700)
    sock = ipc / "candidate.sock"
    try:
        sock.unlink()
    except FileNotFoundError:
        pass
    return ipc, sock


def _wait_for_broker_socket(path: Path, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            time.sleep(0.05)
            continue
        if stat.S_ISSOCK(mode):
            return
        raise CanaryContainerError("candidate broker IPC path is not a Unix socket")
    raise CanaryContainerError("candidate broker socket did not become ready")


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
    # P5g's public worker protocol predates the broker implementation and uses
    # the semantic action-source name. Keep that stable outside P5h.
    if result.get("candidate_action_source") == "candidate_strategist_broker":
        result["candidate_action_source"] = "candidate_strategist"
    result["arena"] = {key: str(host_arena[key]) for key in _INTERNAL_ARENA}
    return result


def run_container_worker(
    request_text: str,
    *,
    docker: str | None = None,
    image: str | None = None,
    runner: Callable[..., object] = subprocess.run,
    wait_for_broker: Callable[[Path, float], None] = _wait_for_broker_socket,
) -> dict[str, object]:
    request = _load_request(request_text)
    arena = _host_arena(request)
    manifest = _candidate_manifest(request)
    selected_docker = _docker_binary(docker)
    selected_image = _image_id(image)
    _preflight(selected_docker, selected_image, runner=runner)
    internal = _internal_request(request, manifest)
    payload = json.dumps(internal, ensure_ascii=False, separators=(",", ":"))
    inner_timeout = float(internal.get("wall_timeout_s", DEFAULT_INNER_TIMEOUT_S))
    game_name = "docich-nh-canary-game-" + uuid.uuid4().hex
    broker_name: str | None = None
    try:
        if manifest is not None:
            ipc_dir, socket_path = _prepare_ipc_dir(arena)
            broker_name = "docich-nh-canary-broker-" + uuid.uuid4().hex
            broker_argv = build_candidate_broker_argv(
                docker=selected_docker,
                image=selected_image,
                name=broker_name,
                ipc_dir=ipc_dir,
                manifest=manifest,
            )
            broker_started = runner(
                broker_argv,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            if getattr(broker_started, "returncode", 1) != 0:
                raise CanaryContainerError("candidate broker failed to start")
            wait_for_broker(socket_path, 8.0)

        game_argv = build_container_argv(
            request,
            docker=selected_docker,
            image=selected_image,
            name=game_name,
            host_arena=arena,
            manifest=None,
        )
        try:
            completed = runner(
                game_argv,
                input=payload,
                text=True,
                capture_output=True,
                timeout=min(7200.0, inner_timeout + 15.0),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "schema_version": 1,
                "worker_status": "timeout",
                "error": "container launcher timeout",
            }
        except OSError as exc:
            return {
                "schema_version": 1,
                "worker_status": "error",
                "error": f"container launch failed: {str(exc)[:180]}",
            }
    finally:
        _remove_container(selected_docker, game_name, runner=runner)
        if broker_name is not None:
            _remove_container(selected_docker, broker_name, runner=runner)

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
