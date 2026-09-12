#!/usr/bin/env python3
"""One-shot production E2E probe for the isolated free-strategy PAPER worker.

The probe deliberately does not have Docker access itself.  It registers one
no-trade candidate in the real production lab, waits for the already-running
systemd worker (the only process granted the docker supplementary group) to
execute it through gVisor, verifies the durable receipt/state, then moves only
that exact operational probe to review_due so it consumes no research slot.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path("/home/ubuntu/docich")
ENV_FILE = Path("/home/ubuntu/.config/docich/free-strategy-worker.env")
SERVICE = "docich-free-strategy-worker.service"
NAME = "__docich_ops_smoke__"
FAMILY = "ops_smoke"
THESIS = "Bounded production E2E probe; never places PAPER targets."
SYMBOL = "BTC/JPY"
IMAGE_RE = re.compile(r"(?:[A-Za-z0-9._/:-]+@)?sha256:[0-9a-f]{64}\Z")
POLL_SECONDS = 5.0
DEADLINE_SECONDS = 420.0


class SmokeError(RuntimeError):
    pass


def _fail(code: str) -> None:
    print(f"free_strategy_smoke={code}", file=sys.stderr)
    raise SystemExit(1)


def _read_worker_env(path: Path = ENV_FILE) -> tuple[Path, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SmokeError("worker_env_unavailable") from exc
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SmokeError("worker_env_invalid")
        key, value = line.split("=", 1)
        if key in {"DOCICH_FREE_STRATEGY_TRADING_DIR", "DOCICH_FREE_STRATEGY_IMAGE"}:
            if key in values or not value or any(ch in value for ch in "\r\n\0"):
                raise SmokeError("worker_env_invalid")
            values[key] = value
    trading = values.get("DOCICH_FREE_STRATEGY_TRADING_DIR")
    image = values.get("DOCICH_FREE_STRATEGY_IMAGE")
    if not trading or not image or not IMAGE_RE.fullmatch(image):
        raise SmokeError("worker_env_invalid")
    trading_path = Path(trading)
    if not trading_path.is_absolute():
        raise SmokeError("worker_env_invalid")
    try:
        resolved_root = ROOT.resolve(strict=True)
        resolved_trading = trading_path.resolve(strict=True)
    except OSError as exc:
        raise SmokeError("worker_env_unavailable") from exc
    if resolved_trading == resolved_root or resolved_root not in resolved_trading.parents:
        raise SmokeError("worker_trading_dir_outside_root")
    return resolved_trading, image


def _worker_pid() -> int:
    try:
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", SERVICE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if active.returncode:
            raise SmokeError("worker_not_active")
        raw = subprocess.check_output(
            ["systemctl", "show", SERVICE, "--property=MainPID", "--value"],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()
        pid = int(raw)
        if pid <= 1:
            raise ValueError
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SmokeError("worker_identity_unavailable") from exc
    if b"free-strategy-worker" not in cmdline:
        raise SmokeError("worker_identity_mismatch")
    return pid


def _run_cli(*args: str) -> dict:
    cmd = [str(ROOT / "bin" / "docich"), "free-strategy", *args]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeError("cli_unavailable") from exc
    if proc.returncode:
        raise SmokeError("cli_failed")
    try:
        data = json.loads(proc.stdout)
    except (TypeError, ValueError) as exc:
        raise SmokeError("cli_output_invalid") from exc
    if not isinstance(data, dict):
        raise SmokeError("cli_output_invalid")
    return data


def _decode(raw) -> dict:
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, str):
        raw = raw.encode()
    if not isinstance(raw, (bytes, bytearray)):
        raise SmokeError("lab_state_invalid")
    try:
        value = json.loads(bytes(raw))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SmokeError("lab_state_invalid") from exc
    if not isinstance(value, dict):
        raise SmokeError("lab_state_invalid")
    return value


def _snapshot(db_path: Path, identity: str, artifact: str) -> dict:
    try:
        with sqlite3.connect(db_path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """SELECT e.id,e.artifact,e.phase,e.revision,e.account,e.strategy_state,
                          e.pending,e.last_bar,e.last_error,e.last_success,e.end_at,a.payload
                   FROM experiments e JOIN artifacts a ON a.digest=e.artifact
                   WHERE e.id=?""",
                (identity,),
            ).fetchone()
            if row is None or row["artifact"] != artifact:
                raise SmokeError("smoke_identity_missing")
            payload = _decode(row["payload"])
            account = _decode(row["account"])
            state = _decode(row["strategy_state"])
            pending = _decode(row["pending"])
            decisions = db.execute(
                "SELECT COUNT(*) FROM decisions WHERE experiment=?", (identity,)
            ).fetchone()[0]
            fills = db.execute(
                "SELECT COUNT(*) FROM fills WHERE experiment=?", (identity,)
            ).fetchone()[0]
    except sqlite3.Error as exc:
        raise SmokeError("lab_unavailable") from exc
    if (
        payload.get("name") != NAME
        or payload.get("family") != FAMILY
        or payload.get("symbols") != [SYMBOL]
        or account.get("positions") not in ({}, None)
        or pending != {}
    ):
        raise SmokeError("smoke_identity_mismatch")
    return {
        "phase": row["phase"],
        "revision": row["revision"],
        "last_bar": row["last_bar"],
        "last_error": row["last_error"],
        "last_success": row["last_success"],
        "end_at": row["end_at"],
        "state": state,
        "decisions": decisions,
        "fills": fills,
    }


def _finalize(db_path: Path, identity: str, artifact: str, *, now: float) -> None:
    """Free the observation slot without deleting audit evidence."""
    try:
        with sqlite3.connect(db_path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT e.artifact,e.phase,e.account,e.pending,a.payload
                   FROM experiments e JOIN artifacts a ON a.digest=e.artifact
                   WHERE e.id=?""",
                (identity,),
            ).fetchone()
            if row is None or row["artifact"] != artifact:
                raise SmokeError("smoke_cleanup_refused")
            payload = _decode(row["payload"])
            account = _decode(row["account"])
            pending = _decode(row["pending"])
            fills = db.execute(
                "SELECT COUNT(*) FROM fills WHERE experiment=?", (identity,)
            ).fetchone()[0]
            if (
                payload.get("name") != NAME
                or payload.get("family") != FAMILY
                or payload.get("symbols") != [SYMBOL]
                or fills != 0
                or account.get("positions") not in ({}, None)
                or pending != {}
                or row["phase"] not in {"research", "paper_validating", "review_due"}
            ):
                raise SmokeError("smoke_cleanup_refused")
            if row["phase"] != "review_due":
                db.execute(
                    """UPDATE experiments
                       SET phase='review_due',end_at=?,pending=?,revision=revision+1,last_error=NULL
                       WHERE id=?""",
                    (now - 1.0, b"{}", identity),
                )
            db.commit()
    except sqlite3.Error as exc:
        raise SmokeError("smoke_cleanup_failed") from exc


