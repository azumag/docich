"""Crash-safe primitives and replace-mode coordinator for game switching.

P0 (:class:`GameSwitchStore`) establishes the canonical state, locking,
generation allocation and request-receipt contracts.

P1 (:class:`GameSwitchCoordinator`) drives those contracts through the
design's replace-mode state machine. It is deliberately not wired to the
existing CLI lifecycle yet: adapters that implement the P1 contract
(:class:`CoordinatorAdapter`) arrive in P2.

The coordinator runs every adapter call inside a bounded worker thread so a
hung adapter cannot hold the exclusive game-switch lock forever.  Adapters
must additionally respect the monotonic deadline passed to each call.
"""
from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator, Mapping, Protocol, runtime_checkable

from .watchdog import next_rotation_game

from .naming import (
    NameValidationError,
    runtime_directory,
    runtime_id_generation,
    runtime_names,
    validate_game_name,
    validate_runtime_id,
)


SCHEMA_VERSION = 2
RECEIPT_SCHEMA_VERSION = 1
STATE_FILE = "game_switch.json"
LOCK_FILE = "locks/game-switch.lock"
REQUESTS_DIR = "game-switch/requests"

PHASES = frozenset(
    {
        "idle",
        "validating",
        "preparing",
        "quiescing",
        "starting",
        "probing",
        "committing",
        "ready",
        "stopping",
        "rolling_back",
        "failed",
        "recovery_required",
    }
)
OPERATIONS = frozenset({"start", "stop", "switch", "restart", "rotate", "recover"})
TERMINAL_RECEIPT_STATUSES = frozenset({"succeeded", "failed", "rolled_back"})

CrashHook = Callable[[str, Path], None]


class GameSwitchError(RuntimeError):
    """Base error for the game-switch contract."""


class StateCorruptError(GameSwitchError):
    """The canonical state cannot be trusted and must fail closed."""


class GameSwitchBusyError(GameSwitchError):
    """Another process currently holds the switch lock."""


class RequestConflictError(GameSwitchError):
    """A request id was reused with a different payload."""


