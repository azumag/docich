"""docich webui: Tailscale 経由のモデルチェーン / バックオフ管理 UI (stdlib only).

Architecture: ThreadingHTTPServer + vanilla JS single-page.
Security: Tailscale ACL が主防御。任意 token は二層目 (未設定なら無効)。
State: soren_root = ELOOP_LIB_DIR 相当 (games/soviet_now or /home/ubuntu/soren)。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import signal
import subprocess
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import GlobalConfig

# --- allowlist / validation --------------------------------------------------

WEBUI_ALLOWLIST = {
    # chain (core/config.sh:33-78)
    "AI_COMMON_AGENTS",
    "MODEL_IMPROVE_LIST",
    "RADIO_AGENTS",
    "RADIO_PREPASS_AGENTS",
    "COMMENT_AGENTS",
    "COMMENT_TRANSLATION_AGENTS",
    # backoff
    "AI_BACKOFF_SEC_ITEMS",
    "AI_AGENT_BACKOFF_SEC",
    "AI_BACKOFF_FAILURE_SEC",  # PR #125: 一過性のプロバイダ/CLI失敗用の短バックオフ
    # peak hours (core/config.sh:88-104)
    "PEAK_HOURS_AGENT_SWAP_ENABLED",
    "PEAK_HOURS_WINDOWS",
    "PEAK_HOURS_TZ",
    "PEAK_HOURS_PRIORITY_AGENT",
    "PEAK_HOURS_AGENT_PREFERENCE",
    "PEAK_HOURS_QUEUE_GATE_ENABLED",
}

# hard defaults from core/config.sh
DEFAULTS: dict[str, str] = {
    "AI_COMMON_AGENTS": "opencode:deepseek-v4-flash-free,codex:amd-token-factory-deepseek-v4-flash,codex:openrouter/free,local,codex:deepseek-v4-flash,codex:minimax-m3,opencode-go:muse-spark-1.2-contributor",
    "MODEL_IMPROVE_LIST": "opencode:deepseek-v4-flash-free,codex:amd-token-factory-deepseek-v4-flash,codex:deepseek-v4-flash,codex:minimax-m3",
    "RADIO_AGENTS": "",  # inherits AI_COMMON_AGENTS
    "RADIO_PREPASS_AGENTS": "",  # inherits AI_COMMON_AGENTS
    "COMMENT_AGENTS": "",  # inherits AI_COMMON_AGENTS
    "COMMENT_TRANSLATION_AGENTS": "",  # inherits COMMENT_AGENTS
    "AI_BACKOFF_SEC_ITEMS": "deepseek-v4-flash-free:86400 amd-token-factory-deepseek-v4-flash:86400 openrouter/free:86400 local:1800 deepseek-v4-flash:18000 minimax-m3:18000 muse-spark-1.2-contributor:86400",
    "AI_AGENT_BACKOFF_SEC": "600",
    "AI_BACKOFF_FAILURE_SEC": "300",
    "PEAK_HOURS_AGENT_SWAP_ENABLED": "1",
    "PEAK_HOURS_WINDOWS": "10-13,15-19",
    "PEAK_HOURS_TZ": "Asia/Tokyo",
    "PEAK_HOURS_PRIORITY_AGENT": "codex:minimax-m3",
    "PEAK_HOURS_AGENT_PREFERENCE": "codex:minimax-m3,codex:openrouter/free,local",
    "PEAK_HOURS_QUEUE_GATE_ENABLED": "1",
}

AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
# For AI_BACKOFF_SEC_ITEMS name part (model without prefix)
BACKOFF_NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
# .env は worker が bash で source する (set -a; . ./.env) ため、値にシェル構文を
# 混入させてはならない。TZ は IANA 名に限定する。
TZ_RE = re.compile(r"^[A-Za-z0-9_+./:-]{1,64}$")
SECRET_SUBSTRINGS = ("API_KEY", "TOKEN", "SECRET", "STREAM_KEY", "PASSWORD")

# --- overlay constants --------------------------------------------------------
OVERLAY_CATEGORIES = {"game", "worker", "chat", "radio", "prediction", "rollback", "system"}
OVERLAY_TITLE_LIMIT = 120
OVERLAY_BODY_LIMIT = 500
WORK_TITLE_LIMIT = 80
WORK_BODY_LIMIT = 240
TOP_LINE_LIMIT = 120
TOP_MAX_LINES = 4

# --- helpers -----------------------------------------------------------------


def _resolve_soren_root(g: GlobalConfig, override: str | None) -> Path:
    if override:
        p = Path(override).expanduser().resolve()
        return p
    if g.webui.soren_root:
        raw = g.webui.soren_root.strip()
        p = Path(raw)
        if not p.is_absolute():
            p = (g.repo_root / p).resolve()
        else:
            p = p.resolve()
        return p
    # auto discover: games/soviet_now relative to repo_root
    cand = g.repo_root / "games" / "soviet_now"
    if (cand / "eloop_lib.sh").is_file():
        return cand.resolve()
    # fallback: current working directory if it looks like soren
    cwd = Path.cwd()
    if (cwd / "eloop_lib.sh").is_file():
        return cwd.resolve()
    return cand.resolve()


def _dotenv_path(soren_root: Path) -> Path:
    return soren_root / ".env"


def _dotenv_mtime(soren_root: Path) -> int:
    p = _dotenv_path(soren_root)
    try:
        return int(p.stat().st_mtime)
    except Exception:
        return 0


def _read_dotenv_dict(soren_root: Path) -> dict[str, str]:
    p = _dotenv_path(soren_root)
    values: dict[str, str] = {}
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return values
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#") or "=" not in line_stripped:
            continue
        key, val = line_stripped.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        # inline comment removal (simple: " #")
        # keep value as-is but strip trailing " #..." if present and not inside quotes (already stripped)
        # Remove " #..." suffix
        if " #" in val:
            # split only if # follows space
            val = val.split(" #", 1)[0].rstrip()
        values.setdefault(key, val)
    return values


def _effective_value(key: str, dotenv: dict[str, str]) -> str:
    if key in dotenv:
        if key == "PEAK_HOURS_WINDOWS":
            # config.sh は ${VAR-...} で読むため、空行でも「空=無効」を保つ
            return dotenv[key].strip()
        if dotenv[key] != "":
            return dotenv[key]
    # handle inheritance
    if key in ("RADIO_AGENTS", "RADIO_PREPASS_AGENTS", "COMMENT_AGENTS"):
        # inherits AI_COMMON_AGENTS
        if "AI_COMMON_AGENTS" in dotenv and dotenv["AI_COMMON_AGENTS"]:
            return dotenv["AI_COMMON_AGENTS"]
        return DEFAULTS["AI_COMMON_AGENTS"]
    if key == "COMMENT_TRANSLATION_AGENTS":
        # inherits COMMENT_AGENTS effective
        ca = _effective_value("COMMENT_AGENTS", dotenv)
        if ca:
            return ca
        return DEFAULTS["AI_COMMON_AGENTS"]
    return DEFAULTS.get(key, "")


def _sanitize_agent(agent: str) -> str:
    v = re.sub(r"[^a-z0-9._-]+", "_", str(agent or "").lower())
    return v.strip("_")


def _fmt_remaining(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h}h{m:02d}m" if m else f"{h}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _is_valid_peak_time(t: str) -> bool:
    t = t.strip()
    if re.fullmatch(r"\d{1,2}", t):
        try:
            h = int(t)
            return 0 <= h <= 23
        except ValueError:
            return False
    if re.fullmatch(r"\d{3,4}", t):
        # HHMM: first part hour, last 2 minute
        try:
            if len(t) == 3:
                h = int(t[0])
                m = int(t[1:])
            else:
                h = int(t[:2])
                m = int(t[2:])
            return 0 <= h <= 23 and 0 <= m <= 59
        except ValueError:
            return False
    if re.fullmatch(r"\d{1,2}:\d{2}", t):
        try:
            h, m = t.split(":")
            return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        except ValueError:
            return False
    return False


def _validate_value(key: str, value: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{key} の値は文字列である必要があります")
    # .env は bash で source されるため、シェルメタ文字を許すキーは存在しない
    if key in ("AI_COMMON_AGENTS", "MODEL_IMPROVE_LIST", "RADIO_AGENTS", "RADIO_PREPASS_AGENTS", "COMMENT_AGENTS", "COMMENT_TRANSLATION_AGENTS", "PEAK_HOURS_AGENT_PREFERENCE"):
        if key == "AI_COMMON_AGENTS" and not value.strip():
            raise ValueError(f"{key} は空にできません")
        if not value.strip():
            # empty means inherit
            return
        parts = [p.strip() for p in value.split(",")]
        if any(not p for p in parts):
            raise ValueError(f"{key} に空の要素が含まれます")
        for p in parts:
            if not AGENT_RE.match(p):
                raise ValueError(f"{key} に不正なエージェント {p!r} が含まれます")
        return
    if key == "PEAK_HOURS_PRIORITY_AGENT":
        if not value.strip():
            return
        if not AGENT_RE.match(value.strip()):
            raise ValueError(f"{key} が不正です: {value!r}")
        return
    if key == "AI_BACKOFF_SEC_ITEMS":
        if not value.strip():
            return
        items = value.strip().split()
        for item in items:
            if ":" not in item:
                raise ValueError(f"{key} の要素 {item!r} は name:sec 形式である必要があります")
            name, sec = item.split(":", 1)
            if not BACKOFF_NAME_RE.match(name):
                raise ValueError(f"{key} のモデル名 {name!r} が不正です")
            if not sec.isdigit():
                raise ValueError(f"{key} の秒数 {sec!r} は整数である必要があります")
            if int(sec) < 60:
                raise ValueError(f"{key} の秒数は60以上である必要があります")
        return
    if key == "AI_AGENT_BACKOFF_SEC":
        if not value.strip().isdigit():
            raise ValueError(f"{key} は整数である必要があります")
        if int(value.strip()) < 60:
            raise ValueError(f"{key} は60以上である必要があります")
        return
    if key == "AI_BACKOFF_FAILURE_SEC":
        # レート制限以外の一過性障害用の短バックオフ (既定 300s)。短すぎる値は
        # リトライの連打になるため 30 秒以上に制限する (config.sh は 1 以上を許容)。
        if not value.strip().isdigit():
            raise ValueError(f"{key} は整数である必要があります")
        if int(value.strip()) < 30:
            raise ValueError(f"{key} は30以上である必要があります")
        return
    if key == "PEAK_HOURS_WINDOWS":
        if not value.strip():
            return
        parts = [p.strip() for p in value.split(",") if p.strip()]
        for part in parts:
            if "-" not in part:
                raise ValueError(f"{key} の要素 {part!r} は START-END 形式である必要があります")
            a, b = part.split("-", 1)
            if not _is_valid_peak_time(a.strip()) or not _is_valid_peak_time(b.strip()):
                raise ValueError(f"{key} の時刻 {part!r} が不正です (HH/HHMM/HH:MM)")
        return
    if key in ("PEAK_HOURS_AGENT_SWAP_ENABLED", "PEAK_HOURS_QUEUE_GATE_ENABLED"):
        # ランタイム (core/helpers.sh) は "1" のみ有効と判定する
        if value.strip() not in ("0", "1"):
            raise ValueError(f"{key} は 0 または 1 である必要があります")
        return
    if key == "PEAK_HOURS_TZ":
        if not TZ_RE.match(value.strip()):
            raise ValueError(f"{key} は IANA タイムゾーン名 (例: Asia/Tokyo) である必要があります")
        return
    raise ValueError(f"未知のキーです: {key}")


def _sanitize_overlay_text(s: str, limit: int) -> str:
    if not isinstance(s, str):
        raise ValueError("文字列である必要があります")
    # forbid control chars
    if any(ord(c) < 32 and c not in ("\n", "\r", "\t") for c in s):
        raise ValueError("制御文字は使用できません")
    # for overlay events, disallow newlines in title, allow in body? Keep simple: strip
    s = s.strip()
    if len(s) > limit:
        raise ValueError(f"{limit}文字以内である必要があります")
    return s


def _validate_overlay_event(ev: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ev, dict):
        raise ValueError("eventはオブジェクトである必要があります")
    cat = str(ev.get("category", "")).strip()
    if cat not in OVERLAY_CATEGORIES:
        raise ValueError(f"categoryは {sorted(OVERLAY_CATEGORIES)} のいずれかである必要があります")
    title = _sanitize_overlay_text(str(ev.get("title", "")), OVERLAY_TITLE_LIMIT)
    if not title:
        raise ValueError("titleは必須です")
    body = _sanitize_overlay_text(str(ev.get("body", "")), OVERLAY_BODY_LIMIT)
    level = str(ev.get("level", "info")).strip() or "info"
    if level not in ("info", "warn", "error"):
        level = "info"
    ts = ev.get("ts")
    try:
        ts_int = int(ts) if ts is not None else int(time.time())
    except Exception:
        ts_int = int(time.time())
    now = int(time.time())
    # allow ts within 7 days past to 60s future
    if ts_int > now + 60 or ts_int < now - 7 * 86400:
        # clamp to now if out of range
        ts_int = now
    return {"ts": ts_int, "category": cat, "title": title, "body": body, "level": level}


def _effective_token(g: GlobalConfig) -> str:
    env_name = g.webui.token_env.strip() or "DOCICH_WEBUI_TOKEN"
    env_val = os.environ.get(env_name, "")
    if env_val and env_val.strip():
        return env_val.strip()
    return (g.webui.token or "").strip()


def _backoff_dir(soren_root: Path) -> Path:
    env = os.environ.get("AI_BACKOFF_DIR")
    if env:
        return Path(env)
    if os.environ.get("ELOOP_LIB_DIR"):
        return Path(os.environ["ELOOP_LIB_DIR"]) / "tmp/state/ai_backoff"
    return soren_root / "tmp/state/ai_backoff"


def _stats_dir(soren_root: Path) -> Path:
    env = os.environ.get("AI_STATS_DIR")
    if env:
        return Path(env)
    if os.environ.get("ELOOP_LIB_DIR"):
        return Path(os.environ["ELOOP_LIB_DIR"]) / "tmp/state/ai_stats"
    return soren_root / "tmp/state/ai_stats"


def _peak_hours_to_minutes_py(token: str) -> int | None:
    token = token.strip()
    if not token:
        return None
    if ":" in token:
        parts = token.split(":")
        if len(parts) != 2:
            return None
        h_str, m_str = parts[0].strip(), parts[1].strip()
        if not h_str.isdigit() or not m_str.isdigit():
            return None
        h, m = int(h_str), int(m_str)
    elif token.isdigit() and len(token) in (3, 4):
        if len(token) == 3:
            h = int(token[0])
            m = int(token[1:])
        else:
            h = int(token[:2])
            m = int(token[2:])
    elif token.isdigit():
        h = int(token)
        m = 0
    else:
        return None
    if h == 24 and m == 0:
        return 1440
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def _is_peak_at(now_min: int, windows: str) -> bool:
    if not windows or not windows.strip():
        return False
    for win in windows.split(","):
        win = win.strip()
        if not win or "-" not in win:
            continue
        a, b = win.split("-", 1)
        a = a.strip()
        b = b.strip()
        start = _peak_hours_to_minutes_py(a)
        end = _peak_hours_to_minutes_py(b)
        if start is None or end is None:
            continue
        if start == end:
            continue
        if start < end:
            if start <= now_min < end:
                return True
        else:
            if now_min >= start or now_min < end:
                return True
    return False


def _current_minutes_in_tz(tz: str) -> int | None:
    tz = (tz or "").strip() or "Asia/Tokyo"
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.datetime.now(ZoneInfo(tz))
        return dt.hour * 60 + dt.minute
    except Exception:
        pass
    try:
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = tz
        try:
            time.tzset()  # type: ignore[attr-defined]
        except Exception:
            pass
        lt = time.localtime()
        mins = lt.tm_hour * 60 + lt.tm_min
        if old_tz is not None:
            os.environ["TZ"] = old_tz
        else:
            os.environ.pop("TZ", None)
        try:
            time.tzset()  # type: ignore[attr-defined]
        except Exception:
            pass
        return mins
    except Exception:
        return None


def _get_peak_status(soren_root: Path) -> dict[str, Any]:
    dotenv = _read_dotenv_dict(soren_root)
    windows = _effective_value("PEAK_HOURS_WINDOWS", dotenv)
    tz = _effective_value("PEAK_HOURS_TZ", dotenv)
    swap = _effective_value("PEAK_HOURS_AGENT_SWAP_ENABLED", dotenv)
    gate = _effective_value("PEAK_HOURS_QUEUE_GATE_ENABLED", dotenv)
    pref = _effective_value("PEAK_HOURS_AGENT_PREFERENCE", dotenv)
    prio = _effective_value("PEAK_HOURS_PRIORITY_AGENT", dotenv)
    now_min = _current_minutes_in_tz(tz)
    is_peak = False
    if now_min is not None:
        try:
            is_peak = _is_peak_at(now_min, windows)
        except Exception:
            is_peak = False
    now_str = ""
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.datetime.now(ZoneInfo(tz))
        now_str = dt.strftime("%H:%M")
    except Exception:
        try:
            lt = time.localtime()
            now_str = f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
        except Exception:
            now_str = ""
    return {
        "windows": windows,
        "tz": tz,
        "swap_enabled": swap,
        "gate_enabled": gate,
        "preference": pref,
        "priority_agent": prio,
        "is_peak_now": is_peak,
        "now_minutes": now_min,
        "now_str": now_str,
    }


def _overlay_events_path(soren_root: Path) -> Path:
    raw = os.environ.get("EVENT_OVERLAY_EVENTS_FILE", "")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (soren_root / p)
    return soren_root / "tmp/state/overlay_events.jsonl"


def _overlay_html_path(soren_root: Path) -> Path:
    raw = os.environ.get("EVENT_OVERLAY_HTML_FILE", "")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (soren_root / p)
    return soren_root / "tmp/state/event_overlay.html"


def _work_indicator_path(soren_root: Path) -> Path:
    raw = os.environ.get("CODEX_WORK_OVERLAY_STATE_FILE", "")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (soren_root / p)
    return soren_root / "tmp/state/codex_work_indicator.json"


def _comment_gen_state_path(soren_root: Path) -> Path:
    raw = os.environ.get("COMMENT_GEN_STATE_FILE", "")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (soren_root / p)
    return soren_root / "tmp/state/.comment_gen_state"


def _radio_state_path(soren_root: Path) -> Path:
    raw = os.environ.get("RADIO_STATE_FILE", "")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (soren_root / p)
    return soren_root / "tmp/state/.radio_state"


def _wildcard_status_path(soren_root: Path) -> Path:
    raw = os.environ.get("WILDCARD_PARALLEL_STATUS_FILE", "")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (soren_root / p)
    return soren_root / "tmp/state/wildcard_parallel_status.json"


def _top_override_path(soren_root: Path) -> Path:
    raw = os.environ.get("BROADCAST_TOP_OVERRIDE_FILE", "")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (soren_root / p)
    return soren_root / "tmp/state/broadcast_top_override.json"


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _load_work_indicator(soren_root: Path) -> dict[str, Any] | None:
    p = _work_indicator_path(soren_root)
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if not txt.strip():
            return None
        data = json.loads(txt)
        if not isinstance(data, dict) or not data.get("active"):
            return None
        return data
    except Exception:
        return None


def _load_overlay_events(soren_root: Path, keep: int | None = None) -> list[dict[str, Any]]:
    p = _overlay_events_path(soren_root)
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except FileNotFoundError:
        return []
    except Exception:
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-keep:] if keep else lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            events.append(item)
    if keep:
        return events[-keep:]
    return events


def _load_top_override(soren_root: Path) -> dict[str, Any] | None:
    p = _top_override_path(soren_root)
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if not txt.strip():
            return None
        data = json.loads(txt)
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def _get_overlay_keep_visible(soren_root: Path) -> tuple[int, int]:
    keep = 180
    visible = 18
    try:
        keep = int(os.environ.get("EVENT_OVERLAY_KEEP_EVENTS", "180") or "180")
    except Exception:
        keep = 180
    try:
        visible = int(os.environ.get("EVENT_OVERLAY_VISIBLE_SEC", "18") or "18")
    except Exception:
        visible = 18
    # dotenv may have different values; check .env as well
    try:
        dotenv = _read_dotenv_dict(soren_root)
        if "EVENT_OVERLAY_KEEP_EVENTS" in dotenv and dotenv["EVENT_OVERLAY_KEEP_EVENTS"].strip().isdigit():
            keep = int(dotenv["EVENT_OVERLAY_KEEP_EVENTS"].strip())
        if "EVENT_OVERLAY_VISIBLE_SEC" in dotenv and dotenv["EVENT_OVERLAY_VISIBLE_SEC"].strip().isdigit():
            visible = int(dotenv["EVENT_OVERLAY_VISIBLE_SEC"].strip())
    except Exception:
        pass
    return max(1, keep), max(1, visible)


def _get_gen_indicators(soren_root: Path, now: int | None = None) -> list[dict[str, Any]]:
    if now is None:
        now = int(time.time())
    indicators: list[dict[str, Any]] = []
    # comment
    try:
        c_path = _comment_gen_state_path(soren_root)
        stale = 90
        try:
            stale = int(os.environ.get("EVENT_OVERLAY_COMMENT_GEN_STALE_SEC", "90") or "90")
        except Exception:
            stale = 90
        try:
            line = c_path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            line = ""
        if line.startswith("generating:"):
            parts = line.split(":")
            ts = 0
            if len(parts) >= 3 and parts[-1].isdigit():
                try:
                    ts = int(parts[-1])
                except Exception:
                    ts = 0
            if ts <= 0:
                try:
                    ts = int(c_path.stat().st_mtime)
                except Exception:
                    ts = now
            age = now - ts
            fresh = 0 <= age <= stale
            indicators.append({
                "key": "comment",
                "icon": "💬",
                "label": "コメント生成中",
                "ts": ts,
                "age": age,
                "fresh": fresh,
                "stale_sec": stale,
                "raw": line,
            })
    except Exception:
        pass
    # radio
    try:
        r_path = _radio_state_path(soren_root)
        alive_stale = 600
        dead_stale = 20
        try:
            alive_stale = int(os.environ.get("EVENT_OVERLAY_RADIO_GEN_STALE_SEC", "600") or "600")
        except Exception:
            alive_stale = 600
        try:
            dead_stale = int(os.environ.get("EVENT_OVERLAY_RADIO_GEN_DEAD_STALE_SEC", "20") or "20")
        except Exception:
            dead_stale = 20
        # also try RADIO_STATE_STALE_SEC as fallback
        try:
            alt = os.environ.get("RADIO_STATE_STALE_SEC")
            if alt and alt.strip().isdigit():
                alive_stale = int(alt.strip())
        except Exception:
            pass
        try:
            line = r_path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            line = ""
        if line:
            fields = line.split(":")
            mode = fields[0] if fields else ""
            corner = fields[1] if len(fields) > 1 else ""
            ts = int(fields[2]) if len(fields) > 2 and fields[2].isdigit() else 0
            owner_pid = int(fields[3]) if len(fields) > 3 and fields[3].isdigit() else 0
            if ts <= 0:
                try:
                    ts = int(r_path.stat().st_mtime)
                except Exception:
                    ts = now
            age = now - ts
            if mode in ("generating", "verifying"):
                alive = bool(owner_pid) and _is_pid_alive(owner_pid)
                window = alive_stale if alive else dead_stale
                fresh = 0 <= age <= window
                label = "ラジオ生成中" if mode == "generating" else "ラジオ検証中"
                if corner:
                    label = f"{label} ({corner})"
                indicators.append({
                    "key": "radio",
                    "icon": "📻",
                    "label": label,
                    "mode": mode,
                    "corner": corner,
                    "ts": ts,
                    "age": age,
                    "fresh": fresh,
                    "stale_sec": window,
                    "owner_pid": owner_pid,
                    "owner_alive": alive,
                    "raw": line,
                })
    except Exception:
        pass
    return indicators


def _get_wildcard_status(soren_root: Path) -> dict[str, Any] | None:
    p = _wildcard_status_path(soren_root)
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if not txt.strip():
            return None
        data = json.loads(txt)
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def _atomic_overlay_write(soren_root: Path, rel_path: Path, data: str, mode: int = 0o644) -> None:
    # rel_path is absolute path already; use its parent
    parent = rel_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    lock_dir = soren_root / "tmp/state/.webui_overlay.lock"
    try:
        lock_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        try:
            age = time.time() - lock_dir.stat().st_mtime
            if age > 10:
                import shutil
                shutil.rmtree(lock_dir, ignore_errors=True)
                lock_dir.mkdir(parents=True, exist_ok=False)
            else:
                raise FileExistsError(f"another overlay edit in progress (age {int(age)}s)")
        except FileExistsError:
            raise
    try:
        fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".overlay.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            Path(tmp).chmod(mode)
            os.replace(tmp, str(rel_path))
        finally:
            try:
                if Path(tmp).exists():
                    Path(tmp).unlink()
            except Exception:
                pass
    finally:
        try:
            lock_dir.rmdir()
        except Exception:
            try:
                import shutil
                shutil.rmtree(lock_dir, ignore_errors=True)
            except Exception:
                pass


def _regenerate_event_overlay(soren_root: Path) -> bool:
    # Best-effort regeneration via generate_event_overlay.py
    try:
        gen_py = soren_root / "generate_event_overlay.py"
        if not gen_py.is_file():
            # try repo root
            cand = Path(__file__).resolve().parents[2] / "games/soviet_now/generate_event_overlay.py"
            if cand.is_file():
                gen_py = cand
            else:
                return False
        events = _overlay_events_path(soren_root)
        html = _overlay_html_path(soren_root)
        work = _work_indicator_path(soren_root)
        keep, visible = _get_overlay_keep_visible(soren_root)
        env = os.environ.copy()
        env["EVENT_OVERLAY_STATE_BASE"] = str(soren_root)
        # ensure COMMENT_GEN_STATE and RADIO_STATE are set for generator
        if "EVENT_OVERLAY_COMMENT_GEN_STATE" not in env:
            env["EVENT_OVERLAY_COMMENT_GEN_STATE"] = str(_comment_gen_state_path(soren_root))
        if "EVENT_OVERLAY_RADIO_STATE" not in env:
            env["EVENT_OVERLAY_RADIO_STATE"] = str(_radio_state_path(soren_root))
        # run generator
        subprocess.run(
            ["python3", str(gen_py), str(events), str(html), str(keep), str(visible), str(work)],
            cwd=str(soren_root),
            env=env,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _game_state_path(soren_root: Path) -> Path:
    return soren_root / "game_state.json"


def _improve_state_path(soren_root: Path) -> Path:
    return soren_root / "tmp/state/improve_state.json"


def _improve_lock_path(soren_root: Path) -> Path:
    return soren_root / "tmp/improve.lock"


def _load_json_file(path: Path) -> Any | None:
    try:
        if not path.is_file():
            return None
        txt = path.read_text(encoding="utf-8", errors="ignore")
        if not txt.strip():
            return None
        return json.loads(txt)
    except Exception:
        return None


def _get_workers_status(soren_root: Path) -> list[dict[str, Any]]:
    workers = [
        "radio_worker",
        "chat_worker",
        "improve_daemon",
        "audio_worker",
        "prediction_worker",
        "youtube_worker",
    ]
    results: list[dict[str, Any]] = []
    for w in workers:
        pid = _find_worker_pid(soren_root, w)
        if w == "improve_daemon" and pid is None:
            try:
                pf = soren_root / "tmp/state/improve_daemon.pid"
                if pf.is_file():
                    raw = pf.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
                    cand = int(raw.strip())
                    try:
                        os.kill(cand, 0)
                        pid = cand
                    except ProcessLookupError:
                        pid = None
                    except PermissionError:
                        pid = cand
            except Exception:
                pid = None
        alive = pid is not None
        results.append({"worker": w, "pid": pid, "alive": alive, "status": "ok" if alive else "not_running"})
    try:
        sdir = soren_root / "tmp/state"
        if sdir.is_dir():
            for p in sdir.glob("*.pid"):
                name = p.stem
                if any(r["worker"] == name for r in results):
                    continue
                pid = None
                try:
                    raw = p.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
                    cand = int(raw.strip())
                    try:
                        os.kill(cand, 0)
                        pid = cand
                    except ProcessLookupError:
                        pid = None
                    except PermissionError:
                        pid = cand
                except Exception:
                    pid = None
                results.append({"worker": name, "pid": pid, "alive": pid is not None, "status": "ok" if pid is not None else "not_running"})
    except Exception:
        pass
    return results


def _find_worker_pid(soren_root: Path, worker: str) -> int | None:
    # worker e.g. "radio_worker" -> pid file tmp/state/radio_worker.pid
    pid_file = soren_root / "tmp/state" / f"{worker}.pid"
    try:
        raw = pid_file.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
        pid = int(raw.strip())
        # check alive
        try:
            os.kill(pid, 0)
            return pid
        except ProcessLookupError:
            return None
        except PermissionError:
            return pid
    except Exception:
        return None


def _send_reload(soren_root: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for worker in ("radio_worker", "chat_worker"):
        pid = _find_worker_pid(soren_root, worker)
        if pid is None:
            results.append({"worker": worker, "pid": None, "status": "not_running"})
            continue
        try:
            os.kill(pid, signal.SIGUSR1)
            results.append({"worker": worker, "pid": pid, "status": "ok"})
        except Exception as exc:
            results.append({"worker": worker, "pid": pid, "status": f"error: {exc}"})
    # log to tmp/state/webui_reload.json
    try:
        out = soren_root / "tmp/state/webui_reload.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": int(time.time()), "results": results}
        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return results


def _log_request(soren_root: Path, method: str, path: str, status: int, latency_ms: int, extra: str = "") -> None:
    try:
        log_file = soren_root / "tmp/debug/webui.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": int(time.time()),
            "method": method,
            "path": path,
            "status": status,
            "latency_ms": latency_ms,
            "extra": extra,
        }
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# --- HTTP handler --------------------------------------------------------

MAX_BODY_BYTES = 64 * 1024

INDEX_HTML = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>docich webui — Soren AI control</title>
<style>
:root{--bg:#0f1115;--card:#1a1e26;--border:#2a3040;--text:#e6e8ee;--muted:#9aa3b2;--accent:#6ea8fe;--ok:#7bd88f;--warn:#ffcc66;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
header{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header h1{font-size:18px;margin:0;font-weight:600}
header .sub{color:var(--muted);font-size:13px}
header .env{margin-left:auto;font-size:13px;color:var(--muted)}
nav{display:flex;gap:6px;padding:10px 12px;border-bottom:1px solid var(--border);overflow:auto}
nav button{padding:8px 14px;border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:8px;cursor:pointer;white-space:nowrap}
nav button.active{background:var(--accent);color:#0a0c10;border-color:transparent;font-weight:600}
main{padding:16px;max-width:1150px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:14px}
.card h2{margin:0 0 8px 0;font-size:15px}
.card h3{margin:0 0 6px 0;font-size:14px}
.card p.desc{margin:0 0 10px 0;color:var(--muted);font-size:13px;line-height:1.5}
label{font-size:13px;color:var(--muted);display:block;margin-bottom:6px}
textarea,input,select{width:100%;padding:10px 12px;border-radius:8px;border:1px solid var(--border);background:#111319;color:var(--text);font-size:14px}
textarea{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;resize:vertical}
.row{display:flex;gap:10px;flex-wrap:wrap}
.row>div{flex:1;min-width:240px}
.actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.btn{padding:9px 14px;border-radius:8px;border:1px solid var(--border);background:#222836;color:var(--text);cursor:pointer;font-size:14px}
.btn.primary{background:var(--accent);color:#0a0c10;border-color:transparent;font-weight:600}
.btn.danger{background:#3a1f26;border-color:#6b2a36;color:#ffb3c0}
.btn:disabled{opacity:0.5;cursor:not-allowed}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;border:1px solid var(--border);color:var(--muted)}
.badge.ok{color:var(--ok);border-color:rgba(123,216,143,0.3);background:rgba(123,216,143,0.1)}
.badge.warn{color:var(--warn);border-color:rgba(255,204,102,0.3);background:rgba(255,204,102,0.1)}
.badge.bad{color:var(--bad);border-color:rgba(255,107,107,0.3);background:rgba(255,107,107,0.1)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left}
th{color:var(--muted);font-weight:600}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.kv{display:grid;grid-template-columns:140px 1fr;gap:6px 12px;font-size:13px}
.kv dt{color:var(--muted)}
.kv dd{margin:0;word-break:break-all}
.toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:#222836;border:1px solid var(--border);color:var(--text);padding:10px 14px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,0.4);display:none;max-width:90vw;z-index:50}
canvas{width:100%;height:220px;background:#111319;border:1px solid var(--border);border-radius:8px}
.help{font-size:12px;color:var(--muted);margin-top:6px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.kpi .val{font-size:22px;font-weight:700;margin:6px 0}
.kpi .subk{font-size:12px;color:var(--muted)}
.sparkline{width:100%;height:80px;background:#111319;border:1px solid var(--border);border-radius:8px}
.bar{height:8px;background:#2a3040;border-radius:999px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--accent);transition:width .3s}
.chip{display:inline-block;padding:6px 10px;border:1px solid var(--border);background:#111319;border-radius:999px;font-size:12px;margin:4px 4px 0 0;cursor:pointer}
.chip:hover{border-color:var(--accent);background:#1a2333}
.ordered{list-style:none;padding:0;margin:8px 0}
.ordered li{display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--border);background:#111319;border-radius:8px;margin-bottom:6px}
.ordered li.inherited{opacity:0.55;border-style:dashed}
.ordered li.dragging{opacity:0.5}
.drag{cursor:grab;padding:2px 6px;color:var(--muted);font-size:14px;user-select:none}
.timeline{display:grid;grid-template-columns:repeat(12,1fr);gap:6px}
@media(max-width:640px){.timeline{grid-template-columns:repeat(6,1fr)}}
.tl{padding:8px 2px;border:1px solid var(--border);border-radius:8px;text-align:center;font-size:12px;background:#111319;cursor:pointer;user-select:none}
.tl.active{background:var(--accent);color:#0a0c10;border-color:transparent;font-weight:600}
.tl input{display:none}
.switch{position:relative;display:inline-block;width:44px;height:24px;vertical-align:middle}
.switch input{opacity:0;width:0;height:0}
.slider{position:absolute;inset:0;background:#2a3040;border-radius:999px;transition:.2s}
.slider:before{content:"";position:absolute;height:18px;width:18px;left:3px;bottom:3px;background:white;border-radius:50%;transition:.2s}
input:checked+.slider{background:var(--accent)}
input:checked+.slider:before{transform:translateX(20px)}
.range{width:100%}
.backoff-card{border:1px solid var(--border);background:#111319;border-radius:10px;padding:12px;margin-bottom:10px}
.backoff-card .head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.preset-btn{padding:6px 10px;border:1px solid var(--border);background:#222836;color:var(--text);border-radius:999px;font-size:12px;margin:2px;cursor:pointer}
.preset-btn:hover{border-color:var(--accent)}
.inherit-row{display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:13px;color:var(--muted)}
</style>
</head>
<body>
<header>
<h1>docich webui</h1>
<div class="sub">Soren モデルチェーン / バックオフ / ピーク帯</div>
<div class="env"><span id="env-mtime"></span> <span class="badge" id="health-badge">...</span> <span id="soren-root" class="mono" style="color:var(--muted);font-size:12px"></span></div>
</header>
<nav id="tabs">
<button data-tab="dashboard" class="active">Dashboard</button>
<button data-tab="status">Status</button>
<button data-tab="overlay">Overlay</button>
<button data-tab="chains">Chains</button>
<button data-tab="backoff">Backoff</button>
<button data-tab="peak">Peak</button>
<button data-tab="stats">Stats</button>
<button data-tab="health">Health</button>
</nav>
<main>
<!-- DASHBOARD -->
<section id="tab-dashboard">
<div class="grid2">
<div class="card kpi"><h3>Game</h3><div class="val" id="kpi-game">-</div><div class="subk" id="kpi-game-sub">-</div></div>
<div class="card kpi"><h3>Improve</h3><div class="val" id="kpi-improve">-</div><div class="subk" id="kpi-improve-sub">-</div></div>
<div class="card kpi"><h3>Workers</h3><div class="val" id="kpi-workers">-</div><div class="subk" id="kpi-workers-sub">-</div></div>
<div class="card kpi"><h3>Peak</h3><div class="val" id="kpi-peak">-</div><div class="subk" id="kpi-peak-sub">-</div></div>
</div>
<div class="card"><h2>7日トレンド (sparkline)</h2><p class="desc">直近7日の winner/attempt。SVG sparkline。</p><svg id="dash-sparkline" class="sparkline" viewBox="0 0 400 80" preserveAspectRatio="none"></svg><div class="help" id="dash-spark-help"></div></div>
<div class="row">
<div class="card" style="flex:1"><h2>Backoff 残り</h2><p class="desc">アクティブなバックオフの残り時間バー。</p><div id="dash-backoff-bars"></div></div>
<div class="card" style="flex:1"><h2>Top 3 Agents</h2><p class="desc">直近7日の winner 上位。</p><table><thead><tr><th>agent</th><th>winner</th></tr></thead><tbody id="dash-top3"></tbody></table></div>
</div>
<div class="card"><h2>Workers</h2><div style="overflow:auto"><table><thead><tr><th>worker</th><th>pid</th><th>status</th></tr></thead><tbody id="dash-workers"></tbody></table></div></div>
</section>
<!-- STATUS -->
<section id="tab-status" style="display:none">
<div class="card"><h2>生成ステータス</h2><p class="desc">コメント/ラジオ生成中、improve/wildcard、game状態を統合表示。10秒ごとに自動更新。</p>
<div class="grid2">
<div class="card kpi"><h3>コメント</h3><div class="val" id="status-comment">-</div><div class="subk mono" id="status-comment-sub">-</div></div>
<div class="card kpi"><h3>ラジオ</h3><div class="val" id="status-radio">-</div><div class="subk mono" id="status-radio-sub">-</div></div>
<div class="card kpi"><h3>Improve</h3><div class="val" id="status-improve">-</div><div class="subk mono" id="status-improve-sub">-</div></div>
<div class="card kpi"><h3>Wildcard</h3><div class="val" id="status-wildcard">-</div><div class="subk mono" id="status-wildcard-sub">-</div></div>
</div>
<div class="card"><h3>生成中インジケータ</h3><div id="status-generators"></div><div class="help">source: tmp/state/.comment_gen_state / .radio_state（フレッシュ判定は comment 90s / radio 600s alive, 20s dead）</div></div>
<div class="row">
<div class="card" style="flex:1"><h3>Game</h3><div class="kv" id="status-game-kv"></div></div>
<div class="card" style="flex:1"><h3>Improve 詳細</h3><div class="kv" id="status-improve-kv"></div><div class="help" style="margin-top:8px">log tail (直近6行)</div><pre id="status-improve-log" class="mono" style="white-space:pre-wrap;background:#111319;border:1px solid var(--border);border-radius:8px;padding:8px;max-height:160px;overflow:auto"></pre></div>
</div>
<div class="card"><h3>Workers 詳細</h3><div style="overflow:auto"><table><thead><tr><th>worker</th><th>pid</th><th>status</th></tr></thead><tbody id="status-workers"></tbody></table></div></div>
<div class="card"><h3>Game Count</h3><div class="kv"><dt>value</dt><dd id="status-game-count" class="mono">-</dd><dt>path</dt><dd id="status-game-count-path" class="mono">-</dd></div></div>
<div class="actions"><button class="btn" id="status-refresh">更新</button></div>
</div>
</section>
<!-- OVERLAY -->
<section id="tab-overlay" style="display:none">
<div class="card"><h2>Overlay 制御</h2><p class="desc">画面に表示される通知・上部サマリ・作業中バナーを編集。変更は <code>tmp/state/overlay_events.jsonl</code> / <code>codex_work_indicator.json</code> / <code>broadcast_top_override.json</code> へ原子書き込み → <code>generate_event_overlay.py</code> でHTML再生成。</p>
<div style="display:flex;gap:6px;margin-bottom:12px">
<button class="btn" data-overlay-sub="notifications" style="background:var(--accent);color:#0a0c10">通知</button>
<button class="btn" data-overlay-sub="work">作業中バナー</button>
<button class="btn" data-overlay-sub="top">上部</button>
<button class="btn" data-overlay-sub="preview">プレビュー</button>
</div>
<!-- Notifications sub -->
<div id="overlay-sub-notifications">
<div class="card"><h3>現在の通知キュー</h3><p class="desc">直近 <span id="overlay-events-keep"></span> 件を保持、画面では <span id="overlay-events-visible"></span> 秒表示。重複はカテゴリ別レベルで色分け。</p>
<div style="overflow:auto"><table><thead><tr><th>#</th><th>time</th><th>category</th><th>title</th><th>body</th><th></th></tr></thead><tbody id="overlay-events-table"></tbody></table></div>
<div class="actions"><button class="btn" id="overlay-events-refresh">更新</button><button class="btn danger" id="overlay-events-clear">全クリア</button></div>
</div>
<div class="card"><h3>通知を追加</h3>
<div class="row"><div><label>category</label><select id="overlay-notify-category"><option value="system">system</option><option value="game">game</option><option value="worker">worker</option><option value="chat">chat</option><option value="radio">radio</option><option value="prediction">prediction</option><option value="rollback">rollback</option></select></div>
<div><label>level</label><select id="overlay-notify-level"><option value="info">info</option><option value="warn">warn</option><option value="error">error</option></select></div></div>
<div style="margin-top:8px"><label>title (1-120)</label><input id="overlay-notify-title" maxlength="120" placeholder="タイトル"/></div>
<div style="margin-top:8px"><label>body (0-500)</label><textarea id="overlay-notify-body" rows="3" maxlength="500" placeholder="本文"></textarea></div>
<div class="actions"><button class="btn primary" id="overlay-notify-push">追加</button></div>
<div id="overlay-notify-msg" class="help"></div>
</div>
<div class="card"><h3>一括編集 (JSON配列)</h3><p class="desc">全キューをJSON配列で上書き。高度な編集用。</p>
<textarea id="overlay-events-bulk" rows="6" class="mono" placeholder='[{"category":"system","title":"...","body":"..."}]'></textarea>
<div class="actions"><button class="btn" id="overlay-events-bulk-save">一括保存</button><button class="btn" id="overlay-events-bulk-load">現在のJSONを読み込む</button></div>
<div id="overlay-events-bulk-msg" class="help"></div>
</div>
</div>
<!-- Work banner sub -->
<div id="overlay-sub-work" style="display:none">
<div class="card"><h3>作業中バナー状態</h3><div class="kv"><dt>active</dt><dd id="work-banner-active">-</dd><dt>title</dt><dd id="work-banner-title" class="mono">-</dd><dt>body</dt><dd id="work-banner-body" class="mono">-</dd><dt>ts</dt><dd id="work-banner-ts" class="mono">-</dd></div>
<div class="actions"><button class="btn" id="work-banner-refresh">更新</button></div>
</div>
<div class="card"><h3>作業中バナーを編集</h3><p class="desc">有効時は画面上部にオレンジの作業中バナー（`event_overlay.html` の `#work`）が4行サマリの代わりに表示される。`codex_work_indicator.sh start/stop` と同等。</p>
<div style="margin-bottom:8px"><label class="switch"><input type="checkbox" id="work-banner-enabled"><span class="slider"></span></label><span id="work-banner-enabled-label" class="badge" style="margin-left:8px">off</span></div>
<div><label>title (1-80)</label><input id="work-banner-title-input" maxlength="80" placeholder="システム自動分析・修正作業中"/></div>
<div style="margin-top:8px"><label>body (0-240)</label><textarea id="work-banner-body-input" rows="2" maxlength="240" placeholder="メリケンAI が確認・修正・検証を進めています"></textarea></div>
<div class="actions"><button class="btn primary" id="work-banner-save">保存</button><button class="btn danger" id="work-banner-disable">無効化</button></div>
<div id="work-banner-msg" class="help"></div>
</div>
</div>
<!-- Top sub -->
<div id="overlay-sub-top" style="display:none">
<div class="card"><h3>上部サマリ</h3><div class="kv"><dt>mode</dt><dd id="top-mode">-</dd><dt>enabled</dt><dd id="top-enabled">-</dd><dt>path</dt><dd id="top-path" class="mono">-</dd></div>
<div id="top-current-lines" class="help"></div>
<div class="actions"><button class="btn" id="top-refresh">更新</button><button class="btn danger" id="top-delete">自動に戻す</button></div>
</div>
<div class="card"><h3>上部を編集</h3><p class="desc">`top-rail` の4行サマリを手動上書き。`broadcast_top_override.json` に保存。未設定時は SOREN/OBS などの自動導出。</p>
<div style="margin-bottom:8px">
<label><input type="radio" name="top-mode" value="auto" checked> 自動（上書きなし）</label>
<label><input type="radio" name="top-mode" value="manual"> 手動 1-4行</label>
<label><input type="radio" name="top-mode" value="hidden"> 非表示</label>
</div>
<div id="top-manual-inputs">
<div><label>行1</label><input id="top-line-1" maxlength="120" placeholder="例: LIVE 720p30 | 43.9k games"/></div>
<div><label>行2</label><input id="top-line-2" maxlength="120"/></div>
<div><label>行3</label><input id="top-line-3" maxlength="120"/></div>
<div><label>行4</label><input id="top-line-4" maxlength="120"/></div>
</div>
<div class="actions"><button class="btn primary" id="top-save">保存</button></div>
<div id="top-msg" class="help"></div>
</div>
</div>
<!-- Preview sub -->
<div id="overlay-sub-preview" style="display:none">
<div class="card"><h3>プレビュー</h3><p class="desc">生成されたHTMLのプレビュー。eventはトースト/work/banner、broadcastはtop/bottom rail。</p>
<div class="row"><div><label>type</label><select id="preview-type"><option value="event">event</option><option value="broadcast">broadcast</option><option value="status">status</option><option value="improve">improve</option></select></div>
<div><label>region (broadcastのみ)</label><select id="preview-region"><option value="full">full</option><option value="top">top</option><option value="bottom">bottom</option><option value="sidebar">sidebar</option></select></div>
<div style="align-self:end"><button class="btn" id="preview-refresh">更新</button></div></div>
<div style="margin-top:10px"><label>HTML (readonly)</label><textarea id="preview-html" rows="12" class="mono" readonly></textarea></div>
<div style="margin-top:8px"><iframe id="preview-iframe" style="width:100%;height:260px;border:1px solid var(--border);border-radius:8px;background:#111319"></iframe></div>
</div>
</div>
</div>
</section>
<!-- CHAINS -->
<section id="tab-chains" style="display:none">
<div class="card"><h2>モデルチェーン</h2><p class="desc">カンマ区切りで優先度順。先頭が最優先で失敗時に次へフォールバック（lib/ai_generate.sh）。<code>AI_COMMON_AGENTS</code> が原典で、他は空ならそれを継承します。変更は .env へ原子書き込み → 10秒以内に hot-reload。</p>
<div id="chains-container"></div>
<div class="actions"><button class="btn primary" id="chains-save">保存</button><button class="btn" id="chains-reload">再読込</button></div>
<div class="help">保存後に radio/chat の reload を自動試行します（PIDファイル経由 USR1）。</div>
<div id="chains-msg" class="help"></div>
</div>
</section>
<!-- BACKOFF -->
<section id="tab-backoff" style="display:none">
<div class="card"><h2>モデル別バックオフ設定</h2><p class="desc"><code>AI_BACKOFF_SEC_ITEMS</code> は "model:sec" を空白区切り。例: <code>deepseek-v4-flash-free:86400 local:1800</code>。レート制限(429)はモデル別の長バックオフ、<code>AI_BACKOFF_FAILURE_SEC</code> は一過性のプロバイダ/CLI失敗に使う短いバックオフ。</p>
<div id="backoff-presets" style="margin-bottom:10px"><label>クイックプリセット (全モデル一括)</label>
<button class="preset-btn" data-bpreset="1800">短 30分</button>
<button class="preset-btn" data-bpreset="3600">1時間</button>
<button class="preset-btn" data-bpreset="18000">5時間</button>
<button class="preset-btn" data-bpreset="86400">24時間</button>
</div>
<div id="backoff-cards"></div>
<div class="row"><div><label>AI_AGENT_BACKOFF_SEC (既定) <span id="backoff-default-val" class="badge"></span></label><input type="range" min="60" max="3600" step="60" id="backoff-default" class="range"/><input id="backoff-default-num" placeholder="600"/></div>
<div><label>AI_BACKOFF_FAILURE_SEC (一過性障害) <span id="backoff-failure-val" class="badge"></span></label><input type="range" min="30" max="3600" step="30" id="backoff-failure" class="range"/><input id="backoff-failure-num" placeholder="300"/></div></div>
<div class="actions"><button class="btn primary" id="backoff-save">保存</button><button class="btn" id="backoff-reload">再読込</button></div>
<div id="backoff-msg" class="help"></div>
</div>
<div class="card"><h2>アクティブ backoff</h2><p class="desc">失敗で作成される <code>tmp/state/ai_backoff/&lt;sanitized&gt;</code> の残り時間。クリアで即時再試行可能。</p>
<div style="overflow:auto"><table><thead><tr><th>agent</th><th>sanitized</th><th>until</th><th>remaining</th><th>状態</th><th></th></tr></thead><tbody id="backoff-table"></tbody></table></div>
<div class="actions"><button class="btn" id="backoff-refresh">更新</button><button class="btn danger" id="backoff-clear-all">全クリア</button></div>
</div>
</section>
<!-- PEAK -->
<section id="tab-peak" style="display:none">
<div class="card"><h2>ピーク時間帯</h2><p class="desc">ピーク中は minimax 等を優先（PEAK_HOURS_AGENT_PREFERENCE）。WINDOWS は "10-13,15-19" のようにカンマ区切り、日跨ぎ "22-02" も可。チェックで hours を選択し、保存時に <code>start-end</code> (end exclusive) にマージされます。</p>
<div><label>PEAK_HOURS_WINDOWS (24h タイムライン)</label><div id="peak-timeline" class="timeline"></div><div class="help">クリックで選択。選択された時間は青。保存時に <span class="mono" id="peak-windows-preview"></span> にシリアライズ。</div></div>
<div class="row" style="margin-top:12px"><div><label>PEAK_HOURS_TZ</label><input id="peak-tz" list="tz-list" placeholder="Asia/Tokyo"/><datalist id="tz-list"><option value="Asia/Tokyo"><option value="UTC"><option value="Asia/Shanghai"><option value="America/New_York"><option value="Europe/London"><option value="Australia/Sydney"><option value="Asia/Seoul"><option value="Europe/Berlin"><option value="America/Los_Angeles"><option value="Asia/Singapore"></datalist></div>
<div><label>PEAK_HOURS_PRIORITY_AGENT</label><input id="peak-priority" placeholder="codex:minimax-m3"/></div></div>
<div style="margin-top:12px"><label>PEAK_HOURS_AGENT_PREFERENCE (ドラッグで順序変更)</label><div id="peak-pref-palette" style="margin-bottom:6px"></div><ul id="peak-pref-list" class="ordered"></ul><div class="row"><div style="flex:1"><input id="peak-pref-custom" placeholder="codex:xxx"/><div class="help">AGENT_RE で検証</div></div><div style="align-self:end"><button class="btn" id="peak-pref-add">追加</button></div></div></div>
<div class="row" style="margin-top:12px"><div><label>PEAK_HOURS_AGENT_SWAP_ENABLED</label><label class="switch"><input type="checkbox" id="peak-swap"><span class="slider"></span></label><span id="peak-swap-label" class="badge" style="margin-left:8px">1</span></div><div><label>PEAK_HOURS_QUEUE_GATE_ENABLED</label><label class="switch"><input type="checkbox" id="peak-gate"><span class="slider"></span></label><span id="peak-gate-label" class="badge" style="margin-left:8px">1</span></div></div>
<div class="card" style="margin-top:12px;background:#111319"><h3>現在ピーク判定</h3><div class="kv"><dt>is_peak_now</dt><dd id="peak-now">-</dd><dt>now</dt><dd id="peak-now-str">-</dd><dt>windows</dt><dd id="peak-now-windows" class="mono">-</dd></div></div>
<div class="actions"><button class="btn primary" id="peak-save">保存</button><button class="btn" id="peak-reload">再読込</button></div>
<div id="peak-msg" class="help"></div>
</div>
</section>
<!-- STATS -->
<section id="tab-stats" style="display:none">
<div class="card"><h2>AI 統計 (ai_stats)</h2><p class="desc"><code>tmp/state/ai_stats/&lt;YYYYMMDD&gt;.jsonl</code> の attempt/winner/fail。直近7日。</p>
<div class="actions" style="margin-bottom:8px"><button class="btn" id="stats-refresh">更新</button><label style="display:flex;gap:6px;align-items:center">days <input id="stats-days" type="number" value="7" min="1" max="30" style="width:80px"/></label></div>
<canvas id="stats-canvas" width="900" height="220"></canvas>
<div style="overflow:auto;margin-top:10px"><table><thead><tr><th>day</th><th>attempt</th><th>winner</th><th>fail</th><th>all_failed</th></tr></thead><tbody id="stats-table"></tbody></table></div>
<div style="overflow:auto;margin-top:10px"><table><thead><tr><th>agent</th><th>attempt</th><th>winner</th></tr></thead><tbody id="stats-agents"></tbody></table></div>
</div>
</section>
<!-- HEALTH -->
<section id="tab-health" style="display:none">
<div class="card"><h2>Health</h2><div class="kv" id="health-kv"></div><div class="actions"><button class="btn" id="health-refresh">更新</button><button class="btn" id="do-reload">radio/chat に reload送信 (USR1)</button></div><div id="health-msg" class="help"></div></div>
</section>
</main>
<div id="toast" class="toast"></div>
<script>
const $ = (s)=>document.querySelector(s);
const $$ = (s)=>[...document.querySelectorAll(s)];
let ENV_MTIME = 0;
let SOREN_ROOT = "";
let READ_ONLY = false;
let chainState = {};
let paletteSet = new Set();
let backoffState = {items:[], def:"600", fail:"300"};
let peakState = {hoursSet:new Set(), tz:"Asia/Tokyo", prefItems:[], swap:"1", gate:"1", priority:""};
let dashTimer = null;
const AGENT_RE = /^[A-Za-z0-9][A-Za-z0-9._:\/-]{0,127}$/;
const BACKOFF_NAME_RE = /^[A-Za-z0-9._\/-]+$/;
const CHAIN_PRESETS = {
  "Latency": "local,codex:deepseek-v4-flash,codex:minimax-m3",
  "Cost": "codex:deepseek-v4-flash-free,codex:amd-token-factory-deepseek-v4-flash,codex:openrouter/free,local,codex:deepseek-v4-flash,codex:minimax-m3",
  "Local": "local"
};
function toast(msg, ms=3000){
  const el = $("#toast");
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(el._t);
  el._t = setTimeout(()=>el.style.display="none", ms);
}
function fmtTime(ts){
  if(!ts) return "-";
  const d = new Date(ts*1000);
  return d.toLocaleString();
}
function esc(s){
  return String(s==null?"":s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
async function api(path, opts={}){
  const headers = opts.headers||{};
  if(!headers["Authorization"]){
    let t = sessionStorage.getItem("webui_token");
    if(!t){
      const m = location.search.match(/[?&]token=([^&]+)/);
      if(m){ t = decodeURIComponent(m[1]); sessionStorage.setItem("webui_token", t); }
    }
    if(t) headers["Authorization"] = "Bearer " + t;
  }
  opts.headers = headers;
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try{ data = JSON.parse(text);}catch(e){ data = text; }
  if(!res.ok){
    const detail = data && data.detail ? data.detail : (typeof data==="string"?data:JSON.stringify(data));
    throw new Error(`${res.status} ${path}: ${detail}`);
  }
  return data;
}
function chainKeys(){ return ["AI_COMMON_AGENTS","MODEL_IMPROVE_LIST","RADIO_AGENTS","RADIO_PREPASS_AGENTS","COMMENT_AGENTS","COMMENT_TRANSLATION_AGENTS"]; }
function fieldLabel(k){
  const m={AI_COMMON_AGENTS:"AI_COMMON_AGENTS (共通原典)",MODEL_IMPROVE_LIST:"MODEL_IMPROVE_LIST (改善)",RADIO_AGENTS:"RADIO_AGENTS",RADIO_PREPASS_AGENTS:"RADIO_PREPASS_AGENTS",COMMENT_AGENTS:"COMMENT_AGENTS",COMMENT_TRANSLATION_AGENTS:"COMMENT_TRANSLATION_AGENTS"};
  return m[k]||k;
}
function peakToMinutes(tok){
  tok=String(tok).trim();
  if(!tok) return null;
  if(tok.includes(":")){
    const p=tok.split(":");
    if(p.length!==2) return null;
    const h=parseInt(p[0].trim(),10), m=parseInt(p[1].trim(),10);
    if(isNaN(h)||isNaN(m)) return null;
    if(h===24&&m===0) return 1440;
    if(h<0||h>23||m<0||m>59) return null;
    return h*60+m;
  }
  if(/^\d{3,4}$/.test(tok)){
    let h,m;
    if(tok.length===3){h=parseInt(tok[0],10); m=parseInt(tok.slice(1),10);}
    else{h=parseInt(tok.slice(0,2),10); m=parseInt(tok.slice(2),10);}
    if(isNaN(h)||isNaN(m)) return null;
    if(h===24&&m===0) return 1440;
    if(h<0||h>23||m<0||m>59) return null;
    return h*60+m;
  }
  if(/^\d{1,2}$/.test(tok)){
    const h=parseInt(tok,10);
    if(isNaN(h)) return null;
    if(h===24) return 1440;
    if(h<0||h>23) return null;
    return h*60;
  }
  return null;
}
function windowsToHoursSet(windows){
  const set=new Set();
  if(!windows||!windows.trim()) return set;
  for(const win of windows.split(",")){
    const t=win.trim(); if(!t||!t.includes("-")) continue;
    const [a,b]=t.split("-",2);
    const sa=peakToMinutes(a.trim()), ea=peakToMinutes(b.trim());
    if(sa==null||ea==null) continue;
    const sh=Math.floor(sa/60), eh=Math.floor(ea/60);
    if(sh===eh) continue;
    if(sh<eh){ for(let h=sh;h<eh;h++) set.add(h%24); }
    else { for(let h=sh;h<24;h++) set.add(h); for(let h=0;h<eh;h++) set.add(h); }
  }
  return set;
}
function hoursSetToWindows(set){
  if(!set||set.size===0) return "";
  const hours=[...set].sort((a,b)=>a-b);
  let ranges=[];
  let start=hours[0], prev=hours[0];
  for(let i=1;i<hours.length;i++){
    const cur=hours[i];
    if(cur===prev+1){ prev=cur; }
    else { ranges.push([start,prev+1]); start=cur; prev=cur; }
  }
  ranges.push([start,prev+1]);
  if(ranges.length>=2 && ranges[0][0]===0 && ranges[ranges.length-1][1]===24){
    const last=ranges.pop(); const first=ranges.shift();
    ranges.unshift([last[0], first[1]]);
  }
  return ranges.map(([s,e])=>`${s}-${e}`).join(",");
}
function sanitizeAgent(agent){
  return String(agent||"").toLowerCase().replace(/[^a-z0-9._-]+/g,"_").replace(/^_+|_+$/g,"");
}
async function loadConfig(){
  const data = await api("/api/config");
  ENV_MTIME = data.env_mtime||0;
  SOREN_ROOT = data.soren_root||"";
  READ_ONLY = !!data.read_only;
  $("#env-mtime").textContent = `mtime=${ENV_MTIME} ${fmtTime(ENV_MTIME)}`;
  $("#soren-root").textContent = SOREN_ROOT;
  const entries = {};
  for(const e of data.entries) entries[e.key]=e;
  // build paletteSet from all chain defaults/effective
  paletteSet = new Set();
  for(const k of chainKeys()){
    const e=entries[k]; if(!e) continue;
    const srcs=[e.default, e.effective, e.value].filter(Boolean);
    for(const s of srcs) for(const p of s.split(",")){ const t=p.trim(); if(t) paletteSet.add(t); }
  }
  // also add backoff names? not needed
  renderChains(entries);
  renderBackoff(entries);
  renderPeak(entries);
  // health badge
  const hb = $("#health-badge");
  hb.textContent = READ_ONLY?"read-only":"read-write";
  hb.className = READ_ONLY?"badge warn":"badge ok";
  applyReadOnly();
  // also update peak now via peak_status
  try{ const ps=await api("/api/peak_status"); $("#peak-now").textContent=ps.is_peak_now?"ピーク中":"オフピーク"; $("#peak-now-str").textContent=ps.now_str||"-"; $("#peak-now-windows").textContent=ps.windows||"(なし)"; }catch(e){}
}
function applyReadOnly(){
  $$("main button").forEach(b=>{ b.disabled = READ_ONLY && b.id !== "backoff-refresh" && b.id !== "stats-refresh" && b.id !== "health-refresh" && b.id !== "chains-reload" && b.id !== "backoff-reload" && b.id !== "peak-reload"; });
  $$("main input, main textarea, main select").forEach(el=>{ if(READ_ONLY) el.disabled=true; else el.disabled=false; });
  // disable chain inherit etc will be handled in render
}
function renderChains(entries){
  const cont=$("#chains-container");
  cont.innerHTML="";
  for(const k of chainKeys()){
    const e=entries[k]||{value:"",effective:"",in_env:false,default:""};
    const inherited = !e.value;
    const src = inherited? e.effective : e.value;
    const items = src? src.split(",").map(s=>s.trim()).filter(Boolean):[];
    chainState[k]={e, inherited, items, value:e.value, effective:e.effective};
    const wrap=document.createElement("div");
    wrap.className="card";
    wrap.dataset.key=k;
    const badges = `${e.in_env?'<span class="badge ok">.envあり</span>':'<span class="badge">既定継承</span>'} ${e.effective && e.effective!==e.value?'<span class="badge warn">effective</span>':''}`;
    wrap.innerHTML=`
      <label>${fieldLabel(k)} ${badges}</label>
      <div class="inherit-row"><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" data-inh="${k}" ${inherited?"checked":""}> 継承 (空で既定に戻す)</label><span class="help">effective: <span class="mono">${esc(e.effective)}</span></span></div>
      <div><label>パレット (クリックで追加)</label><div class="palette" id="palette-${k}"></div></div>
      <div><label>順序 (ドラッグ / 上下 / 削除)</label><ul class="ordered" id="list-${k}"></ul></div>
      <div class="row"><div style="flex:1"><input id="custom-${k}" placeholder="codex:xxx または local"/><div class="help">AGENT_RE <span class="mono">^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$</span></div></div><div style="align-self:end"><button class="btn" data-add="${k}">追加</button></div></div>
      <div style="margin-top:8px"><label>プリセット</label>
        <button class="preset-btn" data-preset="${k}" data-val="Latency">Latency</button>
        <button class="preset-btn" data-preset="${k}" data-val="Cost">Cost</button>
        <button class="preset-btn" data-preset="${k}" data-val="Local">Local</button>
      </div>
      <div class="help">保存時はカンマ区切りにシリアライズ: <span class="mono" id="serial-${k}">${esc(items.join(","))}</span></div>
    `;
    cont.appendChild(wrap);
  }
  // populate palettes and lists
  for(const k of chainKeys()){
    const palEl=document.getElementById(`palette-${k}`);
    if(palEl){
      palEl.innerHTML="";
      for(const ag of [...paletteSet].sort()){
        const chip=document.createElement("span");
        chip.className="chip"; chip.textContent=ag;
        chip.onclick=()=>{
          if(chainState[k].inherited){ toast("継承中は編集できません。チェックを外してください"); return; }
          if(!AGENT_RE.test(ag)){ toast(`不正なエージェント: ${ag}`); return; }
          chainState[k].items.push(ag);
          refreshChainList(k);
        };
        palEl.appendChild(chip);
      }
    }
    refreshChainList(k);
    // inherit toggle
    const inh=document.querySelector(`[data-inh="${k}"]`);
    if(inh) inh.onchange=(e)=>{
      chainState[k].inherited=e.target.checked;
      if(e.target.checked){
        const eff=chainState[k].effective||"";
        chainState[k].items = eff? eff.split(",").map(s=>s.trim()).filter(Boolean):[];
      } else {
        // keep current items but if inherited previously, start from effective
        // leave as is
      }
      refreshChainList(k);
    };
    // custom add
    const btn=document.querySelector(`[data-add="${k}"]`);
    if(btn) btn.onclick=()=>{
      if(chainState[k].inherited){ toast("継承中は編集できません"); return; }
      const inp=document.getElementById(`custom-${k}`);
      const v=inp.value.trim();
      if(!v){ toast("値を入力してください"); return; }
      if(!AGENT_RE.test(v)){ toast(`不正なエージェント: ${v}`); return; }
      chainState[k].items.push(v);
      inp.value="";
      refreshChainList(k);
    };
    // presets
    for(const pb of $$(`[data-preset="${k}"]`)){
      pb.onclick=()=>{
        if(chainState[k].inherited){ toast("継承中はプリセットを適用できません"); return; }
        const kind=pb.getAttribute("data-val");
        const presetStr=CHAIN_PRESETS[kind]||"";
        if(!presetStr) return;
        chainState[k].items = presetStr.split(",").map(s=>s.trim()).filter(Boolean);
        refreshChainList(k);
      };
    }
  }
}
function refreshChainList(k){
  const ul=document.getElementById(`list-${k}`);
  if(!ul) return;
  const st=chainState[k];
  ul.innerHTML="";
  const disabled = st.inherited;
  st.items.forEach((item, idx)=>{
    const li=document.createElement("li");
    li.draggable=!disabled;
    if(disabled) li.classList.add("inherited");
    li.dataset.idx=String(idx);
    li.innerHTML=`<span class="drag">≡</span><span class="mono" style="flex:1">${esc(item)}</span>
      <button class="btn" data-up="${idx}" style="padding:4px 8px">↑</button>
      <button class="btn" data-down="${idx}" style="padding:4px 8px">↓</button>
      <button class="btn danger" data-rem="${idx}" style="padding:4px 8px">×</button>`;
    // buttons
    const up=li.querySelector(`[data-up="${idx}"]`);
    const down=li.querySelector(`[data-down="${idx}"]`);
    const rem=li.querySelector(`[data-rem="${idx}"]`);
    if(up) up.onclick=()=>{ if(disabled) return; if(idx>0){ const a=st.items.splice(idx,1)[0]; st.items.splice(idx-1,0,a); refreshChainList(k); }};
    if(down) down.onclick=()=>{ if(disabled) return; if(idx<st.items.length-1){ const a=st.items.splice(idx,1)[0]; st.items.splice(idx+1,0,a); refreshChainList(k); }};
    if(rem) rem.onclick=()=>{ if(disabled) return; st.items.splice(idx,1); refreshChainList(k); };
    if(disabled){ up.disabled=true; down.disabled=true; rem.disabled=true; }
    // drag
    li.addEventListener("dragstart", (e)=>{ if(disabled){ e.preventDefault(); return; } li.classList.add("dragging"); e.dataTransfer.effectAllowed="move"; e.dataTransfer.setData("text/plain", String(idx)); });
    li.addEventListener("dragend", ()=>li.classList.remove("dragging"));
    ul.appendChild(li);
  });
  // dragover/drop on ul
  ul.ondragover=(e)=>{ e.preventDefault(); e.dataTransfer.dropEffect="move"; };
  ul.ondrop=(e)=>{
    e.preventDefault();
    if(disabled) return;
    const fromIdx=parseInt(e.dataTransfer.getData("text/plain"),10);
    const targetLi=e.target.closest("li");
    if(targetLi){
      const toIdx=parseInt(targetLi.dataset.idx,10);
      if(!isNaN(fromIdx)&&!isNaN(toIdx)&&fromIdx!==toIdx){
        const [moved]=st.items.splice(fromIdx,1);
        st.items.splice(toIdx,0,moved);
        refreshChainList(k);
      }
    }
  };
  const ser=document.getElementById(`serial-${k}`);
  if(ser) ser.textContent = st.inherited? "(継承: "+esc(st.effective)+")" : st.items.join(",");
}
function renderBackoff(entries){
  const bi=entries["AI_BACKOFF_SEC_ITEMS"];
  const bd=entries["AI_AGENT_BACKOFF_SEC"];
  const bf=entries["AI_BACKOFF_FAILURE_SEC"];
  const src=(bi && bi.value)? bi.value : (bi? bi.effective : "");
  const defVal = (bd && bd.value)? bd.value : (bd? bd.default:"600");
  const failVal = (bf && bf.value)? bf.value : (bf? bf.default:"300");
  // parse items
  const rawItems = src? src.trim().split(/\s+/).filter(Boolean):[];
  backoffState.items=[];
  for(const it of rawItems){
    const [name,sec]=it.split(":",2);
    if(!name||!sec) continue;
    if(!BACKOFF_NAME_RE.test(name)) continue;
    const s=parseInt(sec,10);
    if(isNaN(s)||s<60) continue;
    backoffState.items.push({name, sec:s});
  }
  // ensure at least defaults exist if empty
  if(backoffState.items.length===0 && bi && bi.default){
    for(const it of bi.default.trim().split(/\s+/)){
      const [name,sec]=it.split(":",2);
      if(!name||!sec) continue;
      backoffState.items.push({name, sec:parseInt(sec,10)||600});
    }
  }
  backoffState.def=defVal; backoffState.fail=failVal; backoffState.raw=src;
  const cont=$("#backoff-cards");
  cont.innerHTML="";
  backoffState.items.forEach((it, idx)=>{
    const card=document.createElement("div");
    card.className="backoff-card";
    card.innerHTML=`
      <div class="head"><span class="mono">${esc(it.name)}</span><span class="badge">${it.sec}s</span> <button class="btn danger" data-brem="${idx}" style="padding:4px 8px">削除</button></div>
      <div class="row"><div style="flex:1"><input type="range" min="60" max="86400" step="60" value="${it.sec}" data-bslider="${idx}" class="range"/><div class="help">60〜86400秒</div></div><div style="width:120px"><input type="number" min="60" max="86400" value="${it.sec}" data-bnum="${idx}"/></div></div>
      <div class="bar" style="margin-top:8px"><i id="bbar-${idx}" style="width:0%"></i></div><div class="help" id="bbar-text-${idx}">remaining: -</div>
    `;
    cont.appendChild(card);
  });
  // add new model row
  const addRow=document.createElement("div");
  addRow.className="card"; addRow.style.background="#111319";
  addRow.innerHTML=`<label>新規モデル追加 (name:sec 形式、例: local:1800)</label><div class="row"><div style="flex:1"><input id="backoff-new-name" placeholder="モデル名"/><input id="backoff-new-sec" type="number" min="60" max="86400" placeholder="秒数" style="margin-top:6px"/></div><div style="align-self:end"><button class="btn" id="backoff-add">追加</button></div></div>`;
  cont.appendChild(addRow);
  // wire sliders
  backoffState.items.forEach((it,idx)=>{
    const sl=document.querySelector(`[data-bslider="${idx}"]`);
    const num=document.querySelector(`[data-bnum="${idx}"]`);
    const badge=cont.querySelectorAll(".backoff-card")[idx]?.querySelector(".badge");
    const sync=(v)=>{
      let n=parseInt(v,10); if(isNaN(n)||n<60) n=60; if(n>86400) n=86400;
      it.sec=n;
      if(sl) sl.value=String(n);
      if(num) num.value=String(n);
      if(badge) badge.textContent=n+"s";
    };
    if(sl) sl.oninput=(e)=>sync(e.target.value);
    if(num) num.oninput=(e)=>sync(e.target.value);
    const remBtn=document.querySelector(`[data-brem="${idx}"]`);
    if(remBtn) remBtn.onclick=()=>{
      backoffState.items.splice(idx,1);
      renderBackoff(entries);
    };
  });
  const addBtn=document.getElementById("backoff-add");
  if(addBtn) addBtn.onclick=()=>{
    const nEl=document.getElementById("backoff-new-name");
    const sEl=document.getElementById("backoff-new-sec");
    const name=nEl.value.trim(), secStr=sEl.value.trim();
    if(!name||!secStr){ toast("名前と秒数を入力"); return; }
    if(!BACKOFF_NAME_RE.test(name)){ toast("モデル名が不正: "+name); return; }
    const sec=parseInt(secStr,10);
    if(isNaN(sec)||sec<60){ toast("秒数は60以上"); return; }
    if(backoffState.items.some(x=>x.name===name)){ toast("既に存在: "+name); return; }
    backoffState.items.push({name, sec});
    nEl.value=""; sEl.value="";
    renderBackoff(entries);
  };
  // presets for all
  for(const btn of $$("[data-bpreset]")){
    btn.onclick=()=>{
      const v=parseInt(btn.getAttribute("data-bpreset"),10);
      backoffState.items.forEach(it=>it.sec=v);
      renderBackoff(entries);
    };
  }
  // def/fail sliders
  const defSl=$("#backoff-default"), defNum=$("#backoff-default-num"), defBadge=$("#backoff-default-val");
  const failSl=$("#backoff-failure"), failNum=$("#backoff-failure-num"), failBadge=$("#backoff-failure-val");
  if(defSl && defNum){
    defSl.value=backoffState.def; defNum.value=backoffState.def;
    if(defBadge) defBadge.textContent=backoffState.def+"s";
    const syncDef=(v)=>{ let n=parseInt(v,10); if(isNaN(n)||n<60) n=60; if(n>3600) n=3600; backoffState.def=String(n); defSl.value=String(n); defNum.value=String(n); if(defBadge) defBadge.textContent=n+"s"; };
    defSl.oninput=(e)=>syncDef(e.target.value);
    defNum.oninput=(e)=>syncDef(e.target.value);
  }
  if(failSl && failNum){
    failSl.value=backoffState.fail; failNum.value=backoffState.fail;
    if(failBadge) failBadge.textContent=backoffState.fail+"s";
    const syncFail=(v)=>{ let n=parseInt(v,10); if(isNaN(n)||n<30) n=30; if(n>3600) n=3600; backoffState.fail=String(n); failSl.value=String(n); failNum.value=String(n); if(failBadge) failBadge.textContent=n+"s"; };
    failSl.oninput=(e)=>syncFail(e.target.value);
    failNum.oninput=(e)=>syncFail(e.target.value);
  }
  // update remaining bars after fetch
  loadBackoffs();
}
function renderPeak(entries){
  const pw=entries["PEAK_HOURS_WINDOWS"], tz=entries["PEAK_HOURS_TZ"], pp=entries["PEAK_HOURS_PRIORITY_AGENT"], pr=entries["PEAK_HOURS_AGENT_PREFERENCE"], sw=entries["PEAK_HOURS_AGENT_SWAP_ENABLED"], gate=entries["PEAK_HOURS_QUEUE_GATE_ENABLED"];
  const windows = pw? pw.value : "";
  const tzVal = tz? (tz.value||tz.default) : "Asia/Tokyo";
  const pref = pr? (pr.value||pr.effective||pr.default) : "";
  const swap = sw? (sw.value||sw.default||"1") : "1";
  const gateV = gate? (gate.value||gate.default||"1") : "1";
  const prio = pp? (pp.value||"") : "";
  peakState.windows=windows; peakState.tz=tzVal; peakState.prefItems = pref? pref.split(",").map(s=>s.trim()).filter(Boolean):[];
  peakState.swap=swap; peakState.gate=gateV; peakState.priority=prio;
  peakState.hoursSet = windowsToHoursSet(windows);
  // timeline
  const tl=$("#peak-timeline");
  tl.innerHTML="";
  for(let h=0;h<24;h++){
    const cell=document.createElement("label");
    cell.className="tl"+(peakState.hoursSet.has(h)?" active":"");
    cell.innerHTML=`<input type="checkbox" data-hour="${h}" ${peakState.hoursSet.has(h)?"checked":""}>${h}`;
    cell.onclick=(e)=>{
      // toggle
      if(e.target.tagName==="INPUT") return;
      const cb=cell.querySelector("input");
      cb.checked=!cb.checked;
      cell.classList.toggle("active", cb.checked);
      if(cb.checked) peakState.hoursSet.add(h); else peakState.hoursSet.delete(h);
      updatePeakPreview();
    };
    const cb=cell.querySelector("input");
    if(cb) cb.onchange=(e)=>{
      if(e.target.checked){ peakState.hoursSet.add(h); cell.classList.add("active"); } else { peakState.hoursSet.delete(h); cell.classList.remove("active"); }
      updatePeakPreview();
    };
    tl.appendChild(cell);
  }
  updatePeakPreview();
  // tz
  const tzEl=$("#peak-tz"); if(tzEl) tzEl.value=tzVal;
  if(tzEl) tzEl.oninput=(e)=>{ peakState.tz=e.target.value; };
  // priority
  const prEl=$("#peak-priority"); if(prEl) prEl.value=prio;
  if(prEl) prEl.oninput=(e)=>{ peakState.priority=e.target.value.trim(); };
  // pref palette and list
  const pal=$("#peak-pref-palette"); pal.innerHTML="";
  const prefPalette = new Set([...paletteSet, ...peakState.prefItems]);
  for(const ag of [...prefPalette].sort()){
    const chip=document.createElement("span");
    chip.className="chip"; chip.textContent=ag;
    chip.onclick=()=>{
      if(!AGENT_RE.test(ag)){ toast("不正: "+ag); return; }
      if(peakState.prefItems.includes(ag)){ toast("既に追加済み"); return; }
      peakState.prefItems.push(ag);
      refreshPeakPrefList();
    };
    pal.appendChild(chip);
  }
  refreshPeakPrefList();
  const addBtn=$("#peak-pref-add");
  if(addBtn) addBtn.onclick=()=>{
    const inp=$("#peak-pref-custom"); const v=inp.value.trim();
    if(!v){ toast("値を入力"); return; }
    if(!AGENT_RE.test(v)){ toast("不正なエージェント: "+v); return; }
    if(peakState.prefItems.includes(v)){ toast("既に追加済み"); return; }
    peakState.prefItems.push(v); inp.value=""; refreshPeakPrefList();
  };
  // switches
  const swEl=$("#peak-swap"), gateEl=$("#peak-gate");
  if(swEl){ swEl.checked=peakState.swap==="1"; swEl.onchange=(e)=>{ peakState.swap=e.target.checked?"1":"0"; $("#peak-swap-label").textContent=peakState.swap; }; $("#peak-swap-label").textContent=peakState.swap; }
  if(gateEl){ gateEl.checked=peakState.gate==="1"; gateEl.onchange=(e)=>{ peakState.gate=e.target.checked?"1":"0"; $("#peak-gate-label").textContent=peakState.gate; }; $("#peak-gate-label").textContent=peakState.gate; }
}
function updatePeakPreview(){
  const w=hoursSetToWindows(peakState.hoursSet);
  $("#peak-windows-preview").textContent = w||"(なし: 常にオフピーク)";
  peakState.windows=w;
}
function refreshPeakPrefList(){
  const ul=$("#peak-pref-list");
  ul.innerHTML="";
  peakState.prefItems.forEach((item, idx)=>{
    const li=document.createElement("li");
    li.draggable=true;
    li.dataset.idx=String(idx);
    li.innerHTML=`<span class="drag">≡</span><span class="mono" style="flex:1">${esc(item)}</span>
      <button class="btn" data-pup="${idx}" style="padding:4px 8px">↑</button>
      <button class="btn" data-pdown="${idx}" style="padding:4px 8px">↓</button>
      <button class="btn danger" data-prem="${idx}" style="padding:4px 8px">×</button>`;
    const up=li.querySelector(`[data-pup="${idx}"]`);
    const down=li.querySelector(`[data-pdown="${idx}"]`);
    const rem=li.querySelector(`[data-prem="${idx}"]`);
    if(up) up.onclick=()=>{ if(idx>0){ const a=peakState.prefItems.splice(idx,1)[0]; peakState.prefItems.splice(idx-1,0,a); refreshPeakPrefList(); }};
    if(down) down.onclick=()=>{ if(idx<peakState.prefItems.length-1){ const a=peakState.prefItems.splice(idx,1)[0]; peakState.prefItems.splice(idx+1,0,a); refreshPeakPrefList(); }};
    if(rem) rem.onclick=()=>{ peakState.prefItems.splice(idx,1); refreshPeakPrefList(); };
    li.addEventListener("dragstart",(e)=>{ li.classList.add("dragging"); e.dataTransfer.setData("text/plain", String(idx)); });
    li.addEventListener("dragend",()=>li.classList.remove("dragging"));
    ul.appendChild(li);
  });
  ul.ondragover=(e)=>{ e.preventDefault(); };
  ul.ondrop=(e)=>{
    e.preventDefault();
    const fromIdx=parseInt(e.dataTransfer.getData("text/plain"),10);
    const target=e.target.closest("li");
    if(target){
      const toIdx=parseInt(target.dataset.idx,10);
      if(!isNaN(fromIdx)&&!isNaN(toIdx)&&fromIdx!==toIdx){
        const [m]=peakState.prefItems.splice(fromIdx,1);
        peakState.prefItems.splice(toIdx,0,m);
        refreshPeakPrefList();
      }
    }
  };
}
async function saveChains(){
  if(READ_ONLY){ toast("read-only モードのため保存できません"); return; }
  const payload={};
  for(const k of chainKeys()){
    const st=chainState[k];
    if(!st) continue;
    let val="";
    if(st.inherited) val="";
    else {
      // validate
      for(const ag of st.items){ if(!AGENT_RE.test(ag)){ toast(`${k} に不正なエージェント ${ag}`); return; } }
      if(k==="AI_COMMON_AGENTS" && st.items.length===0){ toast("AI_COMMON_AGENTS は空にできません"); return; }
      val=st.items.join(",");
    }
    payload[k]=val;
  }
  try{
    const res=await api("/api/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({values:payload,expected_mtime:ENV_MTIME})});
    ENV_MTIME=res.env_mtime||ENV_MTIME;
    $("#env-mtime").textContent=`mtime=${ENV_MTIME} ${fmtTime(ENV_MTIME)}`;
    toast("保存しました。10秒以内に hot-reload されます");
    await loadConfig();
    try{ await api("/api/reload",{method:"POST"}); }catch(e){}
  }catch(e){ toast(String(e),5000); }
}
async function saveBackoff(){
  if(READ_ONLY){ toast("read-only"); return; }
  const itemsStr = backoffState.items.map(it=>`${it.name}:${it.sec}`).join(" ");
  const payload={
    "AI_BACKOFF_SEC_ITEMS": itemsStr,
    "AI_AGENT_BACKOFF_SEC": String(backoffState.def),
    "AI_BACKOFF_FAILURE_SEC": String(backoffState.fail)
  };
  // validate
  for(const it of backoffState.items){
    if(!BACKOFF_NAME_RE.test(it.name)){ toast(`モデル名不正 ${it.name}`); return; }
    if(it.sec<60){ toast(`秒数は60以上 ${it.name}`); return; }
  }
  if(parseInt(payload["AI_AGENT_BACKOFF_SEC"],10)<60){ toast("AI_AGENT_BACKOFF_SEC は60以上"); return; }
  if(parseInt(payload["AI_BACKOFF_FAILURE_SEC"],10)<30){ toast("AI_BACKOFF_FAILURE_SEC は30以上"); return; }
  try{
    const res=await api("/api/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({values:payload,expected_mtime:ENV_MTIME})});
    ENV_MTIME=res.env_mtime||ENV_MTIME;
    $("#env-mtime").textContent=`mtime=${ENV_MTIME} ${fmtTime(ENV_MTIME)}`;
    toast("保存しました");
    await loadConfig();
    try{ await api("/api/reload",{method:"POST"}); }catch(e){}
  }catch(e){ toast(String(e),5000); }
}
async function savePeak(){
  if(READ_ONLY){ toast("read-only"); return; }
  const payload={
    "PEAK_HOURS_WINDOWS": peakState.windows,
    "PEAK_HOURS_TZ": peakState.tz.trim(),
    "PEAK_HOURS_PRIORITY_AGENT": peakState.priority.trim(),
    "PEAK_HOURS_AGENT_PREFERENCE": peakState.prefItems.join(","),
    "PEAK_HOURS_AGENT_SWAP_ENABLED": peakState.swap,
    "PEAK_HOURS_QUEUE_GATE_ENABLED": peakState.gate
  };
  // validate priority
  if(payload["PEAK_HOURS_PRIORITY_AGENT"] && !AGENT_RE.test(payload["PEAK_HOURS_PRIORITY_AGENT"])){ toast("PRIORITY_AGENT 不正"); return; }
  for(const ag of peakState.prefItems){ if(!AGENT_RE.test(ag)){ toast("PREFERENCE 不正: "+ag); return; } }
  if(payload["PEAK_HOURS_TZ"] && !/^[A-Za-z0-9_+.\/:-]{1,64}$/.test(payload["PEAK_HOURS_TZ"])){ toast("TZ 不正"); return; }
  try{
    const res=await api("/api/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({values:payload,expected_mtime:ENV_MTIME})});
    ENV_MTIME=res.env_mtime||ENV_MTIME;
    $("#env-mtime").textContent=`mtime=${ENV_MTIME} ${fmtTime(ENV_MTIME)}`;
    toast("保存しました");
    await loadConfig();
    try{ await api("/api/reload",{method:"POST"}); }catch(e){}
  }catch(e){ toast(String(e),5000); }
}
async function loadBackoffs(){
  const data = await api("/api/backoffs");
  const tbody = $("#backoff-table");
  if(tbody){
    tbody.innerHTML="";
    for(const b of data.backoffs){
      const tr = document.createElement("tr");
      const untilTxt = b.until?fmtTime(b.until):"-";
      const state = b.active?'<span class="badge bad">active</span>':'<span class="badge ok">ready</span>';
      tr.innerHTML = `<td class="mono">${esc(b.agent)}</td><td class="mono">${esc(b.sanitized)}</td><td>${untilTxt}</td><td>${b.remaining_text}</td><td>${state}</td><td>${b.active?`<button class="btn danger" data-clear="${esc(b.sanitized)}">クリア</button>`:""}</td>`;
      tbody.appendChild(tr);
    }
    for(const btn of $$("[data-clear]")){
      btn.onclick = async ()=>{
        const id = btn.getAttribute("data-clear");
        try{ await api(`/api/backoffs/${encodeURIComponent(id)}`,{method:"DELETE"}); toast(`クリア: ${id}`); await loadBackoffs(); await loadDashboard(); }catch(e){ toast(String(e)); }
      };
    }
  }
  // update backoff cards bars
  for(const it of backoffState.items){
    const sanit=sanitizeAgent(it.name);
    const found=data.backoffs.find(x=>x.sanitized===sanit||x.agent===it.name);
    const idx=backoffState.items.indexOf(it);
    const bar=document.getElementById(`bbar-${idx}`);
    const txt=document.getElementById(`bbar-text-${idx}`);
    if(found && bar && txt){
      if(found.active){
        const pct=Math.min(100, Math.round(found.remaining / it.sec * 100));
        bar.style.width=pct+"%";
        bar.style.background="var(--bad)";
        txt.textContent=`remaining ${found.remaining_text} / ${it.sec}s`;
      } else {
        bar.style.width="0%";
        txt.textContent="ready";
      }
    }
  }
  // dashboard bars also update via renderDashboard but we keep
}
async function loadStats(){
  const days = parseInt($("#stats-days").value||"7",10);
  const data = await api(`/api/stats?days=${days}`);
  const tbody = $("#stats-table");
  tbody.innerHTML="";
  for(const d of data.days){
    const tr=document.createElement("tr");
    tr.innerHTML=`<td>${d.day}</td><td>${d.attempt}</td><td>${d.winner}</td><td>${d.fail}</td><td>${d.all_failed}</td>`;
    tbody.appendChild(tr);
  }
  const agBody=$("#stats-agents");
  agBody.innerHTML="";
  const agents = data.by_agent||{};
  const sorted = Object.entries(agents).sort((a,b)=>b[1].winner - a[1].winner);
  for(const [agent, v] of sorted){
    const tr=document.createElement("tr");
    tr.innerHTML=`<td class="mono">${esc(agent)}</td><td>${v.attempt}</td><td>${v.winner}</td>`;
    agBody.appendChild(tr);
  }
  drawStats(data.days);
}
function drawStats(days){
  const canvas=$("#stats-canvas");
  const ctx=canvas.getContext("2d");
  const W=canvas.width, H=canvas.height;
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle="#111319"; ctx.fillRect(0,0,W,H);
  if(!days.length) return;
  const max = Math.max(1, ...days.map(d=>Math.max(d.attempt,d.winner,d.fail)));
  const padL=40, padR=10, padT=10, padB=24;
  const plotW=W-padL-padR, plotH=H-padT-padB;
  ctx.strokeStyle="#2a3040"; ctx.lineWidth=1;
  for(let i=0;i<=4;i++){
    const y=padT + (plotH*i/4);
    ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(W-padR,y); ctx.stroke();
    ctx.fillStyle="#9aa3b2"; ctx.font="11px system-ui"; ctx.fillText(String(Math.round(max*(1-i/4))),4,y+4);
  }
  const n=days.length, bw=plotW/n*0.6, gap=plotW/n*0.4;
  days.forEach((d, idx)=>{
    const x=padL + idx*(bw+gap) + gap/2;
    const cols=[["attempt","#6ea8fe"],["winner","#7bd88f"],["fail","#ff6b6b"]];
    cols.forEach((c, ci)=>{
      const val=d[c[0]]||0;
      const h=(val/max)*plotH;
      ctx.fillStyle=c[1];
      const bw2=bw/3-2;
      const xx=x+ci*(bw/3);
      ctx.fillRect(xx, padT+plotH-h, bw2, h);
    });
    ctx.fillStyle="#9aa3b2"; ctx.font="10px system-ui"; ctx.fillText(d.day.slice(4), x, H-6);
  });
  ctx.fillStyle="#6ea8fe"; ctx.fillRect(W-160,8,10,10); ctx.fillStyle="#e6e8ee"; ctx.font="11px system-ui"; ctx.fillText("attempt",W-145,17);
  ctx.fillStyle="#7bd88f"; ctx.fillRect(W-100,8,10,10); ctx.fillStyle="#e6e8ee"; ctx.fillText("winner",W-85,17);
  ctx.fillStyle="#ff6b6b"; ctx.fillRect(W-45,8,10,10); ctx.fillStyle="#e6e8ee"; ctx.fillText("fail",W-30,17);
}
async function loadHealth(){
  const data=await api("/api/health");
  const kv=$("#health-kv");
  kv.innerHTML="";
  const rows=[["soren_root",data.soren_root],["env_mtime",`${data.env_mtime} (${fmtTime(data.env_mtime)})`],["read_only",String(data.read_only)],["bind",data.bind||""],["port",String(data.port||"")],["uptime_sec",String(data.uptime||"")]];
  for(const [k,v] of rows){
    const dt=document.createElement("dt"); dt.textContent=k;
    const dd=document.createElement("dd"); dd.textContent=v; dd.className="mono";
    kv.appendChild(dt); kv.appendChild(dd);
  }
  if(data.reload){
    const dt=document.createElement("dt"); dt.textContent="last_reload";
    const dd=document.createElement("dd"); dd.textContent=JSON.stringify(data.reload);
    kv.appendChild(dt); kv.appendChild(dd);
  }
}
async function loadDashboard(){
  try{
    const [gs, is, workers, peak, backoffs, stats] = await Promise.all([
      api("/api/game_state").catch(()=>({exists:false,state:"-",score:"-"})),
      api("/api/improve_state").catch(()=>({status:"unknown",is_locked:false})),
      api("/api/workers").catch(()=>({workers:[]})),
      api("/api/peak_status").catch(()=>({is_peak_now:false,windows:"",now_str:"-"})),
      api("/api/backoffs").catch(()=>({backoffs:[]})),
      api("/api/stats?days=7").catch(()=>({days:[],by_agent:{}}))
    ]);
    renderDashboard(gs,is,workers,peak,backoffs,stats);
  }catch(e){ console.warn("dashboard",e); }
}
function renderDashboard(gs,is,workers,peak,backoffs,stats){
  // kpi game
  const gsState = gs.exists? (gs.state||"UNKNOWN") : "no file";
  const gsScore = gs.score!=null? gs.score : "-";
  const gsMtime = gs.mtime? fmtTime(gs.mtime) : "-";
  $("#kpi-game").textContent = gsState;
  $("#kpi-game-sub").textContent = `score ${gsScore} / mtime ${gsMtime}`;
  // improve
  const impStatus = is.status||"idle";
  const impLock = is.is_locked? "locked" : "unlocked";
  const impPid = is.pid? `pid ${is.pid}` : "no pid";
  const impAlive = is.alive? "alive" : "not alive";
  $("#kpi-improve").textContent = impStatus;
  $("#kpi-improve-sub").textContent = `${impLock} ${impPid} ${impAlive}`;
  // workers
  const wlist = workers.workers||[];
  const aliveCount = wlist.filter(w=>w.alive).length;
  $("#kpi-workers").textContent = `${aliveCount}/${wlist.length}`;
  $("#kpi-workers-sub").textContent = wlist.map(w=>`${w.worker}:${w.alive?"ok":"down"}`).join(" ")||"-";
  const wtbody=$("#dash-workers");
  if(wtbody){
    wtbody.innerHTML="";
    for(const w of wlist){
      const tr=document.createElement("tr");
      tr.innerHTML=`<td class="mono">${esc(w.worker)}</td><td>${w.pid||"-"}</td><td>${w.alive?'<span class="badge ok">alive</span>':'<span class="badge bad">down</span>'}</td>`;
      wtbody.appendChild(tr);
    }
  }
  // peak
  $("#kpi-peak").textContent = peak.is_peak_now? "ピーク中":"オフピーク";
  $("#kpi-peak-sub").textContent = `${peak.windows||"-"} TZ:${peak.tz} now ${peak.now_str||"-"}`;
  // sparkline
  const days=stats.days||[];
  drawSparkline(days);
  $("#dash-spark-help").textContent = days.length? `${days.length}日分`:"データなし";
  // backoff bars
  const barCont=$("#dash-backoff-bars");
  barCont.innerHTML="";
  const active = (backoffs.backoffs||[]).filter(b=>b.active).sort((a,b)=>b.remaining-a.remaining).slice(0,5);
  if(active.length===0) barCont.innerHTML='<div class="help">アクティブな backoff なし</div>';
  else {
    for(const b of active){
      const row=document.createElement("div");
      row.style.marginBottom="8px";
      row.innerHTML=`<div style="display:flex;justify-content:space-between;font-size:12px"><span class="mono">${esc(b.agent)}</span><span>${esc(b.remaining_text)}</span></div><div class="bar"><i style="width:${Math.min(100, Math.round(b.remaining/86400*100))}%"></i></div>`;
      barCont.appendChild(row);
    }
  }
  // top3
  const byAgent=stats.by_agent||{};
  const sorted=Object.entries(byAgent).sort((a,b)=>b[1].winner - a[1].winner).slice(0,3);
  const top3=$("#dash-top3");
  top3.innerHTML="";
  if(sorted.length===0) top3.innerHTML='<tr><td colspan="2" class="help">データなし</td></tr>';
  else for(const [agent,v] of sorted){
    const tr=document.createElement("tr");
    tr.innerHTML=`<td class="mono">${esc(agent)}</td><td>${v.winner}</td>`;
    top3.appendChild(tr);
  }
}
function drawSparkline(days){
  const svg=$("#dash-sparkline");
  svg.innerHTML="";
  if(!days.length) return;
  const W=400, H=80, pad=6;
  const max = Math.max(1, ...days.map(d=>Math.max(d.winner||0, d.attempt||0)));
  const points = days.map((d,i)=>{
    const x = pad + (W-2*pad)* i / Math.max(1,days.length-1);
    const y = H-pad - (H-2*pad)*( (d.winner||0)/max );
    return `${x},${y}`;
  }).join(" ");
  const poly=document.createElementNS("http://www.w3.org/2000/svg","polyline");
  poly.setAttribute("points", points);
  poly.setAttribute("fill","none");
  poly.setAttribute("stroke","#7bd88f");
  poly.setAttribute("stroke-width","2");
  svg.appendChild(poly);
  // attempt line
  const points2 = days.map((d,i)=>{
    const x = pad + (W-2*pad)* i / Math.max(1,days.length-1);
    const y = H-pad - (H-2*pad)*( (d.attempt||0)/max );
    return `${x},${y}`;
  }).join(" ");
  const poly2=document.createElementNS("http://www.w3.org/2000/svg","polyline");
  poly2.setAttribute("points", points2);
  poly2.setAttribute("fill","none");
  poly2.setAttribute("stroke","#6ea8fe");
  poly2.setAttribute("stroke-width","1.5");
  poly2.setAttribute("opacity","0.7");
  svg.appendChild(poly2);
}
let statusTimer=null;
let overlayPreviewTimer=null;
async function loadStatus(){
  try{
    const data=await api("/api/overlay/status");
    // comment / radio
    const gens=data.generators||[];
    // kpi cards
    const cGen=data.generators.find(g=>g.key==="comment");
    const rGen=data.generators.find(g=>g.key==="radio");
    const imp=data.improve||{};
    const wc=data.wildcard||{};
    const game=data.game||{};
    // status-comment
    if(cGen){
      $("#status-comment").textContent=cGen.fresh?"生成中":"古い";
      $("#status-comment").className="val "+(cGen.fresh?"badge ok":"badge warn");
      $("#status-comment-sub").textContent=`${cGen.label} age ${cGen.age}s / ${cGen.stale_sec}s ts ${fmtTime(cGen.ts)} raw ${esc(cGen.raw||"")}`;
    } else {
      $("#status-comment").textContent="待機中";
      $("#status-comment-sub").textContent=`raw: ${esc(data.comment_gen.raw||"-")} fresh: -`;
    }
    if(rGen){
      $("#status-radio").textContent=rGen.fresh?"生成中":"古い";
      $("#status-radio").className="val "+(rGen.fresh?"badge ok":"badge warn");
      $("#status-radio-sub").textContent=`${rGen.label} age ${rGen.age}s / ${rGen.stale_sec}s pid ${rGen.owner_pid||"-"} alive ${rGen.owner_alive?"yes":"no"} raw ${esc(rGen.raw||"")}`;
    } else {
      $("#status-radio").textContent="待機中";
      $("#status-radio-sub").textContent=`raw: ${esc(data.radio_state.raw||"-")}`;
    }
    $("#status-improve").textContent=imp.status||"idle";
    $("#status-improve-sub").textContent=`pid ${imp.pid||"-"} alive ${imp.alive?"yes":"no"} locked ${imp.is_locked?"yes":"no"} mtime ${fmtTime(imp.mtime)}`;
    const wcActive=wc.data && (wc.data.phase==="generating"||wc.data.phase==="running");
    $("#status-wildcard").textContent=wcActive? (wc.data.phase||"active"):"idle";
    $("#status-wildcard-sub").textContent= wc.data? `phase ${wc.data.phase||"-"} pid ${wc.data.controller_pid||"-"} exists ${wc.exists?"yes":"no"}`:"-";
    // generators list
    const gCont=$("#status-generators");
    gCont.innerHTML="";
    if(gens.length===0) gCont.innerHTML='<div class="help">生成中なし</div>';
    else for(const g of gens){
      const d=document.createElement("div");
      d.className="card"; d.style.background="#111319"; d.style.marginBottom="6px";
      d.innerHTML=`<div style="display:flex;gap:8px;align-items:center"><span style="font-size:18px">${esc(g.icon||"")}</span><span class="mono" style="flex:1">${esc(g.label)}</span><span class="badge ${g.fresh?"ok":"warn"}">${g.fresh?"fresh":"stale"}</span><span class="badge">${g.age}s</span><span class="mono" style="font-size:11px">${fmtTime(g.ts)}</span></div><div class="help">key ${esc(g.key)} stale ${g.stale_sec}s raw ${esc(g.raw||"")}</div>`;
      gCont.appendChild(d);
    }
    // game kv
    const gKv=$("#status-game-kv");
    gKv.innerHTML="";
    for(const [k,v] of [["state",game.state||"-"],["score",String(game.score!=null?game.score:"-")],["mtime",fmtTime(game.mtime)],["path",game.path||"-"]]){
      const dt=document.createElement("dt"); dt.textContent=k;
      const dd=document.createElement("dd"); dd.textContent=v; dd.className="mono";
      gKv.appendChild(dt); gKv.appendChild(dd);
    }
    // improve kv
    const iKv=$("#status-improve-kv");
    iKv.innerHTML="";
    for(const [k,v] of [["status",imp.status||"-"],["phase",(imp.data&&imp.data.phase)||"-"],["detail",(imp.data&&imp.data.detail)||"-"],["progress",String((imp.data&&imp.data.progress)||0)],["pid",String(imp.pid||"-")],["alive",imp.alive?"yes":"no"],["locked",imp.is_locked?"yes":"no"],["mtime",fmtTime(imp.mtime)]]){
      const dt=document.createElement("dt"); dt.textContent=k;
      const dd=document.createElement("dd"); dd.textContent=v; dd.className="mono";
      iKv.appendChild(dt); iKv.appendChild(dd);
    }
    $("#status-improve-log").textContent=(imp.log_tail||[]).join("\n")||"(log empty)";
    // workers
    const wBody=$("#status-workers");
    wBody.innerHTML="";
    for(const w of (data.workers||[])){
      const tr=document.createElement("tr");
      tr.innerHTML=`<td class="mono">${esc(w.worker)}</td><td>${w.pid||"-"}</td><td>${w.alive?'<span class="badge ok">alive</span>':'<span class="badge bad">down</span>'}</td>`;
      wBody.appendChild(tr);
    }
    $("#status-game-count").textContent=data.game_count.value!=null?String(data.game_count.value):"-";
    $("#status-game-count-path").textContent=data.game_count.path||"-";
  }catch(e){ console.warn("loadStatus",e); toast(String(e),4000); }
}
async function loadOverlayEvents(){
  try{
    const data=await api("/api/overlay/events");
    $("#overlay-events-keep").textContent=data.keep;
    $("#overlay-events-visible").textContent=data.visible_sec;
    const tb=$("#overlay-events-table");
    tb.innerHTML="";
    (data.events||[]).forEach((ev, idx)=>{
      const tr=document.createElement("tr");
      tr.innerHTML=`<td>${idx}</td><td class="mono" style="font-size:11px">${fmtTime(ev.ts)}</td><td><span class="badge">${esc(ev.category)}</span> <span class="badge ${ev.level==="error"?"bad":ev.level==="warn"?"warn":""}">${esc(ev.level||"info")}</span></td><td class="mono">${esc(ev.title)}</td><td class="mono" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(ev.body||"")}</td><td><button class="btn danger" data-del-ev="${idx}" style="padding:4px 8px">×</button></td>`;
      tb.appendChild(tr);
    });
    if((data.events||[]).length===0) tb.innerHTML='<tr><td colspan="6" class="help">キュー空</td></tr>';
    for(const btn of $$("[data-del-ev]")){
      btn.onclick=async()=>{
        const idx=btn.getAttribute("data-del-ev");
        try{ await api(`/api/overlay/events/${idx}`,{method:"DELETE"}); toast(`削除 ${idx}`); await loadOverlayEvents(); }catch(e){ toast(String(e)); }
      };
    }
    // bulk textarea sync
    const bulk=$("#overlay-events-bulk");
    if(bulk && document.activeElement!==bulk) bulk.value=JSON.stringify(data.events||[],null,2);
  }catch(e){ toast(String(e)); }
}
async function pushOverlayEvent(){
  const cat=$("#overlay-notify-category").value;
  const level=$("#overlay-notify-level").value;
  const title=$("#overlay-notify-title").value.trim();
  const body=$("#overlay-notify-body").value.trim();
  if(!title){ toast("title 必須"); return; }
  try{
    await api("/api/overlay/events",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({category:cat,title,body,level})});
    toast("通知を追加しました");
    $("#overlay-notify-title").value=""; $("#overlay-notify-body").value="";
    await loadOverlayEvents();
  }catch(e){ toast(String(e),5000); $("#overlay-notify-msg").textContent=String(e); }
}
async function saveOverlayBulk(){
  const txt=$("#overlay-events-bulk").value.trim();
  let arr=[];
  try{ arr= txt? JSON.parse(txt):[]; }catch(e){ toast("JSON parse error: "+e); return; }
  if(!Array.isArray(arr)){ toast("配列である必要があります"); return; }
  try{
    await api("/api/overlay/events",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({events:arr})});
    toast("一括保存しました");
    await loadOverlayEvents();
  }catch(e){ toast(String(e),5000); $("#overlay-events-bulk-msg").textContent=String(e); }
}
async function loadWorkBanner(){
  try{
    const data=await api("/api/overlay/work_banner");
    const active=!!data.active;
    $("#work-banner-active").textContent=active?"有効":"無効";
    $("#work-banner-active").className="badge "+(active?"bad":"ok");
    $("#work-banner-title").textContent=data.title||"-";
    $("#work-banner-body").textContent=data.body||"-";
    $("#work-banner-ts").textContent=data.ts?fmtTime(data.ts):"-";
    $("#work-banner-enabled").checked=active;
    $("#work-banner-enabled-label").textContent=active?"on":"off";
    if(active){
      $("#work-banner-title-input").value=data.title||"";
      $("#work-banner-body-input").value=data.body||"";
    }
  }catch(e){ toast(String(e)); }
}
async function saveWorkBanner(){
  const enabled=$("#work-banner-enabled").checked;
  const title=$("#work-banner-title-input").value.trim();
  const body=$("#work-banner-body-input").value.trim();
  try{
    await api("/api/overlay/work_banner",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({active:enabled,title,body})});
    toast(enabled?"作業中バナー有効化":"バナー無効化");
    await loadWorkBanner();
  }catch(e){ toast(String(e),5000); $("#work-banner-msg").textContent=String(e); }
}
async function disableWorkBanner(){
  try{ await api("/api/overlay/work_banner",{method:"DELETE"}); toast("無効化"); await loadWorkBanner(); }catch(e){ toast(String(e)); }
}
async function loadTop(){
  try{
    const data=await api("/api/overlay/top");
    $("#top-mode").textContent=data.mode||"auto";
    $("#top-enabled").textContent= String(data.enabled);
    $("#top-path").textContent=data.path||"-";
    const cur=document.getElementById("top-current-lines");
    if(data.lines && data.lines.length) cur.textContent="現在: "+data.lines.join(" | ");
    else cur.textContent=data.mode==="hidden"?"非表示":data.mode==="manual"?"(lines empty)":"自動";
    // radio sync
    const mode=data.mode||"auto";
    for(const r of $$('input[name="top-mode"]')) r.checked=(r.value===mode);
    // inputs
    for(let i=1;i<=4;i++){
      const el=document.getElementById(`top-line-${i}`);
      el.value=(data.lines && data.lines[i-1])||"";
      el.disabled=(mode!=="manual");
    }
  }catch(e){ toast(String(e)); }
}
async function saveTop(){
  const modeEl=$$('input[name="top-mode"]:checked')[0];
  const mode=modeEl?modeEl.value:"auto";
  let enabled=null; let lines=[];
  if(mode==="auto"){ enabled=null; lines=[]; }
  else if(mode==="hidden"){ enabled=false; lines=[]; }
  else { enabled=true; for(let i=1;i<=4;i++){ const v=document.getElementById(`top-line-${i}`).value.trim(); if(v) lines.push(v); } }
  try{
    if(enabled===null){
      await api("/api/overlay/top",{method:"DELETE"});
      toast("自動に戻しました");
    } else {
      await api("/api/overlay/top",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled,lines})});
      toast("上部保存");
    }
    await loadTop();
  }catch(e){ toast(String(e),5000); $("#top-msg").textContent=String(e); }
}
async function loadPreview(){
  const typ=$("#preview-type").value;
  const region=$("#preview-region").value;
  try{
    const data=await api(`/api/overlay/preview?type=${encodeURIComponent(typ)}&region=${encodeURIComponent(region)}`);
    $("#preview-html").value=data.html||"";
    const iframe=document.getElementById("preview-iframe");
    try{ iframe.srcdoc=data.html||""; }catch(e){ iframe.src="about:blank"; }
  }catch(e){ toast(String(e)); }
}
document.addEventListener("DOMContentLoaded",()=>{
  $$("#tabs button").forEach(btn=>btn.onclick=()=>{
    $$("#tabs button").forEach(b=>b.classList.remove("active"));
    btn.classList.add("active");
    const tab=btn.getAttribute("data-tab");
    $$("main section").forEach(s=>s.style.display="none");
    $(`#tab-${tab}`).style.display="block";
    if(tab==="backoff") loadBackoffs();
    if(tab==="stats") loadStats();
    if(tab==="health") loadHealth();
    if(tab==="dashboard") loadDashboard();
    if(tab==="status") { loadStatus(); if(statusTimer) clearInterval(statusTimer); statusTimer=setInterval(loadStatus,10000); }
    else { if(statusTimer) { clearInterval(statusTimer); statusTimer=null; } }
    if(tab==="overlay") { loadOverlayEvents(); loadWorkBanner(); loadTop(); loadPreview(); }
  });
  // overlay sub tabs
  $$("[data-overlay-sub]").forEach(btn=>btn.onclick=()=>{
    $$("[data-overlay-sub]").forEach(b=>{ b.style.background=""; b.style.color=""; });
    btn.style.background="var(--accent)"; btn.style.color="#0a0c10";
    const sub=btn.getAttribute("data-overlay-sub");
    $$("#overlay-sub-notifications, #overlay-sub-work, #overlay-sub-top, #overlay-sub-preview").forEach(el=>el.style.display="none");
    const target=document.getElementById(`overlay-sub-${sub}`);
    if(target) target.style.display="block";
    if(sub==="notifications") loadOverlayEvents();
    if(sub==="work") loadWorkBanner();
    if(sub==="top") loadTop();
    if(sub==="preview") loadPreview();
  });
  // top mode radio change
  $$('input[name="top-mode"]').forEach(r=>r.onchange=()=>{
    const mode=$$('input[name="top-mode"]:checked')[0]?.value||"auto";
    for(let i=1;i<=4;i++){ const el=document.getElementById(`top-line-${i}`); if(el) el.disabled=(mode!=="manual"); }
  });
  // work banner toggle label
  const wbEn=document.getElementById("work-banner-enabled");
  if(wbEn) wbEn.onchange=(e)=>{ document.getElementById("work-banner-enabled-label").textContent=e.target.checked?"on":"off"; };
  // initial load
  loadConfig().catch(e=>toast(String(e),5000));
  loadBackoffs().catch(()=>{});
  loadDashboard();
  dashTimer=setInterval(loadDashboard,10000);
  // handlers
  $("#chains-save").onclick=saveChains;
  $("#chains-reload").onclick=()=>loadConfig().catch(e=>toast(String(e)));
  $("#backoff-save").onclick=saveBackoff;
  $("#backoff-reload").onclick=()=>loadConfig().catch(e=>toast(String(e)));
  $("#backoff-refresh").onclick=()=>loadBackoffs();
  $("#backoff-clear-all").onclick=async()=>{
    if(!confirm("全 backoff をクリアしますか？")) return;
    try{ await api("/api/backoffs/clear",{method:"POST"}); toast("全クリア"); await loadBackoffs(); await loadDashboard(); }catch(e){ toast(String(e)); }
  };
  $("#peak-save").onclick=savePeak;
  $("#peak-reload").onclick=()=>loadConfig().catch(e=>toast(String(e)));
  $("#stats-refresh").onclick=()=>loadStats();
  $("#health-refresh").onclick=()=>loadHealth();
  $("#do-reload").onclick=async()=>{
    try{ const r=await api("/api/reload",{method:"POST"}); toast(JSON.stringify(r.results)); await loadHealth(); }catch(e){ toast(String(e)); }
  };
  // status
  const sRefresh=document.getElementById("status-refresh");
  if(sRefresh) sRefresh.onclick=()=>loadStatus();
  // overlay
  const oERefresh=document.getElementById("overlay-events-refresh");
  if(oERefresh) oERefresh.onclick=()=>loadOverlayEvents();
  const oEClear=document.getElementById("overlay-events-clear");
  if(oEClear) oEClear.onclick=async()=>{ if(!confirm("全クリアしますか？")) return; try{ await api("/api/overlay/events",{method:"DELETE"}); toast("全クリア"); await loadOverlayEvents(); }catch(e){ toast(String(e)); } };
  const oPush=document.getElementById("overlay-notify-push");
  if(oPush) oPush.onclick=()=>pushOverlayEvent();
  const oBulkSave=document.getElementById("overlay-events-bulk-save");
  if(oBulkSave) oBulkSave.onclick=()=>saveOverlayBulk();
  const oBulkLoad=document.getElementById("overlay-events-bulk-load");
  if(oBulkLoad) oBulkLoad.onclick=()=>loadOverlayEvents();
  const wSave=document.getElementById("work-banner-save");
  if(wSave) wSave.onclick=()=>saveWorkBanner();
  const wDisable=document.getElementById("work-banner-disable");
  if(wDisable) wDisable.onclick=()=>disableWorkBanner();
  const wRefresh=document.getElementById("work-banner-refresh");
  if(wRefresh) wRefresh.onclick=()=>loadWorkBanner();
  const tSave=document.getElementById("top-save");
  if(tSave) tSave.onclick=()=>saveTop();
  const tRefresh=document.getElementById("top-refresh");
  if(tRefresh) tRefresh.onclick=()=>loadTop();
  const tDel=document.getElementById("top-delete");
  if(tDel) tDel.onclick=async()=>{ try{ await api("/api/overlay/top",{method:"DELETE"}); toast("自動に戻しました"); await loadTop(); }catch(e){ toast(String(e)); } };
  const pRefresh=document.getElementById("preview-refresh");
  if(pRefresh) pRefresh.onclick=()=>loadPreview();
  const pType=document.getElementById("preview-type");
  if(pType) pType.onchange=()=>loadPreview();
  const pRegion=document.getElementById("preview-region");
  if(pRegion) pRegion.onchange=()=>loadPreview();
});
</script>
</body>
</html>

"""


