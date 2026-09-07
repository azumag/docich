"""Coordinator adapter for the externally supervised Soren game runtime."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from .base import AdapterError


class SorenCoordinatorAdapter:
    name = "soren"

    def __init__(self, g, game, spec: RuntimeSpec):
        self.g = g
        self.game = game
        self.spec = spec
        self.agent_enabled = False
        self.requires_round_boundary = True
        self.round_boundary_timeout_s = game.lifecycle.boundary_timeout_s
        raw = game.raw.get("soren") or {}
        if not isinstance(raw, dict):
            raise AdapterError("[soren] はテーブルである必要があります")
        root = raw.get("root", "/home/ubuntu/soren")
        if not isinstance(root, str) or not root.startswith("/") or "\x00" in root:
            raise AdapterError("[soren].root は安全な絶対パスである必要があります")
        self.root = Path(root).resolve()
        self.broker = self.root / "lib/game_lifecycle.py"
        self.control = self.root / "game_lifecycle_control.sh"
        self._request_id: str | None = None
        self._fresh_started_at: float | None = None

    def _check(self, deadline: float, cancel) -> None:
        if cancel is not None and cancel.is_set():
            raise DeadlineExceededError("Soren lifecycle call はcancelされました")
        if time.monotonic() >= deadline:
            raise DeadlineExceededError("Soren lifecycle call のdeadlineを超過しました")

    def _run(self, argv: list[str], deadline: float, cancel) -> tuple[int, dict]:
        self._check(deadline, cancel)
        timeout = max(0.1, min(15.0, deadline - time.monotonic()))
        try:
            result = subprocess.run(argv, cwd=self.root, text=True, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ReadinessTimeoutError("Soren lifecycle command がtimeoutしました") from exc
        payload = {}
        try:
            payload = json.loads((result.stdout.strip().splitlines() or ["{}"]) [-1])
        except (ValueError, TypeError):
            pass
        return result.returncode, payload if isinstance(payload, dict) else {}

    def _broker(self, command: str, request_id: str | None, deadline: float, cancel, *extra: str):
        argv = ["python3", str(self.broker), "--root", str(self.root), command]
        if request_id is not None:
            argv += ["--request-id", request_id]
        argv += list(extra)
        return self._run(argv, deadline, cancel)

    def _status(self, deadline: float, cancel) -> dict:
        rc, payload = self._broker("status", None, deadline, cancel)
        if rc != 0:
            raise AdapterError("Soren lifecycle statusを取得できません")
        return payload

    @staticmethod
    def _ack(payload: dict) -> dict:
        ack = payload.get("ack")
        return ack if isinstance(ack, dict) else {}

    def _wait_status(self, request_id: str, wanted: set[str], deadline: float, cancel) -> str:
        while True:
            payload = self._status(deadline, cancel)
            ack = self._ack(payload)
            if ack.get("request_id") != request_id:
                raise AdapterError("Soren lifecycle request identityが変化しました")
            status = str(ack.get("status") or "")
            if status in wanted:
                return status
            if status in {"failed", "timeout", "unsupported", "cancelled", "resumed"}:
                raise AdapterError(f"Soren lifecycleが停止しました: {status}")
            self._check(deadline, cancel)
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    def preflight(self, deadline: float, cancel) -> None:
        self._check(deadline, cancel)
        if not self.broker.is_file() or not self.control.is_file():
            raise AdapterError("Soren lifecycle controlが見つかりません")

    def request_round_boundary(self, request_id: str, deadline: float, cancel) -> None:
        self._request_id = request_id
        remaining = max(1.0, deadline - time.monotonic())
        rc, payload = self._broker(
            "request", request_id, deadline, cancel,
            "--game", self.spec.game, "--generation", str(self.spec.generation),
            "--deadline-sec", str(remaining),
        )
        if rc != 0 or self._ack(payload).get("request_id") != request_id:
            raise AdapterError("Soren lifecycle boundary要求を受理できません")
        self._wait_status(request_id, {"boundary"}, deadline, cancel)

    def cancel_round_boundary(self, request_id: str, deadline: float, cancel) -> bool:
        rc, payload = self._broker("cancel", request_id, deadline, cancel)
        return rc == 0 and self._ack(payload).get("status") == "cancelled"

    def cleanup_runtime(self, deadline: float, cancel) -> None:
        request_id = self._request_id
        if not request_id:
            ack = self._ack(self._status(deadline, cancel))
            request_id = str(ack.get("request_id") or "")
        if not request_id:
            raise AdapterError("Soren lifecycle stop requestがありません")
        rc, _payload = self._run([str(self.control), "stop-after-boundary", request_id], deadline, cancel)
        if rc != 0:
            raise AdapterError(f"Soren game-only stopに失敗しました (rc={rc})")
        self._wait_status(request_id, {"stopped"}, deadline, cancel)

    def materialize_runtime(self, deadline: float, cancel) -> None:
        payload = self._status(deadline, cancel)
        ack = self._ack(payload)
        request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        resource = payload.get("resource") if isinstance(payload.get("resource"), dict) else {}
        stopped = ack.get("status") == "stopped" or (
            not ack and resource.get("status") == "stopped"
            and request.get("request_id") == resource.get("request_id")
            and request.get("game") == resource.get("game")
            and request.get("generation") == resource.get("generation")
        )
        if stopped:
            request_id = str(ack.get("request_id") or request.get("request_id") or "")
            if not request_id:
                raise AdapterError("停止済みSoren request identityがありません")
            self._fresh_started_at = time.time()
            rc, _ = self._run([str(self.control), "fresh-start", request_id], deadline, cancel)
            if rc != 0:
                raise AdapterError(f"Soren fresh start準備に失敗しました (rc={rc})")

    def readiness(self, deadline: float, cancel) -> None:
        while True:
            self._check(deadline, cancel)
            payload = self._status(deadline, cancel)
            if not self._ack(payload):
                if self._live_pid("soren_loop.pid", "soren_loop.sh") and self._live_pid("soviet_watchdog.pid", "soviet_watchdog.sh"):
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=1.0) as response:
                            if 200 <= response.status < 400:
                                return
                    except (urllib.error.URLError, OSError):
                        pass
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    def _live_pid(self, filename: str, expected: str) -> bool:
        try:
            pid = int((self.root / "tmp/state" / filename).read_text().strip())
            stat = (Path("/proc") / str(pid) / "stat").read_text()
            cmdline = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            uptime = float(Path("/proc/uptime").read_text().split()[0])
            ticks = int(stat.rsplit(") ", 1)[1].split()[19])
            hz = int(subprocess.check_output(["getconf", "CLK_TCK"], text=True).strip())
            started_at = time.time() - uptime + ticks / hz
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return False
        if expected not in cmdline:
            return False
        return self._fresh_started_at is None or started_at + 1 >= self._fresh_started_at

    def alive(self, deadline: float, cancel) -> bool:
        payload = self._status(deadline, cancel)
        return self._ack(payload).get("status") != "stopped"

    def start_agent(self, deadline: float, cancel) -> None:
        self._check(deadline, cancel)

    def stop_agent(self, deadline: float, cancel) -> None:
        self._check(deadline, cancel)