class InvalidTransitionError(GameSwitchError):
    """The requested state transition is not valid from the current phase."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_detail(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:240]


def _prepare_private_dir(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    for created in reversed(missing):
        os.chmod(created, 0o700)
        _fsync_directory(created)
        _fsync_directory(created.parent)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(
    path: Path,
    payload: Mapping[str, object],
    *,
    crash_hook: CrashHook | None = None,
) -> None:
    """Durably replace a private JSON file in the documented commit order."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if crash_hook is not None:
            crash_hook("after_file_fsync", path)
        os.replace(temporary_path, path)
        if crash_hook is not None:
            crash_hook("after_replace", path)
        _fsync_directory(path.parent)
        if crash_hook is not None:
            crash_hook("after_directory_fsync", path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def validate_request_id(request_id: str) -> str:
    try:
        parsed = uuid.UUID(str(request_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise NameValidationError("request_id はUUIDで指定してください") from exc
    canonical = str(parsed)
    if str(request_id).lower() != canonical:
        raise NameValidationError("request_id は標準UUID形式で指定してください")
    return canonical


def new_request_id() -> str:
    return str(uuid.uuid4())


def new_runtime_id(generation: int) -> str:
    runtime_names(generation)  # validates generation
    return f"g{generation}-{secrets.token_hex(4)}"


def validate_request(operation: str, target: str | None) -> tuple[str, str | None]:
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise NameValidationError("operation が不正です")
    if target is not None:
        target = validate_game_name(target)
    if operation in {"start", "switch", "restart"} and target is None:
        raise NameValidationError(f"{operation} にはtarget gameが必要です")
    if operation in {"stop", "recover", "rotate"} and target is not None:
        raise NameValidationError(f"{operation} にはtarget gameを指定できません")
    return operation, target


def _initial_state() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "phase": "idle",
        "operation": None,
        "request_id": None,
        "deadline_at": None,
        "next_generation": 1,
        "active": None,
        "candidate": None,
        "previous": None,
        "retiring": [],
        "last_result": None,
        "last_error": None,
        "updated_at": _utc_now(),
    }


def _runtime_generations(state: Mapping[str, object]) -> list[int]:
    values: list[int] = []
    for key in ("active", "candidate", "previous"):
        value = state.get(key)
        if isinstance(value, dict) and isinstance(value.get("generation"), int):
            values.append(value["generation"])
    retiring = state.get("retiring")
    if isinstance(retiring, list):
        for value in retiring:
            if isinstance(value, dict) and isinstance(value.get("generation"), int):
                values.append(value["generation"])
    return values


def _validate_runtime(value: object, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise StateCorruptError(f"{label} はobjectまたはnullである必要があります")
    required = {
        "game",
        "adapter",
        "generation",
        "runtime_id",
        "lease_id",
        "game_window",
        "agent_window",
        "adapter_session",
        "started_at",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise StateCorruptError(f"{label} に必須項目がありません: {missing}")
    try:
        validate_game_name(value["game"])
        validate_runtime_id(value["runtime_id"])
        names = runtime_names(value["generation"])
    except (NameValidationError, TypeError) as exc:
        raise StateCorruptError(f"{label} の識別子が不正です: {_safe_detail(exc)}") from exc
    if runtime_id_generation(value["runtime_id"]) != value["generation"]:
        raise StateCorruptError(f"{label}.runtime_id がgenerationと一致しません")
    if value["game_window"] != names.game_window:
        raise StateCorruptError(f"{label}.game_window がgenerationと一致しません")
    if value["agent_window"] != names.agent_window:
        raise StateCorruptError(f"{label}.agent_window がgenerationと一致しません")
    if value["adapter_session"] != names.adapter_session:
        raise StateCorruptError(f"{label}.adapter_session がgenerationと一致しません")
    if not isinstance(value["adapter"], str) or not value["adapter"]:
        raise StateCorruptError(f"{label}.adapter が不正です")
    if value["lease_id"] is not None:
        try:
            validate_request_id(value["lease_id"])
        except NameValidationError as exc:
            raise StateCorruptError(f"{label}.lease_id が不正です") from exc
    if not isinstance(value["started_at"], str) or not value["started_at"]:
        raise StateCorruptError(f"{label}.started_at が不正です")


def validate_state(state: Mapping[str, object]) -> None:
    required = {
        "schema_version",
        "revision",
        "phase",
        "operation",
        "request_id",
        "deadline_at",
        "next_generation",
        "active",
        "candidate",
        "previous",
        "retiring",
        "last_result",
        "last_error",
        "updated_at",
    }
    missing = sorted(required - state.keys())
    if missing:
        raise StateCorruptError(f"canonical stateに必須項目がありません: {missing}")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise StateCorruptError("未対応のgame_switch schemaです")
    revision = state.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise StateCorruptError("revision が不正です")
    phase = state.get("phase")
    if phase not in PHASES:
        raise StateCorruptError("phase が不正です")
    operation = state.get("operation")
    if operation is not None and operation not in OPERATIONS:
        raise StateCorruptError("operation が不正です")
    request_id = state.get("request_id")
    if request_id is not None:
        try:
            validate_request_id(request_id)
        except NameValidationError as exc:
            raise StateCorruptError("request_id が不正です") from exc
    in_progress_phases = PHASES - {"idle", "ready", "failed", "recovery_required"}
    if phase in in_progress_phases and (operation is None or request_id is None):
        raise StateCorruptError("進行中phaseにはoperationとrequest_idが必要です")
    if phase in {"idle", "ready"} and (operation is not None or request_id is not None):
        raise StateCorruptError("安定phaseではoperationとrequest_idを保持できません")
    if (operation is None) != (request_id is None):
        raise StateCorruptError("operationとrequest_idは同時に設定または解除してください")
    next_generation = state.get("next_generation")
    if (
        not isinstance(next_generation, int)
        or isinstance(next_generation, bool)
        or next_generation < 1
    ):
        raise StateCorruptError("next_generation が不正です")
    for label in ("active", "candidate", "previous"):
        _validate_runtime(state.get(label), label)
    retiring = state.get("retiring")
    if not isinstance(retiring, list):
        raise StateCorruptError("retiring はlistである必要があります")
    for index, runtime in enumerate(retiring):
        _validate_runtime(runtime, f"retiring[{index}]")
    active = state.get("active")
    candidate = state.get("candidate")
    previous = state.get("previous")
    if phase == "idle" and any(value is not None for value in (active, candidate, previous)):
        raise StateCorruptError("idle phaseはruntimeを保持できません")
    if phase == "ready" and (active is None or candidate is not None or previous is not None):
        raise StateCorruptError("ready phaseはactiveだけを保持する必要があります")
    if phase in {"validating", "stopping"} and candidate is not None:
        raise StateCorruptError(f"{phase} phaseはcandidateを保持できません")
    runtime_generations: list[int] = []
    for runtime in [active, candidate, previous, *retiring]:
        if isinstance(runtime, dict):
            runtime_generations.append(int(runtime["generation"]))
    if len(runtime_generations) != len(set(runtime_generations)):
        raise StateCorruptError("runtime generationが重複しています")
    generations = _runtime_generations(state)
    if generations and next_generation <= max(generations):
        raise StateCorruptError("next_generation が既存generationより先へ進んでいません")
    if not isinstance(state.get("updated_at"), str):
        raise StateCorruptError("updated_at が不正です")


class GameSwitchLock:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / LOCK_FILE
        self._file = None
        self.exclusive: bool | None = None

    def acquire(self, *, exclusive: bool, blocking: bool = False) -> "GameSwitchLock":
        if self._file is not None:
            raise RuntimeError("game switch lock は既に取得済みです")
        _prepare_private_dir(self.path.parent)
        lock_file = self.path.open("a+", encoding="utf-8")
        os.chmod(self.path, 0o600)
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(lock_file.fileno(), operation)
        except BlockingIOError as exc:
            lock_file.close()
            raise GameSwitchBusyError("ゲーム切替が進行中です") from exc
        self._file = lock_file
        self.exclusive = exclusive
        return self

    def release(self) -> None:
        if self._file is None:
            return
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
            self.exclusive = None

    def __enter__(self) -> "GameSwitchLock":
        if self._file is None:
            self.acquire(exclusive=True)
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()


class CanonicalStateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / STATE_FILE

    def load(self) -> tuple[dict[str, object], bool]:
        if not self.path.is_file():
            return _initial_state(), True
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StateCorruptError(f"canonical stateを読み込めません: {_safe_detail(exc)}") from exc
        if not isinstance(raw, dict):
            raise StateCorruptError("canonical stateはJSON objectである必要があります")
        schema = raw.get("schema_version")
        if schema != SCHEMA_VERSION:
            raise StateCorruptError("未対応または欠落したgame_switch schemaです")
        validate_state(raw)
        return raw, False

    def save(
        self,
        state: Mapping[str, object],
        *,
        increment_revision: bool = True,
        crash_hook: CrashHook | None = None,
    ) -> dict[str, object]:
        saved = copy.deepcopy(dict(state))
        if increment_revision:
            saved["revision"] = int(saved.get("revision", 0)) + 1
        saved["schema_version"] = SCHEMA_VERSION
        saved["updated_at"] = _utc_now()
        validate_state(saved)
        atomic_write_json(self.path, saved, crash_hook=crash_hook)
        return saved

    def initialize(self, *, crash_hook: CrashHook | None = None) -> dict[str, object]:
        state, needs_write = self.load()
        if needs_write:
            state = self.save(state, crash_hook=crash_hook)
        return state

    def transition(
        self,
        expected_phases: set[str] | frozenset[str],
        phase: str,
        *,
        updates: Mapping[str, object] | None = None,
        crash_hook: CrashHook | None = None,
    ) -> dict[str, object]:
        if phase not in PHASES:
            raise InvalidTransitionError(f"未知のphaseです: {phase}")
        state, _migrated = self.load()
        if state["phase"] not in expected_phases:
            raise InvalidTransitionError(
                f"phase {state['phase']} から {phase} へ遷移できません"
            )
        next_state = copy.deepcopy(state)
        next_state["phase"] = phase
        if updates:
            for key, value in updates.items():
                if key in {"schema_version", "revision", "updated_at"}:
                    raise InvalidTransitionError(f"coordinator管理項目は直接更新できません: {key}")
                next_state[key] = copy.deepcopy(value)
        return self.save(next_state, crash_hook=crash_hook)


def _request_payload_hash(operation: str, target: str | None, payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        {"operation": operation, "target": target, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_receipt(
    receipt: Mapping[str, object],
    state_dir: Path,
    *,
    expected_request_id: str | None = None,
) -> None:
    required = {
        "schema_version",
        "request_id",
        "operation",
        "target",
        "payload_hash",
        "generation",
        "runtime_id",
        "runtime_dir",
        "game_window",
        "agent_window",
        "adapter_session",
        "status",
        "result",
        "created_at",
        "updated_at",
    }
    missing = sorted(required - receipt.keys())
    if missing:
        raise StateCorruptError(f"request receiptに必須項目がありません: {missing}")
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise StateCorruptError("request receiptのschemaが不正です")
    try:
        request_id = validate_request_id(receipt.get("request_id"))
    except NameValidationError as exc:
        raise StateCorruptError("request receiptのIDが不正です") from exc
    if expected_request_id is not None and request_id != validate_request_id(expected_request_id):
        raise StateCorruptError("request receiptのIDがファイル名と一致しません")
    try:
        validate_request(receipt.get("operation"), receipt.get("target"))
    except NameValidationError as exc:
        raise StateCorruptError("request receiptのoperation/targetが不正です") from exc
    generation = receipt.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise StateCorruptError("request receiptのgenerationが不正です")
    payload_hash = receipt.get("payload_hash")
    if (
        not isinstance(payload_hash, str)
        or len(payload_hash) != 64
        or any(ch not in "0123456789abcdef" for ch in payload_hash)
    ):
        raise StateCorruptError("request receiptのpayload hashが不正です")
    try:
        runtime_id = validate_runtime_id(receipt.get("runtime_id"))
        names = runtime_names(generation)
        expected_dir = runtime_directory(Path(state_dir), runtime_id)
    except (NameValidationError, TypeError) as exc:
        raise StateCorruptError("request receiptのruntime identityが不正です") from exc
    if runtime_id_generation(runtime_id) != generation:
        raise StateCorruptError("request receiptのruntime_idがgenerationと一致しません")
    if receipt.get("runtime_dir") != str(expected_dir):
        raise StateCorruptError("request receiptのruntime_dirが不正です")
    if receipt.get("game_window") != names.game_window:
        raise StateCorruptError("request receiptのgame_windowが不正です")
    if receipt.get("agent_window") != names.agent_window:
        raise StateCorruptError("request receiptのagent_windowが不正です")
    if receipt.get("adapter_session") != names.adapter_session:
        raise StateCorruptError("request receiptのadapter_sessionが不正です")
    status = receipt.get("status")
    if status not in {"allocating", "accepted", *TERMINAL_RECEIPT_STATUSES}:
        raise StateCorruptError("request receiptのstatusが不正です")
    result = receipt.get("result")
    if status in TERMINAL_RECEIPT_STATUSES:
        if not isinstance(result, dict):
            raise StateCorruptError("terminal request receiptにはresult objectが必要です")
    elif result is not None:
        raise StateCorruptError("非terminal request receiptのresultはnullである必要があります")
    for label in ("created_at", "updated_at"):
        if not isinstance(receipt.get(label), str) or not receipt.get(label):
            raise StateCorruptError(f"request receiptの{label}が不正です")


class RequestReceiptStore:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.directory = Path(state_dir) / REQUESTS_DIR

    def _path(self, request_id: str) -> Path:
        request_id = validate_request_id(request_id)
        return self.directory / f"{request_id}.json"

    def load(self, request_id: str) -> dict[str, object] | None:
        path = self._path(request_id)
        if not path.is_file():
            return None
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StateCorruptError(f"request receiptを読み込めません: {_safe_detail(exc)}") from exc
        if not isinstance(receipt, dict):
            raise StateCorruptError("request receiptはJSON objectである必要があります")
        validate_receipt(receipt, self.state_dir, expected_request_id=request_id)
        return receipt

    def save(
        self,
        receipt: Mapping[str, object],
        *,
        crash_hook: CrashHook | None = None,
    ) -> dict[str, object]:
        request_id = validate_request_id(receipt.get("request_id"))
        _prepare_private_dir(self.directory)
        saved = copy.deepcopy(dict(receipt))
        saved["schema_version"] = RECEIPT_SCHEMA_VERSION
        saved["request_id"] = request_id
        saved["updated_at"] = _utc_now()
        validate_receipt(saved, self.state_dir, expected_request_id=request_id)
        atomic_write_json(self._path(request_id), saved, crash_hook=crash_hook)
        return saved

    def receipts(self) -> list[dict[str, object]]:
        if not self.directory.is_dir():
            return []
        found: list[dict[str, object]] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                found_receipt = self.load(path.stem)
            except NameValidationError as exc:
                raise StateCorruptError(f"不正なrequest receipt名です: {path.name}") from exc
            if found_receipt is not None:
                found.append(found_receipt)
        return found

    def max_generation(self) -> int:
        return max((int(r["generation"]) for r in self.receipts()), default=0)

    def prune_terminal(self, max_receipts: int = 1000) -> int:
        if max_receipts < 1:
            raise ValueError("max_receipts は1以上である必要があります")
        receipts = self.receipts()
        if len(receipts) <= max_receipts:
            return 0
        terminal = sorted(
            (r for r in receipts if r.get("status") in TERMINAL_RECEIPT_STATUSES),
            key=lambda r: str(r.get("updated_at", "")),
        )
        removable = min(len(terminal), len(receipts) - max_receipts)
        for receipt in terminal[:removable]:
            self._path(receipt["request_id"]).unlink()
        if removable:
            _fsync_directory(self.directory)
        return removable


@dataclass(frozen=True)
class RequestAcceptance:
    request_id: str
    generation: int
    status: str
    existing: bool
    receipt: Mapping[str, object]


class GameSwitchTransaction:
    """Exclusive-lock scope that P1 keeps for the whole transition."""

    def __init__(self, store: "GameSwitchStore", lock: GameSwitchLock):
        self.store = store
        self.lock = lock

    def accept_request(
        self,
        request_id: str,
        operation: str,
        target: str | None,
        payload: Mapping[str, object] | None = None,
        *,
        crash_hook: CrashHook | None = None,
    ) -> RequestAcceptance:
        return self.store._accept_request_locked(
            self.lock,
            request_id,
            operation,
            target,
            payload,
            crash_hook=crash_hook,
        )

    def finish_request(
        self,
        request_id: str,
        status: str,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        return self.store._finish_request_locked(self.lock, request_id, status, result)

    def transition(
        self,
        expected_phases: set[str] | frozenset[str],
        phase: str,
        *,
        updates: Mapping[str, object] | None = None,
        crash_hook: CrashHook | None = None,
    ) -> dict[str, object]:
        self.store._require_exclusive_lock(self.lock)
        return self.store.canonical.transition(
            expected_phases,
            phase,
            updates=updates,
            crash_hook=crash_hook,
        )


class GameSwitchStore:
    """Single-writer P0 facade used by the future coordinator."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.canonical = CanonicalStateStore(self.state_dir)
        self.receipts = RequestReceiptStore(self.state_dir)

    @contextmanager
    def lock(self, *, exclusive: bool, blocking: bool = False) -> Iterator[GameSwitchLock]:
        held = GameSwitchLock(self.state_dir).acquire(exclusive=exclusive, blocking=blocking)
        try:
            yield held
        finally:
            held.release()

    @contextmanager
    def transaction(self, *, blocking: bool = False) -> Iterator[GameSwitchTransaction]:
        """Hold the exclusive lock across a complete coordinator transition."""

        with self.lock(exclusive=True, blocking=blocking) as held:
            yield GameSwitchTransaction(self, held)

    def initialize(self) -> dict[str, object]:
        with self.lock(exclusive=True):
            return self.canonical.initialize()

    def _classify_existing(
        self,
        receipt: Mapping[str, object],
        payload_hash: str,
    ) -> RequestAcceptance:
        if receipt.get("payload_hash") != payload_hash:
            raise RequestConflictError("同じrequest_idが異なるpayloadで使用されています")
        status = str(receipt.get("status", "in_progress"))
        if status not in TERMINAL_RECEIPT_STATUSES:
            status = "in_progress"
        return RequestAcceptance(
            request_id=str(receipt["request_id"]),
            generation=int(receipt["generation"]),
            status=status,
            existing=True,
            receipt=copy.deepcopy(receipt),
        )

    def classify_request(
        self,
        request_id: str,
        operation: str,
        target: str | None,
        payload: Mapping[str, object] | None = None,
    ) -> RequestAcceptance:
        request_id = validate_request_id(request_id)
        operation, target = validate_request(operation, target)
        payload_hash = _request_payload_hash(operation, target, payload or {})
        receipt = self.receipts.load(request_id)
        if receipt is None:
            raise GameSwitchBusyError("別のゲーム切替が進行中です")
        return self._classify_existing(receipt, payload_hash)

    def accept_request(
        self,
        request_id: str,
        operation: str,
        target: str | None,
        payload: Mapping[str, object] | None = None,
        *,
        crash_hook: CrashHook | None = None,
    ) -> RequestAcceptance:
        """Reserve a request in a short P0 transaction.

        P1 must use :meth:`transaction` and call ``transaction.accept_request``
        so the same exclusive lock remains held through quiesce, commit and
        cleanup. This convenience is useful for receipt recovery and tests.
        """

        try:
            with self.transaction(blocking=False) as transaction:
                return transaction.accept_request(
                    request_id,
                    operation,
                    target,
                    payload,
                    crash_hook=crash_hook,
                )
        except GameSwitchBusyError:
            return self.classify_request(request_id, operation, target, payload)

    def _accept_request_locked(
        self,
        lock: GameSwitchLock,
        request_id: str,
        operation: str,
        target: str | None,
        payload: Mapping[str, object] | None = None,
        *,
        crash_hook: CrashHook | None = None,
    ) -> RequestAcceptance:
        self._require_exclusive_lock(lock)
        request_id = validate_request_id(request_id)
        operation, target = validate_request(operation, target)
        request_payload = copy.deepcopy(dict(payload or {}))
        payload_hash = _request_payload_hash(operation, target, request_payload)

        state = self.canonical.initialize()
        existing = self.receipts.load(request_id)
        if existing is not None:
            classified = self._classify_existing(existing, payload_hash)
            if existing.get("status") in TERMINAL_RECEIPT_STATUSES:
                return classified
        canonical_request_id = state.get("request_id")
        if canonical_request_id is not None and canonical_request_id != request_id:
            raise GameSwitchBusyError(
                "別requestの未完了canonical stateがあるため、先に復旧が必要です"
            )
        if state.get("phase") in {"failed", "recovery_required"}:
            raise GameSwitchBusyError("canonical stateの復旧が必要です")
        if canonical_request_id == request_id and existing is None:
            raise StateCorruptError("canonical requestに対応するreceiptがありません")

        nonterminal_receipts = [
            receipt
            for receipt in self.receipts.receipts()
            if receipt.get("status") not in TERMINAL_RECEIPT_STATUSES
            and receipt.get("request_id") != request_id
        ]
        if nonterminal_receipts:
            raise GameSwitchBusyError(
                "別requestの未完了receiptがあるため、先に復旧が必要です"
            )

        if existing is not None:
            if state["next_generation"] <= classified.generation:
                next_state = copy.deepcopy(state)
                next_state["next_generation"] = classified.generation + 1
                state = self.canonical.save(next_state)
            if existing.get("status") == "allocating":
                if canonical_request_id == request_id and state.get("phase") != "validating":
                    raise StateCorruptError(
                        "allocating receiptとcanonical phaseを安全にreconcileできません"
                    )
                if canonical_request_id is None and state.get("phase") not in {"idle", "ready"}:
                    raise StateCorruptError(
                        "allocating receiptとcanonical phaseを安全にreconcileできません"
                    )
                next_state = copy.deepcopy(state)
                next_state.update(
                    {
                        "phase": "validating",
                        "operation": operation,
                        "request_id": request_id,
                    }
                )
                self.canonical.save(next_state)
                existing = dict(existing)
                existing["status"] = "accepted"
                existing = self.receipts.save(existing)
                classified = self._classify_existing(existing, payload_hash)
            elif canonical_request_id is None:
                raise StateCorruptError("accepted receiptに対応するcanonical requestがありません")
            return classified

        if canonical_request_id is not None or state.get("phase") not in {"idle", "ready"}:
            raise GameSwitchBusyError("未完了canonical stateがあるため、先に復旧が必要です")

        generation = max(int(state["next_generation"]), self.receipts.max_generation() + 1)
        runtime_id = new_runtime_id(generation)
        names = runtime_names(generation)
        runtime_dir = runtime_directory(self.state_dir, runtime_id)
        receipt: dict[str, object] = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "request_id": request_id,
            "operation": operation,
            "target": target,
            "payload_hash": payload_hash,
            "generation": generation,
            "runtime_id": runtime_id,
            "runtime_dir": str(runtime_dir),
            "game_window": names.game_window,
            "agent_window": names.agent_window,
            "adapter_session": names.adapter_session,
            "status": "allocating",
            "result": None,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }

        def receipt_hook(stage: str, path: Path) -> None:
            if crash_hook is not None:
                crash_hook(f"receipt_{stage}", path)

        receipt = self.receipts.save(receipt, crash_hook=receipt_hook)

        next_state = copy.deepcopy(state)
        next_state.update(
            {
                "phase": "validating",
                "operation": operation,
                "request_id": request_id,
                "next_generation": generation + 1,
            }
        )

        def canonical_hook(stage: str, path: Path) -> None:
            if crash_hook is not None:
                crash_hook(f"canonical_{stage}", path)

        self.canonical.save(next_state, crash_hook=canonical_hook)
        receipt["status"] = "accepted"
        receipt = self.receipts.save(receipt)
        self.receipts.prune_terminal()
        return RequestAcceptance(
            request_id=request_id,
            generation=generation,
            status="accepted",
            existing=False,
            receipt=copy.deepcopy(receipt),
        )

    def _require_exclusive_lock(self, lock: GameSwitchLock) -> None:
        if lock.path != self.state_dir / LOCK_FILE or lock.exclusive is not True or lock._file is None:
            raise RuntimeError("有効なexclusive game-switch lockが必要です")

    def _finish_request_locked(
        self,
        lock: GameSwitchLock,
        request_id: str,
        status: str,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        self._require_exclusive_lock(lock)
        if status not in TERMINAL_RECEIPT_STATUSES:
            raise ValueError("terminal status が不正です")
        receipt = self.receipts.load(request_id)
        if receipt is None:
            raise StateCorruptError("完了対象のrequest receiptがありません")
        current_status = receipt.get("status")
        requested_result = copy.deepcopy(dict(result))
        if current_status in TERMINAL_RECEIPT_STATUSES:
            if current_status != status or receipt.get("result") != requested_result:
                raise RequestConflictError("terminal request resultを変更できません")
            return dict(receipt)
        updated = dict(receipt)
        updated["status"] = status
        updated["result"] = requested_result
        saved = self.receipts.save(updated)
        self.receipts.prune_terminal()
        return saved

    def finish_request(
        self,
        request_id: str,
        status: str,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        with self.transaction() as transaction:
            return transaction.finish_request(request_id, status, result)


# ---------------------------------------------------------------------------
# P1: replace-mode coordinator
# ---------------------------------------------------------------------------
#
# State machine (design v2 §5):
#
#   switch/restart/rotate:
#     validating --accept--> preparing --preflight--> quiescing --stop old-->
#     starting --materialize--> probing --readiness/agent--> committing --commit-->
#     ready
#   start:  same sequence with no previous runtime to quiesce
#   stop:   validating --> quiescing --stop agent--> stopping --cleanup game--> idle
#
# Any pre-commit failure enters rolling_back: the candidate is cleaned up and
# the previous game is restored (re-lease when still alive, otherwise a fresh
# generation restart).  A failed restore leaves phase=failed with `previous`
# retained so recover() can retry.
#
# Canonical writes are never caught: a crash hook or I/O failure propagates and
# the durable atomic write leaves either the old or the new state.  Only
# adapter calls (preflight/materialize/readiness/cleanup/agent) are treated as
# recoverable step failures.

DEFAULT_REQUEST_TIMEOUT_S = 600.0
QUIESCE_VERIFY_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 0.1
PREFLIGHT_TIMEOUT_S = 60.0
STOP_AGENT_TIMEOUT_S = 60.0
START_TIMEOUT_S = 60.0
AGENT_START_TIMEOUT_S = 60.0
CLEANUP_TIMEOUT_S = 120.0
PROBE_TIMEOUT_S = 5.0
CANCEL_GRACE_S = 0.5
ROLLBACK_TIMEOUT_S = 120.0

ERROR_BUSY = "busy"
ERROR_REQUEST_CONFLICT = "request_conflict"
ERROR_INVALID_GAME = "invalid_game"
ERROR_ALREADY_ACTIVE = "already_active"
ERROR_NO_ACTIVE_GAME = "no_active_game"
ERROR_INVALID_ROTATION = "invalid_rotation"
ERROR_PREPARE_FAILED = "prepare_failed"
ERROR_QUIESCE_FAILED = "quiesce_failed"
ERROR_START_FAILED = "start_failed"
ERROR_READINESS_TIMEOUT = "readiness_timeout"
ERROR_AGENT_START_FAILED = "agent_start_failed"
ERROR_ROLLBACK_FAILED = "rollback_failed"
ERROR_STATE_CORRUPT = "state_corrupt"
ERROR_RECOVERY_REQUIRED = "recovery_required"
ERROR_PROBE_FAILED = "probe_failed"
ERROR_TIMEOUT = "timeout"
ERROR_INTERNAL = "internal"

IN_PROGRESS_PHASES = frozenset(PHASES - {"idle", "ready", "failed", "recovery_required"})


@dataclass(frozen=True)
class StepTimeouts:
    """Per-step caps for adapter calls, each also bounded by the request
    deadline.  A cap keeps one hung adapter call from holding the exclusive
    game-switch lock forever (design v2 §5: individual limits per step)."""

    preflight_s: float = PREFLIGHT_TIMEOUT_S
    stop_agent_s: float = STOP_AGENT_TIMEOUT_S
    start_s: float = START_TIMEOUT_S
    agent_start_s: float = AGENT_START_TIMEOUT_S
    cleanup_s: float = CLEANUP_TIMEOUT_S
    probe_s: float = PROBE_TIMEOUT_S


class ReadinessTimeoutError(GameSwitchError):
    """The runtime did not become ready before its deadline."""


class DeadlineExceededError(GameSwitchError):
    """The request-wide deadline passed before a step could complete."""


@dataclass(frozen=True)
class RuntimeSpec:
    """Identity of one runtime, derived from canonical/receipt or allocated.

    ``adapter`` may be empty until the factory resolves the game; it is filled
    from the produced adapter's ``name`` before any canonical write.
    """

    game: str
    adapter: str
    generation: int
    runtime_id: str
    lease_id: str | None
    runtime_dir: Path
    game_window: str
    agent_window: str
    adapter_session: str

    @classmethod
    def from_runtime(
        cls, state_dir: Path, runtime: Mapping[str, object]
    ) -> "RuntimeSpec":
        generation = int(runtime["generation"])
        names = runtime_names(generation)
        runtime_id = validate_runtime_id(str(runtime["runtime_id"]))
        return cls(
            game=str(runtime["game"]),
            adapter=str(runtime["adapter"]),
            generation=generation,
            runtime_id=runtime_id,
            lease_id=runtime.get("lease_id"),
            runtime_dir=runtime_directory(state_dir, runtime_id),
            game_window=names.game_window,
            agent_window=names.agent_window,
            adapter_session=names.adapter_session,
        )


def runtime_state_dict(spec: RuntimeSpec, started_at: str) -> dict[str, object]:
    return {
        "game": spec.game,
        "adapter": spec.adapter,
        "generation": spec.generation,
        "runtime_id": spec.runtime_id,
        "lease_id": spec.lease_id,
        "game_window": spec.game_window,
        "agent_window": spec.agent_window,
        "adapter_session": spec.adapter_session,
        "started_at": started_at,
    }


@runtime_checkable
class CoordinatorAdapter(Protocol):
    """P1 adapter contract driven by :class:`GameSwitchCoordinator`.

    An instance is bound to exactly one runtime and must only touch that
    runtime's generation-specific resources (windows, sessions, runtime dir).

    Every call receives the request-wide monotonic deadline and must respect
    it: raise :class:`ReadinessTimeoutError` (or return promptly) once the
    deadline passes.  The coordinator additionally bounds each call in a
    worker thread and sets ``cancel`` when the step times out.  An adapter
    MUST stop its in-flight side effects once ``cancel`` is set: a late
    completion after a timeout would otherwise race with the coordinator's
    rollback and create a runtime that canonical no longer tracks.  A
    non-cooperative adapter that keeps running after cancel is a contract
    violation and its late side effects cannot be prevented by the
    coordinator.

    ``materialize_runtime`` must be idempotent per runtime: a retry or
    recovery may call it again for an already-created runtime.
    ``cleanup_runtime`` must tear down the whole runtime; the coordinator
    also calls ``stop_agent`` first whenever teardown ordering matters.
    """

    name: str
    agent_enabled: bool

    def preflight(self, deadline: float, cancel: threading.Event) -> None:
        """Validate the game definition and dependencies. Side-effect free."""

    def materialize_runtime(self, deadline: float, cancel: threading.Event) -> None:
        """Create this runtime's sessions/windows and ownership tags."""

    def readiness(self, deadline: float, cancel: threading.Event) -> None:
        """Raise :class:`ReadinessTimeoutError` unless ready by ``deadline``."""

    def alive(self, deadline: float, cancel: threading.Event) -> bool:
        """True while this runtime's game process/session still exists."""

    def cleanup_runtime(self, deadline: float, cancel: threading.Event) -> None:
        """Stop this runtime's own game process/session. Missing targets are
        treated as success, ownership mismatches are errors."""

    def start_agent(self, deadline: float, cancel: threading.Event) -> None:
        """Start this runtime's agent window bound to its runtime identity."""

    def stop_agent(self, deadline: float, cancel: threading.Event) -> None:
        """Stop this runtime's agent window. A missing target is success."""


AdapterFactory = Callable[[RuntimeSpec], CoordinatorAdapter]
MirrorWriter = Callable[[Path, str | None], None]


@dataclass(frozen=True)
class SwitchResult:
    """Terminal or pending outcome of a coordinator request."""

    request_id: str
    operation: str
    status: str  # succeeded | rolled_back | failed | in_progress | busy | request_conflict
    target: str | None
    from_game: str | None
    to_game: str | None
    generation: int | None
    error_code: str | None
    detail: str | None
    warnings: tuple[str, ...]
    cleanup_pending: bool
    receipt: Mapping[str, object] | None


def _result_from_receipt(
    receipt: Mapping[str, object],
    *,
    warnings: tuple[str, ...] = (),
    cleanup_pending: bool = False,
) -> SwitchResult:
    result = receipt.get("result") or {}
    return SwitchResult(
        request_id=str(receipt["request_id"]),
        operation=str(receipt["operation"]),
        status=str(receipt["status"]),
        target=receipt.get("target"),
        from_game=result.get("from_game"),
        to_game=result.get("to_game"),
        generation=receipt.get("generation"),
        error_code=result.get("error_code"),
        detail=result.get("detail"),
        warnings=tuple(warnings),
        cleanup_pending=bool(cleanup_pending or result.get("cleanup_pending")),
        receipt=copy.deepcopy(dict(receipt)),
    )


def atomic_write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Durably replace a private text file (used for the compat mirror)."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


class GameSwitchCoordinator:
    """Replace-mode state machine over the P0 store (design v2 §5, §7)."""

    def __init__(
        self,
        store: GameSwitchStore,
        adapter_factory: AdapterFactory,
        *,
        mirror_writer: MirrorWriter | None = None,
        crash_hook: CrashHook | None = None,
        default_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        quiesce_verify_timeout_s: float = QUIESCE_VERIFY_TIMEOUT_S,
        poll_interval_s: float = POLL_INTERVAL_S,
        step_timeouts: StepTimeouts | None = None,
        cancel_grace_s: float = CANCEL_GRACE_S,
        rollback_timeout_s: float = ROLLBACK_TIMEOUT_S,
    ):
        self.store = store
        self.adapter_factory = adapter_factory
        self.crash_hook = crash_hook
        self.default_timeout_s = default_timeout_s
        self.quiesce_verify_timeout_s = quiesce_verify_timeout_s
        self.poll_interval_s = poll_interval_s
        self.step_timeouts = step_timeouts or StepTimeouts()
        self.cancel_grace_s = cancel_grace_s
        self.rollback_timeout_s = rollback_timeout_s
        self.mirror_writer = mirror_writer
        self.mirror_path = store.state_dir / "current_game"

    # --- public API --------------------------------------------------------

    def start(
        self,
        game: str,
        *,
        request_id: str | None = None,
        timeout_s: float | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> SwitchResult:
        return self._execute("start", game, request_id=request_id, timeout_s=timeout_s, payload=payload)

    def stop(
        self,
        *,
        request_id: str | None = None,
        timeout_s: float | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> SwitchResult:
        return self._execute("stop", None, request_id=request_id, timeout_s=timeout_s, payload=payload)

    def switch(
        self,
        target: str,
        *,
        request_id: str | None = None,
        timeout_s: float | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> SwitchResult:
        return self._execute("switch", target, request_id=request_id, timeout_s=timeout_s, payload=payload)

    def restart(
        self,
        *,
        request_id: str | None = None,
        timeout_s: float | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> SwitchResult:
        return self._execute("restart", None, request_id=request_id, timeout_s=timeout_s, payload=payload)

    def rotate(
        self,
        games: list[str],
        *,
        request_id: str | None = None,
        timeout_s: float | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> SwitchResult:
        if not isinstance(games, list) or not games:
            return SwitchResult(
                request_id=request_id or "",
                operation="rotate",
                status="failed",
                target=None,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=ERROR_INVALID_ROTATION,
                detail="rotation games が空です",
                warnings=(),
                cleanup_pending=False,
                receipt=None,
            )
        try:
            games = [validate_game_name(game) for game in games]
        except NameValidationError as exc:
            return SwitchResult(
                request_id=request_id or "",
                operation="rotate",
                status="failed",
                target=None,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=ERROR_INVALID_ROTATION,
                detail=_safe_detail(exc),
                warnings=(),
                cleanup_pending=False,
                receipt=None,
            )
        return self._execute(
            "rotate", None, games=games, request_id=request_id, timeout_s=timeout_s, payload=payload
        )

    def recover(
        self,
        *,
        timeout_s: float | None = None,
    ) -> SwitchResult:
        try:
            with self.store.transaction() as tx:
                deadline = time.monotonic() + (timeout_s if timeout_s is not None else self.default_timeout_s)
                return self._recover_locked(tx, deadline=deadline)
        except GameSwitchBusyError as exc:
            return SwitchResult(
                request_id="",
                operation="recover",
                status="busy",
                target=None,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=ERROR_BUSY,
                detail=_safe_detail(exc),
                warnings=(),
                cleanup_pending=False,
                receipt=None,
            )

    # --- request plumbing --------------------------------------------------

    def _execute(
        self,
        operation: str,
        target: str | None,
        *,
        games: list[str] | None = None,
        request_id: str | None = None,
        timeout_s: float | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> SwitchResult:
        request_id = new_request_id() if request_id is None else validate_request_id(request_id)
        if operation == "restart":
            if target is not None:
                raise NameValidationError("restart にはtarget gameを指定できません")
            target = None  # resolved from the active runtime inside the lock
        else:
            operation, target = validate_request(operation, target)
        if operation == "rotate":
            # The rotation list selects the target, so it must be part of the
            # request identity: a resend with a different list is a conflict.
            payload = {**(payload or {}), "rotation_games": list(games or [])}
        timeout = float(timeout_s if timeout_s is not None else self.default_timeout_s)
        if timeout <= 0:
            raise ValueError("timeout_s は正の値である必要があります")
        deadline = time.monotonic() + timeout
        deadline_at = (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=timeout)
        ).isoformat().replace("+00:00", "Z")
        try:
            with self.store.transaction() as tx:
                return self._execute_locked(
                    tx,
                    request_id,
                    operation,
                    target,
                    payload,
                    deadline,
                    deadline_at,
                    games=games,
                )
        except GameSwitchBusyError:
            return self._classify_busy(request_id, operation, target, payload)

    def _classify_busy(
        self,
        request_id: str,
        operation: str,
        target: str | None,
        payload: Mapping[str, object] | None,
    ) -> SwitchResult:
        if operation == "start":
            # start->switch fallback resend under lock contention: follow the
            # recorded switch receipt (same target and payload) instead of
            # conflicting on the caller's operation.
            receipt = self.store.receipts.load(request_id)
            if (
                receipt is not None
                and str(receipt["operation"]) == "switch"
                and receipt.get("target") == target
                and _request_payload_hash("switch", target, payload or {})
                == receipt.get("payload_hash")
            ):
                operation = "switch"
        if operation == "restart":
            # restart resolves its target inside the lock, so on lock
            # contention the target is unknown unless the request was
            # already accepted.  A fresh restart under contention is simply
            # busy (P0's validate_request would otherwise reject target=None).
            receipt = self.store.receipts.load(request_id)
            if receipt is None or receipt.get("target") is None:
                return SwitchResult(
                    request_id=request_id,
                    operation=operation,
                    status="busy",
                    target=None,
                    from_game=None,
                    to_game=None,
                    generation=None,
                    error_code=ERROR_BUSY,
                    detail="別のゲーム切替が進行中です",
                    warnings=(),
                    cleanup_pending=False,
                    receipt=None,
                )
            target = str(receipt["target"])
        try:
            classified = self.store.classify_request(request_id, operation, target, payload)
        except RequestConflictError as exc:
            return SwitchResult(
                request_id=request_id,
                operation=operation,
                status="request_conflict",
                target=target,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=ERROR_REQUEST_CONFLICT,
                detail=_safe_detail(exc),
                warnings=(),
                cleanup_pending=False,
                receipt=None,
            )
        except GameSwitchBusyError as exc:
            return SwitchResult(
                request_id=request_id,
                operation=operation,
                status="busy",
                target=target,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=ERROR_BUSY,
                detail=_safe_detail(exc),
                warnings=(),
                cleanup_pending=False,
                receipt=None,
            )
        if classified.status in TERMINAL_RECEIPT_STATUSES:
            return _result_from_receipt(classified.receipt)
        return SwitchResult(
            request_id=request_id,
            operation=operation,
            status="in_progress",
            target=target,
            from_game=None,
            to_game=None,
            generation=classified.generation,
            error_code=None,
            detail="同じrequestが進行中です",
            warnings=(),
            cleanup_pending=False,
            receipt=copy.deepcopy(dict(classified.receipt)),
        )

    def _execute_locked(
        self,
        tx: GameSwitchTransaction,
        request_id: str,
        operation: str,
        target: str | None,
        payload: Mapping[str, object] | None,
        deadline: float,
        deadline_at: str,
        *,
        games: list[str] | None = None,
    ) -> SwitchResult:
        state = self.store.canonical.initialize()
        if state["phase"] in {"failed", "recovery_required"}:
            return SwitchResult(
                request_id=request_id,
                operation=operation,
                status="failed",
                target=target,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=ERROR_RECOVERY_REQUIRED,
                detail="canonical stateの復旧が必要です (`docich recover`)",
                warnings=(),
                cleanup_pending=False,
                receipt=None,
            )
        # A request_id that was already accepted fixes its operation and
        # target.  The only caller-side alias the coordinator sanctions is
        # the start->switch fallback resend under the SAME target and
        # payload: it follows the recorded switch receipt.  Any other
        # operation mismatch falls through and fails as request_conflict at
        # acceptance, per the receipt contract.
        existing = self.store.receipts.load(request_id)
        if existing is not None and operation == "start":
            recorded_operation = str(existing["operation"])
            recorded_target = existing.get("target")
            if recorded_operation == "switch" and recorded_target == target:
                if _request_payload_hash(
                    recorded_operation, recorded_target, payload or {}
                ) == existing.get("payload_hash"):
                    operation = "switch"
        if operation == "start":
            active = state.get("active")
            if active is not None:
                if active["game"] == target:
                    return SwitchResult(
                        request_id=request_id,
                        operation=operation,
                        status="succeeded",
                        target=target,
                        from_game=target,
                        to_game=target,
                        generation=active["generation"],
                        error_code=None,
                        detail="既に起動中です",
                        warnings=(),
                        cleanup_pending=False,
                        receipt=None,
                    )
                return SwitchResult(
                    request_id=request_id,
                    operation=operation,
                    status="failed",
                    target=target,
                    from_game=active["game"],
                    to_game=target,
                    generation=active["generation"],
                    error_code=ERROR_ALREADY_ACTIVE,
                    detail=f"{active['game']} が起動中です。switch を要求してください",
                    warnings=(),
                    cleanup_pending=False,
                    receipt=None,
                )
        if operation == "restart":
            # The target is fixed at first acceptance: a resend must keep the
            # recorded target so the payload hash matches and the request
            # converges to the same terminal result.
            existing_receipt = self.store.receipts.load(request_id)
            if existing_receipt is not None:
                target = existing_receipt.get("target")
                if target is None:
                    raise StateCorruptError("restart receiptにtargetが記録されていません")
            else:
                active = state.get("active")
                if active is None:
                    return SwitchResult(
                        request_id=request_id,
                        operation=operation,
                        status="failed",
                        target=None,
                        from_game=None,
                        to_game=None,
                        generation=None,
                        error_code=ERROR_NO_ACTIVE_GAME,
                        detail="実行中のゲームがありません",
                        warnings=(),
                        cleanup_pending=False,
                        receipt=None,
                    )
                target = active["game"]
        if operation == "stop" and state.get("active") is None:
            return SwitchResult(
                request_id=request_id,
                operation=operation,
                status="succeeded",
                target=None,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=None,
                detail="実行中のゲームはありません",
                warnings=(),
                cleanup_pending=False,
                receipt=None,
            )
        if operation == "rotate":
            current = state["active"]["game"] if state.get("active") else None
            rotate_target = next_rotation_game(games or [], current)
        else:
            rotate_target = None

        try:
            acceptance = tx.accept_request(
                request_id,
                operation,
                None if operation == "rotate" else target,
                payload,
                crash_hook=self.crash_hook,
            )
        except RequestConflictError as exc:
            return SwitchResult(
                request_id=request_id,
                operation=operation,
                status="request_conflict",
                target=target,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=ERROR_REQUEST_CONFLICT,
                detail=_safe_detail(exc),
                warnings=(),
                cleanup_pending=False,
                receipt=None,
            )
        except StateCorruptError:
            reconciled = self._reconcile_stale_receipt(
                tx, request_id, operation, None if operation == "rotate" else target, payload
            )
            if reconciled is not None:
                return reconciled
            raise
        if acceptance.status in TERMINAL_RECEIPT_STATUSES:
            return _result_from_receipt(acceptance.receipt)
        if acceptance.existing:
            # The previous driver is provably dead (we hold the lock).
            # Converge fail-closed through the recovery path.
            return self._recover_locked(tx, deadline=deadline)
        transition_target = rotate_target if rotate_target is not None else target
        return self._run_operation_locked(
            tx, acceptance, operation, transition_target, deadline, deadline_at
        )

    def _reconcile_stale_receipt(
        self,
        tx: GameSwitchTransaction,
        request_id: str,
        operation: str,
        target: str | None,
        payload: Mapping[str, object] | None,
    ) -> SwitchResult | None:
        """Repair a receipt left non-terminal by a crash after the commit
        write.  Only converges when canonical ``last_result`` names the same
        request; anything else fails closed with StateCorruptError."""
        state, _migrated = self.store.canonical.load()
        last_result = state.get("last_result")
        if not isinstance(last_result, dict) or last_result.get("request_id") != request_id:
            return None
        status = last_result.get("status")
        if status not in TERMINAL_RECEIPT_STATUSES:
            return None
        receipt = self.store.receipts.load(request_id)
        if receipt is None or receipt.get("status") in TERMINAL_RECEIPT_STATUSES:
            return None
        expected_hash = _request_payload_hash(operation, target, payload or {})
        if receipt.get("payload_hash") != expected_hash:
            raise RequestConflictError("同じrequest_idが異なるpayloadで使用されています")
        saved = tx.finish_request(request_id, str(status), dict(last_result))
        return _result_from_receipt(saved)

    def _run_operation_locked(
        self,
        tx: GameSwitchTransaction,
        acceptance: RequestAcceptance,
        operation: str,
        target: str | None,
        deadline: float,
        deadline_at: str,
    ) -> SwitchResult:
        try:
            if operation == "stop":
                return self._stop_locked(tx, acceptance, deadline)
            if operation in {"start", "switch", "restart", "rotate"}:
                return self._switch_locked(
                    tx, acceptance, operation, target, deadline, deadline_at
                )
            raise InvalidTransitionError(f"coordinatorは operation {operation} を実行できません")
        except StateCorruptError:
            raise
        except InvalidTransitionError:
            raise
        except DeadlineExceededError as exc:
            return self._rollback_locked(
                tx,
                acceptance,
                deadline,
                target=target,
                error_code=ERROR_TIMEOUT,
                detail=_safe_detail(exc),
            )

    def _check_deadline(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise DeadlineExceededError("request deadline を超過しました")

    def _call_adapter(
        self,
        fn: Callable[[threading.Event], object],
        deadline: float,
        timeout_s: float,
        step_name: str,
    ) -> object:
        """Run one adapter call with a bounded wait.

        The call runs in a daemon worker so that a hung adapter cannot keep
        the exclusive lock forever: after ``timeout_s`` (bounded by the
        request deadline) the step fails with DeadlineExceededError and the
        coordinator rolls back while the lock is released.

        On timeout the ``cancel`` event passed to the adapter is set and the
        worker is given a short grace period to stop, so a cooperative
        adapter cannot complete its side effects after the rollback.  A
        worker still alive after the grace period is abandoned; its late
        side effects are the adapter's contract violation.
        """
        remaining = min(deadline - time.monotonic(), timeout_s)
        if remaining <= 0:
            raise DeadlineExceededError(f"adapter {step_name} のdeadlineを超過しました")
        cancel = threading.Event()
        result: list[object] = []
        error: list[BaseException] = []

        def run() -> None:
            try:
                result.append(fn(cancel))
            except BaseException as exc:  # noqa: BLE001 - re-raised in caller
                error.append(exc)

        worker = threading.Thread(
            target=run, name=f"docich-adapter-{step_name}", daemon=True
        )
        worker.start()
        worker.join(remaining)
        timed_out = False
        if worker.is_alive():
            timed_out = True
            cancel.set()
            worker.join(self.cancel_grace_s)
        if worker.is_alive():
            raise DeadlineExceededError(
                f"adapter {step_name} がtimeoutし、cancelにも応答しません"
            )
        if timed_out:
            raise DeadlineExceededError(f"adapter {step_name} がtimeoutしました")
        if error:
            raise error[0]
        return result[0] if result else None

    def _probe_alive(self, adapter: CoordinatorAdapter, deadline: float) -> bool | None:
        """Probe liveness; None means the probe itself failed (unknown).

        An unknown probe must not be treated as dead: the caller keeps the
        runtime tracked and fails closed instead of clearing it.
        """
        try:
            result = self._call_adapter(
                lambda cancel: adapter.alive(deadline, cancel), deadline, self.step_timeouts.probe_s, "alive"
            )
        except Exception:
            return None
        return bool(result)

    def _make_adapter(self, spec: RuntimeSpec, deadline: float) -> CoordinatorAdapter:
        adapter = self._call_adapter(
            lambda cancel: self.adapter_factory(spec), deadline, self.step_timeouts.start_s, "factory"
        )
        if not isinstance(adapter, CoordinatorAdapter):
            raise GameSwitchError("adapter factory が CoordinatorAdapter を返していません")
        if not isinstance(adapter.name, str) or not adapter.name:
            raise GameSwitchError("adapter が name を返していません")
        if spec.adapter and adapter.name != spec.adapter:
            # A runtime recorded under one adapter kind must not be torn down
            # by another: fail closed instead of mis-cleaning.
            raise GameSwitchError(
                f"adapter種別が一致しません (canonical={spec.adapter}, factory={adapter.name})"
            )
        return adapter

    # --- start/switch/restart/rotate --------------------------------------

    def _switch_locked(
        self,
        tx: GameSwitchTransaction,
        acceptance: RequestAcceptance,
        operation: str,
        target: str,
        deadline: float,
        deadline_at: str,
    ) -> SwitchResult:
        state, _migrated = self.store.canonical.load()
        old_active = state.get("active")
        spec = self._candidate_spec(acceptance, target)
        try:
            adapter = self._make_adapter(spec, deadline)
        except Exception as exc:
            return self._rollback_locked(
                tx,
                acceptance,
                deadline,
                target=target,
                error_code=ERROR_INVALID_GAME,
                detail=_safe_detail(exc),
            )
        spec = replace(spec, adapter=adapter.name)

        self._check_deadline(deadline)
        tx.transition(
            {"validating"}, "preparing", updates={"deadline_at": deadline_at},
            crash_hook=self.crash_hook,
        )
        try:
            self._call_adapter(
                lambda cancel: adapter.preflight(deadline, cancel),
                deadline,
                self.step_timeouts.preflight_s,
                "preflight",
            )
        except Exception as exc:
            return self._rollback_locked(
                tx,
                acceptance,
                deadline,
                target=target,
                error_code=_failure_code(exc, ERROR_PREPARE_FAILED),
                detail=_safe_detail(exc),
            )
        self._check_deadline(deadline)

        if old_active is not None:
            old_spec = RuntimeSpec.from_runtime(self.store.state_dir, old_active)
            try:
                old_adapter = self._make_adapter(old_spec, deadline)
            except Exception as exc:
                return self._rollback_locked(
                    tx,
                    acceptance,
                    deadline,
                    target=target,
                    error_code=ERROR_PREPARE_FAILED,
                    detail=f"old runtime adapter: {_safe_detail(exc)}",
                )
            tx.transition(
                {"preparing"}, "quiescing",
                updates={"active": None, "previous": old_active},
                crash_hook=self.crash_hook,
            )
            try:
                self._call_adapter(
                    lambda cancel: old_adapter.stop_agent(deadline, cancel),
                    deadline,
                    self.step_timeouts.stop_agent_s,
                    "stop_agent",
                )
                self._call_adapter(
                    lambda cancel: old_adapter.cleanup_runtime(deadline, cancel),
                    deadline,
                    self.step_timeouts.cleanup_s,
                    "cleanup",
                )
            except Exception as exc:
                return self._rollback_locked(
                    tx,
                    acceptance,
                    deadline,
                    target=target,
                    error_code=_failure_code(exc, ERROR_QUIESCE_FAILED),
                    detail=_safe_detail(exc),
                )
            if not self._wait_stopped(old_adapter, deadline):
                return self._rollback_locked(
                    tx,
                    acceptance,
                    deadline,
                    target=target,
                    error_code=ERROR_QUIESCE_FAILED,
                    detail="旧runtimeの停止を確認できませんでした",
                )
        else:
            tx.transition({"preparing"}, "quiescing", crash_hook=self.crash_hook)

        candidate_rd = runtime_state_dict(spec, _utc_now())
        tx.transition(
            {"quiescing"}, "starting", updates={"candidate": candidate_rd},
            crash_hook=self.crash_hook,
        )
        try:
            self._call_adapter(
                lambda cancel: adapter.materialize_runtime(deadline, cancel),
                deadline,
                self.step_timeouts.start_s,
                "materialize",
            )
        except Exception as exc:
            return self._rollback_locked(
                tx,
                acceptance,
                deadline,
                target=target,
                error_code=_failure_code(exc, ERROR_START_FAILED),
                detail=_safe_detail(exc),
            )
        self._check_deadline(deadline)

        tx.transition({"starting"}, "probing", crash_hook=self.crash_hook)
        try:
            self._call_adapter(
                lambda cancel: adapter.readiness(deadline, cancel),
                deadline,
                max(deadline - time.monotonic(), 0.0),
                "readiness",
            )
        except Exception as exc:
            return self._rollback_locked(
                tx,
                acceptance,
                deadline,
                target=target,
                error_code=_failure_code(exc),
                detail=_safe_detail(exc),
            )
        self._check_deadline(deadline)

        if adapter.agent_enabled:
            try:
                self._call_adapter(
                    lambda cancel: adapter.start_agent(deadline, cancel),
                    deadline,
                    self.step_timeouts.agent_start_s,
                    "start_agent",
                )
            except Exception as exc:
                return self._rollback_locked(
                    tx,
                    acceptance,
                    deadline,
                    target=target,
                    error_code=_failure_code(exc, ERROR_AGENT_START_FAILED),
                    detail=_safe_detail(exc),
                )
        self._check_deadline(deadline)

        state, _migrated = self.store.canonical.load()
        old_for_retiring = state.get("previous")
        retiring = state.get("retiring") or []
        if old_for_retiring is not None:
            retiring = [old_for_retiring, *retiring]
        from_game = old_active["game"] if old_active is not None else None
        last_result = {
            "request_id": acceptance.request_id,
            "operation": operation,
            "status": "succeeded",
            "from_game": from_game,
            "to_game": target,
            "generation": acceptance.generation,
        }
        # The single atomic commit write is the commit point (design §5 E).
        tx.transition(
            {"probing"}, "ready",
            updates={
                "active": candidate_rd,
                "candidate": None,
                "previous": None,
                "retiring": retiring,
                "operation": None,
                "request_id": None,
                "deadline_at": None,
                "last_result": last_result,
                "last_error": None,
            },
            crash_hook=self.crash_hook,
        )
        receipt = tx.finish_request(acceptance.request_id, "succeeded", last_result)
        warnings: list[str] = []
        cleanup_pending = self._finalize_locked(tx, deadline, warnings=warnings)
        return _result_from_receipt(receipt, warnings=tuple(warnings), cleanup_pending=cleanup_pending)

    def _candidate_spec(self, acceptance: RequestAcceptance, target: str) -> RuntimeSpec:
        receipt = acceptance.receipt
        generation = int(receipt["generation"])
        names = runtime_names(generation)
        runtime_id = str(receipt["runtime_id"])
        return RuntimeSpec(
            game=target,
            adapter="",
            generation=generation,
            runtime_id=runtime_id,
            lease_id=str(uuid.uuid4()),
            runtime_dir=runtime_directory(self.store.state_dir, runtime_id),
            game_window=names.game_window,
            agent_window=names.agent_window,
            adapter_session=names.adapter_session,
        )

    def _wait_stopped(self, adapter: CoordinatorAdapter, deadline: float) -> bool:
        """Confirm the runtime is gone.  An unknown probe (exception) is never
        treated as stopped, so a hang or probe failure fails the wait."""
        limit = min(deadline, time.monotonic() + self.quiesce_verify_timeout_s)
        while time.monotonic() < limit:
            if self._probe_alive(adapter, deadline) is False:
                return True
            time.sleep(self.poll_interval_s)
        return self._probe_alive(adapter, deadline) is False

    def _teardown_runtime(self, adapter: CoordinatorAdapter, deadline: float) -> bool:
        """Stop agent and game, then confirm the runtime is actually gone.

        A cleanup that returns without raising is not enough: the runtime
        must be observed dead before the coordinator drops it from tracking.
        """
        try:
            self._call_adapter(
                lambda cancel: adapter.stop_agent(deadline, cancel),
                deadline,
                self.step_timeouts.stop_agent_s,
                "stop_agent",
            )
            self._call_adapter(
                lambda cancel: adapter.cleanup_runtime(deadline, cancel),
                deadline,
                self.step_timeouts.cleanup_s,
                "cleanup",
            )
            return self._wait_stopped(adapter, deadline)
        except Exception:
            return False

    # --- stop ---------------------------------------------------------------

    def _stop_locked(
        self,
        tx: GameSwitchTransaction,
        acceptance: RequestAcceptance,
        deadline: float,
    ) -> SwitchResult:
        state, _migrated = self.store.canonical.load()
        active = state.get("active")
        tx.transition({"validating"}, "quiescing", crash_hook=self.crash_hook)
        warnings: list[str] = []
        cleanup_pending = False
        if active is not None:
            try:
                adapter = self._make_adapter(RuntimeSpec.from_runtime(self.store.state_dir, active), deadline)
            except Exception as exc:
                return self._fail_locked(
                    tx,
                    acceptance,
                    deadline,
                    error_code=ERROR_QUIESCE_FAILED,
                    detail=f"old runtime adapter: {_safe_detail(exc)}",
                    keep_active=True,
                )
            try:
                self._call_adapter(
                    lambda cancel: adapter.stop_agent(deadline, cancel),
                    deadline,
                    self.step_timeouts.stop_agent_s,
                    "stop_agent",
                )
            except Exception as exc:
                return self._fail_locked(
                    tx,
                    acceptance,
                    deadline,
                    error_code=_failure_code(exc, ERROR_QUIESCE_FAILED),
                    detail=_safe_detail(exc),
                    keep_active=True,
                )
            tx.transition({"quiescing"}, "stopping", crash_hook=self.crash_hook)
            try:
                self._call_adapter(
                    lambda cancel: adapter.cleanup_runtime(deadline, cancel),
                    deadline,
                    self.step_timeouts.cleanup_s,
                    "cleanup",
                )
            except Exception as exc:
                return self._fail_locked(
                    tx,
                    acceptance,
                    deadline,
                    error_code=_failure_code(exc, ERROR_QUIESCE_FAILED),
                    detail=_safe_detail(exc),
                    keep_active=True,
                )
            if not self._wait_stopped(adapter, deadline):
                return self._fail_locked(
                    tx,
                    acceptance,
                    deadline,
                    error_code=ERROR_QUIESCE_FAILED,
                    detail="旧runtimeの停止を確認できませんでした",
                    keep_active=True,
                )
        else:
            tx.transition({"quiescing"}, "stopping", crash_hook=self.crash_hook)
        last_result = {
            "request_id": acceptance.request_id,
            "operation": "stop",
            "status": "succeeded",
            "from_game": active["game"] if active is not None else None,
            "to_game": None,
            "generation": active["generation"] if active is not None else None,
        }
        tx.transition(
            {"stopping"}, "idle",
            updates={
                "active": None,
                "candidate": None,
                "previous": None,
                "operation": None,
                "request_id": None,
                "deadline_at": None,
                "last_result": last_result,
                "last_error": None,
            },
            crash_hook=self.crash_hook,
        )
        receipt = tx.finish_request(acceptance.request_id, "succeeded", last_result)
        cleanup_pending = self._finalize_locked(tx, deadline, warnings=warnings)
        return _result_from_receipt(receipt, warnings=tuple(warnings), cleanup_pending=cleanup_pending)

    # --- rollback / failure -------------------------------------------------

    def _rollback_locked(
        self,
        tx: GameSwitchTransaction,
        acceptance: RequestAcceptance,
        deadline: float,
        *,
        target: str | None = None,
        error_code: str,
        detail: str,
        warnings: list[str] | None = None,
    ) -> SwitchResult:
        warnings = list(warnings or [])
        cleanup_pending = False
        request_id = acceptance.request_id
        receipt = acceptance.receipt
        operation = str(receipt["operation"])
        target = receipt.get("target") if target is None else target
        generation = int(receipt["generation"])

        # Rollback runs on its own budget: the request-wide deadline is often
        # already spent when a step times out, and restoring the previous
        # game must not fail merely because the original request expired.
        deadline = time.monotonic() + self.rollback_timeout_s

        tx.transition(IN_PROGRESS_PHASES, "rolling_back", crash_hook=self.crash_hook)
        state, _migrated = self.store.canonical.load()
        candidate = state.get("candidate")
        if candidate is not None:
            try:
                adapter = self._make_adapter(
                    RuntimeSpec.from_runtime(self.store.state_dir, candidate), deadline
                )
                torn_down = self._teardown_runtime(adapter, deadline)
            except Exception as exc:
                torn_down = False
            if not torn_down:
                cleanup_pending = True
                warnings.append("candidateの停止を確認できませんでした")
                # Keep the un-cleanable runtime durably tracked so recovery
                # retries it; otherwise the restore commit would forget it.
                tx.transition(
                    {"rolling_back"}, "rolling_back",
                    updates={
                        "candidate": None,
                        "retiring": [candidate, *(state.get("retiring") or [])],
                    },
                    crash_hook=self.crash_hook,
                )
            else:
                state, _migrated = self.store.canonical.load()
                if state.get("candidate") is not None:
                    tx.transition(
                        {"rolling_back"}, "rolling_back",
                        updates={"candidate": None},
                        crash_hook=self.crash_hook,
                    )

        state, _migrated = self.store.canonical.load()
        previous = state.get("previous") or state.get("active")
        if previous is None:
            # Nothing to restore (plain start with no previous game).
            return self._fail_locked(
                tx,
                acceptance,
                deadline,
                error_code=error_code,
                detail=detail,
                warnings=warnings,
                cleanup_pending=cleanup_pending,
            )
        if state.get("previous") is None:
            # Pre-quiesce failure: the rollback target still lives in active.
            # Track it as previous so a failed restore leaves recovery with a
            # durable reference to retry.
            tx.transition(
                {"rolling_back"}, "rolling_back",
                updates={"active": None, "previous": previous},
                crash_hook=self.crash_hook,
            )

        pending_out: list[bool] = []
        restored = self._restore_previous_locked(
            tx,
            request_id,
            operation,
            target,
            generation,
            previous,
            deadline,
            warnings=warnings,
            finish_receipt=True,
            cleanup_pending_out=pending_out,
        )
        cleanup_pending = cleanup_pending or any(pending_out)
        if restored is None:
            return self._fail_locked(
                tx,
                acceptance,
                deadline,
                error_code=ERROR_ROLLBACK_FAILED,
                detail=f"rollback失敗 (原因: {error_code}: {detail})",
                warnings=warnings,
                cleanup_pending=cleanup_pending,
                keep_previous=True,
            )
        return _result_from_receipt(
            restored,
            warnings=tuple(warnings),
            cleanup_pending=cleanup_pending,
        )

    def _restore_previous_locked(
        self,
        tx: GameSwitchTransaction,
        request_id: str,
        operation: str,
        target: str | None,
        generation: int,
        previous: Mapping[str, object],
        deadline: float,
        *,
        warnings: list[str],
        finish_receipt: bool = True,
        cleanup_pending_out: list[bool] | None = None,
    ) -> Mapping[str, object] | None:
        """Restore the previous runtime.  Returns the terminal receipt when
        the restore commits, or None when the restore failed."""
        # Issue a fresh lease up front and bind the adapter to it, so an
        # agent started by the restore carries the lease that canonical
        # active will publish (no lease mismatch on rollback).
        previous_spec = RuntimeSpec.from_runtime(self.store.state_dir, previous)
        new_lease = str(uuid.uuid4())
        previous_spec = replace(previous_spec, lease_id=new_lease)
        try:
            previous_adapter = self._make_adapter(previous_spec, deadline)
        except Exception as exc:
            warnings.append(f"previous adapter生成失敗: {_safe_detail(exc)}")
            return None
        previous_alive = self._probe_alive(previous_adapter, deadline)
        if previous_alive is None:
            warnings.append("previous runtimeの生存確認ができません (probe失敗)")
            return None
        if previous_alive:
            # The runtime was quiescing: a living process is not necessarily a
            # ready game.  Verify readiness BEFORE re-issuing the agent so a
            # readiness failure cannot leave a fresh-lease agent behind.
            try:
                self._call_adapter(
                    lambda cancel: previous_adapter.stop_agent(deadline, cancel),
                    deadline,
                    self.step_timeouts.stop_agent_s,
                    "stop_agent",
                )
                self._call_adapter(
                    lambda cancel: previous_adapter.readiness(deadline, cancel),
                    deadline,
                    max(deadline - time.monotonic(), 0.0),
                    "readiness",
                )
            except Exception as exc:
                warnings.append(f"previous readiness確認失敗: {_safe_detail(exc)}")
                return None
            if previous_adapter.agent_enabled:
                try:
                    self._call_adapter(
                        lambda cancel: previous_adapter.start_agent(deadline, cancel),
                        deadline,
                        self.step_timeouts.agent_start_s,
                        "start_agent",
                    )
                except Exception as exc:
                    # A restore is only complete when the agent is running
                    # again: an agent-enabled rollback must not publish a
                    # runtime whose agent is gone.
                    warnings.append(f"agent再起動失敗: {_safe_detail(exc)}")
                    return None
            restored = dict(previous)
            restored["lease_id"] = new_lease
            last_result = {
                "request_id": request_id,
                "operation": operation,
                "status": "rolled_back",
                "from_game": previous["game"],
                "to_game": target,
                "generation": generation,
                "restored_generation": int(previous["generation"]),
                "error_code": None,
                "detail": None,
            }
            tx.transition(
                {"rolling_back", "failed"}, "ready",
                updates={
                    "active": restored,
                    "candidate": None,
                    "previous": None,
                    "operation": None,
                    "request_id": None,
                    "deadline_at": None,
                    "last_result": last_result,
                    "last_error": None,
                },
                crash_hook=self.crash_hook,
            )
            receipt_done = (
                tx.finish_request(request_id, "rolled_back", last_result) if finish_receipt else last_result
            )
            pending = self._finalize_locked(tx, deadline, warnings=warnings)
            if cleanup_pending_out is not None:
                cleanup_pending_out.append(pending)
            return receipt_done

        # Replace mode: the previous game was stopped, so restore it as a
        # fresh generation (design §5 F).
        state, _migrated = self.store.canonical.load()
        restore_generation = int(state["next_generation"])
        runtime_id = new_runtime_id(restore_generation)
        names = runtime_names(restore_generation)
        rollback_spec = RuntimeSpec(
            game=str(previous["game"]),
            adapter=str(previous["adapter"]),
            generation=restore_generation,
            runtime_id=runtime_id,
            lease_id=str(uuid.uuid4()),
            runtime_dir=runtime_directory(self.store.state_dir, runtime_id),
            game_window=names.game_window,
            agent_window=names.agent_window,
            adapter_session=names.adapter_session,
        )
        rollback_rd = runtime_state_dict(rollback_spec, _utc_now())
        # Persist the generation allocation before any side effect.
        tx.transition(
            {"rolling_back", "failed"}, "rolling_back",
            updates={
                "next_generation": restore_generation + 1,
                "candidate": rollback_rd,
                "operation": operation,
                "request_id": request_id,
            },
            crash_hook=self.crash_hook,
        )
        try:
            adapter = self._make_adapter(rollback_spec, deadline)
        except Exception as exc:
            warnings.append(f"rollback adapter生成失敗: {_safe_detail(exc)}")
            return None
        try:
            self._call_adapter(
                lambda cancel: adapter.materialize_runtime(deadline, cancel),
                deadline,
                self.step_timeouts.start_s,
                "materialize",
            )
            self._check_deadline(deadline)
            self._call_adapter(
                lambda cancel: adapter.readiness(deadline, cancel),
                deadline,
                max(deadline - time.monotonic(), 0.0),
                "readiness",
            )
            if adapter.agent_enabled:
                self._call_adapter(
                    lambda cancel: adapter.start_agent(deadline, cancel),
                    deadline,
                    self.step_timeouts.agent_start_s,
                    "start_agent",
                )
        except Exception as exc:
            try:
                torn_down = self._teardown_runtime(adapter, deadline)
            except Exception:
                torn_down = False
            state, _migrated = self.store.canonical.load()
            if state.get("candidate") is not None:
                if torn_down:
                    tx.transition(
                        {"rolling_back"}, "rolling_back",
                        updates={"candidate": None},
                        crash_hook=self.crash_hook,
                    )
                else:
                    # Keep the rollback candidate tracked: it could not be
                    # confirmed stopped, so it must not vanish from canonical.
                    tx.transition(
                        {"rolling_back"}, "rolling_back",
                        updates={
                            "candidate": None,
                            "retiring": [state["candidate"], *(state.get("retiring") or [])],
                        },
                        crash_hook=self.crash_hook,
                    )
            warnings.append(f"rollback起動失敗: {_safe_detail(exc)}")
            return None
        last_result = {
            "request_id": request_id,
            "operation": operation,
            "status": "rolled_back",
            "from_game": previous["game"],
            "to_game": target,
            "generation": generation,
            "restored_generation": restore_generation,
            "error_code": None,
            "detail": None,
        }
        tx.transition(
            {"rolling_back", "failed"}, "ready",
            updates={
                "active": rollback_rd,
                "candidate": None,
                "previous": None,
                "operation": None,
                "request_id": None,
                "deadline_at": None,
                "last_result": last_result,
                "last_error": None,
            },
            crash_hook=self.crash_hook,
        )
        receipt_done = (
            tx.finish_request(request_id, "rolled_back", last_result) if finish_receipt else last_result
        )
        pending = self._finalize_locked(tx, deadline, warnings=warnings)
        if cleanup_pending_out is not None:
            cleanup_pending_out.append(pending)
        return receipt_done

    def _fail_locked(
        self,
        tx: GameSwitchTransaction,
        acceptance: RequestAcceptance,
        deadline: float,
        *,
        error_code: str,
        detail: str,
        warnings: list[str] | None = None,
        cleanup_pending: bool = False,
        keep_previous: bool = False,
        keep_active: bool = False,
    ) -> SwitchResult:
        warnings = list(warnings or [])
        state, _migrated = self.store.canonical.load()
        last_result = {
            "request_id": acceptance.request_id,
            "operation": str(acceptance.receipt["operation"]),
            "status": "failed",
            "from_game": state.get("active")["game"] if state.get("active") else None,
            "to_game": acceptance.receipt.get("target"),
            "generation": acceptance.generation,
            "error_code": error_code,
            "detail": detail,
            "cleanup_pending": True if cleanup_pending else None,
        }
        updates = {
            "active": state.get("active") if keep_active else None,
            "candidate": state.get("candidate"),
            "previous": state.get("previous") if keep_previous else None,
            "operation": None,
            "request_id": None,
            "deadline_at": None,
            "last_result": last_result,
            "last_error": {"error_code": error_code, "detail": detail},
        }
        tx.transition(
            IN_PROGRESS_PHASES | {"rolling_back"}, "failed",
            updates=updates,
            crash_hook=self.crash_hook,
        )
        receipt = tx.finish_request(acceptance.request_id, "failed", last_result)
        cleanup_pending = self._finalize_locked(tx, deadline, warnings=warnings) or cleanup_pending
        return _result_from_receipt(
            receipt,
            warnings=tuple(warnings),
            cleanup_pending=cleanup_pending,
        )

    def _finalize_locked(self, tx: GameSwitchTransaction, deadline: float, warnings: list[str]) -> bool:
        """Post-terminal housekeeping: mirror the active game and clean
        retiring runtimes.  Failures are warnings, never rollback reasons."""
        state, _migrated = self.store.canonical.load()
        active = state.get("active")
        try:
            self._write_mirror(active["game"] if active else None)
        except Exception as exc:
            warnings.append(f"current_game mirror更新失敗: {_safe_detail(exc)}")
        cleanup_pending = False
        retiring = list(state.get("retiring") or [])
        remaining: list[Mapping[str, object]] = []
        for runtime in retiring:
            try:
                adapter = self._make_adapter(
                    RuntimeSpec.from_runtime(self.store.state_dir, runtime), deadline
                )
                torn_down = self._teardown_runtime(adapter, deadline)
            except Exception:
                torn_down = False
            if not torn_down:
                cleanup_pending = True
                remaining.append(runtime)
                warnings.append("retiring runtimeの停止を確認できませんでした")
        if len(remaining) != len(retiring):
            tx.transition(
                {state["phase"]}, str(state["phase"]),
                updates={"retiring": remaining},
                crash_hook=self.crash_hook,
            )
        return cleanup_pending

    def _write_mirror(self, game: str | None) -> None:
        if self.mirror_writer is not None:
            self.mirror_writer(self.mirror_path, game)
            return
        if game is None:
            try:
                self.mirror_path.unlink()
            except FileNotFoundError:
                pass
        else:
            atomic_write_text(self.mirror_path, f"{game}\n")

    # --- recovery -----------------------------------------------------------

    def _recover_locked(
        self,
        tx: GameSwitchTransaction,
        *,
        deadline: float,
    ) -> SwitchResult:
        state = self.store.canonical.initialize()
        phase = state["phase"]
        warnings: list[str] = []
        if phase == "idle":
            try:
                self._write_mirror(None)
            except Exception as exc:
                warnings.append(f"mirror修復失敗: {_safe_detail(exc)}")
            self._reconcile_dangling_receipt_locked(tx)
            return SwitchResult(
                request_id="",
                operation="recover",
                status="succeeded",
                target=None,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=None,
                detail="recovery は不要でした",
                warnings=tuple(warnings),
                cleanup_pending=False,
                receipt=None,
            )
        if phase == "recovery_required":
            return SwitchResult(
                request_id="",
                operation="recover",
                status="failed",
                target=None,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=ERROR_RECOVERY_REQUIRED,
                detail="canonical stateが壊れているため自動復旧できません",
                warnings=(),
                cleanup_pending=False,
                receipt=None,
            )
        if phase == "failed":
            state, _migrated = self.store.canonical.load()
            cleanup_pending = self._cleanup_leftovers_locked(tx, state, deadline, warnings)
            state, _migrated = self.store.canonical.load()
            previous = state.get("previous")
            active = state.get("active")
            if previous is not None:
                return self._recover_from_previous_locked(
                    tx, previous, deadline, warnings, cleanup_pending
                )
            if active is not None:
                return self._recover_stop_locked(tx, active, deadline, warnings, cleanup_pending)
            last_result = {
                "request_id": "",
                "operation": "recover",
                "status": "succeeded",
                "from_game": None,
                "to_game": None,
                "generation": None,
                "error_code": None,
                "detail": "failed状態から復旧しました",
            }
            tx.transition(
                {"failed"}, "idle",
                updates={
                    "candidate": None,
                    "previous": None,
                    "operation": None,
                    "request_id": None,
                    "deadline_at": None,
                    "last_result": last_result,
                    "last_error": None,
                },
                crash_hook=self.crash_hook,
            )
            try:
                self._write_mirror(None)
            except Exception as exc:
                warnings.append(f"mirror修復失敗: {_safe_detail(exc)}")
            self._reconcile_dangling_receipt_locked(tx)
            return SwitchResult(
                request_id="",
                operation="recover",
                status="succeeded",
                target=None,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=None,
                detail="failed状態から復旧しました",
                warnings=tuple(warnings),
                cleanup_pending=cleanup_pending,
                receipt=None,
            )
        if phase == "ready":
            state, _migrated = self.store.canonical.load()
            active = state.get("active")
            if active is not None:
                try:
                    adapter = self._make_adapter(
                        RuntimeSpec.from_runtime(self.store.state_dir, active), deadline
                    )
                except Exception as exc:
                    adapter = None
                    warnings.append(f"active probe失敗 (adapter生成): {_safe_detail(exc)}")
                if adapter is not None:
                    alive = self._probe_alive(adapter, deadline)
                else:
                    alive = None
                if alive is False:
                    # The game process is gone, but the agent may still be
                    # alive.  Keep the runtime identity tracked and tear it
                    # down (stop_agent -> cleanup -> confirmed dead) before
                    # forgetting it; an unconfirmed teardown stays in
                    # retiring so recovery can retry.
                    try:
                        torn_down = self._teardown_runtime(adapter, deadline)
                    except Exception:
                        torn_down = False
                    last_result = {
                        "request_id": "",
                        "operation": "recover",
                        "status": "failed",
                        "from_game": active["game"],
                        "to_game": None,
                        "generation": active["generation"],
                        "error_code": ERROR_START_FAILED,
                        "detail": "active runtimeが失われています",
                        "cleanup_pending": None if torn_down else True,
                    }
                    updates = {
                        "active": None,
                        "candidate": state.get("candidate"),
                        "previous": state.get("previous"),
                        "last_result": last_result,
                        "last_error": {
                            "error_code": ERROR_START_FAILED,
                            "detail": "active runtimeが失われています",
                        },
                    }
                    if not torn_down:
                        updates["retiring"] = [active, *(state.get("retiring") or [])]
                    tx.transition(
                        {"ready"}, "failed",
                        updates=updates,
                        crash_hook=self.crash_hook,
                    )
                    try:
                        self._write_mirror(None)
                    except Exception as exc:
                        warnings.append(f"mirror修復失敗: {_safe_detail(exc)}")
                    return SwitchResult(
                        request_id="",
                        operation="recover",
                        status="failed",
                        target=None,
                        from_game=active["game"],
                        to_game=None,
                        generation=active["generation"],
                        error_code=ERROR_START_FAILED,
                        detail="active runtimeが失われています",
                        warnings=tuple(warnings),
                        cleanup_pending=not torn_down,
                        receipt=None,
                    )
                if alive is None:
                    # Probe failed: we do NOT know whether the runtime is
                    # alive.  Keep canonical untouched (active retained) and
                    # fail closed instead of clearing it.
                    return SwitchResult(
                        request_id="",
                        operation="recover",
                        status="failed",
                        target=None,
                        from_game=active["game"],
                        to_game=None,
                        generation=active["generation"],
                        error_code=ERROR_PROBE_FAILED,
                        detail="active runtimeの生存確認ができません (probe失敗)",
                        warnings=tuple(warnings),
                        cleanup_pending=False,
                        receipt=None,
                    )
            cleanup_pending = self._finalize_locked(tx, deadline, warnings=warnings)
            self._reconcile_dangling_receipt_locked(tx)
            return SwitchResult(
                request_id="",
                operation="recover",
                status="succeeded",
                target=None,
                from_game=None,
                to_game=None,
                generation=None,
                error_code=None,
                detail="ready状態を確認しました",
                warnings=tuple(warnings),
                cleanup_pending=cleanup_pending,
                receipt=None,
            )
        if state["operation"] == "stop":
            state, _migrated = self.store.canonical.load()
            active = state.get("active")
            if active is not None:
                try:
                    adapter = self._make_adapter(
                        RuntimeSpec.from_runtime(self.store.state_dir, active), deadline
                    )
                except Exception as exc:
                    return self._recover_fail_locked(
                        tx, state, warnings, "adapter生成に失敗しました", _safe_detail(exc)
                    )
                try:
                    self._call_adapter(
                        lambda cancel: adapter.stop_agent(deadline, cancel),
                        deadline,
                        self.step_timeouts.stop_agent_s,
                        "stop_agent",
                    )
                    self._call_adapter(
                        lambda cancel: adapter.cleanup_runtime(deadline, cancel),
                        deadline,
                        self.step_timeouts.cleanup_s,
                        "cleanup",
                    )
                except Exception as exc:
                    return self._recover_fail_locked(
                        tx, state, warnings, "stop再開に失敗しました", _safe_detail(exc)
                    )
                if not self._wait_stopped(adapter, deadline):
                    return self._recover_fail_locked(
                        tx,
                        state,
                        warnings,
                        "旧runtimeの停止を確認できません (runtimeを保持してfailedに留めます)",
                        ERROR_QUIESCE_FAILED,
                    )
            last_result = {
                "request_id": str(state.get("request_id") or ""),
                "operation": "stop",
                "status": "succeeded",
                "from_game": active["game"] if active is not None else None,
                "to_game": None,
                "generation": active["generation"] if active is not None else None,
            }
            tx.transition(
                IN_PROGRESS_PHASES, "idle",
                updates={
                    "active": None,
                    "candidate": None,
                    "previous": None,
                    "operation": None,
                    "request_id": None,
                    "deadline_at": None,
                    "last_result": last_result,
                    "last_error": None,
                },
                crash_hook=self.crash_hook,
            )
            request_id = state.get("request_id")
            if request_id is not None:
                self._finish_recovering_receipt(tx, str(request_id), "succeeded", last_result)
            try:
                self._write_mirror(None)
            except Exception as exc:
                warnings.append(f"mirror修復失敗: {_safe_detail(exc)}")
            return SwitchResult(
                request_id=str(request_id or ""),
                operation="recover",
                status="succeeded",
                target=None,
                from_game=last_result["from_game"],
                to_game=None,
                generation=last_result["generation"],
                error_code=None,
                detail="停止を完了しました",
                warnings=tuple(warnings),
                cleanup_pending=False,
                receipt=None,
            )
        # start/switch/restart/rotate crashed mid-transition: cleanup the
        # candidate and restore the previous game.
        request_id = state.get("request_id")
        receipt = self.store.receipts.load(str(request_id)) if request_id else None
        if receipt is None:
            raise StateCorruptError("進行中canonicalに対応するrequest receiptがありません")
        pseudo = RequestAcceptance(
            request_id=str(request_id),
            generation=int(receipt["generation"]),
            status="in_progress",
            existing=True,
            receipt=receipt,
        )
        return self._rollback_locked(
            tx,
            pseudo,
            deadline,
            error_code="recovery",
            detail="crash後にrecoveryで復旧しました",
        )

    def _recover_from_previous_locked(
        self,
        tx: GameSwitchTransaction,
        previous: Mapping[str, object],
        deadline: float,
        warnings: list[str],
        cleanup_pending: bool,
    ) -> SwitchResult:
        state, _migrated = self.store.canonical.load()
        last_result = state.get("last_result")
        if not isinstance(last_result, dict):
            raise StateCorruptError("failed phaseの復旧に必要なlast_resultがありません")
        request_id = str(last_result.get("request_id") or "")
        operation = str(last_result.get("operation") or "recover")
        target = last_result.get("to_game")
        generation = last_result.get("generation")
        if not isinstance(generation, int):
            raise StateCorruptError("failed phaseの復旧に必要なgenerationがありません")
        # The restore is a rollback-style recovery: give it its own budget so
        # an expired request/recover deadline cannot block the restore.
        deadline = time.monotonic() + self.rollback_timeout_s
        pending_out: list[bool] = []
        restored = self._restore_previous_locked(
            tx,
            request_id,
            operation,
            target,
            generation,
            previous,
            deadline,
            warnings=warnings,
            finish_receipt=False,
            cleanup_pending_out=pending_out,
        )
        if restored is None:
            return self._recover_fail_locked(
                tx, state, warnings, "previous runtimeの復旧に失敗しました", "rollback_failed"
            )
        self._reconcile_dangling_receipt_locked(tx)
        return _result_from_receipt(
            restored,
            warnings=tuple(warnings),
            cleanup_pending=cleanup_pending or any(pending_out),
        )

    def _recover_stop_locked(
        self,
        tx: GameSwitchTransaction,
        active: Mapping[str, object],
        deadline: float,
        warnings: list[str],
        cleanup_pending: bool,
    ) -> SwitchResult:
        try:
            adapter = self._make_adapter(
                RuntimeSpec.from_runtime(self.store.state_dir, active), deadline
            )
            self._call_adapter(
                lambda cancel: adapter.stop_agent(deadline, cancel),
                deadline,
                self.step_timeouts.stop_agent_s,
                "stop_agent",
            )
            self._call_adapter(
                lambda cancel: adapter.cleanup_runtime(deadline, cancel),
                deadline,
                self.step_timeouts.cleanup_s,
                "cleanup",
            )
        except Exception as exc:
            state, _migrated = self.store.canonical.load()
            return self._recover_fail_locked(
                tx, state, warnings, "stop再開に失敗しました", _safe_detail(exc)
            )
        if not self._wait_stopped(adapter, deadline):
            state, _migrated = self.store.canonical.load()
            return self._recover_fail_locked(
                tx,
                state,
                warnings,
                "旧runtimeの停止を確認できません (runtimeを保持してfailedに留めます)",
                ERROR_QUIESCE_FAILED,
            )
        last_result = {
            "request_id": "",
            "operation": "recover",
            "status": "succeeded",
            "from_game": active["game"],
            "to_game": None,
            "generation": active["generation"],
            "error_code": None,
            "detail": None,
        }
        tx.transition(
            {"failed"}, "idle",
            updates={
                "active": None,
                "candidate": None,
                "previous": None,
                "operation": None,
                "request_id": None,
                "deadline_at": None,
                "last_result": last_result,
                "last_error": None,
            },
            crash_hook=self.crash_hook,
        )
        try:
            self._write_mirror(None)
        except Exception as exc:
            warnings.append(f"mirror修復失敗: {_safe_detail(exc)}")
        self._reconcile_dangling_receipt_locked(tx)
        return SwitchResult(
            request_id="",
            operation="recover",
            status="succeeded",
            target=None,
            from_game=active["game"],
            to_game=None,
            generation=active["generation"],
            error_code=None,
            detail="停止を完了しました",
            warnings=tuple(warnings),
            cleanup_pending=cleanup_pending,
            receipt=None,
        )

    def _recover_fail_locked(
        self,
        tx: GameSwitchTransaction,
        state: Mapping[str, object],
        warnings: list[str],
        detail: str,
        error_code: str,
    ) -> SwitchResult:
        last_result = {
            "request_id": str(state.get("request_id") or ""),
            "operation": "recover",
            "status": "failed",
            "from_game": None,
            "to_game": None,
            "generation": None,
            "error_code": error_code,
            "detail": detail,
            "cleanup_pending": None,
        }
        try:
            tx.transition(
                {str(state["phase"])}, "failed",
                updates={
                    "last_result": last_result,
                    "last_error": {"error_code": error_code, "detail": detail},
                },
                crash_hook=self.crash_hook,
            )
        except InvalidTransitionError:
            pass
        return SwitchResult(
            request_id="",
            operation="recover",
            status="failed",
            target=None,
            from_game=None,
            to_game=None,
            generation=None,
            error_code=error_code,
            detail=detail,
            warnings=tuple(warnings),
            cleanup_pending=False,
            receipt=None,
        )

    def _cleanup_leftovers_locked(
        self,
        tx: GameSwitchTransaction,
        state: Mapping[str, object],
        deadline: float,
        warnings: list[str],
    ) -> bool:
        cleanup_pending = False
        candidate = state.get("candidate")
        if candidate is not None:
            try:
                adapter = self._make_adapter(
                    RuntimeSpec.from_runtime(self.store.state_dir, candidate), deadline
                )
                torn_down = self._teardown_runtime(adapter, deadline)
            except Exception as exc:
                torn_down = False
            if not torn_down:
                cleanup_pending = True
                warnings.append("candidateの停止を確認できませんでした")
                # A later restore commit would overwrite candidate with None,
                # losing track of this runtime.  Keep it durably tracked in
                # retiring so recovery/finalize retries the cleanup.
                current, _migrated = self.store.canonical.load()
                tx.transition(
                    {str(current["phase"])}, str(current["phase"]),
                    updates={
                        "candidate": None,
                        "retiring": [candidate, *(current.get("retiring") or [])],
                    },
                    crash_hook=self.crash_hook,
                )
            else:
                state, _migrated = self.store.canonical.load()
                if state.get("candidate") is not None:
                    tx.transition(
                        {str(state["phase"])}, str(state["phase"]),
                        updates={"candidate": None},
                        crash_hook=self.crash_hook,
                    )
        return cleanup_pending

    def _finish_recovering_receipt(
        self,
        tx: GameSwitchTransaction,
        request_id: str,
        status: str,
        result: Mapping[str, object],
    ) -> None:
        receipt = self.store.receipts.load(request_id)
        if receipt is None or receipt.get("status") in TERMINAL_RECEIPT_STATUSES:
            return
        tx.finish_request(request_id, status, result)

    def _reconcile_dangling_receipt_locked(self, tx: GameSwitchTransaction) -> None:
        """Finish a receipt left non-terminal by a crash between the terminal
        canonical write and finish_request, using canonical last_result."""
        state, _migrated = self.store.canonical.load()
        last_result = state.get("last_result")
        if not isinstance(last_result, dict):
            return
        status = last_result.get("status")
        if status not in TERMINAL_RECEIPT_STATUSES:
            return
        request_id = last_result.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return
        receipt = self.store.receipts.load(request_id)
        if receipt is None or receipt.get("status") in TERMINAL_RECEIPT_STATUSES:
            return
        tx.finish_request(request_id, str(status), dict(last_result))


def _failure_code(exc: BaseException, default: str = ERROR_START_FAILED) -> str:
    if isinstance(exc, ReadinessTimeoutError):
        return ERROR_READINESS_TIMEOUT
    if isinstance(exc, DeadlineExceededError):
        return ERROR_TIMEOUT
    return default
