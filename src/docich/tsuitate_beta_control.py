"""Fixed WebUI -> Worker capability. No browser/Bot/Webhook credential reuse."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

PREFIX = b"beta-control-v1\nPOST\n/beta-control\n"
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
STATES = {"stopped", "queued", "playing", "draining", "finished", "queue_timeout", "paused"}
ERRORS = {"token_not_configured", "run_locked", "run_mismatch", "control_not_configured",
          "control_authentication_failed", "control_timeout", "control_unavailable", "terminal_unconfirmed",
          "recovery_not_available", "recovery_checkpoint_invalid", "terminal_result_unavailable", "terminal_storage_failure"}

PLAYER_COLORS = {"sente", "gote"}
PLAYER_ROLES = {
    "pawn", "lance", "knight", "silver", "gold", "bishop", "rook", "king",
    "tokin", "promotedlance", "promotedknight", "promotedsilver", "horse", "dragon",
}
PLAYER_HAND_ROLES = {"pawn", "lance", "knight", "silver", "gold", "bishop", "rook"}
PLAYER_SQUARE = re.compile(r"[1-9][a-i]\Z")


class ControlError(Exception):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code, self.status = code, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ControlError("control_unavailable")


def _control_number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ControlError("control_unavailable")
    return value


def project_player_view(raw) -> dict | None:
    """Rebuild only the Bot-visible PlayerView fields from an authenticated response."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ControlError("control_unavailable")
    your_color = raw.get("yourColor")
    turn = raw.get("turn")
    status = raw.get("status")
    move_number = raw.get("moveNumber")
    if (not isinstance(your_color, str) or your_color not in PLAYER_COLORS
            or not isinstance(turn, str) or turn not in PLAYER_COLORS
            or not isinstance(status, str) or status not in {"playing", "ended"}
            or type(move_number) is not int or move_number < 1):
        raise ControlError("control_unavailable")

    pieces_raw = raw.get("yourPieces")
    if not isinstance(pieces_raw, list) or len(pieces_raw) > 40:
        raise ControlError("control_unavailable")
    pieces = []
    seen = set()
    for piece in pieces_raw:
        if not isinstance(piece, dict):
            raise ControlError("control_unavailable")
        square, role = piece.get("square"), piece.get("role")
        if (not isinstance(square, str) or not PLAYER_SQUARE.fullmatch(square)
                or not isinstance(role, str) or role not in PLAYER_ROLES or square in seen):
            raise ControlError("control_unavailable")
        seen.add(square)
        pieces.append({"square": square, "role": role})

    hand_raw = raw.get("yourHand")
    if (not isinstance(hand_raw, dict)
            or any(not isinstance(key, str) or key not in PLAYER_HAND_ROLES for key in hand_raw)):
        raise ControlError("control_unavailable")
    hand = {}
    total = len(pieces)
    for role, count in hand_raw.items():
        if type(count) is not int or not 0 <= count <= 40:
            raise ControlError("control_unavailable")
        if count:
            hand[role] = count
        total += count
    if total > 40:
        raise ControlError("control_unavailable")

    clocks_raw = raw.get("clocks")
    if not isinstance(clocks_raw, dict):
        raise ControlError("control_unavailable")
    running = clocks_raw.get("running")
    if running is not None and (not isinstance(running, str) or running not in PLAYER_COLORS):
        raise ControlError("control_unavailable")
    if ((status == "playing" and running != turn)
            or (status == "ended" and running is not None)):
        raise ControlError("control_unavailable")
    clocks = {
        "senteMs": _control_number(clocks_raw.get("senteMs")),
        "goteMs": _control_number(clocks_raw.get("goteMs")),
        "running": running,
        "serverTime": _control_number(clocks_raw.get("serverTime")),
    }

    fouls_raw = raw.get("fouls")
    if not isinstance(fouls_raw, dict):
        raise ControlError("control_unavailable")
    fouls = {}
    for key in ("you", "opponent"):
        value = fouls_raw.get(key)
        if type(value) is not int or not 0 <= value <= 10:
            raise ControlError("control_unavailable")
        fouls[key] = value

    you_in_check = raw.get("youInCheck")
    opponent_in_check = raw.get("opponentInCheck")
    if type(you_in_check) is not bool or type(opponent_in_check) is not bool:
        raise ControlError("control_unavailable")

    return {
        "yourColor": your_color,
        "yourPieces": pieces,
        "yourHand": hand,
        "turn": turn,
        "moveNumber": move_number,
        "clocks": clocks,
        "fouls": fouls,
        "youInCheck": you_in_check,
        "opponentInCheck": opponent_in_check,
        "status": status,
    }


