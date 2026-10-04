"""Fixed WebUI -> Worker capability. No browser/Bot/Webhook credential reuse."""
from __future__ import annotations

import hashlib
import hmac
import json
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
          "control_authentication_failed", "control_timeout", "control_unavailable"}


class ControlError(Exception):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code, self.status = code, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ControlError("control_unavailable")


def signed_request(url: str, secret: str, action: str, run_id: str | None = None,
                   now: int | None = None) -> urllib.request.Request:
    """Only an allowlisted operation, fixed path and method enter the MAC."""
    if not isinstance(action, str) or action not in {"status", "start", "stop"} or (action != "status" and
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
