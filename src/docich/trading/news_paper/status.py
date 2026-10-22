"""Read-only status projection for the news PAPER lane (N07).

Everything in this module is a *projection*: it opens the dedicated
``paper.sqlite3`` with ``mode=ro`` (never creating or migrating a store),
reads an allow-listed set of columns and merges the worker's ``health.json``
sidecar. No other account is read or aggregated, and no method writes a file.

Contract (issue #784):

* A missing database or account yields ``state="empty"``; the status command
  never creates a directory, database or WAL file.
* Staleness is computed from durable timestamps (``heartbeat_at``,
  ``market_available_at``, valuation ``as_of``) — never from a file mtime.
* Absent inputs (news/classifier, not connected before N02/N03) are reported
  as ``null`` with a reason, never as fresh.
* ``mode`` is always ``paper`` and ``live_eligible`` is always false.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sqlite3
import stat
import sys
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

MODE = "paper"
LIVE_ELIGIBLE = False
STATUS_SCHEMA_VERSION = 1
HEALTH_FILE_NAME = "health.json"
DB_FILE_NAME = "paper.sqlite3"
WORKER_LOCK_NAME = "worker.lock"
_MAX_REASON_CODES = 16

_STATE_OK = "ok"
_STATE_EMPTY = "empty"
_STATE_BLOCKED = "blocked"
_STATE_UNAVAILABLE = "unavailable"


class StatusError(ValueError):
    """Read-only projection failure; ``code`` is a fixed error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _age_seconds(now: datetime, value: str | None) -> int | None:
    parsed = _parse_ts(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _sqlite_ro_uri(path: Path) -> str:
    return "file:" + urllib.parse.quote(str(path), safe="/") + "?mode=ro"


def _id_like(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise StatusError("VALUE_OUT_OF_RANGE")
    if not value[0].isascii() or not value[0].isalnum():
        raise StatusError("VALUE_OUT_OF_RANGE")
    for ch in value:
        if not ch.isascii() or not (ch.isalnum() or ch in "_.:-"):
            raise StatusError("VALUE_OUT_OF_RANGE")
    return value


def _existing_ledger_path(state_dir: str | Path, run_id: str, scenario_id: str) -> Path | None:
    """Mirror the ledger path rules without creating anything.

    Returns the canonical path when the whole chain exists; ``None`` when a
    directory or the database is absent. A symlinked component, a non-regular
    file or a hardlinked database is refused (the projection must never read a
    store reached through an alias).
    """

    raw = Path(state_dir)
    if not raw.is_absolute() or ".." in raw.parts:
        raise StatusError("STATE_PATH_FORBIDDEN")
    # Mirror the ledger: canonicalise the state root first (macOS /tmp lives
    # behind /private/tmp), then verify that no remaining component is a
    # symlink. The canonical target is therefore fixed and read-only.
    base = Path(os.path.realpath(state_dir))
    if not base.is_absolute():
        raise StatusError("STATE_PATH_FORBIDDEN")
    path = base / "news-paper" / "runs" / _id_like(run_id) / _id_like(scenario_id)
    current = Path(path.anchor)
    for part in (*path.parts[1:], DB_FILE_NAME):
        current = current / part
        try:
            info = os.lstat(current)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except PermissionError as exc:
            raise StatusError("STORE_UNAVAILABLE") from exc
        if stat.S_ISLNK(info.st_mode):
            raise StatusError("STATE_PATH_FORBIDDEN")
        if part == DB_FILE_NAME:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StatusError("STATE_PATH_FORBIDDEN")
        elif not stat.S_ISDIR(info.st_mode):
            raise StatusError("STATE_PATH_FORBIDDEN")
    return current


def _open_ro(
    path: Path, *, state_dir: Path
) -> tuple[sqlite3.Connection | None, int | None]:
    """Open the dedicated store read-only, returning the connection and a lock fd.

    A clean (checkpointed) store is opened with ``immutable=1`` so that SQLite
    creates neither ``-wal`` nor ``-shm`` and even a read-only directory works.
    When a worker currently holds ``worker.lock`` (or a ``-wal`` exists), the
    WAL read path is used instead; the caller keeps a shared flock for the
    duration of the read so the writer cannot start/checkpoint mid-read.
    """

    wal = path.with_name(path.name + "-wal")
    immutable = not os.path.exists(wal)
    lock_fd: int | None = None
    lock_path = state_dir / "news-paper" / WORKER_LOCK_NAME
    if immutable and os.path.exists(lock_path):
        try:
            fd = os.open(lock_path, os.O_RDWR)
        except OSError:
            immutable = False
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                immutable = False
            else:
                lock_fd = fd
    suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    try:
        connection = sqlite3.connect(
            "file:" + urllib.parse.quote(str(path), safe="/") + suffix, uri=True, timeout=5.0
        )
        connection.execute("SELECT 1").fetchone()
        return connection, lock_fd
    except sqlite3.Error:
        if lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock_fd)
        return None, None


def _release_lock(lock_fd: int | None) -> None:
    if lock_fd is None:
        return
    with contextlib.suppress(OSError):
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        os.close(lock_fd)


def _account_block(connection: sqlite3.Connection, account_id: str) -> dict[str, object] | None:
    row = connection.execute(
        "SELECT run_id, scenario_id, quote_currency, initial_cash, cash, revision, created_at "
        "FROM accounts WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row[0],
        "scenario_id": row[1],
        "quote_currency": row[2],
        "initial_cash": str(row[3]),
        "cash": str(row[4]),
        "revision": int(row[5]),
        "created_at": row[6],
    }


def _positions_block(connection: sqlite3.Connection, account_id: str) -> list[dict[str, str]]:
    return [
        {"asset": asset, "quantity": str(quantity), "average_cost": str(average_cost)}
        for asset, quantity, average_cost in connection.execute(
            "SELECT asset, quantity, avg_cost FROM positions WHERE account_id = ? ORDER BY asset",
            (account_id,),
        )
    ]


def _receipt_block(connection: sqlite3.Connection, account_id: str) -> dict[str, object]:
    counts: dict[str, int] = {"accepted": 0, "held": 0, "rejected": 0}
    reasons: dict[str, int] = {}
    latest_decision_at: str | None = None
    uncommitted = 0
    abandoned = 0
    for status, reason_codes, decision_at, committed_at, abandoned_at in connection.execute(
        "SELECT status, reason_codes, decision_at, receipt_committed_at, abandoned_at "
        "FROM decision_receipts WHERE account_id = ?",
        (account_id,),
    ):
        counts[status] = counts.get(status, 0) + 1
        if abandoned_at is not None:
            abandoned += 1
        elif status == "accepted" and committed_at is None:
            uncommitted += 1
        if latest_decision_at is None or str(decision_at) > latest_decision_at:
            latest_decision_at = str(decision_at)
        try:
            codes = json.loads(reason_codes)
        except (TypeError, ValueError):
            continue
        if not isinstance(codes, list):
            continue
        for code in codes:
            if isinstance(code, str):
                reasons[code] = reasons.get(code, 0) + 1
    top = sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:_MAX_REASON_CODES]
    return {
        "counts": counts,
        "reason_counts": [{"code": code, "count": count} for code, count in top],
        "latest_decision_at": latest_decision_at,
        "accepted_uncommitted": uncommitted,
        "abandoned": abandoned,
    }