def signed_request(url: str, secret: str, action: str, run_id: str | None = None,
                   now: int | None = None) -> urllib.request.Request:
    """Only an allowlisted operation, fixed path and method enter the MAC."""
    if not isinstance(action, str) or action not in {"status", "start", "stop", "reconcile"} or (action != "status" and
            (not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id))):
        raise ControlError("invalid_beta_operation", 400)
    payload = {"action": action}
    if action != "status":
        payload["runId"] = run_id
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    timestamp = str(int(time.time()) if now is None else now)
    signature = hmac.new(secret.encode("utf-8"), PREFIX + timestamp.encode("ascii") + b"." + raw, hashlib.sha256).hexdigest()
    return urllib.request.Request(url + "/beta-control", data=raw, method="POST", headers={
        "Content-Type": "application/json", "X-Beta-Control-Timestamp": timestamp,
        "X-Beta-Control-Signature": "sha256=" + signature,
        "User-Agent": "docich-beta-control/1.0",
    })


def call_beta_control(action: str, run_id: str | None = None, *, forbidden_secrets: tuple[str, ...] = ()) -> dict:
    secret = os.environ.get("DOCICH_BETA_CONTROL_SECRET", "")
    if not 32 <= len(secret.encode("utf-8")) <= 4096 or any(secret == value for value in forbidden_secrets if value):
        raise ControlError("control_not_configured")
    url = os.environ.get("DOCICH_BETA_CONTROL_URL", "")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ControlError("control_not_configured") from None
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} or port not in {None, 443}
            or not re.fullmatch(r"docich-tsuitate-bot\.[a-z0-9-]+\.workers\.dev", parsed.hostname or "")):
        raise ControlError("control_not_configured")
    request = signed_request(url.rstrip("/"), secret, action, run_id)
    try:
        # No redirects and no ambient proxy: never send this capability elsewhere.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=5) as response:
            raw = response.read(8193)
        if len(raw) > 8192:
            raise ControlError("control_unavailable")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("state") not in STATES:
            raise ControlError("control_unavailable")
        # Project fixed status fields only. Unexpected remote data never reaches
        # the browser, logs or a new command. Do not proxy raw exception bodies.
        result = {"state": data["state"]}
        for field in ("runId", "gameId", "brainVersion"):
            value = data.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:+/-]+", value)):
                raise ControlError("control_unavailable")
            result[field] = value
        for field in ("completedGames", "reservedGames"):
            value = data.get(field)
            if type(value) is not int or value not in {0, 1}:
                raise ControlError("control_unavailable")
            result[field] = value
        for field in ("stopRequested", "readyForNextRun"):
            if type(data.get(field)) is not bool:
                raise ControlError("control_unavailable")
            result[field] = data[field]
        result["playerView"] = project_player_view(data.get("playerView"))
        result.update(maxGames=1, queueWaitSeconds=60,
                      errorCode=data.get("errorCode") if data.get("errorCode") in ERRORS else None)
        return result
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(8193)
            error = json.loads(raw).get("error") if len(raw) <= 8192 else None
        except Exception:
            error = None
        finally:
            exc.close()
        raise ControlError(error if isinstance(error, str) and error in ERRORS else "control_unavailable",
                           409 if exc.code == 409 else 503) from None
    except ControlError:
        raise
    except Exception:
        raise ControlError("control_unavailable") from None
