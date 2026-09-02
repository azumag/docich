"""Crash-safe P0 primitives for transactional game switching.

This module deliberately does not change the existing CLI lifecycle yet.
It establishes the canonical state, locking, generation allocation and
request-receipt contracts that the P1 coordinator will use.
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
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping

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
    identities: list[tuple[str, int]] = []
    for runtime in [active, candidate, previous, *retiring]:
        if isinstance(runtime, dict):
            identities.append((str(runtime["runtime_id"]), int(runtime["generation"])))
    if len(identities) != len(set(identities)):
        raise StateCorruptError("runtime identityが重複しています")
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
