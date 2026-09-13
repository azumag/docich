"""Soren91 coordinator adapter (Mac remote-renderer edition).

Soren91 renders on a Mac host (CDP-driven renderer + SRT caller) while docich
runs on OCI.  This adapter puts that remote game on the same
``GameSwitchCoordinator`` path as the existing retro/paper corners:

* the *viewer* is a local ``ffplay`` SRT listener wrapped by
  ``presentation.py`` into the broadcast viewport (the same contained-ffplay
  path as the CLI adapter; no OBS window-capture is used);
* the *stream* is started/stopped through the Mac local agent
  (``GET /v1/status``, ``POST /v1/start {srtUrl}``, ``POST /v1/stop``);
* the optional *gameplay bot* (``soren91/main.mjs`` on the soviet_now side,
  driven over remote CDP) runs in the generation-owned agent window and is
  gated by ``[agent] enabled`` so Phase 2 display-switch tests can run with
  the bot off.

Fail-closed: missing env, an unreachable Mac agent, or no SRT listener all
fail ``preflight`` / ``materialize_runtime`` / ``readiness`` instead of
reporting a successful start.  The bearer token never appears in logs or
exception text.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .. import procs
from ..game_switch import (
    DeadlineExceededError,
    ReadinessTimeoutError,
    RuntimeSpec,
)
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter

DEFAULT_AGENT_BASE_URL_ENV = "SOREN91_MACOS_AGENT_BASE_URL"
DEFAULT_AGENT_TOKEN_ENV = "SOREN91_LOCAL_AGENT_TOKEN"
DEFAULT_OCI_TAILSCALE_IP_ENV = "SOREN91_OCI_TAILSCALE_IP"
DEFAULT_SRT_PORT = 19192
DEFAULT_CDP_PORT = 9322
DEFAULT_FFPLAY_BIN = "ffplay"

AGENT_HTTP_TIMEOUT_S = 5.0
LISTENER_POLL_INTERVAL_S = 0.5
STATUS_POLL_INTERVAL_S = 0.5
CLEANUP_PORT_GRACE_S = 5.0


def is_tailscale_ipv4(host: str) -> bool:
    """True only for a Tailscale IPv4 address (100.64.0.0/10)."""
    octets = (host or "").strip().split(".")
    if len(octets) != 4 or any(not part.isdigit() for part in octets):
        return False
    try:
        numbers = [int(part) for part in octets]
    except ValueError:
        return False
    if any(value < 0 or value > 255 for value in numbers):
        return False
    if len(octets[0]) > 3 or (len(octets[0]) > 1 and octets[0].startswith("0")):
        pass  # leading zeros are still parsed as decimal here; range check governs
    return numbers[0] == 100 and 64 <= numbers[1] <= 127


def soren91_raw(game) -> dict:
    raw = game.raw.get("soren91", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AdapterError("[soren91] はテーブルである必要があります")
    return raw


def _local_field_port(field: str) -> str:
    """Extract the port suffix from an ss Local Address:Port field."""
    text = (field or "").strip()
    if text.startswith("["):
        end = text.find("]:")
        if end == -1:
            return ""
        return text[end + 2 :].strip()
    if ":" not in text:
        return ""
    return text.rsplit(":", 1)[1].strip()


def parse_udp_listeners(ss_output: str, port: int) -> bool:
    """True when `ss -H -uln` style output shows a UDP socket on ``port``.

    Pure function over captured output so tests can feed samples without
    spawning processes. The SRT viewer listener is UDP-only, so the old
    TCP-connect probe could never observe it (always ECONNREFUSED).

    Two row shapes are accepted: with a Netid column
    (``udp UNCONN 0 0 <local> <peer>``) and without one, as printed by
    this fleet's ss build (``UNCONN 0 0 <local> <peer>``). TCP rows are
    ignored: a TCP socket on the same number is not our listener.
    """
    want = str(port)
    for line in (ss_output or "").splitlines():
        parts = line.split()
        if not parts:
            continue
        first = parts[0].lower()
        if first == "tcp":
            continue
        if first in ("udp", "udplite", "u_str"):
            local = parts[4] if len(parts) > 4 else ""
        elif first in ("unconn", "estab", "unknown", "state"):
            local = parts[3] if len(parts) > 3 else ""
        else:
            continue
        if _local_field_port(local) == want:
            return True
    return False


def _lsof_udp_bound(port: int) -> bool:
    """Fallback UDP check when `ss` is unavailable."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iUDP:{port}"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode not in (0, 1):
        return False
    pattern = re.compile(rf":{port}(?!\d)")
    lines = (result.stdout or "").splitlines()
    return any(pattern.search(line) for line in lines[1:])


