#!/usr/bin/env python3
"""One-shot production E2E probe for the isolated free-strategy PAPER worker.

The probe deliberately has no Docker access. It registers one no-trade
candidate in the real production lab, waits for the already-running systemd
worker to execute it through gVisor, verifies durable state, then moves only
that exact operational probe to review_due so it consumes no research slot.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path("/home/ubuntu/docich")
SOREN_ROOT = Path("/home/ubuntu/soren")
SERVICE = "docich-free-strategy-worker.service"
NAME = "__docich_ops_smoke__"
FAMILY = "ops_smoke"
THESIS = "Bounded production E2E probe; never places PAPER targets."
SYMBOL = "BTC/JPY"
IMAGE_RE = re.compile(r"(?:[A-Za-z0-9._/:-]+@)?sha256:[0-9a-f]{64}\Z")
POLL_SECONDS = 5.0
DEADLINE_SECONDS = 420.0

ERROR_EXIT_CODES = {
    "worker_not_active": 11,
    "worker_identity_unavailable": 12,
    "worker_identity_mismatch": 13,
    "worker_runtime_invalid": 14,
    "worker_trading_dir_outside_root": 15,
    "cli_unavailable": 16,
    "cli_failed": 17,
    "cli_experiment_capacity": 18,
    "cli_artifact_active": 19,
    "cli_invalid_policy": 20,
    "cli_internal_failed": 21,
    "cli_output_invalid": 22,
    "lab_state_invalid": 23,
    "smoke_identity_missing": 24,
    "lab_unavailable": 25,
    "smoke_identity_mismatch": 26,
    "smoke_cleanup_refused": 27,
    "smoke_cleanup_failed": 28,
    "previous_smoke_still_active": 29,
    "registration_invalid": 30,
    "worker_restarted_during_smoke": 31,
    "worker_cycle_timeout": 32,
    "final_evaluation_invalid": 33,
    "smoke_finalize_invalid": 34,
    "unexpected_failure": 35,
}
CLI_ERROR_MAP = {
    "experiment_capacity": "cli_experiment_capacity",
    "artifact_experiment_active": "cli_artifact_active",
    "invalid_experiment_policy": "cli_invalid_policy",
    "free_strategy_cli_failed": "cli_internal_failed",
}


class SmokeError(RuntimeError):
    pass


def _fail(code: str) -> None:
    # The gateway withholds production stdout/stderr. A bounded numeric exit
    # code is the only signal intentionally allowed across that boundary.
    raise SystemExit(ERROR_EXIT_CODES.get(code, ERROR_EXIT_CODES["unexpected_failure"]))


def _parse_worker_cmdline(
    raw: bytes,
    *,
    root: Path = ROOT,
    soren_root: Path = SOREN_ROOT,
) -> tuple[Path, str]:
    try:
        argv = [item.decode("utf-8", "strict") for item in raw.split(b"\0") if item]
    except UnicodeError as exc:
        raise SmokeError("worker_runtime_invalid") from exc
    if "free-strategy-worker" not in argv:
        raise SmokeError("worker_identity_mismatch")

    def value(flag: str) -> str:
        if argv.count(flag) != 1:
            raise SmokeError("worker_runtime_invalid")
        index = argv.index(flag)
        if index + 1 >= len(argv) or not argv[index + 1]:
            raise SmokeError("worker_runtime_invalid")
        return argv[index + 1]

    trading_raw = value("--trading-dir")
    image = value("--image")
    if not IMAGE_RE.fullmatch(image):
        raise SmokeError("worker_runtime_invalid")
    trading = Path(trading_raw)
    if not trading.is_absolute():
        raise SmokeError("worker_runtime_invalid")

    # Production deploys Soren as a reviewed projection. The configured path
    # may therefore be the docich-facing path while resolve() lands under the
    # canonical Soren projection. Accept only those two exact trading roots;
    # do not broaden this to arbitrary paths under /home/ubuntu.
    docich_trading = root / "run-soren-live" / "trading"
    soren_trading = soren_root / "trading"
    if trading not in {docich_trading, soren_trading}:
        raise SmokeError("worker_trading_dir_outside_root")
    try:
        resolved_trading = trading.resolve(strict=True)
        resolved_allowed = {
            candidate.resolve(strict=True)
            for candidate in (docich_trading, soren_trading)
            if candidate.exists()
        }
    except OSError as exc:
        raise SmokeError("worker_runtime_invalid") from exc
    if not resolved_allowed or resolved_trading not in resolved_allowed:
        raise SmokeError("worker_trading_dir_outside_root")
    return resolved_trading, image


def _worker_runtime() -> tuple[int, Path, str]:
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
        raw_pid = subprocess.check_output(
            ["systemctl", "show", SERVICE, "--property=MainPID", "--value"],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()
        pid = int(raw_pid)
        if pid <= 1:
            raise ValueError
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except SmokeError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SmokeError("worker_identity_unavailable") from exc
    trading, image = _parse_worker_cmdline(cmdline)
    return pid, trading, image


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
    try:
        data = json.loads(proc.stdout)
    except (TypeError, ValueError) as exc:
        raise SmokeError("cli_output_invalid") from exc
    if not isinstance(data, dict):
        raise SmokeError("cli_output_invalid")
    if proc.returncode:
        error = data.get("error")
        raise SmokeError(CLI_ERROR_MAP.get(error, "cli_failed"))
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
    except SmokeError:
        raise
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
    except SmokeError:
        raise
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
    worker_pid, trading_dir, image = _worker_runtime()
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
                current_pid, current_trading, current_image = _worker_runtime()
                if (current_pid, current_trading, current_image) != (worker_pid, trading_dir, image):
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
                "evaluate", identity,
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

    current_pid, current_trading, current_image = _worker_runtime()
    if (current_pid, current_trading, current_image) != (worker_pid, trading_dir, image):
        raise SmokeError("worker_restarted_during_smoke")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeError as exc:
        _fail(str(exc))
    except Exception:
        _fail("unexpected_failure")