class _Handler(BaseHTTPRequestHandler):
    # set by server
    g: GlobalConfig
    soren_root: Path
    read_only: bool
    start_time: float

    def _set_cors(self):
        if self.g.webui.allow_cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,PUT,DELETE,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-WebUI-Token")
            self.send_header("Access-Control-Max-Age", "86400")

    def _check_auth(self) -> bool:
        token = _effective_token(self.g)
        if not token:
            return True
        # check header
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            val = auth[len("Bearer ") :].strip()
            if val == token:
                return True
        x = self.headers.get("X-WebUI-Token", "")
        if x and x == token:
            return True
        # for GET allow query ?token=
        if self.command == "GET":
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            qv = params.get("token", [""])[0]
            if qv and qv == token:
                return True
        return False

    def _send_json(self, code: int, obj: Any):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._set_cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, code: int, error: str, detail: str = "", field: str = ""):
        obj: dict[str, Any] = {"error": error}
        if detail:
            obj["detail"] = detail
        if field:
            obj["field"] = field
        self._send_json(code, obj)

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def log_message(self, format, *args):
        # suppress default stderr; we log via _log_request
        pass

    def do_GET(self):
        t0 = time.monotonic()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        status = 200
        try:
            if not self._check_auth():
                status = 401
                self._send_error_json(401, "unauthorized", "token required")
                return
            if path == "/":
                body = INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._set_cors()
                self.end_headers()
                self.wfile.write(body)
                status = 200
            elif path == "/api/health":
                status = self._handle_health()
            elif path == "/api/config":
                status = self._handle_get_config()
            elif path == "/api/backoffs":
                status = self._handle_get_backoffs()
            elif path == "/api/stats":
                days_raw = query.get("days", ["7"])[0]
                try:
                    days = int(days_raw)
                except ValueError:
                    days = 7
                days = max(1, min(30, days))
                status = self._handle_get_stats(days)
            elif path == "/api/game_state":
                status = self._handle_get_game_state()
            elif path == "/api/improve_state":
                status = self._handle_get_improve_state()
            elif path == "/api/workers":
                status = self._handle_get_workers()
            elif path == "/api/peak_status":
                status = self._handle_get_peak_status()
            elif path == "/api/overlay/status":
                status = self._handle_get_overlay_status()
            elif path == "/api/overlay/events":
                status = self._handle_get_overlay_events()
            elif path == "/api/overlay/work_banner":
                status = self._handle_get_work_banner()
            elif path == "/api/overlay/top":
                status = self._handle_get_top()
            elif path == "/api/overlay/preview":
                # ?type=event|broadcast&region=full|top|bottom|sidebar
                t = query.get("type", ["event"])[0]
                region = query.get("region", ["full"])[0]
                status = self._handle_get_preview(t, region)
            else:
                status = 404
                self._send_error_json(404, "not_found")
        finally:
            latency = int((time.monotonic() - t0) * 1000)
            _log_request(self.soren_root, "GET", parsed.path, status, latency)

    def do_PUT(self):
        t0 = time.monotonic()
        status = 200
        parsed = urllib.parse.urlparse(self.path)
        try:
            if self.read_only:
                status = 403
                self._send_error_json(403, "read_only", "read-only mode")
                return
            if not self._check_auth():
                status = 401
                self._send_error_json(401, "unauthorized")
                return
            if parsed.path == "/api/config":
                status = self._handle_put_config()
            elif parsed.path == "/api/overlay/work_banner":
                status = self._handle_put_work_banner()
            elif parsed.path == "/api/overlay/top":
                status = self._handle_put_top()
            elif parsed.path == "/api/overlay/events":
                status = self._handle_put_overlay_events()
            else:
                status = 404
                self._send_error_json(404, "not_found")
        finally:
            latency = int((time.monotonic() - t0) * 1000)
            _log_request(self.soren_root, "PUT", parsed.path, status, latency)

    def do_DELETE(self):
        t0 = time.monotonic()
        status = 200
        parsed = urllib.parse.urlparse(self.path)
        try:
            if self.read_only:
                status = 403
                self._send_error_json(403, "read_only")
                return
            if not self._check_auth():
                status = 401
                self._send_error_json(401, "unauthorized")
                return
            if parsed.path.startswith("/api/backoffs/"):
                # /api/backoffs/<sanitized>
                part = parsed.path[len("/api/backoffs/") :]
                # decode
                sanitized = urllib.parse.unquote(part)
                status = self._handle_delete_backoff(sanitized)
            elif parsed.path == "/api/overlay/work_banner":
                status = self._handle_delete_work_banner()
            elif parsed.path == "/api/overlay/top":
                status = self._handle_delete_top()
            elif parsed.path == "/api/overlay/events":
                status = self._handle_clear_overlay_events()
            elif parsed.path.startswith("/api/overlay/events/"):
                # /api/overlay/events/<idx>
                part = parsed.path[len("/api/overlay/events/") :]
                status = self._handle_delete_overlay_event(part)
            else:
                status = 404
                self._send_error_json(404, "not_found")
        finally:
            latency = int((time.monotonic() - t0) * 1000)
            _log_request(self.soren_root, "DELETE", parsed.path, status, latency)

    def do_POST(self):
        t0 = time.monotonic()
        status = 200
        parsed = urllib.parse.urlparse(self.path)
        try:
            if self.read_only:
                status = 403
                self._send_error_json(403, "read_only")
                return
            if not self._check_auth():
                status = 401
                self._send_error_json(401, "unauthorized")
                return
            if parsed.path == "/api/backoffs/clear":
                status = self._handle_clear_all_backoffs()
            elif parsed.path == "/api/reload":
                status = self._handle_reload()
            elif parsed.path == "/api/overlay/events":
                status = self._handle_post_overlay_event()
            elif parsed.path == "/api/overlay/events/bulk":
                status = self._handle_post_overlay_events_bulk()
            elif parsed.path == "/api/overlay/preview/refresh":
                status = self._handle_reload()  # alias
            else:
                status = 404
                self._send_error_json(404, "not_found")
        finally:
            latency = int((time.monotonic() - t0) * 1000)
            _log_request(self.soren_root, "POST", parsed.path, status, latency)

    # ---- handlers ----

    def _handle_health(self) -> int:
        dotenv_mtime = _dotenv_mtime(self.soren_root)
        reload_file = self.soren_root / "tmp/state/webui_reload.json"
        reload_data = None
        try:
            if reload_file.is_file():
                reload_data = json.loads(reload_file.read_text(encoding="utf-8"))
        except Exception:
            reload_data = None
        uptime = int(time.time() - self.start_time) if hasattr(self, "start_time") else 0
        self._send_json(
            200,
            {
                "ok": True,
                "soren_root": str(self.soren_root),
                "env_mtime": dotenv_mtime,
                "read_only": self.read_only,
                "bind": self.server.server_address[0] if hasattr(self.server, "server_address") else "",
                "port": self.server.server_address[1] if hasattr(self.server, "server_address") else 0,
                "uptime": uptime,
                "reload": reload_data,
            },
        )
        return 200

    def _handle_get_config(self) -> int:
        dotenv = _read_dotenv_dict(self.soren_root)
        mtime = _dotenv_mtime(self.soren_root)
        entries = []
        for key in sorted(WEBUI_ALLOWLIST):
            val = dotenv.get(key, "")
            eff = _effective_value(key, dotenv)
            default = DEFAULTS.get(key, "")
            # inherit defaults for empty inherited keys
            if key in ("RADIO_AGENTS", "RADIO_PREPASS_AGENTS") and not val:
                default = DEFAULTS["AI_COMMON_AGENTS"]
            if key == "COMMENT_TRANSLATION_AGENTS" and not val:
                # default is COMMENT_AGENTS effective
                default = DEFAULTS["AI_COMMON_AGENTS"]
            in_env = key in dotenv
            masked = False
            # masking not needed for allowlist but keep for safety
            if any(s in key for s in SECRET_SUBSTRINGS):
                masked = True
                val = "***" if val else ""
                eff = "***" if eff else ""
            entries.append(
                {
                    "key": key,
                    "value": val,
                    "effective": eff,
                    "default": default,
                    "in_env": in_env,
                    "masked": masked,
                }
            )
        self._send_json(
            200,
            {
                "soren_root": str(self.soren_root),
                "env_mtime": mtime,
                "read_only": self.read_only,
                "entries": entries,
            },
        )
        return 200

    def _read_body(self) -> tuple[bytes | None, int]:
        # returns (body, status); body None + status != 0 => error already sent
        raw_len = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(raw_len)
        except ValueError:
            self._send_error_json(400, "invalid_content_length", f"Content-Length が数値ではありません: {raw_len!r}")
            return None, 400
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_error_json(413, "body_too_large", f"Content-Length は 0..{MAX_BODY_BYTES} である必要があります")
            return None, 413
        if length == 0:
            return b"{}", 0
        data = self.rfile.read(length)
        if len(data) != length:
            self._send_error_json(400, "body_short_read", "ボディの読み取りが途中で終了しました")
            return None, 400
        return data, 0

    def _handle_put_config(self) -> int:
        body, err = self._read_body()
        if err:
            return err
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            self._send_error_json(400, "invalid_json", str(exc))
            return 400
        # support both {"values": {...}, "expected_mtime": N} and direct dict
        values = None
        expected_mtime = data.get("expected_mtime") if isinstance(data, dict) else None
        if isinstance(data, dict) and "values" in data and isinstance(data["values"], dict):
            values = data["values"]
        elif isinstance(data, dict):
            # if keys look like allowlist, treat as values directly
            if any(k in WEBUI_ALLOWLIST for k in data.keys()):
                values = {k: v for k, v in data.items() if k in WEBUI_ALLOWLIST}
                # expected_mtime may be present as well
            else:
                # empty or values key missing
                values = data.get("values", {})
                if not isinstance(values, dict):
                    values = {}
        else:
            values = {}
        # filter to allowlist
        updates: dict[str, str] = {}
        for k, v in values.items():
            if k not in WEBUI_ALLOWLIST:
                self._send_error_json(400, "not_allowed", f"key not in allowlist: {k}", field=k)
                return 400
            sv = str(v) if v is not None else ""
            try:
                _validate_value(k, sv)
            except ValueError as exc:
                self._send_error_json(400, "validation_error", str(exc), field=k)
                return 400
            updates[k] = sv
        if not updates:
            self._send_error_json(400, "empty_update", "no valid keys to update")
            return 400
        # handle If-Match header as well
        if expected_mtime is None:
            im = self.headers.get("If-Match")
            if im:
                try:
                    expected_mtime = int(im.strip().strip('"'))
                except ValueError:
                    expected_mtime = None
        try:
            new_mtime = _atomic_env_update(self.soren_root, updates, expected_mtime)
        except FileExistsError as exc:
            self._send_error_json(409, "concurrent_edit", str(exc))
            return 409
        except ValueError as exc:
            # validation or conflict
            msg = str(exc)
            if "mtime mismatch" in msg or "concurrent" in msg:
                self._send_error_json(409, "conflict", msg)
                return 409
            self._send_error_json(400, "validation_error", msg)
            return 400
        except Exception as exc:
            self._send_error_json(500, "write_failed", str(exc))
            return 500
        # auto reload signal (best effort)
        _send_reload(self.soren_root)
        self._send_json(200, {"ok": True, "env_mtime": new_mtime, "updated": list(updates.keys())})
        return 200

    def _handle_get_backoffs(self) -> int:
        bdir = _backoff_dir(self.soren_root)
        now = int(time.time())
        dotenv = _read_dotenv_dict(self.soren_root)
        # collect known agents from effective configs
        known_agents: set[str] = set()
        for key in ("AI_COMMON_AGENTS", "MODEL_IMPROVE_LIST", "RADIO_AGENTS", "RADIO_PREPASS_AGENTS", "COMMENT_AGENTS", "COMMENT_TRANSLATION_AGENTS"):
            eff = _effective_value(key, dotenv)
            if eff:
                for p in eff.split(","):
                    p = p.strip()
                    if p:
                        known_agents.add(p)
        # also include backoff names for default items
        # Map sanitized -> original
        sanitized_to_agent: dict[str, str] = {}
        for ag in known_agents:
            sanitized_to_agent[_sanitize_agent(ag)] = ag
        results: list[dict[str, Any]] = []
        # add entries for known agents
        for ag in sorted(known_agents):
            san = _sanitize_agent(ag)
            bf = bdir / san
            until: int | None = None
            remaining = 0
            active = False
            try:
                if bf.is_file():
                    raw = bf.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
                    until = int(raw)
                    remaining = max(0, until - now)
                    active = remaining > 0
                    # if stale, we report ready but still show remaining 0
                    if not active:
                        until = until  # keep for display
                else:
                    until = None
                    remaining = 0
                    active = False
            except Exception:
                until = None
                remaining = 0
                active = False
            results.append(
                {
                    "agent": ag,
                    "sanitized": san,
                    "until": until,
                    "remaining": remaining,
                    "remaining_text": _fmt_remaining(remaining) if active else "ready",
                    "active": active,
                }
            )
        # add stray files not matching known agents
        try:
            if bdir.is_dir():
                for p in bdir.iterdir():
                    if not p.is_file():
                        continue
                    san = p.name
                    if san in sanitized_to_agent:
                        continue
                    # unknown file
                    try:
                        raw = p.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
                        until = int(raw)
                        remaining = max(0, until - now)
                        active = remaining > 0
                    except Exception:
                        until = None
                        remaining = 0
                        active = False
                    results.append(
                        {
                            "agent": san,
                            "sanitized": san,
                            "until": until,
                            "remaining": remaining,
                            "remaining_text": _fmt_remaining(remaining) if active else "ready",
                            "active": active,
                        }
                    )
        except Exception:
            pass
        self._send_json(200, {"backoffs": results, "now": now, "backoff_dir": str(bdir)})
        return 200

    def _handle_delete_backoff(self, sanitized: str) -> int:
        if not sanitized:
            self._send_error_json(400, "invalid_agent", "empty")
            return 400
        # パストラバーサル遮断: 安全なファイル名のみ許可
        if "/" in sanitized or "\\" in sanitized or ".." in sanitized:
            self._send_error_json(400, "invalid_agent", "path traversal not allowed")
            return 400
        bdir = _backoff_dir(self.soren_root)
        # 元のエージェント名と sanitize 済み ID の両方を試す
        candidates = {sanitized, _sanitize_agent(sanitized)}
        deleted = False
        for cand in candidates:
            target = bdir / cand
            try:
                if target.is_file():
                    target.unlink()
                    deleted = True
            except Exception:
                pass
        self._send_json(200, {"ok": True, "deleted": deleted, "sanitized": _sanitize_agent(sanitized)})
        return 200

    def _handle_clear_all_backoffs(self) -> int:
        bdir = _backoff_dir(self.soren_root)
        count = 0
        try:
            if bdir.is_dir():
                for p in bdir.iterdir():
                    if p.is_file():
                        try:
                            p.unlink()
                            count += 1
                        except Exception:
                            pass
        except Exception as exc:
            self._send_error_json(500, "clear_failed", str(exc))
            return 500
        self._send_json(200, {"ok": True, "cleared": count})
        return 200

    def _handle_get_stats(self, days: int) -> int:
        sdir = _stats_dir(self.soren_root)
        now = time.time()
        # collect days: sorted newest last
        all_files: list[Path] = []
        try:
            if sdir.is_dir():
                all_files = sorted(sdir.glob("*.jsonl"))
        except Exception:
            all_files = []
        # take last `days` files
        files = all_files[-days:] if len(all_files) > days else all_files
        day_stats: list[dict[str, Any]] = []
        by_agent: dict[str, dict[str, int]] = {}
        for f in files:
            day = f.stem
            attempt = 0
            ok = 0
            fail = 0
            winner = 0
            all_failed = 0
            try:
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    ev = rec.get("event", "")
                    ag = rec.get("agent", "")
                    if ev == "attempt":
                        attempt += 1
                        if ag:
                            by_agent.setdefault(ag, {"attempt": 0, "winner": 0})
                            by_agent[ag]["attempt"] += 1
                    elif ev == "ok":
                        ok += 1
                    elif ev == "fail":
                        fail += 1
                    elif ev == "winner":
                        winner += 1
                        if ag:
                            by_agent.setdefault(ag, {"attempt": 0, "winner": 0})
                            by_agent[ag]["winner"] += 1
                    elif ev == "all_failed":
                        all_failed += 1
            except Exception:
                continue
            day_stats.append(
                {"day": day, "attempt": attempt, "ok": ok, "fail": fail, "winner": winner, "all_failed": all_failed}
            )
        self._send_json(200, {"days": day_stats, "by_agent": by_agent, "stats_dir": str(sdir)})
        return 200

    def _handle_get_game_state(self) -> int:
        path = _game_state_path(self.soren_root)
        mtime = 0
        try:
            mtime = int(path.stat().st_mtime) if path.is_file() else 0
        except Exception:
            mtime = 0
        data = _load_json_file(path)
        exists = data is not None
        if not exists:
            data = None
        state = ""
        score = None
        if isinstance(data, dict):
            state = str(data.get("state", "") or "")
            score = data.get("score")
        self._send_json(200, {"exists": exists, "path": str(path), "mtime": mtime, "data": data, "state": state, "score": score})
        return 200

    def _handle_get_improve_state(self) -> int:
        path = _improve_state_path(self.soren_root)
        lock_path = _improve_lock_path(self.soren_root)
        data = _load_json_file(path)
        exists = data is not None
        if data is None:
            data = {}
        is_locked = lock_path.is_file()
        pid = None
        alive = False
        try:
            pid = int(data.get("pid", 0) or 0) if isinstance(data, dict) else 0
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
            else:
                pid = None
        except Exception:
            pid = None
            alive = False
        mtime = 0
        try:
            mtime = int(path.stat().st_mtime) if path.is_file() else 0
        except Exception:
            mtime = 0
        status = str(data.get("status", "idle") if isinstance(data, dict) else "idle")
        self._send_json(200, {"exists": exists, "path": str(path), "mtime": mtime, "data": data, "status": status, "is_locked": is_locked, "pid": pid, "alive": alive})
        return 200

    def _handle_get_workers(self) -> int:
        workers = _get_workers_status(self.soren_root)
        self._send_json(200, {"workers": workers, "now": int(time.time())})
        return 200

    def _handle_get_peak_status(self) -> int:
        try:
            info = _get_peak_status(self.soren_root)
        except Exception as exc:
            info = {
                "windows": "",
                "tz": "Asia/Tokyo",
                "swap_enabled": "1",
                "gate_enabled": "1",
                "preference": "",
                "priority_agent": "",
                "is_peak_now": False,
                "now_minutes": None,
                "now_str": "",
                "error": str(exc),
            }
        self._send_json(200, info)
        return 200

    def _handle_reload(self) -> int:
        results = _send_reload(self.soren_root)
        self._send_json(200, {"ok": True, "results": results})
        return 200

    def _handle_get_overlay_status(self) -> int:
        now = int(time.time())
        # game
        g_path = _game_state_path(self.soren_root)
        g_data = _load_json_file(g_path)
        try:
            g_mtime = int(g_path.stat().st_mtime) if g_path.is_file() else 0
        except Exception:
            g_mtime = 0
        # improve
        imp_path = _improve_state_path(self.soren_root)
        lock_path = _improve_lock_path(self.soren_root)
        imp_data = _load_json_file(imp_path)
        imp_exists = imp_data is not None
        if imp_data is None:
            imp_data = {}
        is_locked = lock_path.is_file()
        pid = None
        alive = False
        try:
            pid = int(imp_data.get("pid", 0) or 0) if isinstance(imp_data, dict) else 0
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
            else:
                pid = None
        except Exception:
            pid = None
            alive = False
        try:
            imp_mtime = int(imp_path.stat().st_mtime) if imp_path.is_file() else 0
        except Exception:
            imp_mtime = 0
        status = str(imp_data.get("status", "idle") if isinstance(imp_data, dict) else "idle")
        # wildcard
        wc_data = _get_wildcard_status(self.soren_root)
        wc_path = _wildcard_status_path(self.soren_root)
        try:
            wc_mtime = int(wc_path.stat().st_mtime) if wc_path.is_file() else 0
        except Exception:
            wc_mtime = 0
        # generators
        gens = _get_gen_indicators(self.soren_root, now)
        # comment/raw
        c_path = _comment_gen_state_path(self.soren_root)
        try:
            c_raw = c_path.read_text(encoding="utf-8", errors="ignore").strip() if c_path.is_file() else ""
        except Exception:
            c_raw = ""
        c_mtime = 0
        try:
            c_mtime = int(c_path.stat().st_mtime) if c_path.is_file() else 0
        except Exception:
            c_mtime = 0
        # radio
        r_path = _radio_state_path(self.soren_root)
        try:
            r_raw = r_path.read_text(encoding="utf-8", errors="ignore").strip() if r_path.is_file() else ""
        except Exception:
            r_raw = ""
        try:
            r_mtime = int(r_path.stat().st_mtime) if r_path.is_file() else 0
        except Exception:
            r_mtime = 0
        # work banner
        work = _load_work_indicator(self.soren_root)
        w_path = _work_indicator_path(self.soren_root)
        try:
            w_mtime = int(w_path.stat().st_mtime) if w_path.is_file() else 0
        except Exception:
            w_mtime = 0
        # game count
        gc_path = self.soren_root / "game_count.txt"
        gc_val = None
        try:
            if gc_path.is_file():
                txt = gc_path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
                gc_val = int(txt.strip()) if txt.strip().isdigit() else None
        except Exception:
            gc_val = None
        try:
            gc_mtime = int(gc_path.stat().st_mtime) if gc_path.is_file() else 0
        except Exception:
            gc_mtime = 0
        # workers
        workers = _get_workers_status(self.soren_root)
        # overlay events
        ev_keep, ev_visible = _get_overlay_keep_visible(self.soren_root)
        ev_path = _overlay_events_path(self.soren_root)
        try:
            ev_count = len(_load_overlay_events(self.soren_root))
        except Exception:
            ev_count = 0
        try:
            ev_mtime = int(ev_path.stat().st_mtime) if ev_path.is_file() else 0
        except Exception:
            ev_mtime = 0
        # log tail for improve
        log_tail: list[str] = []
        try:
            log_path = self.soren_root / "tmp/debug/improve_ai.log"
            if log_path.is_file():
                txt = log_path.read_text(encoding="utf-8", errors="ignore")
                lines = [l for l in txt.splitlines() if l.strip()]
                # strip ANSI
                import re as _re
                ansi_re = _re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
                log_tail = [ansi_re.sub("", l) for l in lines[-6:]]
        except Exception:
            log_tail = []
        self._send_json(200, {
            "now": now,
            "game": {"exists": g_data is not None, "path": str(g_path), "mtime": g_mtime, "data": g_data, "state": str(g_data.get("state","") if isinstance(g_data, dict) else ""), "score": g_data.get("score") if isinstance(g_data, dict) else None},
            "improve": {"exists": imp_exists, "path": str(imp_path), "mtime": imp_mtime, "data": imp_data, "status": status, "pid": pid, "alive": alive, "is_locked": is_locked, "log_tail": log_tail},
            "wildcard": {"exists": wc_data is not None, "path": str(wc_path), "mtime": wc_mtime, "data": wc_data},
            "generators": gens,
            "comment_gen": {"path": str(c_path), "mtime": c_mtime, "raw": c_raw, "exists": bool(c_raw)},
            "radio_state": {"path": str(r_path), "mtime": r_mtime, "raw": r_raw, "exists": bool(r_raw)},
            "work_banner": work,
            "work_banner_path": str(w_path),
            "work_banner_mtime": w_mtime,
            "game_count": {"path": str(gc_path), "mtime": gc_mtime, "value": gc_val, "exists": gc_val is not None},
            "workers": workers,
            "overlay_events": {"path": str(ev_path), "mtime": ev_mtime, "count": ev_count, "keep": ev_keep, "visible_sec": ev_visible},
        })
        return 200

    def _handle_get_overlay_events(self) -> int:
        keep, visible = _get_overlay_keep_visible(self.soren_root)
        events = _load_overlay_events(self.soren_root)
        p = _overlay_events_path(self.soren_root)
        try:
            mtime = int(p.stat().st_mtime) if p.is_file() else 0
        except Exception:
            mtime = 0
        self._send_json(200, {"path": str(p), "mtime": mtime, "keep": keep, "visible_sec": visible, "count": len(events), "events": events})
        return 200

    def _handle_post_overlay_event(self) -> int:
        body, err = self._read_body()
        if err:
            return err
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            self._send_error_json(400, "invalid_json", str(exc))
            return 400
        try:
            ev = _validate_overlay_event(data if isinstance(data, dict) else {})
        except ValueError as exc:
            self._send_error_json(400, "validation_error", str(exc))
            return 400
        keep, _ = _get_overlay_keep_visible(self.soren_root)
        # load existing, append, trim to keep
        events = _load_overlay_events(self.soren_root)
        events.append(ev)
        if len(events) > keep:
            events = events[-keep:]
        # write
        p = _overlay_events_path(self.soren_root)
        content = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + ("\n" if events else "")
        try:
            _atomic_overlay_write(self.soren_root, p, content, 0o644)
        except FileExistsError as exc:
            self._send_error_json(409, "concurrent_edit", str(exc))
            return 409
        except Exception as exc:
            self._send_error_json(500, "write_failed", str(exc))
            return 500
        _regenerate_event_overlay(self.soren_root)
        self._send_json(200, {"ok": True, "event": ev, "count": len(events)})
        return 200

    def _handle_post_overlay_events_bulk(self) -> int:
        # For completeness, not used directly; PUT /api/overlay/events handles bulk replace
        return self._handle_put_overlay_events()

    def _handle_put_overlay_events(self) -> int:
        body, err = self._read_body()
        if err:
            return err
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            self._send_error_json(400, "invalid_json", str(exc))
            return 400
        # support either {"events": [...]} or raw array
        arr = None
        if isinstance(data, dict) and "events" in data:
            arr = data["events"]
        elif isinstance(data, list):
            arr = data
        elif isinstance(data, dict) and any(k in data for k in ("category", "title")):
            # single event
            arr = [data]
        else:
            arr = data.get("events", []) if isinstance(data, dict) else []
        if not isinstance(arr, list):
            self._send_error_json(400, "validation_error", "events must be array")
            return 400
        keep, _ = _get_overlay_keep_visible(self.soren_root)
        if len(arr) > keep:
            self._send_error_json(400, "validation_error", f"events too many (keep {keep})")
            return 400
        validated: list[dict[str, Any]] = []
        for idx, item in enumerate(arr):
            try:
                validated.append(_validate_overlay_event(item if isinstance(item, dict) else {}))
            except ValueError as exc:
                self._send_error_json(400, "validation_error", f"index {idx}: {exc}")
                return 400
        p = _overlay_events_path(self.soren_root)
        content = "\n".join(json.dumps(e, ensure_ascii=False) for e in validated) + ("\n" if validated else "")
        try:
            _atomic_overlay_write(self.soren_root, p, content, 0o644)
        except FileExistsError as exc:
            self._send_error_json(409, "concurrent_edit", str(exc))
            return 409
        except Exception as exc:
            self._send_error_json(500, "write_failed", str(exc))
            return 500
        _regenerate_event_overlay(self.soren_root)
        self._send_json(200, {"ok": True, "count": len(validated)})
        return 200

    def _handle_clear_overlay_events(self) -> int:
        p = _overlay_events_path(self.soren_root)
        try:
            _atomic_overlay_write(self.soren_root, p, "", 0o644)
        except FileExistsError as exc:
            self._send_error_json(409, "concurrent_edit", str(exc))
            return 409
        except Exception as exc:
            self._send_error_json(500, "write_failed", str(exc))
            return 500
        _regenerate_event_overlay(self.soren_root)
        self._send_json(200, {"ok": True, "cleared": True})
        return 200

    def _handle_delete_overlay_event(self, part: str) -> int:
        try:
            idx = int(part.strip())
        except Exception:
            self._send_error_json(400, "invalid_index", "index must be integer")
            return 400
        events = _load_overlay_events(self.soren_root)
        if idx < 0 or idx >= len(events):
            self._send_error_json(404, "not_found", f"index {idx} out of range")
            return 404
        events.pop(idx)
        p = _overlay_events_path(self.soren_root)
        content = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + ("\n" if events else "")
        try:
            _atomic_overlay_write(self.soren_root, p, content, 0o644)
        except FileExistsError as exc:
            self._send_error_json(409, "concurrent_edit", str(exc))
            return 409
        except Exception as exc:
            self._send_error_json(500, "write_failed", str(exc))
            return 500
        _regenerate_event_overlay(self.soren_root)
        self._send_json(200, {"ok": True, "deleted": idx, "count": len(events)})
        return 200

    def _handle_get_work_banner(self) -> int:
        p = _work_indicator_path(self.soren_root)
        work = _load_work_indicator(self.soren_root)
        try:
            mtime = int(p.stat().st_mtime) if p.is_file() else 0
        except Exception:
            mtime = 0
        if work is None:
            self._send_json(200, {"active": False, "exists": False, "path": str(p), "mtime": mtime})
            return 200
        self._send_json(200, {"active": True, "exists": True, "path": str(p), "mtime": mtime, "title": work.get("title",""), "body": work.get("body",""), "ts": work.get("ts",0), "data": work})
        return 200

    def _handle_put_work_banner(self) -> int:
        body, err = self._read_body()
        if err:
            return err
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            self._send_error_json(400, "invalid_json", str(exc))
            return 400
        if not isinstance(data, dict):
            self._send_error_json(400, "validation_error", "body must be object")
            return 400
        active = bool(data.get("active", True))
        title = str(data.get("title", "")).strip()
        body_txt = str(data.get("body", "")).strip()
        if active:
            if not title:
                title = "システム自動分析・修正作業中"
            if not body_txt:
                body_txt = "メリケンAI が確認・修正・検証を進めています"
            try:
                title = _sanitize_overlay_text(title, WORK_TITLE_LIMIT)
                body_txt = _sanitize_overlay_text(body_txt, WORK_BODY_LIMIT)
            except ValueError as exc:
                self._send_error_json(400, "validation_error", str(exc))
                return 400
            state = {"active": True, "ts": int(time.time()), "title": title, "body": body_txt}
            content = json.dumps(state, ensure_ascii=False) + "\n"
            p = _work_indicator_path(self.soren_root)
            try:
                _atomic_overlay_write(self.soren_root, p, content, 0o644)
            except FileExistsError as exc:
                self._send_error_json(409, "concurrent_edit", str(exc))
                return 409
            except Exception as exc:
                self._send_error_json(500, "write_failed", str(exc))
                return 500
            _regenerate_event_overlay(self.soren_root)
            self._send_json(200, {"ok": True, "active": True, "title": title, "body": body_txt})
            return 200
        else:
            p = _work_indicator_path(self.soren_root)
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                pass
            _regenerate_event_overlay(self.soren_root)
            self._send_json(200, {"ok": True, "active": False})
            return 200

    def _handle_delete_work_banner(self) -> int:
        p = _work_indicator_path(self.soren_root)
        deleted = False
        try:
            if p.is_file():
                p.unlink()
                deleted = True
        except Exception as exc:
            self._send_error_json(500, "delete_failed", str(exc))
            return 500
        _regenerate_event_overlay(self.soren_root)
        self._send_json(200, {"ok": True, "deleted": deleted, "active": False})
        return 200

    def _handle_get_top(self) -> int:
        p = _top_override_path(self.soren_root)
        data = _load_top_override(self.soren_root)
        try:
            mtime = int(p.stat().st_mtime) if p.is_file() else 0
        except Exception:
            mtime = 0
        if data is None:
            self._send_json(200, {"exists": False, "path": str(p), "mtime": mtime, "enabled": None, "mode": "auto", "lines": []})
            return 200
        enabled = data.get("enabled")
        lines = data.get("lines", [])
        if not isinstance(lines, list):
            lines = []
        # sanitize for display
        lines = [str(x) for x in lines][:TOP_MAX_LINES]
        mode = "auto"
        if enabled is True:
            mode = "manual"
        elif enabled is False:
            mode = "hidden"
        self._send_json(200, {"exists": True, "path": str(p), "mtime": mtime, "enabled": enabled, "mode": mode, "lines": lines, "updated_at": data.get("updated_at"), "data": data})
        return 200

    def _handle_put_top(self) -> int:
        body, err = self._read_body()
        if err:
            return err
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            self._send_error_json(400, "invalid_json", str(exc))
            return 400
        if not isinstance(data, dict):
            self._send_error_json(400, "validation_error", "body must be object")
            return 400
        enabled = data.get("enabled")
        # enabled can be None (auto), True (manual), False (hidden)
        if enabled is None and "enabled" not in data and "lines" not in data:
            self._send_error_json(400, "validation_error", "enabled or lines required")
            return 400
        if enabled is None and "enabled" not in data:
            # treat as delete auto?
            enabled = None
        elif enabled is not None and not isinstance(enabled, bool):
            # allow string "true"/"false" ?
            if isinstance(enabled, str):
                if enabled.lower() in ("true","1"):
                    enabled = True
                elif enabled.lower() in ("false","0"):
                    enabled = False
                else:
                    self._send_error_json(400, "validation_error", "enabled must be boolean")
                    return 400
            else:
                self._send_error_json(400, "validation_error", "enabled must be boolean")
                return 400
        lines = data.get("lines", [])
        if not isinstance(lines, list):
            self._send_error_json(400, "validation_error", "lines must be array")
            return 400
        if enabled is True:
            if not lines:
                self._send_error_json(400, "validation_error", "manual mode requires 1-4 lines")
                return 400
            if len(lines) > TOP_MAX_LINES:
                self._send_error_json(400, "validation_error", f"lines max {TOP_MAX_LINES}")
                return 400
            cleaned: list[str] = []
            for idx, l in enumerate(lines):
                s = str(l).strip()
                if not s:
                    self._send_error_json(400, "validation_error", f"line {idx} empty")
                    return 400
                if len(s) > TOP_LINE_LIMIT:
                    self._send_error_json(400, "validation_error", f"line {idx} too long")
                    return 400
                if any(ord(c) < 32 and c not in ("\t",) for c in s):
                    self._send_error_json(400, "validation_error", f"line {idx} has control chars")
                    return 400
                cleaned.append(s)
            lines = cleaned
        elif enabled is False:
            lines = []
        else: # enabled is None -> auto (delete)
            p = _top_override_path(self.soren_root)
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                pass
            self._send_json(200, {"ok": True, "mode": "auto", "deleted": True})
            return 200
        # write manual/hidden
        p = _top_override_path(self.soren_root)
        payload = {"enabled": enabled, "lines": lines, "updated_at": int(time.time()), "updated_by": "webui"}
        content = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            _atomic_overlay_write(self.soren_root, p, content, 0o644)
        except FileExistsError as exc:
            self._send_error_json(409, "concurrent_edit", str(exc))
            return 409
        except Exception as exc:
            self._send_error_json(500, "write_failed", str(exc))
            return 500
        mode = "manual" if enabled is True else "hidden"
        self._send_json(200, {"ok": True, "mode": mode, "enabled": enabled, "lines": lines})
        return 200

    def _handle_delete_top(self) -> int:
        p = _top_override_path(self.soren_root)
        deleted = False
        try:
            if p.is_file():
                p.unlink()
                deleted = True
        except Exception as exc:
            self._send_error_json(500, "delete_failed", str(exc))
            return 500
        self._send_json(200, {"ok": True, "deleted": deleted, "mode": "auto"})
        return 200

    def _handle_get_preview(self, typ: str, region: str) -> int:
        if typ not in ("event", "broadcast", "status", "improve"):
            typ = "event"
        if region not in ("full", "top", "bottom", "sidebar"):
            region = "full"
        if typ == "event":
            p = _overlay_html_path(self.soren_root)
            try:
                html = p.read_text(encoding="utf-8", errors="ignore")
                mtime = int(p.stat().st_mtime) if p.is_file() else 0
            except Exception:
                html = ""
                mtime = 0
            self._send_json(200, {"type": typ, "region": region, "path": str(p), "mtime": mtime, "html": html})
            return 200
        elif typ == "broadcast":
            # For broadcast, we return the static HTML source for preview; dynamic state is via /__soren_overlay/broadcast/state but we can embed.
            # Try to find direct_broadcast_overlay.html
            cand = self.soren_root / "overlays/direct_broadcast_overlay.html"
            if not cand.is_file():
                # try games/soviet_now
                cand2 = Path(__file__).resolve().parents[2] / "games/soviet_now/overlays/direct_broadcast_overlay.html"
                if cand2.is_file():
                    cand = cand2
            try:
                html = cand.read_text(encoding="utf-8", errors="ignore") if cand.is_file() else ""
                mtime = int(cand.stat().st_mtime) if cand.is_file() else 0
                self._send_json(200, {"type": typ, "region": region, "path": str(cand), "mtime": mtime, "html": html})
            except Exception as exc:
                self._send_error_json(500, "read_failed", str(exc))
                return 500
            return 200
        else:
            p = self.soren_root / f"tmp/state/{'status' if typ=='status' else 'improve'}_overlay.html"
            try:
                html = p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""
                mtime = int(p.stat().st_mtime) if p.is_file() else 0
                self._send_json(200, {"type": typ, "region": region, "path": str(p), "mtime": mtime, "html": html})
            except Exception as exc:
                self._send_error_json(500, "read_failed", str(exc))
                return 500
            return 200


