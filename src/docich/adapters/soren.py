"""Coordinator adapter for the externally supervised Soren game runtime."""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
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
        self._last_command_output = ""

    def _check(self, deadline: float, cancel) -> None:
        if cancel is not None and cancel.is_set():
            raise DeadlineExceededError("Soren lifecycle call はcancelされました")
        if time.monotonic() >= deadline:
            raise DeadlineExceededError("Soren lifecycle call のdeadlineを超過しました")

    def _run(
        self,
        argv: list[str],
        deadline: float,
        cancel,
        *,
        timeout_cap_s: float = 15.0,
    ) -> tuple[int, dict]:
        # Clear first so a timeout (which raises below without setting output)
        # can never be misattributed to a previous command's stale text.
        self._last_command_output = ""
        self._check(deadline, cancel)
        timeout = max(0.1, min(timeout_cap_s, deadline - time.monotonic()))
        try:
            result = subprocess.run(argv, cwd=self.root, text=True, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ReadinessTimeoutError("Soren lifecycle command がtimeoutしました") from exc
        self._last_command_output = "\n".join((result.stdout or "", result.stderr or ""))[:8192]
        payload = {}
        try:
            payload = json.loads((result.stdout.strip().splitlines() or ["{}"]) [-1])
        except (ValueError, TypeError):
            pass
        return result.returncode, payload if isinstance(payload, dict) else {}

    @staticmethod
    def _classify_stop_failure(output: str) -> str:
        known = (
            ("改善プロセスの停止確認に失敗", "improve_stop_failed"),
            ("予想ワーカーの停止確認に失敗", "prediction_stop_failed"),
            ("停止要求の期限切れ", "deadline_expired"),
            ("共有表示未準備/legacy bridge", "overlay_unsupported"),
        )
        for marker, reason in known:
            if marker in output:
                return reason
        return "unknown_stop_failure"

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

    def _wait_player_change_prepared(self, request_id: str, deadline: float, cancel) -> None:
        """Drive the side-effect-free boundary poll for a player transaction.

        The normal loop polls ``boundary`` after its own game bookkeeping. A
        one-game JEV loop intentionally exits and parks, however, so the
        explicit ``finish`` command must still be able to prepare an already
        GAMEOVER board without requiring a second loop process. Calling the
        broker's boundary command here is safe for both cases: it only records
        ``waiting``/``prepared`` and never sends input or stops the bridge.
        """

        while True:
            payload = self._status(deadline, cancel)
            ack = self._ack(payload)
            if ack.get("request_id") != request_id:
                raise AdapterError("Soren lifecycle request identityが変化しました")
            status = str(ack.get("status") or "")
            if status == "prepared":
                return
            if status in {"failed", "timeout", "unsupported", "cancelled", "resumed"}:
                raise AdapterError(f"Soren lifecycleが停止しました: {status}")

            rc, boundary = self._broker("boundary", request_id, deadline, cancel)
            boundary_ack = self._ack(boundary)
            if boundary_ack and boundary_ack.get("request_id") != request_id:
                raise AdapterError("Soren lifecycle boundary request identityが変化しました")
            if rc not in {0, 1}:
                boundary_status = str(boundary_ack.get("status") or "unknown")
                raise AdapterError(f"Soren player_change boundaryを確定できません: {boundary_status}")
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

    def reconfigure_player(
        self,
        *,
        request_id: str,
        target_policy: str,
        run_id: str,
        expected_player_generation: int,
        config_hash: str,
        deadline: float,
        cancel,
        game_generation: int | None = None,
    ) -> dict:
        """Switch the player at a confirmed game boundary.

        This is intentionally separate from ``request_round_boundary`` and
        the normal game-switch path.  The Soren bridge remains alive; the
        lifecycle broker parks only the game-owned loop and commits the
        player snapshot with a generation CAS.  No normal strategy or
        improvement endpoint is called here.
        """

        self._check(deadline, cancel)
        if self.spec.game != "sorengame":
            raise AdapterError("player reconfigure はsorengame専用です")
        if target_policy not in {"existing", "jev"}:
            raise AdapterError("target_policy はexistingまたはjevである必要があります")
        try:
            canonical_run_id = str(uuid.UUID(str(run_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AdapterError("run_id がUUIDではありません") from exc
        if canonical_run_id != str(run_id):
            raise AdapterError("run_id は標準UUID形式である必要があります")
        if type(expected_player_generation) is not int or expected_player_generation < 0:
            raise AdapterError("expected_player_generation が不正です")
        if not re.fullmatch(r"[0-9a-f]{64}", str(config_hash)):
            raise AdapterError("config_hash が不正です")

        self.preflight(deadline, cancel)
        rc, capabilities = self._broker("capabilities", None, deadline, cancel)
        advertised = capabilities.get("capabilities")
        if rc != 0 or not isinstance(advertised, list) or "player_policy_v1" not in advertised:
            raise AdapterError("Soren player_policy_v1 capabilityを確認できません")
        advertised_generation = capabilities.get("game_generation")
        if type(advertised_generation) is int and advertised_generation >= 1:
            generation = advertised_generation
        else:
            generation = self.spec.generation
        if game_generation is not None:
            if type(game_generation) is not int or game_generation < 1 or game_generation != generation:
                raise AdapterError("Soren game_generationが変化しました")
            generation = game_generation

        self._request_id = request_id
        remaining = deadline - time.monotonic()
        if remaining <= 0.1:
            raise DeadlineExceededError("Soren player_change要求のdeadlineが短すぎます")
        rc, payload = self._broker(
            "request", request_id, deadline, cancel,
            "--game", "sorengame", "--generation", str(generation),
            "--deadline-sec", str(remaining),
            "--operation", "player_change",
            "--target-policy", target_policy,
            "--run-id", canonical_run_id,
            "--expected-player-generation", str(expected_player_generation),
            "--config-hash", config_hash,
        )
        ack = self._ack(payload)
        if rc != 0 or ack.get("request_id") != request_id or ack.get("status") not in {"accepted", "prepared"}:
            raise AdapterError("Soren player_change要求を受理できません")

        self._wait_player_change_prepared(request_id, deadline, cancel)
        rc, committed = self._run(
            [str(self.control), "player-commit", request_id], deadline, cancel
        )
        committed_ack = self._ack(committed)
        if (
            rc != 0
            or committed.get("status") != "committed"
            or committed_ack.get("status") not in {"committed", ""}
        ):
            raise AdapterError("Soren player_changeのcommitに失敗しました")
        player_state = committed.get("player_state")
        if not isinstance(player_state, dict) or player_state.get("policy") != target_policy:
            raise AdapterError("Soren player_changeのcommit結果を検証できません")
        return player_state

    def cancel_round_boundary(self, request_id: str, deadline: float, cancel) -> bool:
        rc, payload = self._run(
            [str(self.control), "cancel", request_id], deadline, cancel
        )
        return rc == 0 and self._ack(payload).get("status") == "cancelled"

    def _diagnostic_log_path(self) -> Path | None:
        """State-dir log file for lifecycle controller output (failures only)."""
        state_dir = getattr(self.g, "state_dir", None)
        if state_dir is None:
            return None
        return Path(state_dir) / "logs" / "soren_adapter.log"

    def _record_command_output(self, operation: str, request_id: str, rc: object) -> None:
        """Append the last controller output for post-mortem diagnosis.

        Best-effort only: logging must never break the adapter call. The
        output is lifecycle controller text (game state, pids, fixed reason
        markers); request identity is already public to the operator logs.
        """
        try:
            path = self._diagnostic_log_path()
            if path is None:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            output = (self._last_command_output or "")[-8192:]
            entry = (
                f"[{stamp}] operation={operation} request_id={request_id} "
                f"game={self.spec.game} generation={self.spec.generation} rc={rc}\n"
                f"{output}\n---\n"
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(entry)
        except OSError:
            pass

    def cleanup_runtime(self, deadline: float, cancel) -> None:
        request_id = self._request_id
        if not request_id:
            ack = self._ack(self._status(deadline, cancel))
            request_id = str(ack.get("request_id") or "")
        if not request_id:
            raise AdapterError("Soren lifecycle stop requestがありません")
        # The fixed stop path may spend up to 30 seconds draining the
        # watchdog after stopping the other game workers.  A 15 second
        # subprocess cap killed the controller halfway through, leaving the
        # bridge/BGM alive.  Keep this bounded, but above the full game-only
        # teardown contract.
        try:
            rc, _payload = self._run(
                [str(self.control), "stop-after-boundary", request_id],
                deadline,
                cancel,
                timeout_cap_s=90.0,
            )
        except ReadinessTimeoutError:
            self._record_command_output("stop-after-boundary", request_id, "timeout")
            raise
        if rc != 0:
            reason = self._classify_stop_failure(self._last_command_output)
            self._record_command_output("stop-after-boundary", request_id, rc)
            raise AdapterError(f"Soren game-only stopに失敗しました rc={rc} reason={reason}")
        self._wait_status(request_id, {"stopped"}, deadline, cancel)

    def materialize_runtime(self, deadline: float, cancel) -> None:
        payload = self._status(deadline, cancel)
        ack = self._ack(payload)
        request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        resource = payload.get("resource") if isinstance(payload.get("resource"), dict) else {}
        resumable = ack.get("status") in {"stopped", "cancelled"} or (
            not ack and resource.get("status") == "stopped"
            and request.get("request_id") == resource.get("request_id")
            and request.get("game") == resource.get("game")
            and request.get("generation") == resource.get("generation")
        )
        if resumable:
            request_id = str(ack.get("request_id") or request.get("request_id") or "")
            if not request_id:
                raise AdapterError("停止済みSoren request identityがありません")
            # An irreversible stopped runtime must prove that its game workers
            # were born after this fresh start.  A cancelled rollback restores
            # the already-running previous runtime, so applying that timestamp
            # fence would reject the healthy processes that cancellation just
            # recovered.
            if ack.get("status") == "stopped" or not ack:
                self._fresh_started_at = time.time()
            rc, _ = self._run([str(self.control), "fresh-start", request_id], deadline, cancel)
            if rc != 0:
                raise AdapterError(f"Soren fresh start準備に失敗しました (rc={rc})")

    def readiness(self, deadline: float, cancel) -> None:
        while True:
            self._check(deadline, cancel)
            payload = self._status(deadline, cancel)
            if not self._ack(payload):
                if self._live_process("soren_loop.sh") and self._live_process("soviet_watchdog.sh"):
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=1.0) as response:
                            if 200 <= response.status < 400:
                                return
                    except (urllib.error.URLError, OSError):
                        pass
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    def _live_process(self, expected: str) -> bool:
        matches: list[float] = []
        proc_root = Path("/proc")
        try:
            uptime = float((proc_root / "uptime").read_text().split()[0])
            hz = int(subprocess.check_output(["getconf", "CLK_TCK"], text=True).strip())
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return False
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
                cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                ticks = int(stat.rsplit(") ", 1)[1].split()[19])
            except (OSError, ValueError, IndexError):
                continue
            words = cmdline.split()
            if not any(word == expected or word.endswith("/" + expected) for word in words):
                continue
            matches.append(time.time() - uptime + ticks / hz)
        return len(matches) == 1 and (self._fresh_started_at is None or matches[0] + 1 >= self._fresh_started_at)

    def _live_pid(self, filename: str, expected: str) -> bool:
        """Compatibility helper retained for callers with a trustworthy pidfile."""
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
        # Both states require an explicit materialize step before canonical can
        # publish the runtime again.  ``cancelled`` may already have live
        # processes, but its broker state must still be archived; reporting it
        # as ready-alive makes rollback skip materialization and readiness can
        # never accept the non-empty lifecycle request.
        return self._ack(payload).get("status") not in {"stopped", "cancelled"}

    def start_agent(self, deadline: float, cancel) -> None:
        self._check(deadline, cancel)

    def stop_agent(self, deadline: float, cancel) -> None:
        self._check(deadline, cancel)
