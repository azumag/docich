"""Fail-closed Docker/gVisor execution with bounded pipes and mandatory cleanup.

The operator owns Docker and its runsc registration; candidates cannot configure
either. No mounts, generated images, host environment, or fallback executors.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import selectors
import subprocess
import time
import uuid

from .contract import Artifact, IMAGE_RE, MAX_INPUT, MAX_OUTPUT, StrategyError, decode, encode, validate_decision


class SandboxError(StrategyError):
    pass


class DockerSandbox:
    def __init__(self, image: str, *, state_dir: Path, docker: str = "docker", wall_seconds: float = 5):
        if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
            raise SandboxError("immutable_image_required")
        if not 0 < wall_seconds <= 10:
            raise SandboxError("invalid_time_limit")
        self.image, self.docker = image, docker
        self.state_dir = Path(state_dir)
        self.wall_seconds = wall_seconds

    def _command(self, *args: str, timeout: float = 10) -> bytes:
        try:
            result = subprocess.run([self.docker, *args], capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxError("sandbox_control_unavailable") from exc
        if result.returncode:
            raise SandboxError("sandbox_control_failed")
        if len(result.stdout) > MAX_INPUT:
            raise SandboxError("sandbox_control_output_limit")
        return result.stdout

    def preflight(self) -> dict:
        info = decode(self._command("info", "--format", '{{json .}}'), limit=MAX_INPUT)
        if not isinstance(info, dict) or info.get("OSType") != "linux" or "runsc" not in info.get("Runtimes", {}):
            raise SandboxError("gvisor_required")
        if not info.get("MemoryLimit") or not info.get("PidsLimit") or not info.get("CPUCfsQuota"):
            raise SandboxError("resource_limits_unavailable")
        images = decode(self._command("image", "inspect", self.image), limit=MAX_INPUT)
        if not isinstance(images, list) or len(images) != 1:
            raise SandboxError("runtime_image_unavailable")
        config = images[0].get("Config", {})
        if config.get("Volumes") or config.get("Labels", {}).get("org.docich.paper-strategy.abi") != "1":
            raise SandboxError("runtime_image_invalid")
        return {"runtime": "runsc", "image": self.image, "abi": 1}

    @contextmanager
    def _singleflight(self):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Never replace or unlink this inode: all processes share one execution slot.
        with (self.state_dir / "sandbox.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SandboxError("sandbox_busy") from exc
            yield

    def _create_args(self, name: str) -> list[str]:
        return ["create", "--name", name, "--label", "org.docich.paper-sandbox=1",
                "--runtime", "runsc", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--user", "65532:65532", "--cpus", "1", "--memory", "512m",
                "--memory-swap", "512m", "--pids-limit", "32", "--ipc", "none",
                "--ulimit", "nofile=64:64", "--ulimit", "core=0:0",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777",
                "--log-driver", "none", "--restart", "no", "--workdir", "/tmp",
                "--entrypoint", "/usr/local/bin/python", "--interactive", self.image,
                "-I", "-B", "/opt/docich/guest.py"]

    def _attach(self, name: str, payload: bytes) -> bytes:
        try:
            proc = subprocess.Popen([self.docker, "start", "--attach", "--interactive", name],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise SandboxError("sandbox_start_failed") from exc
        output, errors = bytearray(), bytearray()
        remaining = memoryview(payload)
        deadline = time.monotonic() + self.wall_seconds
        try:
            with selectors.DefaultSelector() as selector:
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_WRITE if stream is proc.stdin else selectors.EVENT_READ)
                while selector.get_map():
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise SandboxError("sandbox_timeout")
                    for key, _ in selector.select(min(left, 0.1)):
                        stream = key.fileobj
                        if stream is proc.stdin:
                            try:
                                count = os.write(stream.fileno(), remaining[:8192])
                                remaining = remaining[count:]
                            except BrokenPipeError:
                                remaining = remaining[:0]
                            if not remaining:
                                selector.unregister(stream)
                                stream.close()
                        else:
                            chunk = os.read(stream.fileno(), 8192)
                            if not chunk:
                                selector.unregister(stream)
                                continue
                            buffer = output if stream is proc.stdout else errors
                            buffer.extend(chunk)
                            if len(buffer) > MAX_OUTPUT:
                                raise SandboxError("sandbox_output_limit")
                try:
                    code = proc.wait(timeout=max(0.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired as exc:
                    raise SandboxError("sandbox_timeout") from exc
                if code != 0:
                    raise SandboxError("strategy_execution_failed")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
        return bytes(output)

    def run(self, artifact: Artifact, context: dict) -> dict:
        if artifact.payload["image"] != self.image:
            raise SandboxError("runtime_image_mismatch")
        payload = encode({"source": artifact.payload["source"], "context": context})
        if len(payload) > MAX_INPUT:
            raise SandboxError("sandbox_input_limit")
        with self._singleflight():
            self.preflight()
            # Kill/reap only this runner's orphan after host crash before doing more work.
            orphan_file = self.state_dir / "sandbox.active"
            if orphan_file.exists():
                orphan = orphan_file.read_text().strip()
                if not re.fullmatch(r"docich-paper-[0-9a-f]{32}", orphan):
                    raise SandboxError("invalid_cleanup_receipt")
                self._remove(orphan)
                orphan_file.unlink()
            name = "docich-paper-" + uuid.uuid4().hex
            with orphan_file.open("w") as handle:
                os.chmod(orphan_file, 0o600)
                handle.write(name)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                self._command(*self._create_args(name))
                raw = self._attach(name, payload)
                decision = validate_decision(raw, artifact.payload["symbols"])
            finally:
                # A failed cleanup leaves a durable receipt and fails this decision.
                self._remove(name)
                orphan_file.unlink()
            return decision

    def _remove(self, name: str) -> None:
        # rm may report 'not found' after an uncertain create; inspect existence first.
        found = self._command("container", "ls", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}")
        if found.strip():
            self._command("rm", "--force", name)
        remaining = self._command("container", "ls", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}")
        if remaining.strip():
            raise SandboxError("sandbox_cleanup_failed")
