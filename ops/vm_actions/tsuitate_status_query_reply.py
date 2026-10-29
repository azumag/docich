#!/usr/bin/env python3
"""Build the fixed public Issue reply for one Tsuitate run status query."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

RUN_ID_RE = re.compile(r"hourly-[0-9]{10}\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
TEXT_RE = re.compile(r"[A-Za-z0-9_.:+/-]{1,128}\Z")
STATES = {"stopped", "queued", "playing", "draining", "finished", "queue_timeout", "paused"}
OUTCOMES = {"win", "loss", "draw", "unknown"}
REASONS = {
    "normal", "checkmate", "stalemate", "resign", "timeout", "foul_limit",
    "repetition", "draw", "aborted", "disconnect", "transport_error",
    "protocol_error", "storage_error", "interrupted", "no_move", "unknown",
}
CONFIDENCE = {"verified", "unknown"}
TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z\Z")
STATUS_KEYS = {
    "schema", "status", "runId", "state", "gameId", "completedGames",
    "reservedGames", "readyForNextRun", "brainVersion", "errorCode", "gameResult",
}
RESULT_KEYS = {"outcome", "reason", "endedAt", "moveNumber", "resultConfidence"}


def _text(value, *, optional: bool = False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not TEXT_RE.fullmatch(value):
        raise ValueError("invalid_status")
    return value


def load_status(path: Path, expected_run_id: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != STATUS_KEYS:
        raise ValueError("invalid_status")
    if data.get("schema") != 1 or data.get("status") != "ok":
        raise ValueError("invalid_status")
    if data.get("runId") != expected_run_id or not RUN_ID_RE.fullmatch(expected_run_id):
        raise ValueError("invalid_status")
    if data.get("state") not in STATES:
        raise ValueError("invalid_status")
    for key in ("completedGames", "reservedGames"):
        if type(data.get(key)) is not int or data[key] not in {0, 1}:
            raise ValueError("invalid_status")
    if type(data.get("readyForNextRun")) is not bool:
        raise ValueError("invalid_status")
    game_id = _text(data.get("gameId"), optional=True)
    brain = _text(data.get("brainVersion"), optional=True)
    error = _text(data.get("errorCode"), optional=True)
    result = data.get("gameResult")
    if result is not None:
        if not isinstance(result, dict) or set(result) != RESULT_KEYS:
            raise ValueError("invalid_status")
        if result.get("outcome") not in OUTCOMES or result.get("reason") not in REASONS:
            raise ValueError("invalid_status")
        if not isinstance(result.get("endedAt"), str) or not TIME_RE.fullmatch(result["endedAt"]):
            raise ValueError("invalid_status")
        if type(result.get("moveNumber")) is not int or not 0 <= result["moveNumber"] <= 100000:
            raise ValueError("invalid_status")
        if result.get("resultConfidence") not in CONFIDENCE:
            raise ValueError("invalid_status")
    if data["state"] != "finished" or data["completedGames"] != 1:
        if game_id is not None or result is not None:
            raise ValueError("preterminal_identity_exposed")
    return {**data, "gameId": game_id, "brainVersion": brain, "errorCode": error}


def build_reply(env: dict[str, str]) -> dict[str, str]:
    comment_id = env.get("SOURCE_COMMENT_ID", "")
    actions_run = env.get("ACTIONS_RUN_ID", "")
    attempt = env.get("RUN_ATTEMPT", "")
    sha = env.get("RUN_SHA", "")
    run_id = env.get("TSUITATE_RUN_ID", "")
    outcome = env.get("QUERY_OUTCOME", "")
    if not (comment_id.isdigit() and actions_run.isdigit() and attempt.isdigit()
            and SHA_RE.fullmatch(sha) and RUN_ID_RE.fullmatch(run_id)
            and outcome in {"success", "failure", "cancelled", "skipped"}):
        raise ValueError("invalid_reply_metadata")
    lines = [
        f"Request: https://github.com/azumag/docich/issues/2938#issuecomment-{comment_id}",
        f"Run: https://github.com/azumag/docich/actions/runs/{actions_run}",
        f"Attempt: {attempt}",
        f"SHA: {sha}",
        f"Tsuitate-Run: {run_id}",
    ]
    if outcome != "success":
        lines.extend(("Result: failure", "Reason: query_unavailable"))
        return {"body": "\n".join(lines)}
    status_path = Path(env.get("STATUS_FILE", ""))
    status = load_status(status_path, run_id)
    lines.extend((
        "Result: success",
        "Reason: status_queried",
        f"State: {status['state']}",
        f"Completed-Games: {status['completedGames']}",
        f"Reserved-Games: {status['reservedGames']}",
        f"Ready-For-Next-Run: {'true' if status['readyForNextRun'] else 'false'}",
        f"Game-ID: {status['gameId'] or '-'}",
        f"Brain-Version: {status['brainVersion'] or '-'}",
        f"Error-Code: {status['errorCode'] or '-'}",
    ))
    result = status["gameResult"]
    if result is not None:
        lines.extend((
            f"Outcome: {result['outcome']}",
            f"End-Reason: {result['reason']}",
            f"Ended-At: {result['endedAt']}",
            f"Move-Number: {result['moveNumber']}",
            f"Result-Confidence: {result['resultConfidence']}",
        ))
    return {"body": "\n".join(lines)}


def main() -> int:
    try:
        json.dump(build_reply(dict(os.environ)), sys.stdout, separators=(",", ":"))
    except Exception:
        print("Tsuitate status reply rejected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