def _dotenv_quote(value: str) -> str:
    # .env は bash で source される。空白やシェルメタ文字を含む値は
    # ダブルクォートで包み、内部の " \ $ ` をエスケープする。
    if not re.search(r"[\s\"'\\$`;&|<>()*?~#]", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{escaped}"'


def _atomic_env_update(soren_root: Path, updates: dict[str, str], expected_mtime: int | None) -> int:
    lock_dir = soren_root / "tmp/state/.webui_env.lock"
    # acquire mkdir lock with timeout 30s stale
    try:
        lock_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        # check stale
        try:
            age = time.time() - lock_dir.stat().st_mtime
            if age > 30:
                # stale, remove and retry once
                import shutil

                shutil.rmtree(lock_dir, ignore_errors=True)
                lock_dir.mkdir(parents=True, exist_ok=False)
            else:
                raise FileExistsError(f"another edit in progress (age {int(age)}s)")
        except FileExistsError:
            raise
        except Exception as exc:
            raise FileExistsError(str(exc))
    try:
        env_path = _dotenv_path(soren_root)
        # 防御: キーは allowlist に限定 (呼び出し側に依存しない)
        for k in updates:
            if k not in WEBUI_ALLOWLIST:
                raise ValueError(f"key not in allowlist: {k}")
        current_mtime = _dotenv_mtime(soren_root)
        if expected_mtime is not None and int(expected_mtime) != int(current_mtime):
            raise ValueError(f"mtime mismatch: expected {expected_mtime}, current {current_mtime} (concurrent edit)")
        # backup (名前衝突回避のため ns タイムスタンプ)
        if env_path.is_file():
            backup = soren_root / f".env.bak.{time.time_ns()}"
            try:
                data = env_path.read_bytes()
                backup.write_bytes(data)
                backup.chmod(0o600)
            except Exception:
                print(f"docich: 警告: .env バックアップ作成に失敗しました ({backup.name})", flush=True)
            # 古いバックアップを整理 (7日より古いもの)
            cutoff = time.time() - 7 * 86400
            try:
                for old in soren_root.glob(".env.bak.*"):
                    try:
                        if old.stat().st_mtime < cutoff:
                            old.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                lines = []
        else:
            lines = []
        # build new lines: remove existing keys that are being updated
        new_lines: list[str] = []
        updated_keys = set(updates.keys())
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                new_lines.append(line)
                continue
            k = stripped.split("=", 1)[0].strip()
            if k in updated_keys:
                # skip old line (will be appended at end)
                continue
            new_lines.append(line)
        # append updates
        for k, v in updates.items():
            # 継承キー (RADIO_AGENTS 等) の空値 = 行削除 (既定へ復帰)
            # PEAK_HOURS_WINDOWS は config.sh が ${VAR-...} で読むため、
            # 「空=無効」を明示するには空行を残す必要がある
            if v == "":
                if k == "PEAK_HOURS_WINDOWS":
                    new_lines.append("PEAK_HOURS_WINDOWS=")
                continue
            new_lines.append(f"{k}={_dotenv_quote(v)}")
        # ensure file ends with newline
        content = "\n".join(new_lines) + "\n"
        # atomic write via temp file
        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=str(soren_root), prefix=".env.tmp.")
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            tmp_path.chmod(0o600)
            # replace
            os.replace(str(tmp_path), str(env_path))
            env_path.chmod(0o600)
            # touch to ensure mtime update
            try:
                env_path.touch(exist_ok=True)
            except Exception:
                pass
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
        return _dotenv_mtime(soren_root)
    finally:
        try:
            lock_dir.rmdir()
        except Exception:
            try:
                import shutil

                shutil.rmtree(lock_dir, ignore_errors=True)
            except Exception:
                pass