def _order_block(connection: sqlite3.Connection, account_id: str) -> dict[str, object]:
    counts: dict[str, int] = {}
    pending_buy = 0
    pending_sell = 0
    oldest_pending_expiry: str | None = None
    for status, side, expires_at in connection.execute(
        "SELECT status, side, expires_at FROM paper_orders WHERE account_id = ?",
        (account_id,),
    ):
        counts[status] = counts.get(status, 0) + 1
        if status in {"pending", "partially_filled"}:
            if side == "buy":
                pending_buy += 1
            else:
                pending_sell += 1
            if oldest_pending_expiry is None or str(expires_at) < oldest_pending_expiry:
                oldest_pending_expiry = str(expires_at)
    return {
        "counts": counts,
        "pending_buy": pending_buy,
        "pending_sell": pending_sell,
        "oldest_pending_expires_at": oldest_pending_expiry,
    }


def _fill_block(connection: sqlite3.Connection, account_id: str) -> dict[str, object]:
    row = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(CAST(fee_jpy AS NUMERIC)), 0), MAX(filled_at) "
        "FROM fills WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    count, fee_total, last_filled_at = row if row is not None else (0, 0, None)
    return {
        "count": int(count or 0),
        "fee_jpy_total": str(Decimal(str(fee_total or 0))),
        "last_filled_at": last_filled_at,
    }


def _valuation_block(connection: sqlite3.Connection, account_id: str) -> dict[str, object] | None:
    row = connection.execute(
        "SELECT as_of, cash, positions_value, total_equity, stale FROM valuations "
        "WHERE account_id = ? ORDER BY as_of DESC, rowid DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "as_of": row[0],
        "cash": str(row[1]),
        "positions_value": str(row[2]),
        "total_equity": str(row[3]),
        "stale": bool(row[4]),
    }


