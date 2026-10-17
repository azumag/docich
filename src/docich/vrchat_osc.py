"""VRChat OSC bridge — docich と VR同志アバターの双方向連携 (Issue #316).

This module owns the *only* place where docich talks OSC to VRChat.  It is a
standalone, stdlib-only process so it can also run on the Windows PC that runs
the VRChat client (``python -m docich.vrchat_osc``).

Design rules (see ``docs/vrchat-osc.md``):

* **docich -> VRChat** is driven by an explicit allowlist of avatar parameters
  (``[parameters]``).  Values come from the real speaking state
  (``speaking.json`` written by ``say_enqueue.sh``) and from a JSONL event
  spool that other docich components append to (``docich vr-osc emit``).
* **VRChat -> docich** only accepts parameters explicitly listed in
  ``[inbound]`` and maps them to a small set of named actions.  OSC input is
  never turned into shell commands or arbitrary code.
* **No feedback loops**: values are sent only on change, inbound values that
  echo something we just sent are dropped for a short window, and inbound
  transitions are edge/debounce gated.  An accepted inbound value is recorded as
  the avatar's current state even when it fires no action (a bool falling edge
  included), so the next outbound change is still sent.
* **Fail-soft**: a missing VRChat client, a missing speaking file, a corrupt
  spool line, or a closed UDP port must never stop docich or the bridge.

The bridge exposes a deterministic ``tick(now=...)`` used by the tests, so no
wall-clock sleeps are needed to verify debounce, expiry, and loop prevention.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

PARAM_KINDS = ("bool", "int", "float")
ACTIONS = ("notify", "mode")
MAX_PACKET_BYTES = 65535
MAX_EVENT_LINE = 8192
MAX_JSONL_BYTES = 4 * 1024 * 1024
JSONL_KEEP_LINES = 500
STATUS_INTERVAL = 1.0
DEFAULT_CONFIG_RELATIVE = Path("config") / "vrchat_osc.toml"
PARAMETER_PREFIX = "/avatar/parameters/"
_BUNDLE_HEADER = b"#bundle\x00"
_DEFAULT_KIND_VALUES: dict[str, Any] = {"bool": False, "int": 0, "float": 0.0}


class VrOscError(ValueError):
    """User-facing error for invalid configuration, parameters, or OSC data."""


class VrOscConfigError(VrOscError):
    """Invalid ``config/vrchat_osc.toml``."""


# ---------------------------------------------------------------------------
# OSC 1.0 codec (stdlib only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OscMessage:
    address: str
    args: tuple[Any, ...] = ()


def _pad4(data: bytes) -> bytes:
    remainder = len(data) % 4
    return data if remainder == 0 else data + b"\x00" * (4 - remainder)


def _encode_osc_string(value: str) -> bytes:
    if not isinstance(value, str):
        raise VrOscError("OSC string must be str")
    raw = value.encode("utf-8")
    if b"\x00" in raw:
        raise VrOscError("OSC string must not contain NUL")
    return _pad4(raw + b"\x00")


def _validate_address(address: str) -> None:
    if not isinstance(address, str) or not address.startswith("/"):
        raise VrOscError(f"invalid OSC address: {address!r}")
    if any(ch.isspace() for ch in address):
        raise VrOscError(f"OSC address must not contain whitespace: {address!r}")
    if len(address) > 255:
        raise VrOscError("OSC address is too long")


def encode_message(address: str, args: Sequence[Any] = ()) -> bytes:
    """Encode one OSC message.

    Bool is encoded as int32 0/1 because that is the representation VRChat
    documents for Bool avatar parameters.  NaN/Inf floats are rejected so a
    broken upstream value can never produce a nonsense parameter.
    """
    _validate_address(address)
    tags = ","
    payload = b""
    for arg in args:
        if isinstance(arg, bool):
            tags += "i"
            payload += struct.pack(">i", 1 if arg else 0)
        elif isinstance(arg, int):
            if not -(2**31) <= arg < 2**31:
                raise VrOscError(f"OSC int out of range: {arg}")
            tags += "i"
            payload += struct.pack(">i", arg)
        elif isinstance(arg, float):
            if arg != arg or arg in (float("inf"), float("-inf")):
                raise VrOscError(f"OSC float must be finite: {arg!r}")
            tags += "f"
            payload += struct.pack(">f", arg)
        elif isinstance(arg, str):
            tags += "s"
            payload += _encode_osc_string(arg)
        elif isinstance(arg, (bytes, bytearray)):
            blob = bytes(arg)
            tags += "b"
            payload += struct.pack(">i", len(blob)) + _pad4(blob)
        else:  # pragma: no cover - defensive
            raise VrOscError(f"unsupported OSC argument type: {type(arg).__name__}")
    packet = _encode_osc_string(address) + _encode_osc_string(tags) + payload
    if len(packet) > MAX_PACKET_BYTES:
        raise VrOscError("OSC packet is too large")
    return packet


def _read_osc_string(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end < 0:
        raise VrOscError("unterminated OSC string")
    raw = data[offset:end]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VrOscError("OSC string is not UTF-8") from exc
    next_offset = offset + ((end - offset + 4) & ~3)
    # Tolerate a final string without its zero padding (seen in the wild);
    # anything before the end of the packet remains strict.
    if next_offset > len(data):
        next_offset = len(data)
    return text, next_offset


def decode_message(data: bytes) -> OscMessage:
    if not isinstance(data, (bytes, bytearray)):
        raise VrOscError("OSC packet must be bytes")
    view = bytes(data)
    if not view:
        raise VrOscError("empty OSC packet")
    if len(view) > MAX_PACKET_BYTES:
        raise VrOscError("OSC packet is too large")
    address, offset = _read_osc_string(view, 0)
    _validate_address(address)
    tags, offset = _read_osc_string(view, offset)
    if not tags.startswith(","):
        raise VrOscError("OSC type tag string must start with ','")
    tags = tags[1:]
    args: list[Any] = []
    for tag in tags:
        if tag == "i":
            if offset + 4 > len(view):
                raise VrOscError("truncated OSC int32")
            (value,) = struct.unpack_from(">i", view, offset)
            offset += 4
            args.append(value)
        elif tag == "f":
            if offset + 4 > len(view):
                raise VrOscError("truncated OSC float32")
            (value,) = struct.unpack_from(">f", view, offset)
            offset += 4
            args.append(value)
        elif tag == "s" or tag == "S":
            value, offset = _read_osc_string(view, offset)
            args.append(value)
        elif tag == "b":
            if offset + 4 > len(view):
                raise VrOscError("truncated OSC blob size")
            (size,) = struct.unpack_from(">i", view, offset)
            offset += 4
            if size < 0 or offset + size > len(view):
                raise VrOscError("invalid OSC blob size")
            args.append(view[offset : offset + size])
            offset += (size + 3) & ~3
        elif tag == "T":
            args.append(True)
        elif tag == "F":
            args.append(False)
        elif tag == "N":
            args.append(None)
        elif tag == "h":
            if offset + 8 > len(view):
                raise VrOscError("truncated OSC int64")
            (value,) = struct.unpack_from(">q", view, offset)
            offset += 8
            args.append(value)
        elif tag == "d":
            if offset + 8 > len(view):
                raise VrOscError("truncated OSC float64")
            (value,) = struct.unpack_from(">d", view, offset)
            offset += 8
            args.append(value)
        elif tag == "c":
            if offset + 4 > len(view):
                raise VrOscError("truncated OSC char")
            args.append(chr(struct.unpack_from(">i", view, offset)[0]))
            offset += 4
        elif tag in ("r", "m"):
            if offset + 4 > len(view):
                raise VrOscError("truncated OSC 4-byte value")
            args.append(view[offset : offset + 4])
            offset += 4
        elif tag == "t":
            if offset + 8 > len(view):
                raise VrOscError("truncated OSC timetag")
            args.append(struct.unpack_from(">Q", view, offset)[0])
            offset += 8
        else:
            raise VrOscError(f"unsupported OSC type tag: {tag!r}")
    return OscMessage(address=address, args=tuple(args))


def decode_packet(data: bytes, *, _depth: int = 0) -> tuple[OscMessage, ...]:
    """Decode a message or an OSC bundle (flattened, depth-limited)."""
    view = bytes(data)
    if view.startswith(_BUNDLE_HEADER):
        if _depth >= 4:
            raise VrOscError("OSC bundle nesting is too deep")
        offset = len(_BUNDLE_HEADER) + 8  # timetag
        messages: list[OscMessage] = []
        while offset < len(view):
            if offset + 4 > len(view):
                raise VrOscError("truncated OSC bundle element size")
            (size,) = struct.unpack_from(">i", view, offset)
            offset += 4
            if size < 0 or offset + size > len(view):
                raise VrOscError("invalid OSC bundle element size")
            messages.extend(decode_packet(view[offset : offset + size], _depth=_depth + 1))
            offset += size
        return tuple(messages)
    return (decode_message(view),)


# ---------------------------------------------------------------------------
# parameter values
# ---------------------------------------------------------------------------


def default_value(kind: str) -> Any:
    try:
        return _DEFAULT_KIND_VALUES[kind]
    except KeyError as exc:  # pragma: no cover - validated earlier
        raise VrOscError(f"unknown parameter type: {kind!r}") from exc


def coerce_value(name: str, kind: str, value: Any) -> Any:
    """Coerce an OSC/CLI value to the declared parameter type.

    Bool accepts 0/1 (VRChat sends Bool parameters as int) and booleans.
    Int accepts integral floats/bools.  Everything else fails closed.
    """
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, float) and value in (0.0, 1.0):
            return bool(int(value))
        raise VrOscError(f"{name}: bool は 0/1 のみ受け付けます ({value!r})")
    if kind == "int":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise VrOscError(f"{name}: int が必要です ({value!r})")
    if kind == "float":
        if isinstance(value, bool):
            raise VrOscError(f"{name}: bool は float に変換できません")
        if isinstance(value, (int, float)):
            number = float(value)
            if number != number or number in (float("inf"), float("-inf")):
                raise VrOscError(f"{name}: 有限の数値が必要です")
            return number
        raise VrOscError(f"{name}: float が必要です ({value!r})")
    raise VrOscError(f"{name}: 未知の型 {kind!r}")


def parse_assignment(text: str) -> tuple[str, Any]:
    """Parse ``NAME=VALUE`` from the CLI (no eval, strict scalar literals)."""
    if "=" not in text:
        raise VrOscError(f"NAME=VALUE の形式が必要です: {text!r}")
    name, raw = text.split("=", 1)
    name = name.strip()
    raw = raw.strip()
    if not name or not raw:
        raise VrOscError(f"NAME=VALUE の形式が必要です: {text!r}")
    if any(ch.isspace() for ch in name):
        raise VrOscError(f"パラメータ名に空白は使えません: {name!r}")
    lowered = raw.lower()
    if lowered in ("true", "on"):
        return name, True
    if lowered in ("false", "off"):
        return name, False
    try:
        return name, int(raw, 10)
    except ValueError:
        pass
    try:
        return name, float(raw)
    except ValueError as exc:
        raise VrOscError(f"{name}: 数値または true/false が必要です: {raw!r}") from exc


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        try:
            return bool(left) is bool(right)
        except Exception:  # pragma: no cover - defensive
            return False
    try:
        return left == right
    except Exception:  # pragma: no cover - defensive
        return False


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str
    hold_ms: int = 0
    persist: bool = False

    @property
    def hold_seconds(self) -> float:
        return max(0.0, self.hold_ms / 1000.0)


@dataclass(frozen=True)
class InboundRule:
    parameter: str
    action: str
    values: tuple[str, ...] = ()
    min_interval_ms: int = 500
    title: str = "VR同志"
    body: str = "VRChat アバターから操作を受信しました"

    @property
    def min_interval(self) -> float:
        return max(0.0, self.min_interval_ms / 1000.0)


@dataclass(frozen=True)
class VrOscSettings:
    vrchat_host: str
    vrchat_port: int
    listen_host: str
    listen_port: int
    poll_interval: float
    send_min_interval: float
    echo_suppress: float
    max_sends_per_second: int
    params: Mapping[str, ParamSpec]
    inbound: Mapping[str, InboundRule]
    speaking_parameter: str
    speaking_path: Path | None
    speaking_grace: float
    event_spool: Path
    action_spool: Path
    status_path: Path
    state_path: Path
    config_path: Path | None
    soren_root: Path | None

    def param(self, name: str) -> ParamSpec:
        try:
            return self.params[name]
        except KeyError as exc:
            raise VrOscError(f"未定義のパラメータです: {name}") from exc


_DEFAULT_PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec("AI_Talking", "bool"),
    ParamSpec("AI_Emotion", "int", hold_ms=8000),
    ParamSpec("Alert", "bool", hold_ms=3000),
    ParamSpec("Mode", "int", persist=True),
)


def _default_params() -> dict[str, ParamSpec]:
    return {spec.name: spec for spec in _DEFAULT_PARAMS}


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    raw = data.get(name, {})
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise VrOscConfigError(f"[{name}] はテーブルである必要があります")
    return raw


def _int_field(
    section: Mapping[str, Any], key: str, default: int, *, low: int, high: int, table: str
) -> int:
    raw = section.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise VrOscConfigError(f"[{table}] {key} は整数である必要があります")
    if not low <= raw <= high:
        raise VrOscConfigError(f"[{table}] {key} は {low}..{high} の範囲で指定してください")
    return raw


def _number_field(section: Mapping[str, Any], key: str, default: float, *, table: str) -> float:
    raw = section.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise VrOscConfigError(f"[{table}] {key} は数値である必要があります")
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        raise VrOscConfigError(f"[{table}] {key} は有限の数値である必要があります")
    return value


def _string_field(section: Mapping[str, Any], key: str, default: str, *, table: str) -> str:
    raw = section.get(key, default)
    if not isinstance(raw, str) or not raw.strip():
        raise VrOscConfigError(f"[{table}] {key} は空でない文字列である必要があります")
    return raw.strip()


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (Path(base) / candidate)


def _load_toml(path: Path) -> Mapping[str, Any]:
    import tomllib

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise VrOscConfigError(f"設定を読み込めません: {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise VrOscConfigError(f"設定のTOMLが不正です: {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise VrOscConfigError(f"設定のルートはテーブルである必要があります: {path}")
    return data


def _parse_params(data: Mapping[str, Any]) -> dict[str, ParamSpec]:
    raw = data.get("parameters")
    if raw is None:
        return _default_params()
    if not isinstance(raw, Mapping):
        raise VrOscConfigError("[parameters] はテーブルである必要があります")
    if not raw:
        raise VrOscConfigError("[parameters] には1件以上のパラメータが必要です")
    specs: dict[str, ParamSpec] = {}
    for name, table in raw.items():
        if not isinstance(name, str) or not name:
            raise VrOscConfigError("パラメータ名は空でない文字列である必要があります")
        if isinstance(table, str):  # 旧形式互換: AI_Talking = "bool"
            table = {"type": table}
        if not isinstance(table, Mapping):
            raise VrOscConfigError(f"[parameters.{name}] はテーブルである必要があります")
        kind = table.get("type", "bool")
        if kind not in PARAM_KINDS:
            raise VrOscConfigError(
                f"[parameters.{name}] type は {sorted(PARAM_KINDS)} のいずれかです"
            )
        hold_ms = table.get("hold_ms", 0)
        if isinstance(hold_ms, bool) or not isinstance(hold_ms, int) or hold_ms < 0:
            raise VrOscConfigError(f"[parameters.{name}] hold_ms は0以上の整数です")
        persist = table.get("persist", False)
        if not isinstance(persist, bool):
            raise VrOscConfigError(f"[parameters.{name}] persist は真偽値です")
        specs[name] = ParamSpec(name=name, kind=kind, hold_ms=hold_ms, persist=persist)
    return specs


def _parse_inbound(data: Mapping[str, Any], params: Mapping[str, ParamSpec]) -> dict[str, InboundRule]:
    raw = data.get("inbound", {})
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise VrOscConfigError("[inbound] はテーブルである必要があります")
    rules: dict[str, InboundRule] = {}
    for name, table in raw.items():
        if not isinstance(name, str) or name not in params:
            raise VrOscConfigError(
                f"[inbound.{name}] は [parameters] に定義されたパラメータである必要があります"
            )
        if not isinstance(table, Mapping):
            raise VrOscConfigError(f"[inbound.{name}] はテーブルである必要があります")
        action = table.get("action")
        if action not in ACTIONS:
            raise VrOscConfigError(
                f"[inbound.{name}] action は {sorted(ACTIONS)} のいずれかです"
            )
        values_raw = table.get("values", [])
        if not isinstance(values_raw, (list, tuple)) or any(
            not isinstance(item, str) for item in values_raw
        ):
            raise VrOscConfigError(f"[inbound.{name}] values は文字列の配列です")
        min_interval_ms = table.get("min_interval_ms", 500)
        if isinstance(min_interval_ms, bool) or not isinstance(min_interval_ms, int) or min_interval_ms < 0:
            raise VrOscConfigError(f"[inbound.{name}] min_interval_ms は0以上の整数です")
        title = table.get("title", "VR同志")
        body = table.get("body", "VRChat アバターから操作を受信しました")
        for key, value, limit in (("title", title, 120), ("body", body, 500)):
            if not isinstance(value, str) or len(value) > limit:
                raise VrOscConfigError(f"[inbound.{name}] {key} は{limit}文字以内の文字列です")
            if "\n" in value or "\r" in value:
                raise VrOscConfigError(f"[inbound.{name}] {key} に改行は使えません")
        if action == "mode" and params[name].kind != "int":
            raise VrOscConfigError(f"[inbound.{name}] mode アクションは int パラメータだけに使えます")
        rules[name] = InboundRule(
            parameter=name,
            action=str(action),
            values=tuple(values_raw),
            min_interval_ms=min_interval_ms,
            title=str(title),
            body=str(body),
        )
    return rules


def load_settings(
    *,
    repo_root: Path,
    state_dir: Path,
    config_path: Path | None = None,
    soren_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> VrOscSettings:
    """Load ``config/vrchat_osc.toml`` with safe defaults.

    A missing default config file is not an error (the defaults describe the
    localhost VRChat setup).  An explicitly requested file must exist.
    """
    repo_root = Path(repo_root)
    state_dir = Path(state_dir)
    environment = os.environ if env is None else env
    explicit = config_path is not None
    path = Path(config_path) if config_path is not None else repo_root / DEFAULT_CONFIG_RELATIVE
    data: Mapping[str, Any] = {}
    if path.exists():
        data = _load_toml(path)
    elif explicit:
        raise VrOscConfigError(f"設定ファイルがありません: {path}")

    osc = _section(data, "osc")
    speaking = _section(data, "speaking")
    paths = _section(data, "paths")

    host = environment.get("DOCICH_VRCHAT_OSC_HOST", "").strip() or _string_field(
        osc, "vrchat_host", "127.0.0.1", table="osc"
    )
    port_raw = environment.get("DOCICH_VRCHAT_OSC_PORT", "").strip()
    if port_raw:
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise VrOscConfigError("DOCICH_VRCHAT_OSC_PORT は整数です") from exc
        if not 1 <= port <= 65535:
            raise VrOscConfigError("DOCICH_VRCHAT_OSC_PORT は 1..65535 です")
    else:
        port = _int_field(osc, "vrchat_port", 9000, low=1, high=65535, table="osc")

    listen_host = environment.get("DOCICH_VRCHAT_OSC_LISTEN_HOST", "").strip() or _string_field(
        osc, "listen_host", "127.0.0.1", table="osc"
    )
    listen_port_raw = environment.get("DOCICH_VRCHAT_OSC_LISTEN_PORT", "").strip()
    if listen_port_raw:
        try:
            listen_port = int(listen_port_raw)
        except ValueError as exc:
            raise VrOscConfigError("DOCICH_VRCHAT_OSC_LISTEN_PORT は整数です") from exc
        if not 1 <= listen_port <= 65535:
            raise VrOscConfigError("DOCICH_VRCHAT_OSC_LISTEN_PORT は 1..65535 です")
    else:
        listen_port = _int_field(osc, "listen_port", 9001, low=1, high=65535, table="osc")

    if listen_port == port:
        raise VrOscConfigError("listen_port と vrchat_port は別のポートにしてください")

    poll_interval = _number_field(osc, "poll_interval_ms", 100.0, table="osc") / 1000.0
    send_min_interval = _number_field(osc, "send_min_interval_ms", 50.0, table="osc") / 1000.0
    echo_suppress = _number_field(osc, "echo_suppress_ms", 400.0, table="osc") / 1000.0
    if poll_interval <= 0:
        raise VrOscConfigError("[osc] poll_interval_ms は正の値です")
    if send_min_interval < 0 or echo_suppress < 0:
        raise VrOscConfigError("[osc] のミリ秒設定は0以上です")
    max_sends = _int_field(osc, "max_sends_per_second", 30, low=1, high=1000, table="osc")

    params = _parse_params(data)
    inbound = _parse_inbound(data, params)

    speaking_parameter = _string_field(speaking, "parameter", "AI_Talking", table="speaking")
    if speaking_parameter not in params:
        raise VrOscConfigError(
            f"[speaking] parameter={speaking_parameter!r} は [parameters] にありません"
        )
    if params[speaking_parameter].kind != "bool":
        raise VrOscConfigError("[speaking] parameter は bool パラメータである必要があります")
    for name, rule in inbound.items():
        if rule.action == "mode" and params[name].kind != "int":
            raise VrOscConfigError(f"[inbound.{name}] mode は int のみ扱えます")

    grace_ms = _number_field(speaking, "grace_ms", 500.0, table="speaking")
    if grace_ms < 0:
        raise VrOscConfigError("[speaking] grace_ms は0以上です")

    speaking_file = speaking.get("file", "tmp/state/speaking.json")
    if not isinstance(speaking_file, str) or not speaking_file.strip():
        raise VrOscConfigError("[speaking] file は空でない文字列です")
    speaking_path: Path | None
    if soren_root is None:
        speaking_path = None
    else:
        speaking_path = _resolve(Path(soren_root), speaking_file)

    def _path_field(key: str, default: str) -> Path:
        raw = paths.get(key, default)
        if not isinstance(raw, str) or not raw.strip():
            raise VrOscConfigError(f"[paths] {key} は空でない文字列です")
        return _resolve(state_dir, raw)

    settings = VrOscSettings(
        vrchat_host=host,
        vrchat_port=port,
        listen_host=listen_host,
        listen_port=listen_port,
        poll_interval=poll_interval,
        send_min_interval=send_min_interval,
        echo_suppress=echo_suppress,
        max_sends_per_second=max_sends,
        params=params,
        inbound=inbound,
        speaking_parameter=speaking_parameter,
        speaking_path=speaking_path,
        speaking_grace=grace_ms / 1000.0,
        event_spool=_path_field("event_spool", "vrchat_osc/events.jsonl"),
        action_spool=_path_field("action_spool", "vrchat_osc/actions.jsonl"),
        status_path=_path_field("status", "vrchat_osc/status.json"),
        state_path=_path_field("state", "vrchat_osc/state.json"),
        config_path=path if path.exists() else None,
        soren_root=Path(soren_root) if soren_root is not None else None,
    )
    return settings


def default_soren_root(repo_root: Path) -> Path | None:
    candidate = Path(repo_root) / "games" / "soviet_now"
    return candidate if candidate.is_dir() else None


# ---------------------------------------------------------------------------
# speaking state
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check.

    On Windows we cannot check a foreign PID without risking a TerminateProcess
    call, so we treat it as alive and rely on ``speaking_leave`` to remove the
    file.  On POSIX a crashed queue owner is detected and the avatar is not
    left permanently talking.
    """
    if os.name != "posix":
        return True
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:  # pragma: no cover - defensive
        return True
    return True