def _assert_no_existing_smoke(db_path: Path) -> None:
    if not db_path.exists():
        return
    try:
        with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
            rows = db.execute(
                """SELECT a.payload FROM experiments e
                   JOIN artifacts a ON a.digest=e.artifact
                   WHERE e.phase IN ('research','paper_validating','paused')"""
            ).fetchall()
    except sqlite3.Error as exc:
        raise SmokeError("lab_unavailable") from exc
    for (raw,) in rows:
        payload = _decode(raw)
        if payload.get("family") == FAMILY:
            raise SmokeError("previous_smoke_still_active")


def main() -> int:
    trading_dir, image = _read_worker_env()
    worker_pid = _worker_pid()
    db_path = trading_dir / "free-strategies" / "lab.sqlite3"
    _assert_no_existing_smoke(db_path)

    identity = artifact = None
    with tempfile.TemporaryDirectory(prefix="docich-free-strategy-smoke-", dir="/tmp") as tmp:
        source = Path(tmp) / "strategy.py"
        source.write_text(
            "def decide(context):\n"
            "    previous = context.get('state') or {}\n"
            "    count = int(previous.get('smoke_runs', 0)) + 1\n"
            "    return {'schema_version': 1, 'target_positions': [], "
            "'state': {'smoke_runs': count}, 'reason': 'bounded ops smoke'}\n",
            encoding="utf-8",
        )
        try:
            registered = _run_cli(
                "--trading-dir", str(trading_dir),
                "register",
                "--source", str(source),
                "--image", image,
                "--name", NAME,
                "--family", FAMILY,
                "--thesis", THESIS,
                "--symbols", SYMBOL,
                "--capital", "10000",
                "--days", "1",
            )
            identity = registered.get("experiment")
            artifact = registered.get("artifact")
            if (
                not isinstance(identity, str)
                or not re.fullmatch(r"[0-9a-f]{32}", identity)
                or not isinstance(artifact, str)
                or not re.fullmatch(r"[0-9a-f]{64}", artifact)
            ):
                raise SmokeError("registration_invalid")

            deadline = time.monotonic() + DEADLINE_SECONDS
            while True:
                current = _worker_pid()
                if current != worker_pid:
                    raise SmokeError("worker_restarted_during_smoke")
                snap = _snapshot(db_path, identity, artifact)
                if (
                    snap["decisions"] >= 1
                    and snap["last_bar"] is not None
                    and snap["last_success"] is not None
                    and snap["last_error"] is None
                    and snap["fills"] == 0
                    and int(snap["state"].get("smoke_runs", 0)) >= 1
                    and snap["phase"] == "paper_validating"
                ):
                    break
                if time.monotonic() >= deadline:
                    raise SmokeError("worker_cycle_timeout")
                time.sleep(POLL_SECONDS)

            _finalize(db_path, identity, artifact, now=time.time())
            report = _run_cli(
                "--trading-dir", str(trading_dir),
                "evaluate",
                "--experiment", identity,
            )
            if report.get("live_eligible") is not False or report.get("research_review_due") is not True:
                raise SmokeError("final_evaluation_invalid")
            final = _snapshot(db_path, identity, artifact)
            if final["phase"] != "review_due" or final["fills"] != 0:
                raise SmokeError("smoke_finalize_invalid")
        except BaseException:
            if identity and artifact:
                try:
                    _finalize(db_path, identity, artifact, now=time.time())
                except Exception:
                    pass
            raise

    if _worker_pid() != worker_pid:
        raise SmokeError("worker_restarted_during_smoke")
    print("free_strategy_smoke=passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeError as exc:
        _fail(str(exc))