def _count(connection: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    row = connection.execute(sql, params).fetchone()
    return int(row[0] or 0) if row is not None else 0


def _read_health(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _health_block_missing() -> dict[str, object]:
    return {
        "present": False,
        "status": None,
        "heartbeat_at": None,
        "heartbeat_age_seconds": None,
        "started_at": None,
        "cycle_seq": None,
        "entries_disabled": None,
        "queue": None,
        "last_market_available_at": None,
        "last_fill_at": None,
        "last_error_code": None,
    }


def _health_block(
    path: Path, now: datetime, *, run_id: str, scenario_id: str, account_id: str
) -> dict[str, object]:
    payload = _read_health(path)
    if payload is None:
        return _health_block_missing()
    # The heartbeat file is shared by every run under one state root: never
    # project another run/scenario/account as this one's worker state.
    for key, expected in (
        ("run_id", run_id),
        ("scenario_id", scenario_id),
        ("account_id", account_id),
    ):
        value = payload.get(key)
        if not isinstance(value, str) or value != expected:
            return _health_block_missing()
    queue = None
    if any(
        key in payload
        for key in (
            "queue_depth",
            "queue_dropped_full",
            "queue_dropped_expired",
            "queue_dropped_duplicate",
        )
    ):
        queue = {
            "depth": payload.get("queue_depth"),
            "dropped_full": payload.get("queue_dropped_full"),
            "dropped_expired": payload.get("queue_dropped_expired"),
            "dropped_duplicate": payload.get("queue_dropped_duplicate"),
        }
    heartbeat_at = payload.get("heartbeat_at")
    return {
        "present": True,
        "status": payload.get("status") if isinstance(payload.get("status"), str) else None,
        "heartbeat_at": heartbeat_at if isinstance(heartbeat_at, str) else None,
        "heartbeat_age_seconds": _age_seconds(
            now, heartbeat_at if isinstance(heartbeat_at, str) else None
        ),
        "started_at": payload.get("started_at") if isinstance(payload.get("started_at"), str) else None,
        "cycle_seq": payload.get("cycle_seq") if isinstance(payload.get("cycle_seq"), int) else None,
        "entries_disabled": (
            payload.get("entries_disabled")
            if isinstance(payload.get("entries_disabled"), bool)
            else None
        ),
        "queue": queue,
        "last_market_available_at": (
            payload.get("last_market_available_at")
            if isinstance(payload.get("last_market_available_at"), str)
            else None
        ),
        "last_fill_at": (
            payload.get("last_fill_at") if isinstance(payload.get("last_fill_at"), str) else None
        ),
        "last_error_code": (
            payload.get("last_error_code")
            if isinstance(payload.get("last_error_code"), str)
            else None
        ),
    }


def _empty_status(
    state: str,
    *,
    moment: datetime,
    run_id: str,
    scenario_id: str,
    account_id: str,
    worker: dict[str, object],
    reason: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "state": state,
        "mode": MODE,
        "live_eligible": LIVE_ELIGIBLE,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "account_id": account_id,
        "account": None,
        "positions": [],
        "receipts": None,
        "orders": None,
        "fills": None,
        "valuation": None,
        "pending_work": None,
        "ages": {
            "heartbeat_seconds": worker.get("heartbeat_age_seconds"),
            "market_seconds": _age_seconds(
                moment, worker.get("last_market_available_at")  # type: ignore[arg-type]
            ),
            "news_seconds": None,
            "classifier_seconds": None,
            "not_connected": ["news", "classifier"],
        },
        "worker": worker,
    }
    if reason is not None:
        result["reason_code"] = reason
    return result


def build_status(
    state_dir: str | Path,
    run_id: str,
    scenario_id: str,
    account_id: str,
    *,
    now: datetime | None = None,
    health_file: str | Path | None = None,
) -> dict[str, object]:
    """Build the read-only status projection. Never writes anything."""

    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise StatusError("CLOCK_UNTRUSTED")
    try:
        ledger_path = _existing_ledger_path(state_dir, run_id, scenario_id)
    except StatusError as exc:
        return _empty_status(
            _STATE_BLOCKED,
            moment=moment,
            run_id=run_id,
            scenario_id=scenario_id,
            account_id=account_id,
            worker=_health_block_missing(),
            reason=exc.code,
        )
    health_path = (
        Path(health_file)
        if health_file is not None
        else Path(state_dir) / "news-paper" / HEALTH_FILE_NAME
    )
    worker = _health_block(
        health_path, moment, run_id=run_id, scenario_id=scenario_id, account_id=account_id
    )
    if ledger_path is None:
        return _empty_status(
            _STATE_EMPTY,
            moment=moment,
            run_id=run_id,
            scenario_id=scenario_id,
            account_id=account_id,
            worker=worker,
        )
    connection, lock_fd = _open_ro(ledger_path, state_dir=Path(os.path.realpath(state_dir)))
    if connection is None:
        return _empty_status(
            _STATE_UNAVAILABLE,
            moment=moment,
            run_id=run_id,
            scenario_id=scenario_id,
            account_id=account_id,
            worker=worker,
            reason="STORE_UNAVAILABLE",
        )
    try:
        account = _account_block(connection, account_id)
        if account is None:
            return _empty_status(
                _STATE_EMPTY,
                moment=moment,
                run_id=run_id,
                scenario_id=scenario_id,
                account_id=account_id,
                worker=worker,
                reason="UNKNOWN_KEY",
            )
        positions = _positions_block(connection, account_id)
        receipts = _receipt_block(connection, account_id)
        orders = _order_block(connection, account_id)
        fills = _fill_block(connection, account_id)
        valuation = _valuation_block(connection, account_id)
        event_uses = _count(
            connection,
            "SELECT COUNT(*) FROM event_use WHERE run_id = ? AND scenario_id = ?",
            (run_id, scenario_id),
        )
    except sqlite3.Error:
        return _empty_status(
            _STATE_UNAVAILABLE,
            moment=moment,
            run_id=run_id,
            scenario_id=scenario_id,
            account_id=account_id,
            worker=worker,
            reason="STORE_UNAVAILABLE",
        )
    finally:
        connection.close()
        _release_lock(lock_fd)

    market_seconds = _age_seconds(moment, worker.get("last_market_available_at"))  # type: ignore[arg-type]
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "state": _STATE_OK,
        "mode": MODE,
        "live_eligible": LIVE_ELIGIBLE,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "account_id": account_id,
        "phase": worker.get("status"),
        "entries_disabled": worker.get("entries_disabled"),
        "account": account,
        "positions": positions,
        "receipts": receipts,
        "orders": orders,
        "fills": fills,
        "valuation": valuation,
        "pending_work": {
            "accepted_uncommitted": receipts["accepted_uncommitted"],
            "pending_buy": orders["pending_buy"],
            "pending_sell": orders["pending_sell"],
            "total": int(receipts["accepted_uncommitted"])
            + int(orders["pending_buy"])
            + int(orders["pending_sell"]),
        },
        "event_uses": event_uses,
        "ages": {
            "heartbeat_seconds": worker.get("heartbeat_age_seconds"),
            "market_seconds": market_seconds,
            "news_seconds": None,
            "classifier_seconds": None,
            "not_connected": ["news", "classifier"],
        },
        "worker": worker,
    }


def _identity_from_config(path: str) -> tuple[str, str, str, str]:
    import tomllib

    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StatusError("VALUE_OUT_OF_RANGE") from exc
    for section in ("worker", "dashboard"):
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        keys = ("state_dir", "run_id", "scenario_id", "account_id")
        if all(isinstance(block.get(key), str) and block[key] for key in keys):
            return block["state_dir"], block["run_id"], block["scenario_id"], block["account_id"]
    raise StatusError("VALUE_OUT_OF_RANGE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docich-news-paper-status")
    parser.add_argument("--config", default=None, help="derive the identity from this TOML")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--scenario-id", default=None)
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--health-file", default=None)
    args = parser.parse_args(argv)
    try:
        if args.config is not None:
            state_dir, run_id, scenario_id, account_id = _identity_from_config(args.config)
        else:
            if not all((args.state_dir, args.run_id, args.scenario_id, args.account_id)):
                raise StatusError("VALUE_OUT_OF_RANGE")
            state_dir, run_id, scenario_id, account_id = (
                args.state_dir,
                args.run_id,
                args.scenario_id,
                args.account_id,
            )
    except StatusError as exc:
        print(
            json.dumps(
                {
                    "schema_version": STATUS_SCHEMA_VERSION,
                    "state": _STATE_BLOCKED,
                    "reason_code": exc.code,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    try:
        status = build_status(
            state_dir,
            run_id,
            scenario_id,
            account_id,
            health_file=args.health_file,
        )
    except StatusError as exc:
        status = {
            "schema_version": STATUS_SCHEMA_VERSION,
            "state": _STATE_BLOCKED,
            "reason_code": exc.code,
        }
        print(json.dumps(status, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