def read_speaking(path: Path | None, *, pid_alive: Callable[[int], bool] = _pid_alive) -> bool:
    if path is None:
        return False
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(data, Mapping) or not data.get("speaking"):
        return False
    pid = data.get("pid")
    if isinstance(pid, int) and pid > 0 and not pid_alive(pid):
        return False
    return True


# ---------------------------------------------------------------------------
# JSONL spool (events / actions)
# ---------------------------------------------------------------------------


def _ensure_parent(path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _trim_jsonl(path: Path, *, max_bytes: int = MAX_JSONL_BYTES, keep: int = JSONL_KEEP_LINES) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, size - 512 * 1024))
            tail = handle.read()
    except OSError:
        return
    lines = tail.split(b"\n")[-keep:]
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(b"\n".join(line for line in lines if line) + b"\n")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def append_jsonl(path: Path, record: Mapping[str, Any], *, max_bytes: int = MAX_JSONL_BYTES) -> None:
    """Append one JSON object as a single line (best effort, never partial)."""
    path = Path(path)
    _ensure_parent(path)
    line = json.dumps(dict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = line.encode("utf-8") + b"\n"
    if len(payload) > MAX_EVENT_LINE * 2:  # pragma: no cover - callers validate first
        raise VrOscError("JSONL レコードが大きすぎます")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    descriptor = os.open(path, flags, 0o644)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    _trim_jsonl(path, max_bytes=max_bytes)


class JsonlTail:
    """Read only newly appended, complete JSONL lines.

    Existing lines are skipped on the first read so a restart does not replay
    stale alerts, and rotation/truncation resets the offset safely.
    """

    def __init__(self, path: Path, *, skip_existing: bool = True, max_line: int = MAX_EVENT_LINE):
        self.path = Path(path)
        self.skip_existing = skip_existing
        self.max_line = max_line
        self._offset = 0
        self._buffer = b""
        self._opened = False
        # Snapshot the size at construction time: lines that already existed
        # then are "old" (never replayed), lines appended afterwards are new.
        try:
            self._initial_size = self.path.stat().st_size
        except OSError:
            self._initial_size = 0

    def read(self) -> list[dict[str, Any]]:
        try:
            stat = self.path.stat()
        except OSError:
            self._opened = False
            self._offset = 0
            self._buffer = b""
            return []
        if stat.st_size < self._offset:
            self._offset = 0
            self._buffer = b""
        if not self._opened:
            self._opened = True
            if self.skip_existing:
                self._offset = self._initial_size
        if stat.st_size <= self._offset:
            return []
        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(stat.st_size - self._offset)
        except OSError:
            return []
        self._offset += len(chunk)
        chunk = self._buffer + chunk
        lines = chunk.split(b"\n")
        self._buffer = lines.pop()
        if len(self._buffer) > self.max_line:
            self._buffer = b""
        events: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            if len(line) > self.max_line:
                continue
            try:
                decoded = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(decoded, dict):
                events.append(decoded)
        return events


def append_events(
    path: Path,
    assignments: Iterable[tuple[str, Any]],
    *,
    settings: VrOscSettings,
    hold_ms: int | None = None,
    now: float | None = None,
) -> int:
    """Validate and append ``emit`` events for the bridge."""
    count = 0
    stamp = time.time() if now is None else now
    for name, raw in assignments:
        spec = settings.param(name)
        if name == settings.speaking_parameter:
            raise VrOscError(
                f"{name} は speaking.json から導出されるため emit できません"
            )
        value = coerce_value(name, spec.kind, raw)
        effective_hold = spec.hold_ms if hold_ms is None else hold_ms
        if effective_hold < 0:
            raise VrOscError("hold は0以上の秒数です")
        record = {
            "ts": round(stamp, 3),
            "param": name,
            "value": value,
            "hold_ms": int(effective_hold),
        }
        append_jsonl(path, record)
        count += 1
    return count


# ---------------------------------------------------------------------------
# inbound actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InboundAction:
    parameter: str
    value: Any
    action: str
    record: Mapping[str, Any]


class ActionSpool:
    """Default sink: append a validated action record to the spool."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def __call__(self, record: Mapping[str, Any]) -> None:
        append_jsonl(self.path, record)


class OverlaySink:
    """Optional docich-side effect: show ``notify`` actions on the overlay.

    Imported lazily so the bridge can run on a Windows VRChat host where
    ``fcntl`` is unavailable.  Failures are the caller's to absorb.
    """

    def __init__(self, soren_root: Path):
        self.soren_root = Path(soren_root)

    def __call__(self, record: Mapping[str, Any]) -> None:
        if record.get("action") != "notify":
            return
        from .overlay_queue import append_event

        append_event(
            self.soren_root,
            {
                "category": "system",
                "title": str(record.get("title") or "VR同志"),
                "body": str(record.get("body") or ""),
                "level": "info",
            },
            regenerate=True,
        )


class InboundReceiver:
    """Allowlisted OSC -> named action translation with edge/debounce gating."""

    def __init__(self, settings: VrOscSettings):
        self.settings = settings
        self._last_value: dict[str, Any] = {}
        self._last_fire_at: dict[str, float] = {}
        self.received = 0
        self.dropped = 0
        self.actions = 0

    def handle(
        self,
        data: bytes,
        now: float,
        *,
        is_echo: Callable[[str, Any, float], bool] | None = None,
        wallclock: float | None = None,
        on_observe: Callable[[str, Any], None] | None = None,
    ) -> list[InboundAction]:
        try:
            messages = decode_packet(data)
        except VrOscError:
            self.dropped += 1
            return []
        self.received += len(messages)
        if wallclock is None:
            wallclock = time.time()
        results: list[InboundAction] = []
        for message in messages:
            address = message.address
            if not address.startswith(PARAMETER_PREFIX):
                continue
            name = address[len(PARAMETER_PREFIX) :]
            if "/" in name or not name:
                self.dropped += 1
                continue
            rule = self.settings.inbound.get(name)
            if rule is None:
                self.dropped += 1
                continue
            if not message.args:
                self.dropped += 1
                continue
            spec = self.settings.params[name]
            try:
                value = coerce_value(name, spec.kind, message.args[0])
            except VrOscError:
                self.dropped += 1
                continue
            previous = self._last_value.get(name)
            self._last_value[name] = value
            if is_echo is not None and is_echo(name, value, now):
                self.dropped += 1
                continue
            # Recording the accepted value is deliberately separate from the
            # notify gate below: a bool falling edge produces no action, but the
            # avatar's new state still has to be remembered. Otherwise the next
            # outbound ``true`` compares equal to the stale record and is never
            # sent.
            if on_observe is not None:
                on_observe(name, value)
            if rule.min_interval > 0:
                last_fire = self._last_fire_at.get(name)
                if last_fire is not None and now - last_fire < rule.min_interval:
                    self.dropped += 1
                    continue
            if spec.kind == "bool":
                # Rising edge only: a held/stuck "true" must not re-fire.
                if previous is True or value is not True:
                    continue
            else:
                if previous is not None and _same_value(previous, value):
                    continue
            if rule.action == "mode":
                index = int(value)
                if rule.values and not 0 <= index < len(rule.values):
                    self.dropped += 1
                    continue
                label = rule.values[index] if rule.values else str(index)
                record = {
                    "ts": round(wallclock, 3),
                    "action": "mode",
                    "parameter": name,
                    "value": index,
                    "name": label,
                }
            else:
                record = {
                    "ts": round(wallclock, 3),
                    "action": "notify",
                    "parameter": name,
                    "value": True if spec.kind == "bool" else value,
                    "title": rule.title,
                    "body": rule.body,
                }
            self._last_fire_at[name] = now
            self.actions += 1
            results.append(
                InboundAction(parameter=name, value=value, action=rule.action, record=record)
            )
        return results


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


def _resolve_udp_address(host: str, port: int) -> tuple[str, int]:
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
    except socket.gaierror as exc:
        raise VrOscError(f"VRChat のホスト名を解決できません: {host}: {exc}") from exc
    if not infos:  # pragma: no cover - defensive
        raise VrOscError(f"VRChat のホスト名を解決できません: {host}")
    return infos[0][4]


class OscSender:
    """Small UDP sender.  A closed port is an error to report, not a crash."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        sock: socket.socket | None = None,
    ):
        self.address = _resolve_udp_address(host, port)
        self._sock = sock if sock is not None else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sent = 0
        self.last_error: str | None = None

    def send_param(self, name: str, value: Any) -> None:
        packet = encode_message(PARAMETER_PREFIX + name, (value,))
        self._sock.sendto(packet, self.address)
        self.sent += 1

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# bridge
# ---------------------------------------------------------------------------