def _udp_listener_bound(port: int) -> bool:
    """True when a local UDP socket is bound on ``port`` (ss, else lsof)."""
    try:
        result = subprocess.run(
            ["ss", "-H", "-uln"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        return _lsof_udp_bound(port)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return parse_udp_listeners(result.stdout or "", port)


def _validated_port(value, *, key: str, default: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterError(f"[soren91].{key} は1024-65535の整数である必要があります")
    if not 1024 <= value <= 65535:
        raise AdapterError(f"[soren91].{key} は1024-65535の整数である必要があります")
    return value


class Soren91CoordinatorAdapter(CliCoordinatorAdapter):
    """Runtime-aware adapter showing the Mac-rendered Soren91 stream."""

    name = "soren91"

    def __init__(self, g, game, spec: RuntimeSpec):
        super().__init__(g, game, spec)
        # Soren91 has no round boundary yet (Phase 3 owns the Soren本編
        # boundary linkage).  Hide the capability like CLI games with
        # require_round_boundary=false so the coordinator takes the
        # immediate-quiesce path.
        self.agent_enabled = bool(game.agent.enabled)
        self.requires_round_boundary = False
        self.request_round_boundary = None
        self.cancel_round_boundary = None

        raw = soren91_raw(game)
        self.agent_base_url_env = str(raw.get("agent_base_url_env", DEFAULT_AGENT_BASE_URL_ENV))
        self.agent_token_env = str(raw.get("agent_token_env", DEFAULT_AGENT_TOKEN_ENV))
        self.oci_tailscale_ip_env = str(
            raw.get("oci_tailscale_ip_env", DEFAULT_OCI_TAILSCALE_IP_ENV)
        )
        for key in ("agent_base_url_env", "agent_token_env", "oci_tailscale_ip_env"):
            if not getattr(self, key).strip() or "\x00" in getattr(self, key):
                raise AdapterError(f"[soren91].{key} は空でない環境変数名である必要があります")
        self.srt_port = _validated_port(raw.get("srt_port"), key="srt_port", default=DEFAULT_SRT_PORT)
        self.cdp_port = _validated_port(raw.get("cdp_port"), key="cdp_port", default=DEFAULT_CDP_PORT)
        self.ffplay_bin = str(raw.get("ffplay_bin", DEFAULT_FFPLAY_BIN))
        if not self.ffplay_bin.strip() or "\x00" in self.ffplay_bin:
            raise AdapterError("[soren91].ffplay_bin は空でない実行ファイル名である必要があります")
        bot_path = raw.get("bot_path", "")
        if bot_path is None:
            bot_path = ""
        if not isinstance(bot_path, str) or "\x00" in bot_path:
            raise AdapterError("[soren91].bot_path は文字列である必要があります")
        self.bot_path = bot_path.strip()

    # --- resolved runtime values (env is read per call, never cached) -------

    def _agent_base(self) -> tuple[str, str]:
        """Return (base_url, host) for the Mac local agent, validated."""
        value = (os.environ.get(self.agent_base_url_env) or "").strip()
        if not value:
            raise AdapterError("Mac agent の base URL が設定されていません")
        try:
            parsed = urllib.parse.urlparse(value)
        except ValueError as exc:
            raise AdapterError("Mac agent の base URL が不正です") from exc
        if parsed.scheme not in ("http", "https"):
            raise AdapterError("Mac agent の base URL はhttp(s)である必要があります")
        if parsed.username or parsed.password:
            raise AdapterError("Mac agent の base URL にuserinfoは使えません")
        host = (parsed.hostname or "").strip()
        if not is_tailscale_ipv4(host):
            raise AdapterError("Mac agent の host はTailscale IPv4である必要があります")
        if not parsed.port:
            raise AdapterError("Mac agent の base URL にはportが必要です")
        return value.rstrip("/"), host

    def _agent_token(self) -> str:
        token = os.environ.get(self.agent_token_env) or ""
        if not token:
            raise AdapterError("Mac agent の token が設定されていません")
        return token

    def _oci_ip(self) -> str:
        value = (os.environ.get(self.oci_tailscale_ip_env) or "").strip()
        if not value:
            raise AdapterError("OCI Tailscale IP が設定されていません")
        if not is_tailscale_ipv4(value):
            raise AdapterError("OCI Tailscale IP はTailscale IPv4である必要があります")
        return value

    def caller_srt_url(self) -> str:
        """SRT URL handed to the Mac agent (caller dials our listener)."""
        return f"srt://{self._oci_ip()}:{self.srt_port}?mode=caller"

    def listener_srt_url(self) -> str:
        """SRT URL our ffplay viewer listens on (same port, listener mode)."""
        return f"srt://{self._oci_ip()}:{self.srt_port}?mode=listener"

    def remote_cdp_url(self) -> str:
        """CDP URL the OCI bot uses to drive the Mac renderer."""
        _, host = self._agent_base()
        return f"http://{host}:{self.cdp_port}"

    # --- viewer / session commands -------------------------------------------

    def _viewer_command_inner(self) -> list[str]:
        return [
            self.ffplay_bin,
            "-loglevel", "warning",
            "-nostats",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-an",
            "-i", self.listener_srt_url(),
        ]

    def _xterm_command(self) -> list[str]:
        inner = self._viewer_command_inner()
        d = self.g.display
        if d.viewport_width > 0 and d.viewport_height > 0:
            return [
                sys.executable, str(Path(__file__).resolve().parents[1] / "presentation.py"),
                "--display", d.name, "--title", f"docich-present-{self.spec.runtime_id}",
                "--x", str(d.viewport_x), "--y", str(d.viewport_y),
                "--width", str(d.viewport_width), "--height", str(d.viewport_height),
                "--", *inner,
            ]
        return inner

    def _game_command(self) -> list[str]:
        # The game itself runs on the Mac renderer; the owned session birth
        # window only needs a long-lived placeholder so the session exists
        # for ownership-tagged viewer/agent windows.
        return ["tail", "-f", "/dev/null"]

    def _node_bin(self) -> str:
        resolved = procs.which("node")
        if not resolved:
            raise AdapterError("node が見つかりません (soren91 bot)")
        return resolved

    def _agent_command(self) -> list[str]:
        if not self.bot_path:
            raise AdapterError("[soren91].bot_path が設定されていません (agent enabled)")
        return [self._node_bin(), self.bot_path]

    def _agent_window_env(self) -> dict:
        return {"SOREN91_REMOTE_CDP_URL": self.remote_cdp_url()}

    # --- Mac local-agent HTTP -------------------------------------------------

    def _agent_request(
        self, method: str, path: str, deadline: float, cancel, *, body: dict | None = None
    ) -> tuple[int, dict]:
        base, _host = self._agent_base()
        token = self._agent_token()
        data = None
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{base}{path}", data=data, headers=headers, method=method
        )
        self._check_active(deadline, cancel)
        timeout = max(0.1, min(AGENT_HTTP_TIMEOUT_S, deadline - time.monotonic()))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
                return response.status, payload if isinstance(payload, dict) else {}
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8") or "{}")
            except (ValueError, OSError):
                payload = {}
            return exc.code, payload if isinstance(payload, dict) else {}
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            raise AdapterError(f"Mac agent に到達できません ({type(exc).__name__})") from exc

    def _agent_running(self, deadline: float, cancel) -> bool:
        _status, payload = self._agent_request("GET", "/v1/status", deadline, cancel)
        return payload.get("running") is True

    def _listener_bound(self) -> bool:
        try:
            self._oci_ip()
        except AdapterError:
            return False
        return _udp_listener_bound(self.srt_port)

    def _wait_listener(self, deadline: float, cancel) -> None:
        while True:
            if self._listener_bound():
                return
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("adapter call はcancelされました")
            if time.monotonic() >= deadline:
                raise ReadinessTimeoutError("SRT listener がbindされませんでした")
            time.sleep(min(LISTENER_POLL_INTERVAL_S, max(0.0, deadline - time.monotonic())))

    # --- CoordinatorAdapter contract ------------------------------------------

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        # Fail closed before any side effect: env/ports shape first.
        self._agent_base()
        if not self._agent_token():
            raise AdapterError("Mac agent の token が設定されていません")
        self._oci_ip()
        self._game_command()
        if not (procs.which(self.ffplay_bin) or shutil.which(self.ffplay_bin)):
            raise AdapterError(f"{self.ffplay_bin} が見つかりません")
        self._check_ffplay_srt()
        if not Path(__file__).resolve().parents[1].joinpath("presentation.py").is_file():
            raise AdapterError("presentation.py が見つかりません")
        if self.g.display.viewport_width > 0:
            for binary in ("Xvfb", "xdotool"):
                if not procs.which(binary):
                    raise AdapterError(f"{binary} が見つかりません")
        if self.agent_enabled:
            self._node_bin()
            if not self.bot_path:
                raise AdapterError("[soren91].bot_path が設定されていません (agent enabled)")
            if not Path(self.bot_path).is_file():
                raise AdapterError("soren91 bot が見つかりません")
            # The bot dials the Mac renderer over CDP; resolve now so a bad
            # base URL fails here, not mid-corner.
            self.remote_cdp_url()
        self._check_active(deadline, cancel)

    def _check_ffplay_srt(self) -> None:
        try:
            result = subprocess.run(
                [self.ffplay_bin, "-protocols"],
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdapterError(f"{self.ffplay_bin} のprotocol確認に失敗しました") from exc
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        if result.returncode != 0 or "srt" not in output.lower():
            raise AdapterError(f"{self.ffplay_bin} にSRT対応がありません")

    def materialize_runtime(self, deadline: float, cancel) -> None:
        # Viewer first (generation-owned tmux session + game window through
        # presentation.py), then tell the Mac renderer to dial in.  The POST
        # only happens after the listener port accepts, so the first caller
        # packets are never lost to a missing listener.
        super().materialize_runtime(deadline, cancel)
        self._check_active(deadline, cancel)
        self._wait_listener(deadline, cancel)
        self._check_active(deadline, cancel)
        try:
            if self._agent_running(deadline, cancel):
                return
        except AdapterError:
            raise
        _status, payload = self._agent_request(
            "POST", "/v1/start", deadline, cancel, body={"srtUrl": self.caller_srt_url()}
        )
        if _status == 409:
            return
        if _status not in (200, 202) or payload.get("ok") is not True:
            raise AdapterError("Mac agent の renderer 起動に失敗しました")

    def readiness(self, deadline: float, cancel) -> None:
        # The contained presenter window must exist (super), the Mac renderer
        # must report running, and our listener port must accept — all three
        # before the coordinator may commit.  Anything else fails closed.
        super().readiness(deadline, cancel)
        while True:
            try:
                running = self._agent_running(deadline, cancel)
            except DeadlineExceededError:
                # A spent deadline while polling is a readiness timeout, not
                # a coordinator abort — unless cancel was explicitly set.
                if cancel is not None and cancel.is_set():
                    raise
                running = False
            bound = self._listener_bound()
            if running and bound:
                return
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("adapter call はcancelされました")
            if time.monotonic() >= deadline:
                if not running:
                    raise ReadinessTimeoutError("Mac renderer がrunningになりませんでした")
                raise ReadinessTimeoutError("SRT listener がbindされませんでした")
            time.sleep(min(STATUS_POLL_INTERVAL_S, max(0.0, deadline - time.monotonic())))

    def alive(self, deadline: float, cancel) -> bool:
        self._check_active(deadline, cancel)
        if not super().alive(deadline, cancel):
            return False
        try:
            return self._agent_running(deadline, cancel)
        except AdapterError:
            return False

    def cleanup_runtime(self, deadline: float, cancel) -> None:
        # Stop the remote renderer first (best-effort: it may already be
        # gone), then tear down our own windows/session, then prove the SRT
        # port is free for the next generation.
        try:
            self._agent_request("POST", "/v1/stop", deadline, cancel)
        except AdapterError:
            self._check_active(deadline, cancel)
        super().cleanup_runtime(deadline, cancel)
        self._check_active(deadline, cancel)
        grace_until = time.monotonic() + min(
            CLEANUP_PORT_GRACE_S, max(0.0, deadline - time.monotonic())
        )
        while self._listener_bound():
            if time.monotonic() >= grace_until or time.monotonic() >= deadline:
                raise AdapterError("SRT port が解放されませんでした")
            time.sleep(min(LISTENER_POLL_INTERVAL_S, max(0.0, deadline - time.monotonic())))

    def start_agent(self, deadline: float, cancel) -> None:
        if not self.agent_enabled:
            return
        self._check_active(deadline, cancel)
        target = self._agent_window_target()
        if self.tmux.window_target_exists(target):
            self._verify_window_ownership(target, "agent")
            return
        self._check_active(deadline, cancel)
        self.tmux.create_window_owned(
            self.spec.agent_window,
            self._agent_command(),
            self._ownership("agent"),
            env=self._agent_window_env(),
        )
        self._check_active(deadline, cancel)

    def stop_agent(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        target = self._agent_window_target()
        if self.tmux.window_target_exists(target):
            self._check_active(deadline, cancel)
            self.tmux.kill_window_owned(target, self._ownership("agent"))