# --- public entrypoint -------------------------------------------------------


def run_webui(
    g: GlobalConfig,
    bind: str | None = None,
    port: int | None = None,
    soren_root: str | None = None,
    read_only: bool | None = None,
    dry_run: bool = False,
) -> int:
    effective_bind = bind or g.webui.bind or "127.0.0.1"
    effective_port = port if port is not None else (g.webui.port or 8787)
    eff_soren_root = _resolve_soren_root(g, soren_root)
    eff_read_only = read_only if read_only is not None else bool(g.webui.read_only)

    if dry_run:
        print(f"docich webui dry-run")
        print(f"  bind: {effective_bind}:{effective_port}")
        print(f"  soren_root: {eff_soren_root}")
        print(f"  read_only: {eff_read_only}")
        print(f"  token: {'set' if _effective_token(g) else '(none)'}")
        print(f"  allow_cors: {g.webui.allow_cors}")
        # validate soren_root
        if not (eff_soren_root / "eloop_lib.sh").is_file():
            print(f"  WARNING: {eff_soren_root}/eloop_lib.sh not found (soren_root may be wrong)")
        if not (eff_soren_root / ".env").is_file():
            print(f"  WARNING: {eff_soren_root}/.env not found (will be created on first save)")
        print("  endpoints: /, /api/health, /api/config, /api/backoffs, /api/stats, /api/reload, /api/game_state, /api/improve_state, /api/workers, /api/peak_status")
        return 0

    # validate soren_root
    if not eff_soren_root.is_dir():
        print(f"docich: エラー: soren_root が見つかりません: {eff_soren_root}", flush=True)
        return 2
    if not (eff_soren_root / "eloop_lib.sh").is_file():
        print(f"docich: 警告: {eff_soren_root}/eloop_lib.sh が見つかりません (soren_rootの指定を確認してください)", flush=True)
    if eff_read_only:
        print(f"docich: webui read-only mode enabled")

    # warn if binding to 0.0.0.0
    if effective_bind == "0.0.0.0":
        print(f"docich: 警告: 0.0.0.0 にバインドするとインターネットから到達可能です。Tailscale serve (127.0.0.1) を推奨します", flush=True)

    token = _effective_token(g)
    if token:
        print(f"docich: webui token 認証が有効です (env {g.webui.token_env})")
    else:
        print(f"docich: webui は Tailscale ACL のみに依存します (token無し)")

    # set up handler class with closure
    class BoundHandler(_Handler):
        pass

    BoundHandler.g = g
    BoundHandler.soren_root = eff_soren_root
    BoundHandler.read_only = eff_read_only
    BoundHandler.start_time = time.time()

    server = ThreadingHTTPServer((effective_bind, effective_port), BoundHandler)
    # allow address reuse
    server.daemon_threads = True

    print(f"docich: webui 起動: http://{effective_bind}:{effective_port}/ (soren_root={eff_soren_root})")
    if effective_bind == "127.0.0.1":
        print(f"docich: Tailscale 公開例: tailscale serve --bg --https=443 http://127.0.0.1:{effective_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0