@dataclass
class TickResult:
    sent: tuple[str, ...] = ()
    actions: tuple[InboundAction, ...] = ()


@dataclass
class _Slot:
    value: Any
    expires_at: float | None = None


class VrOscBridge:
    def __init__(
        self,
        settings: VrOscSettings,
        *,
        sender: OscSender | Any,
        clock: Callable[[], float] = time.monotonic,
        wallclock: Callable[[], float] = time.time,
        sinks: Sequence[Callable[[Mapping[str, Any]], None]] = (),
        log: Callable[[str], None] | None = None,
    ):
        self.settings = settings
        self.sender = sender
        self._clock = clock
        self._wallclock = wallclock
        self._sinks = list(sinks)
        self._log = log or (lambda message: None)
        self._slots: dict[str, _Slot] = {
            name: _Slot(default_value(spec.kind)) for name, spec in settings.params.items()
        }
        self._synced: dict[str, tuple[Any, float]] = {}
        self._send_times: deque[float] = deque()
        self._last_send_at = float("-inf")
        self._talking = False
        self._talking_seen_at = float("-inf")
        self._tail = JsonlTail(settings.event_spool)
        self.receiver = InboundReceiver(settings)
        self._status_written_at = float("-inf")
        self._last_error: str | None = None
        self._last_error_log_at = float("-inf")
        self.sent = 0
        self._load_persisted()

    # -- state ---------------------------------------------------------

    @property
    def talking(self) -> bool:
        return self._talking

    def values(self) -> dict[str, Any]:
        return {name: slot.value for name, slot in self._slots.items()}

    def _load_persisted(self) -> None:
        path = self.settings.state_path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, Mapping):
            return
        stored = raw.get("params")
        if not isinstance(stored, Mapping):
            return
        for name, value in stored.items():
            spec = self.settings.params.get(name)
            if spec is None or not spec.persist:
                continue
            try:
                self._slots[name] = _Slot(coerce_value(name, spec.kind, value))
            except VrOscError:
                continue

    def _persist(self) -> None:
        stored = {
            name: self._slots[name].value
            for name, spec in self.settings.params.items()
            if spec.persist
        }
        _atomic_write_json(
            self.settings.state_path,
            {"params": stored, "updated_at": round(self._wallclock(), 3)},
        )

    # -- tick ----------------------------------------------------------

    def tick(
        self,
        *,
        now: float | None = None,
        packets: Iterable[bytes] = (),
    ) -> TickResult:
        current = self._clock() if now is None else now
        try:
            self._apply_events(current)
            self._apply_speaking(current)
        except Exception as exc:  # pragma: no cover - defensive, keep fail-soft
            self._record_error(f"state refresh failed: {exc}", current)
        actions: list[InboundAction] = []
        for packet in packets:
            try:
                actions.extend(self._handle_inbound(packet, current))
            except Exception as exc:  # pragma: no cover - defensive
                self._record_error(f"inbound handling failed: {exc}", current)
        self._expire(current)
        sent = self._flush(current)
        if current - self._status_written_at >= STATUS_INTERVAL:
            self._write_status(current)
        return TickResult(sent=tuple(sent), actions=tuple(actions))

    def _apply_events(self, now: float) -> None:
        for event in self._tail.read():
            name = event.get("param")
            if not isinstance(name, str) or name not in self.settings.params:
                continue
            if name == self.settings.speaking_parameter:
                continue
            spec = self.settings.params[name]
            try:
                value = coerce_value(name, spec.kind, event.get("value"))
            except VrOscError:
                continue
            hold_ms = event.get("hold_ms", spec.hold_ms)
            if isinstance(hold_ms, bool) or not isinstance(hold_ms, (int, float)) or hold_ms < 0:
                hold_ms = spec.hold_ms
            expires_at = now + (float(hold_ms) / 1000.0) if hold_ms > 0 else None
            self._slots[name] = _Slot(value=value, expires_at=expires_at)

    def _apply_speaking(self, now: float) -> None:
        name = self.settings.speaking_parameter
        if self.settings.speaking_path is None:
            return
        if read_speaking(self.settings.speaking_path):
            self._talking = True
            self._talking_seen_at = now
        elif self._talking and now - self._talking_seen_at <= self.settings.speaking_grace:
            pass  # grace: avoid flicker between consecutive chunks
        else:
            self._talking = False
        self._slots[name] = _Slot(value=self._talking)

    def _expire(self, now: float) -> None:
        for name, slot in self._slots.items():
            if slot.expires_at is None or now < slot.expires_at:
                continue
            spec = self.settings.params[name]
            slot.value = default_value(spec.kind)
            slot.expires_at = None

    def is_echo(self, name: str, value: Any, now: float) -> bool:
        last = self._synced.get(name)
        if last is None:
            return False
        last_value, synced_at = last
        if now - synced_at > self.settings.echo_suppress:
            return False
        return _same_value(last_value, value)

    def _handle_inbound(self, packet: bytes, now: float) -> list[InboundAction]:
        actions = self.receiver.handle(
            packet,
            now,
            is_echo=self.is_echo,
            wallclock=self._wallclock(),
            on_observe=lambda name, value: self._observe_inbound(name, value, now),
        )
        for action in actions:
            name = action.parameter
            spec = self.settings.params[name]
            value = coerce_value(name, spec.kind, action.value)
            self._observe_inbound(name, value, now)
            self._dispatch(action.record)
        return actions

    def _observe_inbound(self, name: str, value: Any, now: float) -> None:
        """Adopt an accepted inbound value as the current avatar state.

        This runs for every accepted value — a bool falling edge included, which
        fires no action — and is what lets the outbound side detect the next
        change.  ``_slots`` and ``_synced`` are updated together so the adopted
        value is not bounced straight back.
        """
        spec = self.settings.params[name]
        self._slots[name] = _Slot(value=value)
        self._synced[name] = (value, now)
        if spec.persist:
            self._persist()

    def _dispatch(self, record: Mapping[str, Any]) -> None:
        for sink in self._sinks:
            try:
                sink(record)
            except Exception as exc:  # pragma: no cover - sink-specific
                self._record_error(f"action sink failed: {exc}", self._clock())

    def _flush(self, now: float) -> list[str]:
        # ``send_min_interval`` throttles *batches* (one per tick), not the
        # individual datagrams of a single sync, so a fresh bridge can bring
        # all parameters up to date in one poll.
        if now - self._last_send_at < self.settings.send_min_interval:
            return []
        sent: list[str] = []
        for name, spec in self.settings.params.items():
            slot = self._slots[name]
            previous = self._synced.get(name)
            if previous is not None and _same_value(previous[0], slot.value):
                continue
            if not self._rate_ok(now):
                break
            try:
                self.sender.send_param(name, slot.value)
            except OSError as exc:
                self._record_error(f"OSC 送信に失敗しました ({name}): {exc}", now)
                break
            self._synced[name] = (slot.value, now)
            self._send_times.append(now)
            self.sent += 1
            sent.append(name)
            if spec.persist:
                self._persist()
        if sent:
            self._last_send_at = now
        return sent

    def _rate_ok(self, now: float) -> bool:
        while self._send_times and now - self._send_times[0] >= 1.0:
            self._send_times.popleft()
        return len(self._send_times) < self.settings.max_sends_per_second

    def _record_error(self, message: str, now: float) -> None:
        self._last_error = message
        if now - self._last_error_log_at >= 5.0:
            self._last_error_log_at = now
            self._log(message)

    # -- status --------------------------------------------------------

    def status(self, now: float | None = None) -> dict[str, Any]:
        current = self._clock() if now is None else now
        return {
            "ts": round(self._wallclock(), 3),
            "vrchat": {"host": self.settings.vrchat_host, "port": self.settings.vrchat_port},
            "listen": {"host": self.settings.listen_host, "port": self.settings.listen_port},
            "talking": self._talking,
            "params": self.values(),
            "synced": {name: value for name, (value, _) in self._synced.items()},
            "counters": {
                "sent": self.sent,
                "received": self.receiver.received,
                "dropped": self.receiver.dropped,
                "actions": self.receiver.actions,
            },
            "last_error": self._last_error,
        }

    def _write_status(self, now: float) -> None:
        self._status_written_at = now
        _atomic_write_json(self.settings.status_path, self.status(now))

    # -- run -----------------------------------------------------------

    def run(
        self,
        *,
        duration: float | None = None,
        stop_event: Any = None,
    ) -> int:
        """Run until interrupted (or ``duration`` seconds elapse)."""
        receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            receiver_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                receiver_socket.bind((self.settings.listen_host, self.settings.listen_port))
            except OSError as exc:
                raise VrOscError(
                    f"OSC 受信ポート {self.settings.listen_host}:{self.settings.listen_port} "
                    f"を確保できません: {exc}"
                ) from exc
            receiver_socket.setblocking(False)
            return self._loop(receiver_socket, duration=duration, stop_event=stop_event)
        except KeyboardInterrupt:
            return 0
        finally:
            try:
                receiver_socket.close()
            except OSError:
                pass

    def _loop(self, receiver_socket: socket.socket, *, duration: float | None, stop_event: Any) -> int:
        started = self._clock()
        previous_signal: Any = None
        previous_term: Any = None
        stopped = {"value": False}

        def _stop(signum: int, frame: Any) -> None:  # pragma: no cover - signal path
            stopped["value"] = True

        try:
            previous_signal = signal.getsignal(signal.SIGINT)
            previous_term = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGINT, _stop)
            signal.signal(signal.SIGTERM, _stop)
        except (ValueError, OSError):  # pragma: no cover - non-main thread
            previous_signal = previous_term = None

        try:
            while not stopped["value"]:
                if stop_event is not None and stop_event.is_set():
                    break
                now = self._clock()
                if duration is not None and now - started >= duration:
                    break
                try:
                    readable, _, _ = select.select(
                        [receiver_socket], [], [], self.settings.poll_interval
                    )
                except OSError as exc:  # pragma: no cover - defensive
                    if exc.errno == errno.EINTR:
                        continue
                    raise
                packets: list[bytes] = []
                if readable:
                    packets = self._drain(receiver_socket)
                try:
                    self.tick(packets=packets)
                except Exception as exc:  # pragma: no cover - defensive
                    self._record_error(f"tick failed: {exc}", self._clock())
            return 0
        finally:
            if previous_signal is not None:
                try:
                    signal.signal(signal.SIGINT, previous_signal)
                    signal.signal(signal.SIGTERM, previous_term)
                except (ValueError, OSError):  # pragma: no cover
                    pass
            self._write_status(self._clock())

    @staticmethod
    def _drain(receiver_socket: socket.socket, limit: int = 64) -> list[bytes]:
        packets: list[bytes] = []
        for _ in range(limit):
            try:
                data, _address = receiver_socket.recvfrom(MAX_PACKET_BYTES)
            except BlockingIOError:
                break
            except OSError:
                break
            packets.append(data)
        return packets


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    try:
        _ensure_parent(path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        pass  # status/state files are best effort; never break the bridge


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_rate(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"数値が必要です: {value!r}") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("0以上の秒数を指定してください")
    return number


def configure_parser(parser: argparse.ArgumentParser, *, standalone: bool = False) -> None:
    parser.add_argument(
        "--osc-config",
        metavar="PATH",
        help="VRChat OSC 設定 (既定: <repo>/config/vrchat_osc.toml)",
    )
    parser.add_argument(
        "--soren-root",
        metavar="PATH",
        help="soviet_now ルート (既定: <repo>/games/soviet_now)。speaking.json の読み取り元",
    )
    if standalone:
        parser.add_argument("--repo-root", default=".", metavar="PATH")
        parser.add_argument("--state-dir", default=None, metavar="PATH")

    sub = parser.add_subparsers(dest="vr_osc_command", required=True)

    bridge = sub.add_parser("bridge", help="OSC Bridge を常駐実行する (docich <-> VRChat)")
    bridge.add_argument("--duration", type=_parse_rate, metavar="SEC", help="指定秒数で終了する (スモークテスト用)")
    bridge.add_argument("--dry-run", action="store_true", help="設定を表示して終了する")
    bridge.add_argument("--quiet", action="store_true", help="起動時の設定表示を抑制する")

    emit = sub.add_parser("emit", help="docich 側イベントを spool へ積む (表情・アラート等)")
    emit.add_argument("assignments", nargs="+", metavar="NAME=VALUE", help="例: AI_Emotion=2 Alert=true")
    emit.add_argument("--hold", type=_parse_rate, metavar="SEC", help="保持秒数 (省略時は設定の hold_ms)")
    emit.add_argument("--dry-run", action="store_true", help="書き込まずに内容を表示する")

    send = sub.add_parser("send", help="OSC を1回だけ VRChat へ送る (bridge 不要)")
    send.add_argument("assignments", nargs="+", metavar="NAME=VALUE")
    send.add_argument("--dry-run", action="store_true", help="送信せず内容を表示する")

    inject = sub.add_parser("inject", help="VRChat からの OSC を模擬注入する (実機なしの受信テスト)")
    inject.add_argument("assignments", nargs="+", metavar="NAME=VALUE")
    inject.add_argument("--dry-run", action="store_true", help="送信せず内容を表示する")

    status = sub.add_parser("status", help="bridge の状態と設定を表示する")
    status.add_argument("--json", action="store_true", help="JSON で表示する")

    monitor = sub.add_parser(
        "monitor", help="VRChat の代わりに OSC を受信して表示する (実機なしの送信テスト)"
    )
    monitor.add_argument("--port", type=int, default=None, help="待受ポート (既定: osc.vrchat_port)")
    monitor.add_argument("--duration", type=_parse_rate, metavar="SEC", help="指定秒数で終了する")
    monitor.add_argument("--json", action="store_true", help="JSON Lines で表示する")


def _resolve_settings(args: argparse.Namespace, *, repo_root: Path, state_dir: Path) -> VrOscSettings:
    soren_root = getattr(args, "soren_root", None)
    if soren_root:
        soren_path = Path(soren_root).expanduser().resolve()
        if not soren_path.is_dir():
            raise VrOscError(f"--soren-root がディレクトリではありません: {soren_path}")
    else:
        soren_path = default_soren_root(repo_root)
    config = getattr(args, "osc_config", None)
    return load_settings(
        repo_root=Path(repo_root),
        state_dir=Path(state_dir),
        config_path=Path(config).expanduser() if config else None,
        soren_root=soren_path,
    )


def _describe(settings: VrOscSettings) -> str:
    lines = [
        "VRChat OSC bridge 設定:",
        f"  送信先 (VRChat): {settings.vrchat_host}:{settings.vrchat_port}",
        f"  受信 (VRChat -> docich): {settings.listen_host}:{settings.listen_port}",
        f"  設定ファイル: {settings.config_path or '(既定値)'}",
        f"  speaking.json: {settings.speaking_path or '(なし: AI_Talking は常に false)'}",
        f"  event spool: {settings.event_spool}",
        f"  action spool: {settings.action_spool}",
        "  パラメータ:",
    ]
    for name, spec in settings.params.items():
        flags = []
        if spec.hold_ms:
            flags.append(f"hold={spec.hold_ms}ms")
        if spec.persist:
            flags.append("persist")
        if name == settings.speaking_parameter:
            flags.append("speaking.json 由来")
        suffix = f" ({', '.join(flags)})" if flags else ""
        lines.append(f"    {name}: {spec.kind}{suffix}")
    if settings.inbound:
        lines.append("  受信アクション (allowlist):")
        for name, rule in settings.inbound.items():
            values = f" values={list(rule.values)}" if rule.values else ""
            lines.append(f"    {name} -> {rule.action}{values}")
    else:
        lines.append("  受信アクション: なし (VRChat -> docich は無効)")
    return "\n".join(lines)


def run_args(
    args: argparse.Namespace,
    *,
    repo_root: Path,
    state_dir: Path,
) -> int:
    settings = _resolve_settings(args, repo_root=Path(repo_root), state_dir=Path(state_dir))
    command = args.vr_osc_command

    if command == "status":
        return _cmd_status(settings, json_output=bool(getattr(args, "json", False)))

    if command == "monitor":
        return _cmd_monitor(
            settings,
            port=getattr(args, "port", None),
            duration=getattr(args, "duration", None),
            json_output=bool(getattr(args, "json", False)),
        )

    if command == "emit":
        assignments = [parse_assignment(item) for item in args.assignments]
        if args.dry_run:
            for name, value in assignments:
                spec = settings.param(name)
                print(f"emit {name}={coerce_value(name, spec.kind, value)!r} -> {settings.event_spool}")
            return 0
        count = append_events(settings.event_spool, assignments, settings=settings, hold_ms=_hold_ms(args))
        print(f"docich: {count} 件のイベントを積みました ({settings.event_spool})")
        return 0

    if command == "send":
        assignments = [parse_assignment(item) for item in args.assignments]
        resolved: list[tuple[str, Any]] = [
            (name, coerce_value(name, settings.param(name).kind, value)) for name, value in assignments
        ]
        if args.dry_run:
            for name, value in resolved:
                print(f"send {name}={value!r} -> {settings.vrchat_host}:{settings.vrchat_port}")
            return 0
        sender = OscSender(settings.vrchat_host, settings.vrchat_port)
        try:
            for name, value in resolved:
                sender.send_param(name, value)
        finally:
            sender.close()
        print(f"docich: {len(resolved)} 件を {settings.vrchat_host}:{settings.vrchat_port} へ送信しました")
        return 0

    if command == "inject":
        assignments = [parse_assignment(item) for item in args.assignments]
        resolved = [
            (name, coerce_value(name, settings.param(name).kind, value)) for name, value in assignments
        ]
        for name, _value in resolved:
            if name not in settings.inbound:
                raise VrOscError(f"{name} は [inbound] にないため bridge は受理しません")
        target_host = "127.0.0.1" if settings.listen_host in ("0.0.0.0", "::") else settings.listen_host
        if args.dry_run:
            for name, value in resolved:
                print(f"inject {name}={value!r} -> {target_host}:{settings.listen_port}")
            return 0
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for name, value in resolved:
                sock.sendto(
                    encode_message(PARAMETER_PREFIX + name, (value,)),
                    _resolve_udp_address(target_host, settings.listen_port),
                )
        finally:
            sock.close()
        print(f"docich: {len(resolved)} 件を {target_host}:{settings.listen_port} へ注入しました")
        return 0

    if command == "bridge":
        if not args.quiet:
            print(_describe(settings), flush=True)
        if args.dry_run:
            return 0
        sender = OscSender(settings.vrchat_host, settings.vrchat_port)
        sinks: list[Callable[[Mapping[str, Any]], None]] = [ActionSpool(settings.action_spool)]
        if settings.soren_root is not None:
            sinks.append(OverlaySink(settings.soren_root))

        def _log(message: str) -> None:
            print(f"docich vr-osc: {message}", file=sys.stderr, flush=True)

        bridge = VrOscBridge(settings, sender=sender, sinks=sinks, log=_log)
        try:
            print(
                f"docich: OSC bridge を開始します ({settings.listen_host}:{settings.listen_port} 受信 / "
                f"{settings.vrchat_host}:{settings.vrchat_port} 送信)",
                flush=True,
            )
            return bridge.run(duration=args.duration)
        finally:
            sender.close()

    raise VrOscError(f"未知の vr-osc コマンドです: {command}")


def _hold_ms(args: argparse.Namespace) -> int | None:
    hold = getattr(args, "hold", None)
    if hold is None:
        return None
    return int(round(float(hold) * 1000))


def _cmd_monitor(
    settings: VrOscSettings,
    *,
    port: int | None = None,
    duration: float | None = None,
    json_output: bool = False,
) -> int:
    """Act as VRChat's OSC listener to verify docich -> VRChat messages."""
    bind_port = settings.vrchat_port if port is None else int(port)
    if not 1 <= bind_port <= 65535:
        raise VrOscError("--port は 1..65535 です")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", bind_port))
        except OSError as exc:
            raise VrOscError(f"待受ポート 127.0.0.1:{bind_port} を確保できません: {exc}") from exc
        sock.settimeout(0.2)
        if not json_output:
            print(
                f"docich: 127.0.0.1:{bind_port} で OSC を待ち受けます "
                f"(別ターミナルで `docich vr-osc send ...`)",
                flush=True,
            )
        deadline = time.monotonic() + duration if duration else None
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                return 0
            try:
                data, address = sock.recvfrom(MAX_PACKET_BYTES)
            except socket.timeout:
                continue
            except KeyboardInterrupt:  # pragma: no cover - interactive
                return 0
            try:
                messages = decode_packet(data)
            except VrOscError as exc:
                print(f"docich vr-osc: 不正な OSC パケット ({address}): {exc}", file=sys.stderr)
                continue
            for message in messages:
                if json_output:
                    print(json.dumps(
                        {"from": f"{address[0]}:{address[1]}", "address": message.address,
                         "args": [value if not isinstance(value, bytes) else value.hex()
                                  for value in message.args]},
                        ensure_ascii=False,
                    ), flush=True)
                else:
                    print(f"{message.address} {list(message.args)}", flush=True)
    finally:
        sock.close()


def _cmd_status(settings: VrOscSettings, *, json_output: bool) -> int:
    status: Mapping[str, Any] | None = None
    try:
        raw = json.loads(settings.status_path.read_text(encoding="utf-8"))
        if isinstance(raw, Mapping):
            status = raw
    except (OSError, ValueError):
        status = None
    if json_output:
        payload = {
            "config": {
                "vrchat_host": settings.vrchat_host,
                "vrchat_port": settings.vrchat_port,
                "listen_host": settings.listen_host,
                "listen_port": settings.listen_port,
                "config_path": str(settings.config_path) if settings.config_path else None,
                "params": {
                    name: {"type": spec.kind, "hold_ms": spec.hold_ms, "persist": spec.persist}
                    for name, spec in settings.params.items()
                },
                "inbound": {name: rule.action for name, rule in settings.inbound.items()},
            },
            "status": status,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(_describe(settings))
    if status is None:
        print("\n状態: bridge は未起動です (status file がありません)")
        return 0
    counters = status.get("counters") or {}
    print("\n状態:")
    print(f"  talking: {status.get('talking')}")
    print(f"  params: {status.get('params')}")
    print(f"  counters: {counters}")
    if status.get("last_error"):
        print(f"  last_error: {status.get('last_error')}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone entry point: ``python -m docich.vrchat_osc ...``."""
    parser = argparse.ArgumentParser(
        prog="python -m docich.vrchat_osc",
        description="VRChat OSC bridge (docich Issue #316)",
    )
    configure_parser(parser, standalone=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    repo_root = Path(getattr(args, "repo_root", ".")).expanduser().resolve()
    state_dir = (
        Path(args.state_dir).expanduser().resolve()
        if getattr(args, "state_dir", None)
        else repo_root / "run"
    )
    try:
        return run_args(args, repo_root=repo_root, state_dir=state_dir)
    except VrOscError as exc:
        print(f"docich vr-osc: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
