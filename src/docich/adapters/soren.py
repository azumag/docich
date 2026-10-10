"""Coordinator adapter for the externally supervised Soren game runtime."""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from ..game_switch import (
    DeadlineExceededError, GameSwitchStore, ReadinessTimeoutError, RuntimeSpec,
    committed_retirement_result,
)
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
        self._completed_stop_receipt: dict | None = None

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

    def _checked_stop_state(self, payload: dict, receipt: dict, *, stopped=False) -> dict:
        if (type(payload.get("schema")) is not int or payload["schema"] != 1
                or any(key not in payload for key in ("request", "ack", "resource"))
                or receipt.get("game") != self.spec.game
                or type(receipt.get("generation")) is not int
                or receipt["generation"] != self.spec.generation):
            raise AdapterError("Soren stopのbroker/所有者が一致しません")
        ack = self._checked_round_boundary_ack(payload, receipt)
        resource = payload.get("resource")
        if resource is not None:
            if self._round_boundary_identity(resource) != self._round_boundary_identity(receipt):
                raise AdapterError("Soren stopのresource所有者が一致しません")
        if stopped and (ack.get("status") != "stopped" or not isinstance(resource, dict)
                        or resource.get("status") != "stopped"):
            raise AdapterError("Soren stopの一致するstopped resourceがありません")
        return ack

    def _wait_status(self, request_id: str, wanted: set[str], deadline: float, cancel,
                     *, receipt: dict) -> str:
        while True:
            payload = self._status(deadline, cancel)
            if receipt.get("request_id") != request_id:
                raise AdapterError("Soren lifecycle request identityが変化しました")
            ack = self._checked_stop_state(payload, receipt)
            status = str(ack.get("status") or "")
            if status in wanted:
                self._checked_stop_state(payload, receipt, stopped=True)
                return status
            if status in {"failed", "timeout", "unsupported", "cancelled", "resumed"}:
                raise AdapterError(f"Soren lifecycleが停止しました: {status}")
            self._check(deadline, cancel)
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _round_boundary_identity(record: dict) -> tuple:
        # Ordinary switches use the immutable broker receipt, never an allocated
        # next_generation or a later reconstructed deadline.
        if (type(record.get("schema")) is not int or record["schema"] != 1
                or not isinstance(record.get("request_id"), str) or not record["request_id"]
                or not isinstance(record.get("game"), str) or not record["game"]
                or type(record.get("generation")) is not int or record["generation"] < 1
                or type(record.get("deadline_epoch")) not in (int, float)
                or not math.isfinite(record["deadline_epoch"]) or record["deadline_epoch"] <= 0
                or not isinstance(record.get("deadline_at"), str) or not record["deadline_at"]
                or record.get("operation") is not None):
            raise AdapterError("Soren lifecycle boundary receipt identityが不正です")
        return tuple(record[key] for key in
                     ("schema", "request_id", "game", "generation", "deadline_epoch", "deadline_at"))

    def _checked_round_boundary_ack(self, payload: dict, receipt: dict) -> dict:
        request = payload.get("request")
        ack = self._ack(payload)
        identity = self._round_boundary_identity(receipt)
        if (not isinstance(request, dict) or self._round_boundary_identity(request) != identity
                or self._round_boundary_identity(ack) != identity):
            raise AdapterError("Soren lifecycle boundary request identityが変化しました")
        return ack

    def _wait_round_boundary(self, receipt: dict, deadline: float, cancel) -> None:
        """Poll the broker without input, stop, or a new request/generation.

        A runner waiting for MOVE after founding STOP cannot reach its normal
        post-game boundary poll. The coordinator drives the same broker check;
        only the broker decides whether its fresh evidence permits a boundary.
        """
        while True:
            self._check(deadline, cancel)
            ack = self._checked_round_boundary_ack(self._status(deadline, cancel), receipt)
            status = ack.get("status")
            if status == "boundary":
                self._check(deadline, cancel)
                return
            if status not in {"accepted", "waiting"}:
                raise AdapterError("Soren lifecycle boundaryを待機できない状態です")

            self._check(deadline, cancel)
            rc, payload = self._broker("boundary", receipt["request_id"], deadline, cancel)
            ack = self._checked_round_boundary_ack(payload, receipt)
            self._check(deadline, cancel)
            if rc == 0 and ack.get("status") == "boundary":
                return
            if rc != 1 or ack.get("status") != "waiting":
                raise AdapterError("Soren lifecycle boundaryを確定できません")
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
        receipt = payload.get("request")
        if not isinstance(receipt, dict):
            raise AdapterError("Soren lifecycle boundary receiptがありません")
        self._checked_round_boundary_ack(payload, receipt)
        if receipt["game"] != self.spec.game or receipt["generation"] != self.spec.generation:
            raise AdapterError("Soren lifecycle boundary receiptのgame/generationが変化しました")
        self._wait_round_boundary(dict(receipt), deadline, cancel)

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

    def _retired_singleton_is_superseded(self, deadline: float, cancel) -> bool:
        """A restored singleton owns the processes; an obsolete lease does not.

        Only a recorded retiring identity beside a stable, distinct owner can
        converge without stopping the shared external game. A live broker
        request or missing process proof keeps ordinary cleanup blocked. The
        coordinator holds the canonical writer lock during teardown.

        Only another Soren runtime for the same game can own these singleton
        processes. A CLI active slot and an idle broker do not prove either
        process release or ownership transfer; that case must use the normal
        request-bound stop and stopped ACK, or retain the retiring identity.
        """
        state_dir = getattr(self.g, "state_dir", None)
        if state_dir is None:
            return False
        store = GameSwitchStore(state_dir)
        state, missing = store.canonical.load()
        active = state.get("active") or {}
        identity = {key: getattr(self.spec, key) for key in
                    ("game", "adapter", "runtime_id", "generation", "lease_id")}
        collides = any(active.get(key) == identity[key] for key in
                       ("runtime_id", "generation", "lease_id"))
        if (missing or state.get("phase") != "ready"
                or state.get("request_id") is not None
                or state.get("candidate") is not None or state.get("previous") is not None
                or active.get("adapter") != "soren" or self.spec.adapter != "soren"
                or active.get("game") != self.spec.game or collides
                or not active.get("lease_id") or not self.spec.lease_id
                or not any(all(runtime.get(key) == value for key, value in identity.items())
                           for runtime in state.get("retiring") or [])):
            return False
        payload = self._status(deadline, cancel)
        if (type(payload.get("schema")) is not int or payload["schema"] != 1
                or any(key not in payload or payload[key] is not None
                       for key in ("request", "ack", "resource"))):
            return False
        processes = {name: self._singleton_process_identity(name) for name in
                     ("soren_loop.sh", "soviet_watchdog.sh")}
        if (any(identity is None for identity in processes.values())
                or processes["soren_loop.sh"][0] == processes["soviet_watchdog.sh"][0]):
            return False
        self._check(deadline, cancel)
        if any(self._singleton_process_identity(name) != identity
               for name, identity in processes.items()):
            return False
        current, missing = store.canonical.load()
        return not missing and current == state

    def can_restore_live_singleton(self, deadline: float, cancel) -> bool:
        """Prove a failed restore attempt shares the still-running previous.

        This permits re-leasing an existing singleton only; it never permits
        materializing it or forgetting the failed candidate before commit.
        The ordinary stable-owner retirement proof runs after that commit.
        """
        store = GameSwitchStore(self.g.state_dir)
        state, missing = store.canonical.load()
        keys = ("game", "adapter", "runtime_id", "generation", "lease_id")
        previous = state.get("previous") or {}
        retiring = state.get("retiring") or []
        last = state.get("last_result") or {}
        if (missing or state.get("phase") != "failed" or self.agent_enabled
                or state.get("active") is not None or state.get("candidate") is not None
                or state.get("request_id") is not None or len(retiring) != 1
                or self.spec.adapter != "soren" or not self.spec.lease_id
                or any(previous.get(k) != getattr(self.spec, k) for k in keys)
                or last.get("error_code") != "rollback_failed"):
            return False
        failed = retiring[0]
        if (failed.get("cleanup_role") != "failed_candidate"
                or failed.get("adapter") != "soren" or failed.get("game") != self.spec.game
                or not failed.get("lease_id")
                or any(failed.get(k) == previous.get(k) for k in
                       ("runtime_id", "generation", "lease_id"))):
            return False
        try:
            receipt = store.receipts.load(last.get("request_id"))
            result = (receipt.get("result") or {}) if receipt else {}
            generation = receipt.get("generation") if receipt else None
            if (not receipt or receipt.get("status") != "failed"
                    or receipt.get("operation") not in {"start", "switch", "rotate", "restart"}
                    or result.get("status") != "failed"
                    or result.get("error_code") != "rollback_failed"
                    or result.get("request_id") != last.get("request_id")
                    or result.get("to_game") != last.get("to_game")
                    or receipt.get("target") != last.get("to_game")
                    or result.get("operation") != receipt.get("operation")
                    or generation != last.get("generation")
                    or type(generation) is not int
                    or not previous["generation"] < generation
                    or failed.get("generation") != generation + 1):
                return False
        except (RuntimeError, ValueError, TypeError, KeyError):
            return False
        payload = self._status(deadline, cancel)
        if (type(payload.get("schema")) is not int or payload["schema"] != 1
                or any(k not in payload or payload[k] is not None
                       for k in ("request", "ack", "resource", "control"))):
            return False
        processes = {name: self._singleton_process_identity(name) for name in
                     ("soren_loop.sh", "soviet_watchdog.sh")}
        if (any(value is None for value in processes.values())
                or processes["soren_loop.sh"][0] == processes["soviet_watchdog.sh"][0]):
            return False
        self._check(deadline, cancel)
        if any(self._singleton_process_identity(name) != value for name, value in processes.items()):
            return False
        current, missing = store.canonical.load()
        return not missing and current == state

    def _singleton_process_identity(self, expected: str, *, proc_root=Path("/proc")):
        """Prove the fixed-root script is a shell's program, not a data argument.

        This stricter proof is limited to no-stop retirement. Unknown shell
        options or launch shapes fail closed instead of weakening the fence.
        """
        matches = []
        try:
            script = (self.root / expected).resolve(strict=True)
            entries = list(proc_root.iterdir())
        except OSError:
            return None
        shells = {Path(name).resolve() for name in ("/bin/bash", "/bin/sh", "/bin/dash")}
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                before = (entry / "stat").read_text().rsplit(") ", 1)[1].split()
                argv = (entry / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
                cwd = (entry / "cwd").resolve(strict=True)
                exe = (entry / "exe").resolve(strict=True)
                if (before[0] in {"Z", "X"} or len(argv) < 2 or cwd != self.root
                        or exe not in shells or Path(argv[0]).name not in {"bash", "sh", "dash"}):
                    continue
                index = 2 if argv[1] == "--" else 1
                if len(argv) <= index or argv[index].startswith("-"):
                    continue
                if (cwd / argv[index]).resolve(strict=True) != script:
                    continue
                after = (entry / "stat").read_text().rsplit(") ", 1)[1].split()
                ticks = int(before[19])
                if ticks <= 0 or after[0] in {"Z", "X"} or int(after[19]) != ticks:
                    continue
                matches.append((int(entry.name), ticks))
            except (OSError, UnicodeError, ValueError, IndexError):
                continue
        return matches[0] if len(matches) == 1 else None

    def _owns_failed_candidate_cleanup(self) -> bool:
        """Only a canonical candidate/retiring identity may create a stop.

        A failed fresh-start clears the previous broker request. It must not
        therefore become an un-stoppable singleton. The coordinator owns the
        writer lock here; refuse any competing Soren identity, including a
        previous or newly committed owner of the same external processes.
        """
        state_dir = getattr(self.g, "state_dir", None)
        if state_dir is None:
            return False
        state, missing = GameSwitchStore(state_dir).canonical.load()
        if missing:
            return False
        identity = {key: getattr(self.spec, key) for key in
                    ("game", "adapter", "runtime_id", "generation", "lease_id")}
        candidates = [state.get("candidate"), *(state.get("retiring") or [])]
        if not any(isinstance(item, dict) and all(item.get(k) == v for k, v in identity.items())
                   for item in candidates):
            return False
        if not self.spec.lease_id:
            raise AdapterError("candidate/retiring Sorenのleaseがありません")
        if state.get("phase") not in {"rolling_back", "failed", "ready", "idle"}:
            raise AdapterError("candidate/retiring Sorenを停止できるphaseではありません")
        if any(isinstance(item, dict) and item.get("adapter") == "soren"
               for item in (state.get("active"), state.get("previous"))):
            raise AdapterError("active/previous Sorenがあるためcandidateの停止を拒否します")
        for item in candidates:
            if isinstance(item, dict) and item.get("adapter") == "soren":
                if any(item.get(k) != v for k, v in identity.items()):
                    raise AdapterError("別Soren identityがあるためcandidateの停止を拒否します")
        return True

    def _committed_source_request(self) -> str | None:
        """Resolve R from the retiring identity and its immutable receipt.

        Current active and last_result may already describe another switch,
        stop or recovery. Broker request data is not commit evidence.
        """
        store = GameSwitchStore(self.g.state_dir)
        state, missing = store.canonical.load()
        if missing:
            raise AdapterError("canonical Soren retirementがありません")
        identity = {key: getattr(self.spec, key) for key in
                    ("game", "adapter", "runtime_id", "generation", "lease_id")}
        for runtime in state.get("retiring") or []:
            if not all(runtime.get(key) == value for key, value in identity.items()):
                continue
            if "retirement" not in runtime:
                return None
            proof = runtime["retirement"]
            try:
                if not isinstance(proof, dict):
                    raise ValueError("missing commit proof")
                receipt = store.receipts.load(proof.get("request_id"))
                result = committed_retirement_result(runtime, receipt)
                if (result["operation"] not in {"switch", "rotate"}
                        or result["to_game"] == self.spec.game):
                    raise ValueError("not a retiring source switch")
            except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                raise AdapterError("退役Sorenのcommit/receipt所有権を証明できません") from exc
            return result["request_id"]
        return None

    def _candidate_cleanup_request(self, deadline: float, cancel) -> str:
        # Stable across adapter reconstruction/recovery. The broker persists
        # the first accepted deadline; an already accepted request is polled,
        # never replaced with a fresh timeout or another UUID.
        identity = ":".join(str(getattr(self.spec, key)) for key in
                            ("game", "runtime_id", "generation", "lease_id"))
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "docich:soren-candidate-cleanup:" + identity))
        source_request = self._committed_source_request()
        committed_source = source_request is not None
        if committed_source:
            request_id = source_request
        if not committed_source:
            # Neither a matching broker C nor an empty broker proves the
            # canonical role. Legacy normal retirement must not become C.
            state, _ = GameSwitchStore(self.g.state_dir).canonical.load()
            identity = {key: getattr(self.spec, key) for key in
                        ("game", "adapter", "runtime_id", "generation", "lease_id")}
            candidate = state.get("candidate")
            known_candidate = isinstance(candidate, dict) and all(
                candidate.get(key) == value for key, value in identity.items()
            )
            known_candidate = known_candidate or any(
                runtime.get("cleanup_role") == "failed_candidate"
                and "retirement" not in runtime
                and all(runtime.get(key) == value for key, value in identity.items())
                for runtime in state.get("retiring") or []
            )
            if not known_candidate:
                raise AdapterError("退役Sorenをfailed candidateとして停止する証拠がありません")
        payload = self._status(deadline, cancel)
        if (type(payload.get("schema")) is not int or payload["schema"] != 1
                or any(key not in payload for key in ("request", "ack", "resource"))):
            raise AdapterError("Soren candidate cleanupのbroker状態が不明です")
        if all(payload[key] is None for key in ("request", "ack", "resource")):
            if committed_source:
                raise AdapterError("退役Sorenの元stop requestを確認できません")
            self._check(deadline, cancel)
            self.request_round_boundary(request_id, deadline, cancel)
            return request_id
        receipt = payload.get("request")
        if (not isinstance(receipt, dict) or receipt.get("request_id") != request_id
                or receipt.get("game") != self.spec.game
                or receipt.get("generation") != self.spec.generation):
            raise AdapterError("Soren candidate cleanupのrequest所有者が一致しません")
        resource = payload.get("resource")
        if resource is not None and (not isinstance(resource, dict) or any(
                resource.get(key) != receipt[key] for key in ("request_id", "game", "generation"))):
            raise AdapterError("Soren candidate cleanupのresource所有者が一致しません")
        ack = self._checked_round_boundary_ack(payload, receipt)
        if ack.get("status") in {"accepted", "waiting"}:
            self._wait_round_boundary(dict(receipt), deadline, cancel)
        elif ack.get("status") not in {"boundary", "stop_requested", "stopping", "stopped"}:
            raise AdapterError("Soren candidate cleanupのboundaryを確認できません")
        return request_id

    def cleanup_runtime(self, deadline: float, cancel) -> None:
        if self._retired_singleton_is_superseded(deadline, cancel):
            return
        state_dir = getattr(self.g, "state_dir", None)
        if state_dir is not None:
            state, missing = GameSwitchStore(state_dir).canonical.load()
            identity = {key: getattr(self.spec, key) for key in
                        ("game", "adapter", "runtime_id", "generation", "lease_id")}
            owners = [state.get(key) for key in ("active", "candidate", "previous")]
            owners.extend(state.get("retiring") or [])
            if missing or not any(
                    isinstance(owner, dict) and all(owner.get(k) == v for k, v in identity.items())
                    for owner in owners):
                raise AdapterError("canonical Soren identityが一致しないため停止を拒否します")
            active = state.get("active") or {}
            if (not missing and active.get("game") == self.spec.game
                    and active.get("adapter") == "soren"
                    and active.get("lease_id") != self.spec.lease_id
                    and any(runtime.get("runtime_id") == self.spec.runtime_id
                            and runtime.get("lease_id") == self.spec.lease_id
                            for runtime in state.get("retiring") or [])):
                raise AdapterError("別Soren ownerが稼働中のため旧singletonの停止を拒否します")
        if self._owns_failed_candidate_cleanup():
            request_id = self._candidate_cleanup_request(deadline, cancel)
        else:
            request_id = self._request_id
            if not request_id:
                ack = self._ack(self._status(deadline, cancel))
                request_id = str(ack.get("request_id") or "")
        if not request_id:
            raise AdapterError("Soren lifecycle stop requestがありません")
        payload = self._status(deadline, cancel)
        receipt = payload.get("request")
        if not isinstance(receipt, dict) or receipt.get("request_id") != request_id:
            raise AdapterError("Soren stopの元requestを確認できません")
        self._checked_stop_state(payload, receipt)
        receipt = dict(receipt)  # Pin the complete identity before the controller runs.
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
        self._wait_status(request_id, {"stopped"}, deadline, cancel, receipt=receipt)
        self._completed_stop_receipt = receipt

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
            self._completed_stop_receipt = None

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
        if self._retired_singleton_is_superseded(deadline, cancel):
            return False
        payload = self._status(deadline, cancel)
        if self._completed_stop_receipt is not None:
            self._checked_stop_state(payload, self._completed_stop_receipt, stopped=True)
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
