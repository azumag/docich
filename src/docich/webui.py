"""docich webui: Tailscale 経由のモデルチェーン / バックオフ管理 UI (stdlib only).

Architecture: ThreadingHTTPServer + vanilla JS single-page.
Security: Tailscale ACL (loopback bind + tailscale serve) が主防御。任意 token は
  二層目 (未設定なら無効)。非loopback bind + read_only=false (writable) + token
  未設定という組み合わせは起動時 error にする (issue #41, fail closed)。token は
  URL query では受理/生成しない (Authorization: Bearer / X-WebUI-Token ヘッダのみ、
  timing-safe 比較)。
Mutation defense (issue #42): PUT/POST/DELETE には Host/Origin allowlist・
  CSRF token (Authorization と別の HMAC 署名 token、Origin 非送信のフォームでは
  取得不能)・Content-Type (application/json 必須。HTML form は送れない) の3層を
  課す。read_only_token で認証した caller は "viewer" identity として全 mutation
  を拒否 (server 全体の webui.read_only とは独立)。/api/workers, /api/stream,
  /api/config の PUT/POST は追加で confirm:true (または X-Docich-Confirm ヘッダ)
  を要求する (dangerous action の再確認)。token 未設定 (既定の loopback 開発) でも
  Host/Origin/CSRF/Content-Type チェック自体は有効なままで、CSRF token の発行
  (`GET /api/csrf`) は token 有無に関わらず _check_auth() と同じ経路で行う。
State: soren_root = ELOOP_LIB_DIR 相当 (games/soviet_now or /home/ubuntu/soren)。
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import speech
from .config import (
    GlobalConfig,
    _parse_origin_str,
    effective_read_only_token,
    effective_webui_token,
    is_loopback_bind,
)
from .runtime_backend import (
    CAPABILITY_GET_STATUS,
    CAPABILITY_LIST_WORKERS,
    TOGGLEABLE_WORKERS,
    RuntimeBackend,
    SorenBackend,
    _find_worker_pid,
    _game_state_path,
    _get_workers_status,
    _is_worker_paused,
    _pid_is_active,
    _pid_matches_worker_process,
    _process_is_zombie,
    _read_game_status,
    _set_worker_paused,
    _worker_pause_marker_path,
)

# --- allowlist / validation --------------------------------------------------

WEBUI_ALLOWLIST = {
    # chain (core/config.sh:33-78)
    "AI_COMMON_AGENTS",
    "MODEL_IMPROVE_LIST",
    "MODEL_IMPROVE_PEAK_LIST",
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
    # improve peak (core/config.sh:289-292)
    "IMPROVE_PEAK_CHAIN_ENABLED",
    "IMPROVE_PEAK_HOUR_DEFER_ENABLED",
    # stream (lib/direct_stream.py load_config) — 変更反映には direct_stream の再起動が必要
    "SOREN_DIRECT_STREAM_SIZE",
    "SOREN_DIRECT_STREAM_FPS",
    "SOREN_DIRECT_STREAM_VIDEO_KBPS",
    "SOREN_DIRECT_STREAM_AUDIO_KBPS",
    "SOREN_DIRECT_STREAM_AUDIO_DELAY_MS",
    "DOCICH_CC_ENABLED",
}

# hard defaults from core/config.sh
DEFAULTS: dict[str, str] = {
    "AI_COMMON_AGENTS": "opencode:deepseek-v4-flash-free,codex:amd-token-factory-deepseek-v4-flash,codex:openrouter/free,local,codex:deepseek-v4-flash,codex:minimax-m3,opencode-go:muse-spark-1.2-contributor",
    "MODEL_IMPROVE_LIST": "opencode:deepseek-v4-flash-free,codex:amd-token-factory-deepseek-v4-flash,codex:deepseek-v4-flash,codex:minimax-m3",
    "MODEL_IMPROVE_PEAK_LIST": "",  # inherits MODEL_IMPROVE_LIST
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
    "IMPROVE_PEAK_CHAIN_ENABLED": "0",
    "IMPROVE_PEAK_HOUR_DEFER_ENABLED": "0",
    # lib/direct_stream.py load_config の既定値
    "SOREN_DIRECT_STREAM_SIZE": "1280x720",
    "SOREN_DIRECT_STREAM_FPS": "30",
    "SOREN_DIRECT_STREAM_VIDEO_KBPS": "4500",
    "SOREN_DIRECT_STREAM_AUDIO_KBPS": "160",
    "SOREN_DIRECT_STREAM_AUDIO_DELAY_MS": "0",
    "DOCICH_CC_ENABLED": "0",
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

# --- prompts constants ----------------------------------------------------------
PROMPTS_REL_DIRS = ["prompts", "soren91/prompts"]
PROMPTS_MAX_BYTES = 200 * 1024
PROMPTS_MAX_FILES = 64
PROMPTS_PREVIEW_LEN = 500
PROMPT_FNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md$")

# --- audio queue constants ----------------------------------------------------
AUDIO_TEXT_LIMIT = 1000
AUDIO_SOURCE_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
AUDIO_SPEAKER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
AUDIO_QUEUE_PREVIEW_LEN = 120

# --- stream control constants ---------------------------------------------------
# 配信 (direct_stream worker) とチャット送信のオンオフは supervisor (start_all.sh)
# の pause gate (`tmp/state/<worker>.paused` マーカー) を通じて行う。マーカーが
# ある間 supervisor は当該 worker を起動・respawn しない。
STREAM_WORKER = "direct_stream"
# フォールバック信号経路の総猶予。runner の正常終了チェーンは
# stdin q (15秒) → SIGINT (15秒) の最大約30秒+起動分を要し得るため、
# KILL への切替はその猶予が尽きた後 (35秒経過後) のみとする。
STREAM_STOP_WAIT_SEC = 45
STREAM_STOP_GRACE_BEFORE_KILL_SEC = 35
STREAM_START_WAIT_SEC = 20
STREAM_STOP_SCRIPT_TIMEOUT_SEC = 25
STREAM_SIZE_RE = re.compile(r"^([0-9]{2,5})x([0-9]{2,5})$")

# lib/direct_stream.py load_config の検証範囲と一致させる
STREAM_INT_RANGES: dict[str, tuple[int, int]] = {
    "SOREN_DIRECT_STREAM_FPS": (1, 60),
    "SOREN_DIRECT_STREAM_VIDEO_KBPS": (500, 6000),
    "SOREN_DIRECT_STREAM_AUDIO_KBPS": (64, 320),
    "SOREN_DIRECT_STREAM_AUDIO_DELAY_MS": (0, 2000),
}

# --- worker control constants ---------------------------------------------------
# 予想 (prediction_worker) / 改善 (improve_daemon) ワーカーのオンオフも
# supervisor (start_all.sh) の pause gate (`tmp/state/<worker>.paused` マーカー)
# を通じて行う。マーカーがある間 supervisor は当該 worker を起動・respawn しない。
WORKER_CONTROL_TARGETS = ("prediction_worker", "improve_daemon")
# TERM 後の退出待ち。prediction_worker/improve_daemon は trap で即終了するため短くて足りる。
WORKER_STOP_WAIT_SEC = 10
# improve_daemon 自身は即終了しても、その子として稼働する改善ジョブは
# AI 呼び出し等の退出に時間を要するため別猶予を持つ。
IMPROVE_JOB_TERM_WAIT_SEC = 6
IMPROVE_JOB_KILL_WAIT_SEC = 3
# start 後、supervisor の自動 respawn (poll 3秒 + backoff) を待つ時間。
WORKER_START_WAIT_SEC = 30
IMPROVE_JOB_COMMAND_RE = re.compile(r"[/ ]eloop_improve(_runtime\.[^ ]+)?\.sh(?:\s|$)")

# --- RuntimeBackend rollback flag (issue #43) --------------------------------
# GET /api/workers, /api/game_state は既定で RuntimeBackend (runtime_backend.py)
# 経由になる。backend 抽出に問題があった場合、この環境変数を truthy にすると
# 旧経路 (capability 判定を挟まず直接 _get_workers_status 等を呼ぶ、リファクタ前
# と同じ挙動) に戻せる。API contract (JSON 形状) はどちらの経路でも同じ。
LEGACY_RUNTIME_READS_ENV = "DOCICH_WEBUI_LEGACY_RUNTIME_READS"


def _legacy_runtime_reads_enabled() -> bool:
    return os.environ.get(LEGACY_RUNTIME_READS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


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


# --- prompts helpers ---------------------------------------------------------

def _prompts_dirs(soren_root: Path) -> list[Path]:
    return [(soren_root / rel).resolve() for rel in PROMPTS_REL_DIRS]


def _prompt_mtime(p: Path) -> int:
    try:
        return int(p.stat().st_mtime)
    except Exception:
        return 0


def _resolve_prompt_path(soren_root: Path, prompt_id: str) -> Path:
    if not isinstance(prompt_id, str) or not prompt_id:
        raise ValueError("prompt id は必須です")
    if "\x00" in prompt_id or "\\" in prompt_id:
        raise ValueError("prompt id に不正な文字が含まれます")
    # forbid absolute and traversal
    if prompt_id.startswith("/") or prompt_id.startswith("./") or "//" in prompt_id:
        raise ValueError("prompt id が不正です")
    parts = prompt_id.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("prompt id に不正なパス要素が含まれます")
    if len(parts) < 2:
        raise ValueError("prompt id は dir/file.md 形式である必要があります")
    # dir must be one of PROMPTS_REL_DIRS
    dir_part = "/".join(parts[:-1])
    if dir_part not in PROMPTS_REL_DIRS:
        raise ValueError(f"prompt dir は {PROMPTS_REL_DIRS} のいずれかである必要があります: {dir_part!r}")
    fname = parts[-1]
    if not PROMPT_FNAME_RE.match(fname):
        raise ValueError(f"prompt filename が不正です: {fname!r}")
    if len(prompt_id) > 200:
        raise ValueError("prompt id が長すぎます")
    candidate = (soren_root / prompt_id).resolve()
    # must be inside one of the prompts dirs
    allowed = False
    for root in _prompts_dirs(soren_root):
        try:
            if candidate.is_relative_to(root):
                allowed = True
                break
        except Exception:
            # Python <3.9 fallback: check string prefix
            try:
                candidate.relative_to(root)
                allowed = True
                break
            except Exception:
                continue
    if not allowed:
        raise ValueError("prompt path が許可されたディレクトリ外です")
    return candidate


def _validate_prompt_content(text: str) -> None:
    if not isinstance(text, str):
        raise ValueError("content は文字列である必要があります")
    b = text.encode("utf-8")
    if len(b) > PROMPTS_MAX_BYTES:
        raise ValueError(f"content が大きすぎます ({len(b)} > {PROMPTS_MAX_BYTES})")
    if any(ord(c) < 32 and c not in ("\n", "\r", "\t") for c in text):
        raise ValueError("制御文字は使用できません")


def _list_prompts(soren_root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for rel_dir in PROMPTS_REL_DIRS:
        root = (soren_root / rel_dir).resolve()
        if not root.is_dir():
            continue
        for p in sorted(root.glob("*.md")):
            # skip hidden / resource forks and non-conforming names
            if p.name.startswith(".") or p.name.startswith("._"):
                continue
            if not PROMPT_FNAME_RE.match(p.name):
                continue
            try:
                rel = p.relative_to(soren_root.resolve())
            except Exception:
                try:
                    rel = p.relative_to(soren_root)
                except Exception:
                    rel = Path(rel_dir) / p.name
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                txt = ""
            try:
                size = p.stat().st_size
            except Exception:
                size = len(txt.encode("utf-8"))
            items.append(
                {
                    "id": str(rel),
                    "name": p.name,
                    "rel": str(rel),
                    "dir": rel_dir,
                    "size": size,
                    "mtime": _prompt_mtime(p),
                    "preview": txt[:PROMPTS_PREVIEW_LEN],
                }
            )
    # sort by dir then name for stable order
    items.sort(key=lambda x: (x["dir"], x["name"]))
    if len(items) > PROMPTS_MAX_FILES:
        items = items[:PROMPTS_MAX_FILES]
    return items


def _atomic_prompt_write(soren_root: Path, target: Path, content: str, expected_mtime: int | None) -> int:
    lock_dir = soren_root / "tmp/state/.webui_prompts.lock"
    try:
        lock_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        try:
            age = time.time() - lock_dir.stat().st_mtime
            if age > 30:
                import shutil

                shutil.rmtree(lock_dir, ignore_errors=True)
                lock_dir.mkdir(parents=True, exist_ok=False)
            else:
                raise FileExistsError(f"another prompt edit in progress (age {int(age)}s)")
        except FileExistsError:
            raise
        except Exception as exc:
            raise FileExistsError(str(exc))
    try:
        if target.is_file() and expected_mtime is not None:
            try:
                cur = int(target.stat().st_mtime)
            except Exception:
                cur = 0
            if int(expected_mtime) != cur:
                raise ValueError(f"mtime mismatch: expected {expected_mtime}, current {cur} (concurrent edit)")
        # backup if exists
        if target.is_file():
            backup = target.parent / f"{target.name}.bak.{time.time_ns()}"
            try:
                data = target.read_bytes()
                backup.write_bytes(data)
                backup.chmod(0o600)
            except Exception:
                print(f"docich: 警告: prompt バックアップ作成に失敗しました ({backup.name})", flush=True)
            cutoff = time.time() - 7 * 86400
            try:
                for old in target.parent.glob(f"{target.name}.bak.*"):
                    try:
                        if old.stat().st_mtime < cutoff:
                            old.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
        # atomic write
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path_str = tempfile.mkstemp(dir=str(target.parent), prefix=".prompt.")
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                # ensure newline at EOF? keep as-is
                fh.flush()
                os.fsync(fh.fileno())
            tmp_path.chmod(0o644)
            os.replace(str(tmp_path), str(target))
            try:
                target.touch(exist_ok=True)
            except Exception:
                pass
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
        return _prompt_mtime(target)
    finally:
        try:
            lock_dir.rmdir()
        except Exception:
            try:
                import shutil

                shutil.rmtree(lock_dir, ignore_errors=True)
            except Exception:
                pass


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
    if key == "MODEL_IMPROVE_PEAK_LIST":
        if "MODEL_IMPROVE_PEAK_LIST" in dotenv and dotenv["MODEL_IMPROVE_PEAK_LIST"]:
            return dotenv["MODEL_IMPROVE_PEAK_LIST"]
        # inherits MODEL_IMPROVE_LIST effective
        ml = _effective_value("MODEL_IMPROVE_LIST", dotenv)
        if ml:
            return ml
        return DEFAULTS["MODEL_IMPROVE_LIST"]
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
    if key in ("AI_COMMON_AGENTS", "MODEL_IMPROVE_LIST", "MODEL_IMPROVE_PEAK_LIST", "RADIO_AGENTS", "RADIO_PREPASS_AGENTS", "COMMENT_AGENTS", "COMMENT_TRANSLATION_AGENTS", "PEAK_HOURS_AGENT_PREFERENCE"):
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
    if key in ("PEAK_HOURS_AGENT_SWAP_ENABLED", "PEAK_HOURS_QUEUE_GATE_ENABLED", "IMPROVE_PEAK_CHAIN_ENABLED", "IMPROVE_PEAK_HOUR_DEFER_ENABLED"):
        # ランタイム (core/helpers.sh) は "1" のみ有効と判定する
        if value.strip() not in ("0", "1"):
            raise ValueError(f"{key} は 0 または 1 である必要があります")
        return
    if key == "PEAK_HOURS_TZ":
        if not TZ_RE.match(value.strip()):
            raise ValueError(f"{key} は IANA タイムゾーン名 (例: Asia/Tokyo) である必要があります")
        return
    if key == "SOREN_DIRECT_STREAM_SIZE":
        m = STREAM_SIZE_RE.match(value.strip())
        if not m:
            raise ValueError(f"{key} は WIDTHxHEIGHT 形式 (例: 1280x720) である必要があります")
        w, h = int(m.group(1)), int(m.group(2))
        if not (320 <= w <= 3840) or not (180 <= h <= 2160):
            raise ValueError(f"{key} は 320-3840 x 180-2160 の範囲で指定してください")
        if w % 2 or h % 2:
            raise ValueError(f"{key} は偶数の幅・高さである必要があります")
        return
    if key in STREAM_INT_RANGES:
        lo, hi = STREAM_INT_RANGES[key]
        v = value.strip()
        if not v.isdigit():
            raise ValueError(f"{key} は整数である必要があります")
        if not (lo <= int(v) <= hi):
            raise ValueError(f"{key} は {lo}-{hi} の範囲で指定してください")
        return
    if key == "DOCICH_CC_ENABLED":
        # lib/direct_stream.py の _strict_bool は true/1/yes/on を真と判定するが、
        # .env は bash source されるため安全な 0/1 のみ許容する
        if value.strip() not in ("0", "1"):
            raise ValueError(f"{key} は 0 または 1 である必要があります")
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
    if "\n" in title or "\r" in title:
        raise ValueError("titleに改行は使用できません")
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
    return effective_webui_token(g.webui)


def _effective_read_only_token(g: GlobalConfig) -> str:
    return effective_read_only_token(g.webui)


def _tokens_match(supplied: str, expected: str) -> bool:
    """timing-safe な token 比較 (issue #41)。非ASCII等で compare_digest が
    TypeError を送出するケースでも 500 にせず単に不一致として扱う。"""
    try:
        return hmac.compare_digest(supplied, expected)
    except TypeError:
        return False


# --- issue #42: mutation 防御 (Host/Origin allowlist, CSRF, confirm) --------

# CSRF token の有効期間。ブラウザは webui を開いている間 sessionStorage の bearer
# token を保持し続けるが、CSRF token は API 経由で都度取得し直せるため短めでよい。
CSRF_TTL_SEC = 4 * 3600  # 4時間

# process/stream/config の dangerous action (issue #42): 再確認 (confirm) を要求する
# (method, path) の組。フロントエンドは既存の window.confirm() ダイアログ
# (streamAction/workerControl) または「保存」操作自体を再確認とみなし、
# confirm:true を body に含める。
CONFIRM_REQUIRED_PATHS = {("POST", "/api/workers"), ("POST", "/api/stream"), ("PUT", "/api/config")}


def _make_csrf_token(secret: bytes, ttl: int = CSRF_TTL_SEC, now: int | None = None) -> str:
    """`<exp>.<hmac>` 形式の CSRF token を生成する (server 側で状態を持たない)。

    署名は起動ごとに生成する乱数 secret (`_Handler.csrf_secret`) による HMAC-SHA256。
    exp はブラウザに見える平文だが改竄しても hmac 検証で弾かれるだけなので問題ない。
    """
    now = int(time.time()) if now is None else int(now)
    exp = now + int(ttl)
    mac = hmac.new(secret, str(exp).encode("ascii"), hashlib.sha256).hexdigest()
    return f"{exp}.{mac}"


def _verify_csrf_token(secret: bytes, token: str) -> tuple[bool, str]:
    """CSRF token を検証する。戻り値は (ok, reason)。reason は不一致時のみ意味を持つ
    ("csrf_missing" / "csrf_invalid" / "csrf_expired")。"""
    token = (token or "").strip()
    if not token:
        return False, "csrf_missing"
    exp_s, sep, mac = token.partition(".")
    if not sep or not exp_s or not mac:
        return False, "csrf_invalid"
    try:
        exp = int(exp_s)
    except ValueError:
        return False, "csrf_invalid"
    expected = hmac.new(secret, exp_s.encode("ascii"), hashlib.sha256).hexdigest()
    try:
        if not hmac.compare_digest(mac, expected):
            return False, "csrf_invalid"
    except TypeError:
        return False, "csrf_invalid"
    if exp < int(time.time()):
        return False, "csrf_expired"
    return True, ""


def _is_confirmed(data: Any, headers: Any) -> bool:
    """dangerous action の再確認フラグ (`confirm:true` in body、または
    `X-Docich-Confirm: 1` ヘッダ) が付与されているか。"""
    if isinstance(data, dict):
        v = data.get("confirm")
        if v is True:
            return True
        if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes"):
            return True
        if v == 1:
            return True
    h = str(headers.get("X-Docich-Confirm", "") or "").strip().lower()
    return h in ("1", "true", "yes")


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


# --- stats helpers: chain / stage aggregation --------------------------------

_STATS_LABEL_NORMALIZE_RE = re.compile(r"^(ANALYZE|IMPLEMENT|FIX|REVIEW|ROLLBACK-POSTMORTEM)(?:\(.*\))?$")


def _normalize_stats_label(label: str) -> str:
    """Run_cmd 由来の IMPROVE ラベルを正規化し、CHAIN 集計のフラグメント化を防ぐ.

    例: "ANALYZE(1):primary#2" / "IMPLEMENT(2):fallback" -> "ANALYZE" / "IMPLEMENT"
    RADIO 等のコーナー付きラベルはそのまま返す（段階の区別に必要）。
    """
    if not label:
        return "(empty)"
    # IMPROVE run_cmd は ":primary" / ":fallback" / ":last_resort" を付与する
    if ":primary" in label or ":fallback" in label or ":last_resort" in label:
        base = label.split(":", 1)[0]
        # strip parenthetical retry suffix like "(1)" or "(1.2)"
        base = re.sub(r"\(.*\)$", "", base).strip()
        if base:
            return base
        return label.split(":", 1)[0]
    # 括弧付きの素の IMPROVE ラベルも正規化 (例: "IMPLEMENT(1)")
    m = _STATS_LABEL_NORMALIZE_RE.match(label)
    if m:
        return m.group(1)
    # also handle bare "ANALYZE(1)" etc
    if label.startswith(("ANALYZE", "IMPLEMENT", "FIX(", "REVIEW", "ROLLBACK")):
        stripped = re.sub(r"\(.*\)$", "", label).strip()
        if stripped in ("ANALYZE", "IMPLEMENT", "FIX", "REVIEW", "ROLLBACK-POSTMORTEM", "ROLLBACK"):
            return stripped
        if re.match(r"^(ANALYZE|IMPLEMENT|FIX|REVIEW)", label):
            return re.sub(r"\(.*\)", "", label).split(":")[0].strip() or label
    return label


def _label_chain_hint(label: str) -> str:
    """ラベルから対応するチェーン設定名を推定（表示用ヒント）。"""
    if not label or label == "(empty)":
        return "-"
    base = _normalize_stats_label(label)
    if base in ("ANALYZE", "IMPLEMENT", "FIX", "REVIEW"):
        return "MODEL_IMPROVE_LIST"
    if base == "ROLLBACK-POSTMORTEM" or label.startswith("ROLLBACK"):
        return "ROLLBACK_POSTMORTEM_MODEL"
    if label.startswith("RADIO:"):
        if label.endswith(":prepass"):
            return "RADIO_PREPASS_AGENTS"
        if "batch_commentary" in label:
            return "BATCH_COMMENTARY_AGENTS"
        if "JIJI" in label:
            return "RADIO:JIJI_RESEARCH"
        return "RADIO_AGENTS"
    if label.startswith("COMMENT_TRANSLATION"):
        return "COMMENT_TRANSLATION_AGENTS"
    if label.startswith("COMMENT_CLASSIFIER"):
        return "COMMENT_CLASSIFIER"
    if label.startswith("COMMENT"):
        return "COMMENT_AGENTS"
    if label == "RADIO":
        return "RADIO_AGENTS"
    if label in ("opencode", "opencode-go"):
        return label
    if label.startswith("opencode"):
        return "opencode*"
    if base != label:
        return base
    return "-"


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
    improve_defer = _effective_value("IMPROVE_PEAK_HOUR_DEFER_ENABLED", dotenv)
    improve_peak_enabled = _effective_value("IMPROVE_PEAK_CHAIN_ENABLED", dotenv)
    improve_list = _effective_value("MODEL_IMPROVE_LIST", dotenv)
    improve_peak_list = _effective_value("MODEL_IMPROVE_PEAK_LIST", dotenv)
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
    # effective improve list based on peak
    if improve_peak_enabled == "1" and improve_peak_list:
        # reuse same logic as _get_improve_agents: peak -> peak list else normal
        improve_effective = improve_peak_list if is_peak else improve_list
    else:
        improve_effective = improve_list
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
        "improve_defer_enabled": improve_defer,
        "improve_peak_enabled": improve_peak_enabled,
        "improve_list": improve_list,
        "improve_peak_list": improve_peak_list,
        "improve_effective_list": improve_effective,
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


def _comment_queue_dir(soren_root: Path) -> Path:
    raw = os.environ.get("COMMENT_QUEUE_DIR", "")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (soren_root / p)
    try:
        dotenv = _read_dotenv_dict(soren_root)
        cand = dotenv.get("COMMENT_QUEUE_DIR", "").strip()
        if cand:
            # dotenv dict already strips quotes
            p = Path(cand)
            return p if p.is_absolute() else (soren_root / p)
    except Exception:
        pass
    return soren_root / "tmp/.comment_queue"


def _comment_audio_dedup_dir(soren_root: Path) -> Path:
    raw = os.environ.get("COMMENT_AUDIO_DEDUP_DIR", "")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (soren_root / p)
    try:
        dotenv = _read_dotenv_dict(soren_root)
        cand = dotenv.get("COMMENT_AUDIO_DEDUP_DIR", "").strip()
        if cand:
            p = Path(cand)
            return p if p.is_absolute() else (soren_root / p)
    except Exception:
        pass
    # default mirrors outbound_queue.sh: ${COMMENT_QUEUE_DIR:-tmp/.comment_queue}/audio_dedup
    return _comment_queue_dir(soren_root) / "audio_dedup"


def _comment_audio_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _comment_audio_cleanup_dedup_markers(soren_root: Path, ttl: int) -> None:
    if ttl <= 0:
        return
    dedup_dir = _comment_audio_dedup_dir(soren_root)
    now = int(time.time())
    try:
        if not dedup_dir.is_dir():
            return
        for marker in dedup_dir.iterdir():
            if not marker.is_dir():
                continue
            try:
                mt = int(marker.stat().st_mtime)
            except Exception:
                mt = now
            age = now - mt
            if age > ttl:
                try:
                    import shutil

                    shutil.rmtree(marker, ignore_errors=True)
                except Exception:
                    pass
    except Exception:
        pass


def _comment_audio_claim_enqueue_key(soren_root: Path, text: str) -> bool:
    # returns True if claimed (not deduped), False if dedup within TTL
    ttl_raw = os.environ.get("COMMENT_AUDIO_DEDUP_TTL_SEC", "")
    ttl = 120
    if ttl_raw:
        try:
            ttl = int(ttl_raw.strip())
        except Exception:
            ttl = 120
    else:
        try:
            dotenv = _read_dotenv_dict(soren_root)
            cand = dotenv.get("COMMENT_AUDIO_DEDUP_TTL_SEC", "").strip()
            if cand:
                ttl = int(cand)
        except Exception:
            ttl = 120
    if ttl <= 0:
        return True
    dedup_dir = _comment_audio_dedup_dir(soren_root)
    try:
        dedup_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return True
    key = _comment_audio_hash(text)
    if not key:
        return True
    marker = dedup_dir / key
    now = int(time.time())
    try:
        marker.mkdir(parents=False, exist_ok=False)
        try:
            (marker / "ts").write_text(str(now), encoding="utf-8")
        except Exception:
            pass
        return True
    except FileExistsError:
        try:
            mt = int(marker.stat().st_mtime)
        except Exception:
            mt = now
        age = now - mt
        if age <= ttl:
            return False
        # expired -> replace
        try:
            import shutil

            shutil.rmtree(marker, ignore_errors=True)
        except Exception:
            pass
        try:
            marker.mkdir(parents=False, exist_ok=False)
            try:
                (marker / "ts").write_text(str(now), encoding="utf-8")
            except Exception:
                pass
            _comment_audio_cleanup_dedup_markers(soren_root, ttl)
            return True
        except FileExistsError:
            return False
        except Exception:
            return False


def _validate_audio_text(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("text は文字列である必要があります")
    s = text.strip()
    if not s:
        raise ValueError("text は必須です")
    if len(s) > AUDIO_TEXT_LIMIT:
        raise ValueError(f"text は {AUDIO_TEXT_LIMIT} 文字以内である必要があります")
    if any(ord(c) < 32 and c not in ("\n", "\r", "\t") for c in s):
        raise ValueError("制御文字は使用できません")
    return s


def _validate_audio_source(source: str) -> str:
    if not source:
        return "webui_manual"
    s = str(source).strip()
    if not s:
        return "webui_manual"
    if not AUDIO_SOURCE_RE.match(s):
        raise ValueError(f"source が不正です: {s!r} (英数字._- 1-32)")
    return s


def _validate_audio_speaker(speaker: str) -> str:
    if not speaker:
        return ""
    s = str(speaker).strip()
    if not s:
        return ""
    if len(s) > 64:
        raise ValueError("speaker は 64 文字以内である必要があります")
    if not AUDIO_SPEAKER_RE.match(s):
        raise ValueError(f"speaker が不正です: {s!r}")
    return s


def _enqueue_audio_text(soren_root: Path, text: str, source: str = "webui_manual", speaker: str = "") -> dict[str, Any]:
    cleaned = _validate_audio_text(text)
    src = _validate_audio_source(source)
    spk = _validate_audio_speaker(speaker)
    if not _comment_audio_claim_enqueue_key(soren_root, cleaned):
        return {"ok": True, "dedup": True, "filename": None}
    queue_dir = _comment_queue_dir(soren_root)
    queue_dir.mkdir(parents=True, exist_ok=True)
    ts = time.time_ns()
    filename = f"comment_announce_{ts}_{src}.txt"
    dest = queue_dir / filename
    tmp_fd = None
    tmp_path = None
    try:
        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=str(queue_dir), prefix=".audio.")
        tmp_path = Path(tmp_path_str)
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(cleaned + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        tmp_fd = None
        tmp_path.chmod(0o644)
        os.replace(str(tmp_path), str(dest))
        if spk:
            try:
                (Path(str(dest) + ".speaker")).write_text(spk, encoding="utf-8")
            except Exception:
                pass
        return {"ok": True, "dedup": False, "filename": filename, "path": str(dest)}
    finally:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except Exception:
                pass
        if tmp_path is not None:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass


def _list_audio_queue(soren_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    qdir = _comment_queue_dir(soren_root)
    items: list[dict[str, Any]] = []
    if not qdir.is_dir():
        return items
    try:
        candidates: list[Path] = []
        for p in qdir.glob("*.txt"):
            if p.name.startswith("."):
                continue
            if p.name == "played_hashes.txt":
                continue
            if p.suffix == ".txt":
                candidates.append(p)
        # sort by mtime ascending (oldest first, worker consumes oldest)
        candidates.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0)
        for p in candidates[:limit]:
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                txt = ""
            try:
                st = p.stat()
                mtime = int(st.st_mtime)
                size = st.st_size
            except Exception:
                mtime = 0
                size = len(txt.encode("utf-8"))
            preview = txt.strip()[:AUDIO_QUEUE_PREVIEW_LEN]
            # detect .speaker sidecar
            speaker = ""
            try:
                sp_path = Path(str(p) + ".speaker")
                if sp_path.is_file():
                    speaker = sp_path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                speaker = ""
            # detect .playing
            playing = (p.with_suffix(".playing")).exists() if p.suffix == ".txt" else False
            # need check alternative .playing name: file is .txt, playing is .playing; but if file is already .playing? glob not include.
            items.append(
                {
                    "filename": p.name,
                    "path": str(p),
                    "mtime": mtime,
                    "size": size,
                    "preview": preview,
                    "speaker": speaker,
                    "playing": playing,
                }
            )
        # also include *.playing files that are currently playing
        try:
            for p in qdir.glob("*.playing"):
                if p.name.startswith("."):
                    continue
                # avoid double count if already listed? .playing not in txt list
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    txt = ""
                try:
                    st = p.stat()
                    mtime = int(st.st_mtime)
                    size = st.st_size
                except Exception:
                    mtime = 0
                    size = len(txt.encode("utf-8"))
                preview = txt.strip()[:AUDIO_QUEUE_PREVIEW_LEN]
                speaker = ""
                try:
                    # sidecars for playing use original .txt name? check both
                    for cand in [Path(str(p) + ".speaker"), Path(str(p).replace(".playing", ".txt") + ".speaker")]:
                        if cand.is_file():
                            speaker = cand.read_text(encoding="utf-8", errors="ignore").strip()
                            break
                except Exception:
                    speaker = ""
                items.append(
                    {
                        "filename": p.name,
                        "path": str(p),
                        "mtime": mtime,
                        "size": size,
                        "preview": preview,
                        "speaker": speaker,
                        "playing": True,
                    }
                )
        except Exception:
            pass
        # sort again by mtime
        items.sort(key=lambda x: x["mtime"])
        return items[:limit]
    except Exception:
        return items


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


# --- Twitch predictions -----------------------------------------------------

PREDICTION_OUTCOME_LABELS = ("建国なし", "ロシア建国(ソ連不成立)", "ソ連建国", "粛清")
PREDICTION_ACTIONS = {"create", "resolve", "cancel", "sync"}
PREDICTION_COMMAND_TIMEOUT_SEC = 35


def _prediction_state_dir(soren_root: Path) -> Path:
    """Resolve the same state directory used by the shell worker."""
    raw = os.environ.get("TMP_STATE_DIR", "")
    if not raw:
        try:
            raw = _read_dotenv_dict(soren_root).get("TMP_STATE_DIR", "").strip()
        except Exception:
            raw = ""
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else soren_root / p
    return soren_root / "tmp/state"


def _prediction_script_path(soren_root: Path, allow_fallback: bool = False) -> Path:
    candidate = soren_root / "twitch_predictions.sh"
    if candidate.is_file():
        return candidate
    # A local reference-run WebUI can still point at a VM-like soren_root.  Use
    # the checked-out implementation only as a read-only fallback for status.
    fallback = Path(__file__).resolve().parents[2] / "games/soviet_now/twitch_predictions.sh"
    return fallback if allow_fallback and fallback.is_file() else candidate


def _prediction_command_message(stderr: str, stdout: str = "") -> str:
    """Return a short, secret-safe command message for the UI."""
    for raw in reversed((stderr or "").splitlines()):
        line = raw.strip()
        if line:
            return line[:300]
    for raw in reversed((stdout or "").splitlines()):
        line = raw.strip()
        if line:
            return line[:300]
    return ""


def _run_prediction_command(soren_root: Path, args: list[str], timeout: int = PREDICTION_COMMAND_TIMEOUT_SEC) -> dict[str, Any]:
    """Run the allowlisted prediction wrapper without exposing environment secrets."""
    script = _prediction_script_path(soren_root, allow_fallback=(args[:1] == ["status"]))
    if not script.is_file():
        return {"ok": False, "available": False, "returncode": 127, "result": None, "message": "twitch_predictions.sh not found"}
    if any("\x00" in str(arg) for arg in args):
        return {"ok": False, "available": True, "returncode": 400, "result": None, "message": "invalid prediction command argument"}
    try:
        completed = subprocess.run(
            ["bash", str(script), *args],
            cwd=str(soren_root),
            capture_output=True,
            text=True,
            timeout=max(5, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "available": True, "returncode": 124, "result": None, "message": "prediction command timed out"}
    except Exception as exc:
        return {"ok": False, "available": True, "returncode": 1, "result": None, "message": f"prediction command failed: {exc}"[:300]}
    stdout = (completed.stdout or "").strip()
    result: Any = None
    if stdout:
        try:
            result = json.loads(stdout)
        except Exception:
            result = None
    # Parsed JSON is the structured result; do not echo the entire payload as
    # a human-facing message.  Fall back to stdout only for non-JSON failures.
    message = _prediction_command_message(completed.stderr or "", "" if result is not None else stdout)
    return {
        "ok": completed.returncode == 0,
        "available": True,
        "returncode": int(completed.returncode),
        "result": result,
        "message": message,
    }


def _prediction_clean_local_state(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    allowed = {
        "prediction_id",
        "outcome_ids",
        "game_num",
        "created_at",
        "russia_created",
        "best_outcome",
        "regression_reason_label",
        "recovered",
    }
    clean: dict[str, Any] = {}
    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if key == "outcome_ids":
            if isinstance(value, list):
                clean[key] = [str(v)[:200] for v in value[:8] if v]
        elif key in {"prediction_id", "regression_reason_label"}:
            clean[key] = str(value)[:300]
        elif key in {"game_num", "created_at", "best_outcome"}:
            try:
                clean[key] = int(value)
            except Exception:
                continue
        elif key in {"russia_created", "recovered"}:
            clean[key] = bool(value)
    if not clean.get("prediction_id"):
        return None
    return clean


def _prediction_clean_remote_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not item.get("id"):
        return None
    clean: dict[str, Any] = {
        "id": str(item.get("id", ""))[:300],
        "title": str(item.get("title", ""))[:300],
        "status": str(item.get("status", ""))[:32],
        "created_at": str(item.get("created_at", ""))[:64],
        "ended_at": str(item.get("ended_at", ""))[:64],
        "prediction_window": item.get("prediction_window"),
        "channel_points_used": item.get("channel_points_used"),
        "users": item.get("users"),
        "winning_outcome_id": str(item.get("winning_outcome_id", "") or "")[:300],
    }
    outcomes: list[dict[str, Any]] = []
    raw_outcomes = item.get("outcomes", [])
    if isinstance(raw_outcomes, list):
        for outcome in raw_outcomes[:8]:
            if not isinstance(outcome, dict) or not outcome.get("id"):
                continue
            outcomes.append(
                {
                    "id": str(outcome.get("id", ""))[:300],
                    "title": str(outcome.get("title", ""))[:200],
                    "color": str(outcome.get("color", "") or "")[:32],
                    "users": outcome.get("users"),
                    "channel_points": outcome.get("channel_points"),
                }
            )
    clean["outcomes"] = outcomes
    return clean


def _prediction_retry_status(soren_root: Path, operation: str) -> dict[str, Any] | None:
    if operation not in {"create", "resolve"}:
        return None
    path = _prediction_state_dir(soren_root) / "prediction_retry" / f"{operation}.json"
    data = _load_json_file(path)
    if not isinstance(data, dict):
        return None
    now = int(time.time())
    try:
        next_retry_at = int(data.get("next_retry_at", 0) or 0)
    except Exception:
        next_retry_at = 0
    try:
        attempt = int(data.get("attempt", 0) or 0)
    except Exception:
        attempt = 0
    return {
        "operation": operation,
        "attempt": max(0, attempt),
        "next_retry_at": max(0, next_retry_at),
        "remaining": max(0, next_retry_at - now),
        "active": next_retry_at > now,
        "http_code": str(data.get("http_code", ""))[:16],
        "message": str(data.get("message", ""))[:240],
    }


def _prediction_status_snapshot(soren_root: Path) -> dict[str, Any]:
    command = _run_prediction_command(soren_root, ["status"], timeout=20)
    remote = command.get("result") if isinstance(command.get("result"), dict) else {}
    state_dir = _prediction_state_dir(soren_root)
    local = _prediction_clean_local_state(_load_json_file(state_dir / "current_prediction.json"))
    remote_items: list[dict[str, Any]] = []
    raw_items = remote.get("data", []) if isinstance(remote, dict) else []
    if isinstance(raw_items, list):
        for item in raw_items:
            clean = _prediction_clean_remote_item(item)
            if clean:
                remote_items.append(clean)
    accumulated = _load_json_file(state_dir / "accumulated_games.json")
    if not isinstance(accumulated, dict):
        accumulated = {}
    acc: dict[str, Any] = {}
    for key in ("count", "best_outcome", "russia_created", "soviet_created"):
        if key not in accumulated:
            continue
        value = accumulated[key]
        if key in {"count", "best_outcome"}:
            try:
                acc[key] = int(value)
            except Exception:
                continue
        else:
            acc[key] = bool(value)
    result: dict[str, Any] = {
        "ok": bool(remote.get("ok", False)) if remote else bool(command.get("ok")),
        "available": bool(command.get("available", False)),
        "enabled": bool(remote.get("enabled", False)) if remote else False,
        "configured": bool(remote.get("configured", False)) if remote else False,
        "explore_mode": bool(remote.get("explore_mode", False)) if remote else False,
        "http_code": remote.get("http_code") if isinstance(remote, dict) else None,
        "error": str(remote.get("error", ""))[:240] if isinstance(remote, dict) else "",
        "remote": remote_items,
        "local": local,
        "retry": {
            "create": _prediction_retry_status(soren_root, "create"),
            "resolve": _prediction_retry_status(soren_root, "resolve"),
        },
        "accumulated": acc,
        "worker": {
            "alive": _find_worker_pid(soren_root, "prediction_worker") is not None,
            "pid": _find_worker_pid(soren_root, "prediction_worker"),
        },
    }
    if not result["available"]:
        result["error"] = command.get("message", "prediction script unavailable")[:240]
    elif not result["error"] and not command.get("ok"):
        result["error"] = command.get("message", "prediction status unavailable")[:240]
    return result


def _controlled_worker_pid(soren_root: Path, worker: str) -> int | None:
    """pidfile から稼働中の対象 worker pid を返す (cmdline ガード込み)。"""
    pid = _find_worker_pid(soren_root, worker)
    if pid is None:
        return None
    if not _pid_matches_worker_process(pid, worker):
        return None
    return pid


def _pid_is_improve_job(pid: int) -> bool:
    if not _pid_is_active(pid):
        return False
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        command = " ".join(p.decode("utf-8", "ignore") for p in raw.split(b"\x00") if p)
    except OSError:
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        command = result.stdout.strip()
    return IMPROVE_JOB_COMMAND_RE.search(command) is not None


def _active_improve_job_pid(soren_root: Path) -> int | None:
    imp = _load_json_file(_improve_state_path(soren_root))
    if not isinstance(imp, dict) or str(imp.get("status", "")) != "running":
        return None
    try:
        pid = int(imp.get("pid", 0) or 0)
    except (TypeError, ValueError):
        return None
    return pid if _pid_is_improve_job(pid) else None


def _descendant_pids(root_pid: int) -> list[int]:
    try:
        result = subprocess.run(
            ["ps", "-Ao", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)

    descendants: list[int] = []
    seen: set[int] = {root_pid}
    queue = [root_pid]
    while queue:
        parent = queue.pop(0)
        for child in children.get(parent, []):
            if child in seen:
                continue
            seen.add(child)
            descendants.append(child)
            queue.append(child)
    # 葉から止めると、親が子の終了待ちから抜ける前に全経路を閉じられる。
    descendants.reverse()
    return descendants


def _terminate_process_tree(root_pid: int, known_pids: list[int] | None = None) -> dict[str, Any]:
    if known_pids is not None:
        pids = list(known_pids)
    elif not _pid_is_active(root_pid):
        return {"term_sent": False, "kill_sent": False, "stopped": True, "remaining_pids": []}
    else:
        pids = [root_pid] + _descendant_pids(root_pid)
    term_sent = False
    for pid in pids:
        if _pid_is_active(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                term_sent = True
            except OSError:
                pass

    deadline = time.monotonic() + IMPROVE_JOB_TERM_WAIT_SEC
    while time.monotonic() < deadline:
        alive = [pid for pid in pids if _pid_is_active(pid)]
        if not alive:
            return {"term_sent": term_sent, "kill_sent": False, "stopped": True, "remaining_pids": []}
        time.sleep(0.25)

    kill_sent = False
    remaining_after_kill: set[int] = set()
    for pid in reversed([pid for pid in pids if _pid_is_active(pid)]):
        try:
            os.kill(pid, signal.SIGKILL)
            kill_sent = True
            remaining_after_kill.add(pid)
        except OSError:
            pass
    deadline = time.monotonic() + IMPROVE_JOB_KILL_WAIT_SEC
    while time.monotonic() < deadline:
        remaining_after_kill = {pid for pid in remaining_after_kill if _pid_is_active(pid)}
        if not remaining_after_kill:
            break
        time.sleep(0.25)
    return {
        "term_sent": term_sent,
        "kill_sent": kill_sent,
        "stopped": not remaining_after_kill,
        "remaining_pids": sorted(remaining_after_kill),
    }


def _mark_improve_job_stopped(soren_root: Path) -> bool:
    path = _improve_state_path(soren_root)
    data = _load_json_file(path)
    if not isinstance(data, dict):
        data = {}
    data.update(
        {
            "status": "idle",
            "pid": 0,
            "phase": "webui_stopped",
            "progress": 100,
            "detail": "job_stopped_by_webui",
            "updated_at": int(time.time()),
            "pid_birth_epoch": 0,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return True


def _stop_controlled_worker(soren_root: Path, worker: str) -> dict[str, Any]:
    """pause マーカー作成後、worker と improve ジョブのプロセスツリーを止める。

    supervisor はマーカーがある間 respawn しないため、TERM 後は完全停止のまま。
    improve_daemon の foreground wait を先に解除してから、記録済み改善ジョブの
    子孫まで検証付きで停止する。これにより AI 子プロセスの孤児化を防ぐ。
    """
    _set_worker_paused(soren_root, worker, True)
    job_pid = _active_improve_job_pid(soren_root) if worker == "improve_daemon" else None
    # daemonへTERMすると子は即座に孤児化・親変更し得るため、対象を先に固定する。
    job_pids = [job_pid] + (_descendant_pids(job_pid) if job_pid is not None else [])
    pid = _controlled_worker_pid(soren_root, worker)
    term_sent = False
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
            term_sent = True
        except (ProcessLookupError, PermissionError):
            pass
        # macOS の TERM 死滅子プロセスはゾンビのまま kill(0) 成功し得るため、
        # ゾンビ判定を含めて実プロセス消滅を待つ
        deadline = time.monotonic() + WORKER_STOP_WAIT_SEC
        while time.monotonic() < deadline:
            if pid is None or not _pid_is_active(pid):
                break
            time.sleep(0.5)
    remaining = _controlled_worker_pid(soren_root, worker)
    result: dict[str, Any] = {
        "marker": True,
        "term_sent": term_sent,
        "stopped": remaining is None,
        "remaining_pid": remaining,
    }
    if worker == "improve_daemon":
        job_result = (
            _terminate_process_tree(job_pid, job_pids)
            if job_pid is not None
            else {"term_sent": False, "kill_sent": False, "stopped": True, "remaining_pids": []}
        )
        result["job_pid"] = job_pid
        result["job"] = job_result
        result["stopped"] = result.get("stopped", False) and bool(job_result.get("stopped"))
    return result


def _wait_for_worker_start(soren_root: Path, worker: str) -> dict[str, Any]:
    """マーカー削除後、supervisor による respawn (pidfile 出現+生存) を待つ。"""
    deadline = time.monotonic() + WORKER_START_WAIT_SEC
    while time.monotonic() < deadline:
        pid = _find_worker_pid(soren_root, worker)
        if pid is not None:
            return {"pid": pid, "running": True}
        time.sleep(1.0)
    return {"pid": None, "running": False}


# ---------------------------------------------------------------------------
# VOICEVOX endpoint chain (docich voicevox synth: VOICEVOX_URLS + backoff)
# ---------------------------------------------------------------------------

VOICE_ENDPOINT_ACTIONS = ("probe", "reset", "disable", "enable")


def _voice_speech_config(soren_root: Path) -> "speech.SpeechConfig":
    """Build the synth config the workers see: .env of soren overrides our env."""

    env: dict[str, str] = dict(os.environ)
    env.update(_read_dotenv_dict(soren_root))
    return speech.SpeechConfig.from_env(env=env, soren_root=soren_root)


def _get_voice_endpoints(soren_root: Path, probe: bool = False) -> dict[str, Any]:
    cfg = _voice_speech_config(soren_root)
    report = speech.endpoint_report(cfg, probe=probe)
    report["urls_source"] = "VOICEVOX_URLS" if _read_dotenv_dict(soren_root).get("VOICEVOX_URLS") else "legacy_keys"
    report["chain"] = list(cfg.urls)
    report["speaker"] = cfg.speaker
    log_path = Path(cfg.state_file).with_name(speech.CHAIN_LOG_NAME) if cfg.state_file else None
    tail: list[str] = []
    if log_path and log_path.is_file():
        try:
            tail = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-30:]
        except OSError:
            tail = []
    report["log_tail"] = tail
    return report


def _voice_endpoint_action(soren_root: Path, action: str, url: str = "") -> dict[str, Any]:
    action = str(action or "").strip().lower()
    if action not in VOICE_ENDPOINT_ACTIONS:
        raise ValueError(f"action must be one of {', '.join(VOICE_ENDPOINT_ACTIONS)}")
    cfg = _voice_speech_config(soren_root)
    url = str(url or "").strip().rstrip("/")
    if action in ("disable", "enable") and not url:
        raise ValueError("url is required for disable/enable")
    if url and url not in cfg.urls:
        raise ValueError("url is not in the configured chain")
    if action == "probe":
        report = speech.endpoint_report(cfg, probe=True)
        if url:
            report["endpoints"] = [r for r in report["endpoints"] if r["url"] == url]
        return {"ok": True, "action": action, "url": url, "endpoints": report["endpoints"]}
    if action == "reset":
        speech.reset_endpoint(cfg, url or None)
    elif action == "disable":
        speech.set_endpoint_disabled(cfg, url, True)
    elif action == "enable":
        speech.set_endpoint_disabled(cfg, url, False)
    report = speech.endpoint_report(cfg, probe=False)
    return {"ok": True, "action": action, "url": url, "endpoints": report["endpoints"]}


def _stream_status_file(soren_root: Path) -> Path:
    # lib/direct_stream.py load_config 既定の SOREN_DIRECT_STREAM_STATE_DIR
    return soren_root / "tmp/state/direct_stream/status.json"


def _get_stream_status(soren_root: Path) -> dict[str, Any]:
    """配信 (direct_stream) の状態スナップショット。

    state は次の3値:
      - "live":   runner/ffmpeg プロセスが生存し status.json も running
      - "paused": 停止マーカーあり (オペレータ意図によるオフ、supervisor respawn 対象外)
      - "off":    マーカーなしでプロセスがいない (異常終了・supervisor 停止など)
    """
    paused = _is_worker_paused(soren_root, STREAM_WORKER)
    data = _load_json_file(_stream_status_file(soren_root))
    if not isinstance(data, dict):
        data = {}
    runner_pid = data.get("pid")
    ffmpeg_pid = data.get("ffmpeg_pid")
    runner_alive = isinstance(runner_pid, int) and _is_pid_alive(runner_pid)
    ffmpeg_alive = isinstance(ffmpeg_pid, int) and _is_pid_alive(ffmpeg_pid)
    reported_running = bool(data.get("running"))
    running = reported_running and (runner_alive or ffmpeg_alive)
    if running:
        state = "live"
    elif paused:
        state = "paused"
    else:
        state = "off"
    started_at = data.get("started_at") if isinstance(data.get("started_at"), int) else None
    updated_at = data.get("updated_at") if isinstance(data.get("updated_at"), int) else None
    now = int(time.time())
    out: dict[str, Any] = {
        "ok": True,
        "state": state,
        "paused": paused,
        "running": running,
        "backend": str(data.get("backend", "")) or None,
        "mode": data.get("mode"),
        "fps": data.get("fps"),
        "bitrate": data.get("bitrate"),
        "speed": data.get("speed"),
        "progress": data.get("progress"),
        "frame": data.get("frame"),
        "drop_frames": data.get("drop_frames"),
        "dup_frames": data.get("dup_frames"),
        "out_time": data.get("out_time"),
        "pid": runner_pid if isinstance(runner_pid, int) and runner_pid > 0 else None,
        "ffmpeg_pid": ffmpeg_pid if isinstance(ffmpeg_pid, int) and ffmpeg_pid > 0 else None,
        "runner_alive": runner_alive,
        "ffmpeg_alive": ffmpeg_alive,
        "started_at": started_at,
        "updated_at": updated_at,
        "uptime_sec": (now - started_at) if isinstance(started_at, int) and started_at > 0 else None,
        "status_age_sec": (now - updated_at) if isinstance(updated_at, int) and updated_at > 0 else None,
        "now": now,
    }
    cfg = data.get("config")
    if isinstance(cfg, dict):
        for k in ("width", "height", "video_kbps", "audio_kbps", "closed_captions_active"):
            if k in cfg:
                out[k] = cfg.get(k)
    out["chat_paused"] = _is_worker_paused(soren_root, "chat_worker")
    return out


def _pid_matches_stream_process(pid: int) -> bool:
    """Linux /proc で cmdline を確認し、誤って無関係プロセスを殺さないガード。

    /proc が読めない環境 (macOS 等) は確認不能のため True (許可) を返す。
    runner (`lib/direct_stream.py run`) と ffmpeg バイナリの両方を許容する。
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    except Exception:
        return True
    parts = [p.decode("utf-8", "ignore") for p in raw.split(b"\x00") if p]
    if not parts:
        return False
    joined = " ".join(parts)
    if "direct_stream" in joined:
        return True
    argv0 = Path(parts[0]).name
    return argv0 == "ffmpeg"


def _stream_runner_pids(soren_root: Path) -> list[int]:
    """停止対象の runner / ffmpeg pid を status.json と pidfile の両方から収集する。"""
    pids: list[int] = []
    seen: set[int] = set()
    candidates: list[Any] = []
    data = _load_json_file(_stream_status_file(soren_root))
    if isinstance(data, dict):
        candidates.extend([data.get("pid"), data.get("ffmpeg_pid")])
    pid_file = soren_root / "tmp/state" / f"{STREAM_WORKER}.pid"
    try:
        raw = pid_file.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
        candidates.append(int(raw.strip()))
    except Exception:
        pass
    for c in candidates:
        if isinstance(c, int) and c > 0 and c not in seen:
            seen.add(c)
            if _pid_matches_stream_process(c):
                pids.append(c)
    return pids


def _run_direct_stream_stop_script(soren_root: Path) -> dict[str, Any]:
    """wiki「Stream-Ending」の正規手順: `python3 lib/direct_stream.py stop`。

    runner へ SIGTERM が送られ、runner のシグナルハンドラ
    (_graceful_stop_ffmpeg) が FFmpeg stdin へ `q` を流して RTMP を正常終了
    (FCUnpublish / deleteStream) させる。これが Twitch 側に「意図的な配信終了」
    として伝わり、即座に OFF LINE になる。単純な強制切断は回線断扱いになり
    Disconnect Protection の待ち時間が生じるため、第一選択は必ずこれ。
    """
    script = soren_root / "lib/direct_stream.py"
    if not script.is_file():
        return {"ok": False, "detail": f"missing script: {script}"}
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "stop"],
            cwd=str(soren_root),
            capture_output=True,
            text=True,
            timeout=STREAM_STOP_SCRIPT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": f"timeout after {STREAM_STOP_SCRIPT_TIMEOUT_SEC}s"}
    except OSError as exc:
        return {"ok": False, "detail": str(exc)[:200]}
    detail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
    return {"ok": proc.returncode == 0, "rc": proc.returncode, "detail": detail[-300:]}


def _stop_stream_runner(soren_root: Path) -> dict[str, Any]:
    """pause マーカー作成後、配信を明示終了する。

    第一選択は `_run_direct_stream_stop_script` (stdin q による RTMP 正常終了)。
    スクリプトが使えない・失敗した場合のみ、同等の効果を持つ runner への
    SIGTERM 送信にフォールバックする (runner のハンドラが q を ffmpeg へ転送する)。
    SIGKILL へのエスカレートは runner の q/SIGINT 猶予 (最大約30秒) を守るため
    STREAM_STOP_GRACE_BEFORE_KILL_SEC 経過後のみ。
    """
    _set_worker_paused(soren_root, STREAM_WORKER, True)
    script_result = _run_direct_stream_stop_script(soren_root)
    method = "direct_stream_stop" if script_result.get("ok") else "signal_fallback"
    escalated = False

    def alive_pids() -> list[int]:
        return [p for p in _stream_runner_pids(soren_root) if _is_pid_alive(p)]

    remaining = alive_pids()
    if remaining or not script_result.get("ok"):
        deadline = time.monotonic() + STREAM_STOP_WAIT_SEC
        kill_after = deadline - (STREAM_STOP_WAIT_SEC - STREAM_STOP_GRACE_BEFORE_KILL_SEC)
        while time.monotonic() < deadline:
            alive = alive_pids()
            if not alive:
                break
            sig = signal.SIGKILL if escalated else signal.SIGTERM
            for p in alive:
                try:
                    os.kill(p, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            time.sleep(0.5)
            if not escalated and time.monotonic() > kill_after:
                escalated = True
        remaining = alive_pids()
    return {
        "stopped": not remaining,
        "method": method,
        "escalated_kill": escalated,
        "remaining_pids": remaining,
        "stop_detail": str(script_result.get("detail", "")),
    }


def _wait_for_stream_start(soren_root: Path) -> dict[str, Any]:
    """マーカー削除後、supervisor による respawn を最大 STREAM_START_WAIT_SEC 待つ。"""
    deadline = time.monotonic() + STREAM_START_WAIT_SEC
    while time.monotonic() < deadline:
        st = _get_stream_status(soren_root)
        if st.get("state") == "live":
            return st
        time.sleep(1.0)
    return _get_stream_status(soren_root)


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


_TOKEN_QUERY_RE = re.compile(r"([?&])token=[^&]*", re.IGNORECASE)


def _redact_token_query(value: str) -> str:
    """URL query の `token=...` を伏せる。

    query token は受理/生成しないが (issue #41)、外部から `?token=...` を付けて
    アクセスされた場合や将来の呼び出しミスに備え、ログへは残さない多層防御。
    """
    if not value or "token=" not in value.lower():
        return value
    return _TOKEN_QUERY_RE.sub(r"\1token=REDACTED", value)


def _append_webui_log(soren_root: Path, rec: dict[str, Any]) -> None:
    """webui.log (JSON lines) へ1レコード追記する。secret/body は呼び出し元が
    含めないこと (呼び出し元でその保証をする。ここでは best-effort の書き込みのみ)。"""
    try:
        log_file = soren_root / "tmp/debug/webui.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _log_request(soren_root: Path, method: str, path: str, status: int, latency_ms: int, extra: str = "") -> None:
    _append_webui_log(
        soren_root,
        {
            "ts": int(time.time()),
            "event": "access",
            "method": method,
            "path": _redact_token_query(path),
            "status": status,
            "latency_ms": latency_ms,
            "extra": _redact_token_query(extra),
        },
    )


def _log_authz_event(soren_root: Path, method: str, path: str, identity: str, decision: str, reason: str = "") -> None:
    """issue #42: secret/body を含めない authorization audit event。

    identity は "operator" / "viewer" / "unauthenticated" のみ (token 値は含めない)。
    reason はエラーコード相当の短い定数文字列のみ (invalid_host 等)。body は一切渡さない。
    """
    _append_webui_log(
        soren_root,
        {
            "ts": int(time.time()),
            "event": "authz",
            "method": method,
            "path": _redact_token_query(path),
            "identity": identity,
            "decision": decision,
            "reason": reason,
        },
    )


# --- HTTP handler --------------------------------------------------------

MAX_BODY_BYTES = 256 * 1024

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
.stats-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
.table-wrap{overflow:auto;position:relative;border:1px solid var(--border);border-radius:8px}
.table-wrap table{margin:0}
.table-wrap::after{content:"";position:absolute;right:0;top:0;bottom:0;width:24px;background:linear-gradient(to right, transparent, rgba(0,0,0,0.25));pointer-events:none;opacity:0.6}
.stats-label{font-weight:600}
.stats-hint{font-size:11px;color:var(--muted);display:block;margin-top:2px}
.rate-bar{height:6px;background:var(--border);border-radius:999px;overflow:hidden;margin-top:4px}
.rate-bar>i{display:block;height:100%;border-radius:999px;transition:width .25s}
.rate-bar.ok>i{background:var(--ok)}
.rate-bar.warn>i{background:var(--warn)}
.rate-bar.bad>i{background:var(--bad)}
.depth-mini{font-size:11px;color:var(--muted)}
.accordion-item{border:1px solid var(--border);background:#111319;border-radius:10px;margin-bottom:8px;overflow:hidden}
.accordion-summary{cursor:pointer;display:flex;justify-content:space-between;align-items:center;padding:10px 12px;user-select:none}
.accordion-summary:hover{background:#1a2333}
.accordion-body{padding:0 12px 12px 12px}
.accordion-item.open .accordion-summary{background:#1a2333}
</style>
</head>
<body>
<div id="login-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:100;align-items:center;justify-content:center">
<div class="card" style="max-width:360px;width:90vw">
<h3>ログイン</h3>
<p class="desc">webui token を入力してください。</p>
<div class="row"><input id="login-token-input" type="password" autocomplete="off" placeholder="token" style="flex:1"/></div>
<div class="actions" style="margin-top:8px"><button class="btn primary" id="login-submit">ログイン</button></div>
</div>
</div>
<header>
<h1>docich webui</h1>
<div class="sub">Soren モデルチェーン / バックオフ / ピーク帯</div>
<div class="env"><span id="env-mtime"></span> <span class="badge" id="health-badge">...</span> <span id="soren-root" class="mono" style="color:var(--muted);font-size:12px"></span></div>
</header>
<nav id="tabs">
<button data-tab="dashboard" class="active">Dashboard</button>
<button data-tab="status">Status</button>
<button data-tab="stream">Stream</button>
<button data-tab="overlay">Overlay</button>
<button data-tab="audio">Audio</button>
<button data-tab="predictions">Predictions</button>
<button data-tab="chains">Chains</button>
<button data-tab="backoff">Backoff</button>
<button data-tab="peak">Peak</button>
<button data-tab="prompts">Prompts</button>
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
<!-- STREAM -->
<section id="tab-stream" style="display:none">
<div class="card"><h2>配信コントロール</h2><p class="desc">ffmpeg direct_stream (supervisor 管理) のオンオフ。10秒ごとに自動更新。</p>
<div class="grid2">
<div class="card kpi"><h3>配信状態</h3><div class="val" id="stream-state">-</div><div class="subk mono" id="stream-state-sub">-</div></div>
<div class="card kpi"><h3>チャット送信</h3><div class="val" id="chat-state">-</div><div class="subk mono" id="chat-state-sub">-</div></div>
</div>
<div class="kv" id="stream-detail"></div>
<div class="actions">
<button class="btn primary" id="stream-start">配信開始 (start)</button>
<button class="btn danger" id="stream-stop">配信停止 (stop)</button>
<button class="btn" id="stream-refresh">更新</button>
<span id="stream-msg" class="help"></span>
</div>
<div class="help">stop は <code>tmp/state/direct_stream.paused</code> マーカー作成後、<code>lib/direct_stream.py stop</code> を実行し FFmpeg stdin へ <code>q</code> を送って RTMP 正常終了 (FCUnpublish / deleteStream) させます。Twitch が「意図的な終了」として即座に OFF LINE にするための正規手順 (wiki: Stream-Ending)。強制切断だと回線断扱いになり LIVE が最大90秒残ります。start はマーカー削除後、supervisor の自動再起動 (約3秒周期) を最大20秒待ちます。</div>
</div>
<div class="card"><h3>チャット送信の停止 / 再開</h3><p class="desc"><code>tmp/state/chat_worker.paused</code> で制御。停止中は IRC 受信・コメント生成・投稿キュー消費が止まり、新規 enqueue も積まれません (worker が自己 park / marker 削除で自動復帰)。</p>
<div class="actions">
<button class="btn primary" id="chat-start">チャット再開 (start)</button>
<button class="btn danger" id="chat-stop">チャット停止 (stop)</button>
</div>
<div id="chat-msg" class="help"></div>
</div>
<div class="card"><h3>予想・改善ワーカーの停止 / 開始</h3><p class="desc">prediction_worker / improve_daemon を supervisor の pause gate (<code>tmp/state/&lt;name&gt;.paused</code>) で制御。stop はマーカー作成後に SIGTERM、start はマーカー削除後の supervisor 自動再起動 (約3秒周期 + backoff) を最大30秒待ちます。</p>
<div id="wc-rows"></div>
<div class="help" style="margin-top:8px">improve_daemon を停止すると、稼働中の改善ジョブと AI 子プロセスもツリーごと停止します。予想ワーカー停止中は Twitch 予想の自動作成・解決が止まります。</div>
<div id="wc-msg" class="help"></div>
</div>
<div class="card"><h3>配信設定 (.env)</h3><p class="desc"><code>SOREN_DIRECT_STREAM_*</code> 設定 (.env へ保存)。変更は <b>配信の再起動 (stop → start) 後に反映</b>されます。</p>
<div class="row">
<div><label>解像度 WIDTHxHEIGHT (320-3840 x 180-2160, 偶数)</label><input id="stream-size" placeholder="1280x720"/></div>
<div><label>FPS (1-60)</label><input id="stream-fps" placeholder="30"/></div>
<div><label>映像ビットレート kbps (500-6000)</label><input id="stream-vkbps" placeholder="4500"/></div>
</div>
<div class="row">
<div><label>音声ビットレート kbps (64-320)</label><input id="stream-akbps" placeholder="160"/></div>
<div><label>音声遅延 ms (0-2000)</label><input id="stream-delay" placeholder="0"/></div>
<div style="align-self:end;display:flex;gap:8px;align-items:center"><label class="switch"><input type="checkbox" id="stream-cc"><span class="slider"></span></label><span id="stream-cc-label" class="badge" style="margin-left:8px">CC off</span></div>
</div>
<div class="actions"><button class="btn primary" id="stream-settings-save">設定を保存</button><button class="btn" id="stream-settings-reload">再読込</button></div>
<div id="stream-settings-msg" class="help"></div>
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
<div style="margin-top:8px"><label>body (0-240)</label><textarea id="work-banner-body-input" rows="2" maxlength="240" placeholder=""></textarea></div>
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
<!-- AUDIO -->
<section id="tab-audio" style="display:none">
<div class="card"><h2>Audio キュー (audio-worker)</h2><p class="desc"><code>tmp/.comment_queue/</code> にテキストを積むと <code>audio_worker</code> が <code>say_enqueue.sh</code> で再生する。手動enqueueは <code>lib/outbound_queue.sh:enqueue_audio_text</code> と同等（120秒 dedup）。</p>
<div class="kv"><dt>queue_dir</dt><dd id="audio-queue-dir" class="mono">-</dd><dt>dedup_dir</dt><dd id="audio-dedup-dir" class="mono">-</dd><dt>dedup_count</dt><dd id="audio-dedup-count" class="mono">-</dd><dt>worker</dt><dd id="audio-worker-status" class="mono">-</dd></div>
<div class="actions"><button class="btn" id="audio-queue-refresh">更新</button><button class="btn danger" id="audio-queue-clear">キュー全クリア</button></div>
<div style="overflow:auto;margin-top:10px"><table><thead><tr><th>filename</th><th>time</th><th>speaker</th><th>preview</th><th></th></tr></thead><tbody id="audio-queue-table"></tbody></table></div>
<div class="help">audio_worker が消化するとファイルは自動で消える（.playing → 削除）。dedup は 120秒間 同一テキストの再投入を抑止。</div>
</div>
<div class="card"><h2>音声合成チェーン (VOICEVOX endpoints)</h2><p class="desc"><code>docich voicevox synth</code> は <code>.env</code> の <code>VOICEVOX_URLS</code>（優先順）を上から試し、失敗したエンドポイントは乗数バックオフ（<span id="voice-backoff-desc" class="mono">-</span>）で休ませる。全部休止中でも順に再試行し、合成を拒否はしない。</p>
<div class="kv"><dt>active</dt><dd id="voice-active" class="mono">-</dd><dt>設定元</dt><dd id="voice-urls-source" class="mono">-</dd><dt>state</dt><dd id="voice-state-file" class="mono" style="font-size:11px">-</dd></div>
<div class="actions"><button class="btn" id="voice-refresh">更新</button><button class="btn primary" id="voice-probe-all">全エンドポイント疎通確認</button><button class="btn" id="voice-reset-all">backoff 全リセット</button></div>
<div style="overflow:auto;margin-top:10px"><table><thead><tr><th>#</th><th>endpoint</th><th>状態</th><th>連続失敗</th><th>成功/失敗</th><th>直近 ms (平均)</th><th>直近成功</th><th>直近エラー</th><th></th></tr></thead><tbody id="voice-table"></tbody></table></div>
<div class="help">状態: <b>ready</b>=次の合成で使う候補（上から順） / <b>backoff</b>=失敗後の休止中（残り秒） / <b>disabled</b>=手動で外している。「疎通確認」は GET /version を打って結果を記録する（成功なら backoff 解除、失敗なら backoff 延長）。</div>
<details style="margin-top:8px"><summary>直近イベント / ログ</summary><pre id="voice-log" class="mono" style="font-size:11px;max-height:220px;overflow:auto;white-space:pre-wrap"></pre></details>
</div>
<div class="card"><h3>手動 enqueue</h3><p class="desc">任意のテキストを読み上げキューに投入。丁寧な敬語で書くと配信で自然に聞こえる。例: <code>お待たせしております。現在、システムの解析を進めております。</code></p>
<div><label>text (1-1000文字) <span id="audio-text-count" class="badge">0/1000</span></label><textarea id="audio-text" rows="4" maxlength="1000" placeholder="お待たせしております。現在、…何卒よろしくお願い申し上げます。"></textarea></div>
<div class="row" style="margin-top:8px"><div><label>source (1-32, 英数字._- )</label><input id="audio-source" maxlength="32" placeholder="webui_manual" value="webui_manual"/></div><div><label>speaker override (任意, 例: 46, 109)</label><input id="audio-speaker" maxlength="64" placeholder="(空=既定話者)"/></div></div>
<div class="help">source はファイル名に含まれる識別子。speaker は VOICEVOX話者ID等（空なら既定）。120秒以内の重複テキストはスキップされる。</div>
<div class="actions"><button class="btn primary" id="audio-enqueue">enqueue して読み上げ</button><button class="btn" id="audio-enqueue-clear">クリア</button></div>
<div id="audio-enqueue-msg" class="help"></div>
<div style="margin-top:10px"><label>プリセット</label>
<button class="preset-btn" data-audio-preset="お待たせしております。現在、システムの自動解析を丁寧に進めております。詳細につきまして、少々お待ちくださいませ。何卒よろしくお願い申し上げます。">丁寧: 解析中</button>
<button class="preset-btn" data-audio-preset="作業が完了いたしました。ご協力ありがとうございました。引き続きよろしくお願い申し上げます。">丁寧: 完了</button>
<button class="preset-btn" data-audio-preset="テストです。音声キューが正常に動作しているか確認しています。">テスト</button>
</div>
</div>
</section>
<!-- PREDICTIONS -->
<section id="tab-predictions" style="display:none">
<div class="card"><h2>Twitch 予想管理</h2><p class="desc">自動予想ワーカーと同じ <code>twitch_predictions.sh</code> を通じて、リモートの予想状態を確認・作成・同期・解決・キャンセルします。アクセストークンは画面やAPIレスポンスへ返しません。</p>
<div class="grid2">
<div class="card kpi"><h3>自動予想</h3><div class="val" id="prediction-enabled">-</div><div class="subk" id="prediction-enabled-sub">-</div></div>
<div class="card kpi"><h3>リモート予想</h3><div class="val" id="prediction-remote-count">-</div><div class="subk" id="prediction-remote-sub">-</div></div>
<div class="card kpi"><h3>prediction worker</h3><div class="val" id="prediction-worker">-</div><div class="subk" id="prediction-worker-sub">-</div></div>
<div class="card kpi"><h3>サイクル進捗</h3><div class="val" id="prediction-progress">-</div><div class="subk" id="prediction-progress-sub">-</div></div>
</div>
<div class="actions"><button class="btn" id="prediction-refresh">更新</button><button class="btn" id="prediction-sync">リモート予想を同期</button></div>
<div id="prediction-status-msg" class="help"></div>
</div>
<div class="card"><h3>ローカル予想状態</h3><div id="prediction-local-state" class="kv"></div><div class="help">リモートに ACTIVE/LOCKED があるのにローカル状態がない場合は、先に「リモート予想を同期」を実行してください。</div></div>
<div class="card"><h3>リモート予想一覧</h3><div style="overflow:auto"><table><thead><tr><th>status</th><th>title</th><th>created</th><th>window</th><th>points/users</th><th>id</th></tr></thead><tbody id="prediction-remote-table"></tbody></table></div></div>
<div class="card"><h3>予想を作成</h3><p class="desc">通常は自動ワーカーが改善サイクルの開始時に作成します。手動作成は既存の ACTIVE/LOCKED 予想を確認してから実行してください。</p><div class="row"><div><label>game number (任意)</label><input id="prediction-game-num" type="number" min="0" max="1000000000" value="0"/></div><div style="align-self:end"><button class="btn primary" id="prediction-create">予想を作成</button></div></div><div id="prediction-create-msg" class="help"></div></div>
<div class="card"><h3>現在の予想を操作</h3><p class="desc">解決・キャンセルは Twitch 側の予想を直ちに確定または取り消します。選択肢は自動ワーカーと同じです。</p><div id="prediction-actions" class="actions"></div><div id="prediction-actions-msg" class="help"></div></div>
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
<div class="card" style="margin-top:12px"><h3>改善ピークチェーン</h3><p class="desc">ピーク時のみ使用する改善モデルチェーン。空なら通常の <code>MODEL_IMPROVE_LIST</code> を継承。<code>IMPROVE_PEAK_CHAIN_ENABLED=1</code> かつピーク中の時のみ有効。旧 defer（ピーク時に改善を遅延）は既定で無効（<code>IMPROVE_PEAK_HOUR_DEFER_ENABLED=0</code>）。</p>
<div class="inherit-row"><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="peak-improve-inherit"> 継承（空で通常チェーンへ）</label><span class="help">effective: <span class="mono" id="peak-improve-effective"></span></span></div>
<div><label>パレット (クリックで追加)</label><div class="palette" id="peak-improve-palette"></div></div>
<div><label>順序 (ドラッグ / 上下 / 削除)</label><ul class="ordered" id="peak-improve-list"></ul></div>
<div class="row"><div style="flex:1"><input id="peak-improve-custom" placeholder="codex:xxx または local"/><div class="help">AGENT_RE で検証</div></div><div style="align-self:end"><button class="btn" id="peak-improve-add">追加</button></div></div>
<div class="row" style="margin-top:12px"><div><label>IMPROVE_PEAK_CHAIN_ENABLED</label><label class="switch"><input type="checkbox" id="peak-improve-enabled"><span class="slider"></span></label><span id="peak-improve-enabled-label" class="badge" style="margin-left:8px">0</span></div><div><label>IMPROVE_PEAK_HOUR_DEFER_ENABLED (旧 defer)</label><label class="switch"><input type="checkbox" id="peak-improve-defer"><span class="slider"></span></label><span id="peak-improve-defer-label" class="badge" style="margin-left:8px">0</span> <span class="help">1=ピーク時に改善を遅延（旧挙動） 0=即時実行</span></div></div>
<div class="help">ピーク時の effective improve chain: <span class="mono" id="peak-improve-effective-preview"></span> <span id="peak-improve-peak-badge" class="badge"></span></div>
</div>
<div class="card" style="margin-top:12px;background:#111319"><h3>現在ピーク判定</h3><div class="kv"><dt>is_peak_now</dt><dd id="peak-now">-</dd><dt>now</dt><dd id="peak-now-str">-</dd><dt>windows</dt><dd id="peak-now-windows" class="mono">-</dd><dt>improve_defer</dt><dd id="peak-improve-defer-status">-</dd><dt>improve_effective</dt><dd id="peak-improve-effective-status" class="mono">-</dd></div></div>
<div class="actions"><button class="btn primary" id="peak-save">保存</button><button class="btn" id="peak-reload">再読込</button></div>
<div id="peak-msg" class="help"></div>
</div>
</section>
<!-- PROMPTS -->
<section id="tab-prompts" style="display:none">
<div class="card"><h2>Prompts</h2><p class="desc"><code>prompts/</code> + <code>soren91/prompts/</code> の Markdown プロンプトを参照・編集。保存は mtime 楽観ロック（競合時は 409）。変更は <code>soren_root/prompts/*.md</code> へ原子書き込み。</p>
<div style="overflow:auto"><table><thead><tr><th>id</th><th>size</th><th>mtime</th><th>preview</th></tr></thead><tbody id="prompts-table"></tbody></table></div>
<div class="actions"><button class="btn" id="prompts-refresh">更新</button></div>
<div class="help" id="prompts-roots" style="margin-top:6px"></div>
</div>
<div class="card"><h3 id="prompts-edit-title">編集</h3>
<div class="kv"><dt>file</dt><dd id="prompts-edit-file" class="mono">-</dd><dt>mtime</dt><dd id="prompts-edit-mtime" class="mono">-</dd><dt>size</dt><dd id="prompts-edit-size" class="mono">-</dd></div>
<textarea id="prompts-content" rows="22" class="mono" placeholder="markdown..."></textarea>
<div class="help" id="prompts-size-help"></div>
<div class="actions"><button class="btn primary" id="prompts-save">保存</button><button class="btn" id="prompts-reload">再読込</button></div>
<div id="prompts-msg" class="help"></div>
<details style="margin-top:10px"><summary>プレビュー (raw)</summary><pre id="prompts-preview" class="mono" style="white-space:pre-wrap;background:#111319;border:1px solid var(--border);border-radius:8px;padding:8px;max-height:360px;overflow:auto"></pre></details>
</div>
</section>
<!-- STATS -->
<section id="tab-stats" style="display:none">
<div class="card"><h2>AI 統計 (ai_stats)</h2><p class="desc"><code>tmp/state/ai_stats/&lt;YYYYMMDD&gt;.jsonl</code> の attempt/winner/fail。直近7日。attempt=dispatch試行、winner=最終採用、all_failed=全滅。</p>
<div class="stats-toolbar"><button class="btn" id="stats-refresh">更新</button><label style="display:flex;gap:6px;align-items:center">days <input id="stats-days" type="number" value="7" min="1" max="30" style="width:80px"/></label><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="stats-group-base" checked/> IMPROVE正規化</label><input id="stats-search" placeholder="chain/agentで絞り込み" style="width:200px"/></div>
<canvas id="stats-canvas" width="900" height="220"></canvas>
<div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>day</th><th>attempt</th><th>winner</th><th>fail</th><th>all_failed</th></tr></thead><tbody id="stats-table"></tbody></table></div>
<div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>agent</th><th>attempt</th><th>winner</th><th>winner率</th><th>fail</th><th>失敗率</th></tr></thead><tbody id="stats-agents"></tbody></table></div>
</div>
<div class="card"><h2>最近のAI失敗理由</h2><p class="desc">fail レコードの <code>error</code> フィールド（プロバイダエラー・タイムアウト等の実原因）。最大40件、新しい順。</p>
<div class="table-wrap"><table><thead><tr><th>時刻</th><th>chain</th><th>model</th><th style="min-width:280px">error</th></tr></thead><tbody id="stats-errors"></tbody></table></div>
</div>
<div class="card"><h2>チェーン別統計 (label)</h2><p class="desc">どのチェーンが呼ばれ、どれだけ成功したか。成功率は <code>winner/attempt</code>。PENDINGは生成途中。</p>
<div class="actions" style="margin-bottom:8px"><span class="help">表示: <span id="stats-label-mode" class="badge">base</span> に正規化 <span class="help" id="stats-label-count"></span></span></div>
<div class="table-wrap"><table><thead><tr><th style="min-width:180px">chain</th><th style="min-width:110px">成功率</th><th>attempt</th><th>winner</th><th>all_failed</th><th style="min-width:140px">深度</th><th>pending</th></tr></thead><tbody id="stats-labels"></tbody></table></div>
<div class="help">深度 = 1回で何段目まで試したか。1=先頭で成功、大きいほど深くフォールバック。</div>
</div>
<div class="card"><h2>チェーン×モデル 詳細</h2><p class="desc">各チェーン内でどのモデルが何回 attempt / winner になったか。上位3チェーンを展開、他は折りたたみ。</p>
<div id="stats-label-agents"></div>
<div class="actions"><button class="btn" id="stats-expand-all">全て展開</button><button class="btn" id="stats-collapse-all">全て折りたたむ</button></div>
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
let peakState = {hoursSet:new Set(), tz:"Asia/Tokyo", prefItems:[], swap:"1", gate:"1", priority:"", improveInherit:true, improveItems:[], improveEnabled:"0", improveDefer:"0", improveEffective:"", improvePeakRaw:""};
let promptsState = {list:[], currentId:null, expectedMtime:0};
let dashTimer = null;
let audioState = {items:[], queueDir:"", dedupDir:"", dedupCount:0, worker:null};
let predictionState = {remote:[], local:null, retry:{}, accumulated:{}, worker:null};
const PREDICTION_LABELS = ["建国なし","ロシア建国(ソ連不成立)","ソ連建国","粛清"];
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
function showLogin(){
  const el = $("#login-overlay");
  if(el) el.style.display = "flex";
}
function hideLogin(){
  const el = $("#login-overlay");
  if(el) el.style.display = "none";
}
let CSRF_TOKEN = null;
async function fetchCsrfToken(force){
  if(CSRF_TOKEN && !force) return CSRF_TOKEN;
  const headers = {};
  const t = sessionStorage.getItem("webui_token");
  if(t) headers["Authorization"] = "Bearer " + t;
  try{
    const res = await fetch("/api/csrf", {headers});
    if(!res.ok) return null;
    const data = await res.json();
    CSRF_TOKEN = data.csrf_token;
    return CSRF_TOKEN;
  }catch(e){ return null; }
}
async function api(path, opts={}){
  // token は URL query から読まない (issue #41)。sessionStorage のみを見る。
  // ログインはトップページのフォームに token を入力する手順に統一する。
  const headers = opts.headers||{};
  if(!headers["Authorization"]){
    const t = sessionStorage.getItem("webui_token");
    if(t) headers["Authorization"] = "Bearer " + t;
  }
  // issue #42: GET 以外は CSRF token (X-CSRF-Token) を付与する。
  const method = (opts.method||"GET").toUpperCase();
  if(method!=="GET" && method!=="HEAD" && !headers["X-CSRF-Token"]){
    const tok = await fetchCsrfToken(false);
    if(tok) headers["X-CSRF-Token"] = tok;
  }
  opts.headers = headers;
  let res = await fetch(path, opts);
  if(res.status === 401){
    sessionStorage.removeItem("webui_token");
    showLogin();
  }
  if(res.status === 403 && method!=="GET" && method!=="HEAD" && !opts._csrfRetried){
    // CSRF token が期限切れ/不正だった可能性 → 1回だけ再取得してリトライ
    let errCode = "";
    try{ errCode = (await res.clone().json()).error || ""; }catch(e){}
    if(/^csrf_/.test(errCode)){
      const tok = await fetchCsrfToken(true);
      if(tok){
        headers["X-CSRF-Token"] = tok;
        opts.headers = headers;
        opts._csrfRetried = true;
        res = await fetch(path, opts);
      }
    }
  }
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
  renderStreamSettings(entries);
  // health badge
  const hb = $("#health-badge");
  hb.textContent = READ_ONLY?"read-only":"read-write";
  hb.className = READ_ONLY?"badge warn":"badge ok";
  applyReadOnly();
  // also update peak now via peak_status
  try{ const ps=await api("/api/peak_status"); $("#peak-now").textContent=ps.is_peak_now?"ピーク中":"オフピーク"; $("#peak-now-str").textContent=ps.now_str||"-"; $("#peak-now-windows").textContent=ps.windows||"(なし)"; }catch(e){}
}
function applyReadOnly(){
  $$("main button").forEach(b=>{ b.disabled = READ_ONLY && b.id !== "backoff-refresh" && b.id !== "stats-refresh" && b.id !== "health-refresh" && b.id !== "chains-reload" && b.id !== "backoff-reload" && b.id !== "peak-reload" && b.id !== "prediction-refresh" && b.id !== "stream-refresh" && b.id !== "stream-settings-reload"; });
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
  const windows = pw? pw.effective : "";
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
  // improve peak chain
  const impPeakEnt=entries["MODEL_IMPROVE_PEAK_LIST"], impEnEnt=entries["IMPROVE_PEAK_CHAIN_ENABLED"], impDeferEnt=entries["IMPROVE_PEAK_HOUR_DEFER_ENABLED"];
  const impPeakVal=impPeakEnt?impPeakEnt.value:"";
  const impPeakEff=impPeakEnt?impPeakEnt.effective:"";
  const impEnVal=impEnEnt? (impEnEnt.value||impEnEnt.default||"0"):"0";
  const impDeferVal=impDeferEnt? (impDeferEnt.value||impDeferEnt.default||"0"):"0";
  peakState.improveInherit=!impPeakVal;
  peakState.improveItems=impPeakVal? impPeakVal.split(",").map(s=>s.trim()).filter(Boolean):[];
  peakState.improveEnabled=impEnVal;
  peakState.improveDefer=impDeferVal;
  peakState.improveEffective=impPeakEff;
  const impInhEl=document.getElementById("peak-improve-inherit");
  if(impInhEl){
    impInhEl.checked=peakState.improveInherit;
    impInhEl.onchange=(e)=>{
      peakState.improveInherit=e.target.checked;
      if(e.target.checked){
        const eff=peakState.improveEffective||"";
        peakState.improveItems=eff?eff.split(",").map(s=>s.trim()).filter(Boolean):[];
      }
      refreshPeakImproveList();
      document.getElementById("peak-improve-effective").textContent=peakState.improveInherit?("(継承: "+(peakState.improveEffective||"")+")"):peakState.improveItems.join(",");
      updatePeakImprovePreview();
    };
  }
  const impEnEl=document.getElementById("peak-improve-enabled");
  const impDeferEl=document.getElementById("peak-improve-defer");
  if(impEnEl){
    impEnEl.checked=peakState.improveEnabled==="1";
    impEnEl.onchange=(e)=>{ peakState.improveEnabled=e.target.checked?"1":"0"; document.getElementById("peak-improve-enabled-label").textContent=peakState.improveEnabled; updatePeakImprovePreview(); };
    document.getElementById("peak-improve-enabled-label").textContent=peakState.improveEnabled;
  }
  if(impDeferEl){
    impDeferEl.checked=peakState.improveDefer==="1";
    impDeferEl.onchange=(e)=>{ peakState.improveDefer=e.target.checked?"1":"0"; document.getElementById("peak-improve-defer-label").textContent=peakState.improveDefer; };
    document.getElementById("peak-improve-defer-label").textContent=peakState.improveDefer;
  }
  // palette for improve
  const impPal=document.getElementById("peak-improve-palette");
  if(impPal){
    impPal.innerHTML="";
    const palSet=new Set([...paletteSet, ...peakState.improveItems]);
    // also add from MODEL_IMPROVE_LIST effective
    const baseList=entries["MODEL_IMPROVE_LIST"]?.effective||"";
    for(const p of baseList.split(",")){ const t=p.trim(); if(t) palSet.add(t); }
    for(const ag of [...palSet].sort()){
      const chip=document.createElement("span");
      chip.className="chip"; chip.textContent=ag;
      chip.onclick=()=>{
        if(peakState.improveInherit){ toast("継承中は編集できません。チェックを外してください"); return; }
        if(!AGENT_RE.test(ag)){ toast("不正なエージェント: "+ag); return; }
        peakState.improveItems.push(ag);
        refreshPeakImproveList();
        document.getElementById("peak-improve-effective").textContent=peakState.improveItems.join(",");
        updatePeakImprovePreview();
      };
      impPal.appendChild(chip);
    }
  }
  refreshPeakImproveList();
  const impCustomAdd=document.getElementById("peak-improve-add");
  if(impCustomAdd) impCustomAdd.onclick=()=>{
    if(peakState.improveInherit){ toast("継承中は編集できません"); return; }
    const inp=document.getElementById("peak-improve-custom");
    const v=inp.value.trim();
    if(!v){ toast("値を入力してください"); return; }
    if(!AGENT_RE.test(v)){ toast(`不正なエージェント: ${v}`); return; }
    peakState.improveItems.push(v);
    inp.value="";
    refreshPeakImproveList();
    document.getElementById("peak-improve-effective").textContent=peakState.improveItems.join(",");
    updatePeakImprovePreview();
  };
  document.getElementById("peak-improve-effective").textContent=peakState.improveInherit?("(継承: "+(peakState.improveEffective||"")+")"):peakState.improveItems.join(",");
  updatePeakImprovePreview();
  // status preview
  const deferSt=document.getElementById("peak-improve-defer-status");
  if(deferSt) deferSt.textContent=peakState.improveDefer;
  const effSt=document.getElementById("peak-improve-effective-status");
  if(effSt) effSt.textContent=peakState.improveEnabled==="1" ? (peakState.improveInherit? peakState.improveEffective : peakState.improveItems.join(",")) : (entries["MODEL_IMPROVE_LIST"]?.effective||"");
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
function refreshPeakImproveList(){
  const ul=document.getElementById("peak-improve-list");
  if(!ul) return;
  ul.innerHTML="";
  const disabled=peakState.improveInherit;
  peakState.improveItems.forEach((item, idx)=>{
    const li=document.createElement("li");
    li.draggable=!disabled;
    if(disabled) li.classList.add("inherited");
    li.dataset.idx=String(idx);
    li.innerHTML=`<span class="drag">≡</span><span class="mono" style="flex:1">${esc(item)}</span>
      <button class="btn" data-iup="${idx}" style="padding:4px 8px">↑</button>
      <button class="btn" data-idown="${idx}" style="padding:4px 8px">↓</button>
      <button class="btn danger" data-iremove="${idx}" style="padding:4px 8px">×</button>`;
    const up=li.querySelector(`[data-iup="${idx}"]`);
    const down=li.querySelector(`[data-idown="${idx}"]`);
    const rem=li.querySelector(`[data-iremove="${idx}"]`);
    if(up) up.onclick=()=>{ if(disabled) return; if(idx>0){ const a=peakState.improveItems.splice(idx,1)[0]; peakState.improveItems.splice(idx-1,0,a); refreshPeakImproveList(); updatePeakImprovePreview(); }};
    if(down) down.onclick=()=>{ if(disabled) return; if(idx<peakState.improveItems.length-1){ const a=peakState.improveItems.splice(idx,1)[0]; peakState.improveItems.splice(idx+1,0,a); refreshPeakImproveList(); updatePeakImprovePreview(); }};
    if(rem) rem.onclick=()=>{ if(disabled) return; peakState.improveItems.splice(idx,1); refreshPeakImproveList(); updatePeakImprovePreview(); };
    if(disabled){ if(up) up.disabled=true; if(down) down.disabled=true; if(rem) rem.disabled=true; }
    li.addEventListener("dragstart",(e)=>{ if(disabled){ e.preventDefault(); return; } li.classList.add("dragging"); e.dataTransfer.setData("text/plain", String(idx)); });
    li.addEventListener("dragend",()=>li.classList.remove("dragging"));
    ul.appendChild(li);
  });
  ul.ondragover=(e)=>{ e.preventDefault(); };
  ul.ondrop=(e)=>{
    e.preventDefault();
    if(disabled) return;
    const fromIdx=parseInt(e.dataTransfer.getData("text/plain"),10);
    const target=e.target.closest("li");
    if(target){
      const toIdx=parseInt(target.dataset.idx,10);
      if(!isNaN(fromIdx)&&!isNaN(toIdx)&&fromIdx!==toIdx){
        const [m]=peakState.improveItems.splice(fromIdx,1);
        peakState.improveItems.splice(toIdx,0,m);
        refreshPeakImproveList();
        updatePeakImprovePreview();
      }
    }
  };
  document.getElementById("peak-improve-effective").textContent=peakState.improveInherit?("(継承: "+(peakState.improveEffective||"")+")"):peakState.improveItems.join(",");
}
function updatePeakImprovePreview(){
  const enabled=peakState.improveEnabled==="1";
  const isPeak=document.getElementById("peak-now")?.textContent==="ピーク中";
  let effective="";
  if(!enabled) effective="(無効: 常に通常チェーン)";
  else if(peakState.improveInherit) effective=`継承: ${peakState.improveEffective||""} ${isPeak?"(ピーク中)":"(オフピーク)"}`;
  else effective=peakState.improveItems.join(",") || "(空)";
  const el=document.getElementById("peak-improve-effective-preview");
  if(el) el.textContent=effective + (enabled && isPeak ? " → ピークチェーン有効" : enabled? " (オフピークは通常チェーン)":"");
  const badge=document.getElementById("peak-improve-peak-badge");
  if(badge){ badge.textContent=isPeak?"ピーク中":"オフピーク"; badge.className=isPeak?"badge warn":"badge ok"; }
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
    const res=await api("/api/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({values:payload,expected_mtime:ENV_MTIME,confirm:true})});
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
    const res=await api("/api/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({values:payload,expected_mtime:ENV_MTIME,confirm:true})});
    ENV_MTIME=res.env_mtime||ENV_MTIME;
    $("#env-mtime").textContent=`mtime=${ENV_MTIME} ${fmtTime(ENV_MTIME)}`;
    toast("保存しました");
    await loadConfig();
    try{ await api("/api/reload",{method:"POST"}); }catch(e){}
  }catch(e){ toast(String(e),5000); }
}
async function savePeak(){
  if(READ_ONLY){ toast("read-only"); return; }
  let impPeakVal="";
  if(!peakState.improveInherit){
    for(const ag of peakState.improveItems){ if(!AGENT_RE.test(ag)){ toast("PEAK_IMPROVE 不正: "+ag); return; } }
    impPeakVal=peakState.improveItems.join(",");
  }
  const payload={
    "PEAK_HOURS_WINDOWS": peakState.windows,
    "PEAK_HOURS_TZ": peakState.tz.trim(),
    "PEAK_HOURS_PRIORITY_AGENT": peakState.priority.trim(),
    "PEAK_HOURS_AGENT_PREFERENCE": peakState.prefItems.join(","),
    "PEAK_HOURS_AGENT_SWAP_ENABLED": peakState.swap,
    "PEAK_HOURS_QUEUE_GATE_ENABLED": peakState.gate,
    "MODEL_IMPROVE_PEAK_LIST": impPeakVal,
    "IMPROVE_PEAK_CHAIN_ENABLED": peakState.improveEnabled,
    "IMPROVE_PEAK_HOUR_DEFER_ENABLED": peakState.improveDefer
  };
  // validate priority
  if(payload["PEAK_HOURS_PRIORITY_AGENT"] && !AGENT_RE.test(payload["PEAK_HOURS_PRIORITY_AGENT"])){ toast("PRIORITY_AGENT 不正"); return; }
  for(const ag of peakState.prefItems){ if(!AGENT_RE.test(ag)){ toast("PREFERENCE 不正: "+ag); return; } }
  if(payload["PEAK_HOURS_TZ"] && !/^[A-Za-z0-9_+.\/:-]{1,64}$/.test(payload["PEAK_HOURS_TZ"])){ toast("TZ 不正"); return; }
  try{
    const res=await api("/api/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({values:payload,expected_mtime:ENV_MTIME,confirm:true})});
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
let _lastStatsData = null;
async function loadStats(){
  const days = parseInt($("#stats-days").value||"7",10);
  const data = await api(`/api/stats?days=${days}`);
  _lastStatsData = data;
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
    const rate = v.attempt? (v.winner / v.attempt * 100).toFixed(1) + "%" : "-";
    const frate = v.attempt? (v.fail / v.attempt * 100).toFixed(1) + "%" : "-";
    const tr=document.createElement("tr");
    tr.innerHTML=`<td class="mono">${esc(agent)}</td><td>${v.attempt}</td><td>${v.winner}</td><td>${esc(rate)}</td><td>${v.fail||0}</td><td>${esc(frate)}</td>`;
    agBody.appendChild(tr);
  }
  const errBody=$("#stats-errors");
  if(errBody){
    errBody.innerHTML="";
    const errs=data.recent_errors||[];
    if(errs.length===0){
      errBody.innerHTML='<tr><td colspan="4" class="help">error記録付きのfailなし</td></tr>';
    } else {
      for(const e of errs){
        const t=e.ts? new Date(e.ts*1000).toLocaleTimeString("ja-JP",{hour12:false}) : "-";
        const tr=document.createElement("tr");
        tr.innerHTML=`<td>${esc(t)}</td><td class="mono">${esc(e.label)}</td><td class="mono">${esc(e.agent)}</td><td class="mono" style="font-size:11px;word-break:break-all">${esc(e.error)}</td>`;
        errBody.appendChild(tr);
      }
    }
  }
  drawStats(data.days);
  const groupBase = $("#stats-group-base")?.checked ?? true;
  renderStatsLabels(data, groupBase);
  renderStatsLabelAgents(data, groupBase);
}
function renderStatsLabels(data, groupBase){
  const tb = $("#stats-labels");
  if(!tb) return;
  tb.innerHTML="";
  const byLabel = groupBase ? (data.by_base_label||{}) : (data.by_label||{});
  const byDepth = groupBase ? (data.by_base_depth||{}) : (data.by_label_depth||{});
  let entries = Object.entries(byLabel);
  const q = ($("#stats-search")?.value||"").trim().toLowerCase();
  if(q){
    entries = entries.filter(([label,v])=>{
      const hint=(v.chain_hint||"").toLowerCase();
      return label.toLowerCase().includes(q) || hint.includes(q);
    });
  }
  if(entries.length===0){
    tb.innerHTML='<tr><td colspan="7" class="help">データなし</td></tr>';
    const cnt=$("#stats-label-count"); if(cnt) cnt.textContent="";
    return;
  }
  // sort by attempt desc
  entries.sort((a,b)=>(b[1].attempt||0)-(a[1].attempt||0));
  const modeEl = $("#stats-label-mode");
  if(modeEl) modeEl.textContent = groupBase? "base (正規化)":"raw";
  const cntEl=$("#stats-label-count"); if(cntEl) cntEl.textContent=`(${entries.length}チェーン)`;
  for(const [label, v] of entries){
    const d = byDepth[label] || {winner_depth:{}, failed_depth:{}, avg_winner_depth:0, avg_failed_depth:0, pending:0, total_generations:0};
    const attempt=v.attempt||0, winner=v.winner||0;
    const rate = attempt? (winner/attempt*100):0;
    const rateText = attempt? rate.toFixed(1)+"%":"-";
    let barClass="ok"; if(rate<40) barClass="bad"; else if(rate<70) barClass="warn";
    const wd = d.winner_depth||{}, fd = d.failed_depth||{};
    const wdEntries=Object.entries(wd).sort((a,b)=>parseInt(a[0])-parseInt(b[0]));
    const avgW = d.avg_winner_depth? d.avg_winner_depth.toFixed(1) : "-";
    const wdMini = wdEntries.length? wdEntries.map(([k,c])=>`d${k}:${c}`).join(" "):"-";
    const pending=v.pending!=null? v.pending : (d.pending||0);
    const pendingBadge = pending>0? `<span class="badge bad">${pending}</span>` : `<span class="badge" style="opacity:0.5">0</span>`;
    const chainHint = esc(v.chain_hint||"-");
    const tr=document.createElement("tr");
    tr.style.cursor="pointer";
    tr.title=`${label} → クリックで詳細へ`;
    tr.onclick=()=>{
      const target=document.getElementById(`stats-card-${CSS.escape(label)}`);
      if(target){ target.scrollIntoView({behavior:"smooth",block:"start"}); target.classList.add("open"); const body=target.querySelector(".accordion-body"); if(body) body.style.display="block"; }
    };
    tr.innerHTML=`<td><div class="stats-label mono" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(label)}">${esc(label)}</div><span class="stats-hint mono">${chainHint}</span></td>
      <td><div class="mono" style="font-size:12px">${rateText}</div><div class="rate-bar ${barClass}"><i style="width:${Math.min(100,Math.round(rate))}%"></i></div></td>
      <td>${attempt}</td><td>${winner}</td><td>${v.all_failed||0}</td>
      <td><span class="mono" style="font-size:12px">${avgW}</span><div class="depth-mini">${esc(wdMini)}</div></td>
      <td>${pendingBadge}</td>`;
    tb.appendChild(tr);
  }
}
function renderStatsLabelAgents(data, groupBase){
  const cont = $("#stats-label-agents");
  if(!cont) return;
  cont.innerHTML="";
  const byLabelAgent = groupBase ? (data.by_base_label_agent||{}) : (data.by_label_agent||{});
  const byLabel = groupBase ? (data.by_base_label||{}) : (data.by_label||{});
  let labels = Object.keys(byLabelAgent).sort((a,b)=> (byLabel[b]?.attempt||0) - (byLabel[a]?.attempt||0));
  const q = ($("#stats-search")?.value||"").trim().toLowerCase();
  if(q){
    labels = labels.filter(label=>{
      const v=byLabel[label]||{};
      const hint=(v.chain_hint||"").toLowerCase();
      if(label.toLowerCase().includes(q) || hint.includes(q)) return true;
      const agents=byLabelAgent[label]||{};
      return Object.keys(agents).some(ag=>ag.toLowerCase().includes(q));
    });
  }
  if(labels.length===0){
    cont.innerHTML='<div class="help">データなし</div>';
    return;
  }
  const expandedBySearch = !!q;
  labels.forEach((label, idx)=>{
    const agents = byLabelAgent[label]||{};
    const v = byLabel[label]||{};
    const details = document.createElement("div");
    details.className="accordion-item";
    details.id=`stats-card-${label}`;
    const shouldOpen = expandedBySearch || idx<3;
    if(shouldOpen) details.classList.add("open");
    const agentRows = Object.entries(agents).sort((a,b)=>b[1].winner - a[1].winner || b[1].attempt - a[1].attempt).map(([ag, c])=>{
      const rate = c.attempt? (c.winner/c.attempt*100).toFixed(1)+"%" : "-";
      let barClass="ok"; const rv=c.attempt? c.winner/c.attempt*100:0; if(rv<40) barClass="bad"; else if(rv<70) barClass="warn";
      return `<tr><td class="mono" style="max-width:200px;overflow:hidden;text-overflow:ellipsis" title="${esc(ag)}">${esc(ag)}</td><td>${c.attempt}</td><td>${c.winner}</td><td><span class="mono">${rate}</span><div class="rate-bar ${barClass}" style="margin-top:2px"><i style="width:${Math.min(100,Math.round(rv))}%"></i></div></td><td>${c.fail||0}</td></tr>`;
    }).join("");
    const table = agentRows? `<div class="table-wrap"><table><thead><tr><th>agent</th><th>attempt</th><th>winner</th><th>winner率</th><th>fail</th></tr></thead><tbody>${agentRows}</tbody></table></div>` : '<div class="help">agent詳細なし (all_failedのみ等)</div>';
    const depthInfo = (groupBase? data.by_base_depth : data.by_label_depth)?.[label];
    const depthHelp = depthInfo? `avg深度 ${depthInfo.avg_winner_depth?depthInfo.avg_winner_depth.toFixed(1):"-"} / 世代 ${depthInfo.total_generations||0} / pending ${depthInfo.pending||0}` : "";
    const attempt=v.attempt||0, winner=v.winner||0;
    const rate = attempt? (winner/attempt*100).toFixed(1)+"%":"-";
    const summary = document.createElement("div");
    summary.className="accordion-summary";
    summary.innerHTML=`<span class="mono" style="font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(label)}</span><span class="badge" style="margin-left:8px">${esc(v.chain_hint||"")}</span><span class="mono" style="font-size:12px;margin-left:8px">${attempt}→${winner} ${rate}</span><span style="margin-left:8px;color:var(--muted)">${shouldOpen?"▾":"▸"}</span>`;
    const body=document.createElement("div");
    body.className="accordion-body";
    body.style.display=shouldOpen?"block":"none";
    body.innerHTML=`<div class="help" style="margin-bottom:6px">attempt ${attempt} winner ${winner} all_failed ${v.all_failed||0} | ${esc(depthHelp)}</div>${table}`;
    summary.onclick=()=>{
      const isOpen=details.classList.contains("open");
      if(isOpen){ details.classList.remove("open"); body.style.display="none"; summary.querySelector("span:last-child").textContent="▸"; }
      else { details.classList.add("open"); body.style.display="block"; summary.querySelector("span:last-child").textContent="▾"; }
    };
    details.appendChild(summary);
    details.appendChild(body);
    cont.appendChild(details);
  });
  if(!q && labels.length>20){
    const more=document.createElement("div"); more.className="help"; more.textContent=`他 ${labels.length-20} チェーンは省略。検索で絞り込むか「全て展開」で表示`;
    cont.appendChild(more);
  }
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
let streamTimer=null;
let audioTimer=null;
function renderStream(d){
  const stateMap={live:"LIVE",paused:"停止中 (paused)",off:"オフ"};
  const el=$("#stream-state");
  if(el){ el.textContent=stateMap[d.state]||String(d.state); el.style.color=d.state==="live"?"#4ade80":"inherit"; }
  const sub=$("#stream-state-sub");
  if(sub){
    const parts=[];
    if(d.backend) parts.push(String(d.backend));
    if(d.width&&d.height) parts.push(`${d.width}x${d.height}`);
    if(d.fps) parts.push(`${d.fps}fps`);
    if(d.bitrate) parts.push(String(d.bitrate));
    sub.textContent=parts.join(" | ")||"-";
  }
  const kv=$("#stream-detail");
  if(kv){
    const rows=[
      ["state",d.state],["running",d.running],["paused(マーカー)",d.paused],
      ["pid",d.pid],["ffmpeg_pid",d.ffmpeg_pid],["runner_alive",d.runner_alive],["ffmpeg_alive",d.ffmpeg_alive],
      ["drop_frames",d.drop_frames],["dup_frames",d.dup_frames],["out_time",d.out_time],
      ["uptime_sec",d.uptime_sec],["status_age_sec",d.status_age_sec]
    ];
    kv.innerHTML=rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd class="mono">${esc(String(v??"-"))}</dd>`).join("");
  }
  const cs=$("#chat-state");
  if(cs) cs.textContent=d.chat_paused?"停止中":"稼働";
  const css=$("#chat-state-sub");
  if(css) css.textContent=d.chat_paused?"chat_worker.paused":"chat_worker 稼働中";
  const sBtn=$("#stream-start"), stBtn=$("#stream-stop"), cStart=$("#chat-start"), cStop=$("#chat-stop");
  if(sBtn) sBtn.disabled=(d.state==="live")||READ_ONLY;
  if(stBtn) stBtn.disabled=(d.state==="paused")||READ_ONLY;
  if(cStart) cStart.disabled=(!d.chat_paused)||READ_ONLY;
  if(cStop) cStop.disabled=(!!d.chat_paused)||READ_ONLY;
}
async function loadStream(){
  try{
    const data=await api("/api/stream");
    renderStream(data);
  }catch(e){ console.warn("loadStream",e); toast(String(e),4000); }
}
async function streamAction(action){
  if(READ_ONLY){ toast("read-only"); return; }
  const confirmMsg=action==="stop"?"配信を停止しますか？視聴者には配信終了として見えます。":"配信を開始しますか？";
  if(!confirm(confirmMsg)) return;
  const msg=$("#stream-msg"); if(msg) msg.textContent="処理中...";
  try{
    const res=await api("/api/stream",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,confirm:true})});
    if(msg) msg.textContent=res.hint||((res.ok?"完了":"終了状態: ")+res.state);
    toast(`配信 ${action}: ${res.state}`);
    await loadStream();
  }catch(e){ if(msg) msg.textContent=String(e); toast(String(e),5000); }
}
async function chatAction(action){
  if(READ_ONLY){ toast("read-only"); return; }
  const confirmMsg=action==="stop"?"チャット送信を停止しますか？（IRC受信・コメント生成・投稿が止まります）":"チャット送信を再開しますか？";
  if(!confirm(confirmMsg)) return;
  const msg=$("#chat-msg"); if(msg) msg.textContent="処理中...";
  try{
    const res=await api("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});
    if(msg) msg.textContent=res.chat_paused?"停止しました (worker がループ周期内で park します)":"再開しました (worker が自動復帰します)";
    toast(`チャット ${action}`);
    await loadStream();
  }catch(e){ if(msg) msg.textContent=String(e); toast(String(e),5000); }
}
const WC_WORKERS=[["prediction_worker","予想 prediction_worker"],["improve_daemon","改善 improve_daemon"]];
function wcEl(worker,suffix){ return document.getElementById(`wc-${worker}-${suffix}`); }
function renderWorkersControl(workers){
  const wrap=document.getElementById("wc-rows");
  if(!wrap) return;
  if(!wrap.dataset.built){
    wrap.innerHTML=WC_WORKERS.map(([w,label])=>`
      <div class="row" style="align-items:center;margin-bottom:10px">
        <div style="min-width:200px"><b>${esc(label)}</b><div class="mono" id="wc-${w}-pid" style="font-size:11px;color:var(--muted)">pid=-</div></div>
        <div><span class="badge" id="wc-${w}-badge">-</span></div>
        <div style="display:flex;gap:6px;margin-left:auto">
          <button class="btn primary" id="wc-${w}-start">開始</button>
          <button class="btn danger" id="wc-${w}-stop">停止</button>
        </div>
      </div>`).join("");
    wrap.dataset.built="1";
    for(const [w] of WC_WORKERS){
      const sb=wcEl(w,"start"), tb=wcEl(w,"stop");
      if(sb) sb.onclick=()=>workerControl(w,"start");
      if(tb) tb.onclick=()=>workerControl(w,"stop");
    }
  }
  for(const [w] of WC_WORKERS){
    const row=(workers||[]).find(x=>x.worker===w)||{};
    const badge=wcEl(w,"badge"), pidEl=wcEl(w,"pid");
    if(badge){
      badge.textContent=row.paused?"停止中 (paused)":(row.alive?"稼働中":"停止中");
      badge.className=row.paused?"badge warn":(row.alive?"badge ok":"badge");
    }
    if(pidEl) pidEl.textContent=row.pid?`pid=${row.pid}`:"pid=-";
    const sb=wcEl(w,"start"), tb=wcEl(w,"stop");
    if(sb) sb.disabled=(!!row.alive&&!row.paused)||READ_ONLY;
    if(tb) tb.disabled=(!!row.paused)||READ_ONLY;
  }
}
async function loadWorkersControl(){
  try{
    const data=await api("/api/workers");
    renderWorkersControl(data.workers);
  }catch(e){ console.warn("loadWorkersControl",e); }
}
async function workerControl(worker,action){
  if(READ_ONLY){ toast("read-only"); return; }
  let confirmMsg;
  if(action==="stop"){
    confirmMsg=worker==="improve_daemon"
      ?"改善ワーカーを停止しますか？実行中の改善ジョブも停止します。"
      :"予想ワーカーを停止しますか？Twitch 予想の自動作成・解決が止まります。";
  }else{
    confirmMsg=(worker==="improve_daemon"?"改善ワーカー":"予想ワーカー")+"を開始しますか？";
  }
  if(!confirm(confirmMsg)) return;
  const msg=$("#wc-msg"); if(msg) msg.textContent="処理中...";
  try{
    const res=await api("/api/workers",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({worker,action,confirm:true})});
    if(msg){
      if(action==="start"&&!res.ok&&res.hint) msg.textContent=res.hint;
      else if(res.job_continues_in_background) msg.textContent="停止できません。改善ジョブがまだ稼働しています。";
      else if(action==="stop"&&res.stopped===false) msg.textContent="マーカーを作成しましたが、プロセスの退出を確認できていません。";
      else if(action==="stop") msg.textContent="停止しました (supervisor による再起動は抑止されます)。";
      else msg.textContent="開始しました。";
    }
    toast(`${worker} ${action}`);
    await loadWorkersControl();
  }catch(e){ if(msg) msg.textContent=String(e); toast(String(e),5000); }
}
const STREAM_SETTING_KEYS=["SOREN_DIRECT_STREAM_SIZE","SOREN_DIRECT_STREAM_FPS","SOREN_DIRECT_STREAM_VIDEO_KBPS","SOREN_DIRECT_STREAM_AUDIO_KBPS","SOREN_DIRECT_STREAM_AUDIO_DELAY_MS","DOCICH_CC_ENABLED"];
function renderStreamSettings(entries){
  const val=k=>{ const e=entries[k]; return e ? (e.value||e.effective||e.default||"") : ""; };
  const set=(id,v)=>{ const el=document.getElementById(id); if(el) el.value=v; };
  set("stream-size",val("SOREN_DIRECT_STREAM_SIZE")||"1280x720");
  set("stream-fps",val("SOREN_DIRECT_STREAM_FPS")||"30");
  set("stream-vkbps",val("SOREN_DIRECT_STREAM_VIDEO_KBPS")||"4500");
  set("stream-akbps",val("SOREN_DIRECT_STREAM_AUDIO_KBPS")||"160");
  set("stream-delay",val("SOREN_DIRECT_STREAM_AUDIO_DELAY_MS")||"0");
  const cc=val("DOCICH_CC_ENABLED")==="1";
  const ccEl=document.getElementById("stream-cc");
  if(ccEl){ ccEl.checked=cc; const lb=document.getElementById("stream-cc-label"); if(lb) lb.textContent=cc?"CC on":"CC off"; }
}
async function saveStreamSettings(){
  if(READ_ONLY){ toast("read-only"); return; }
  const payload={
    SOREN_DIRECT_STREAM_SIZE:String($("#stream-size").value||"").trim(),
    SOREN_DIRECT_STREAM_FPS:String($("#stream-fps").value||"").trim(),
    SOREN_DIRECT_STREAM_VIDEO_KBPS:String($("#stream-vkbps").value||"").trim(),
    SOREN_DIRECT_STREAM_AUDIO_KBPS:String($("#stream-akbps").value||"").trim(),
    SOREN_DIRECT_STREAM_AUDIO_DELAY_MS:String($("#stream-delay").value||"").trim(),
    DOCICH_CC_ENABLED:(document.getElementById("stream-cc")&&document.getElementById("stream-cc").checked)?"1":"0"
  };
  if(!/^[0-9]{2,5}x[0-9]{2,5}$/.test(payload.SOREN_DIRECT_STREAM_SIZE)){ toast("解像度は WIDTHxHEIGHT 形式で入力してください"); return; }
  for(const k of ["SOREN_DIRECT_STREAM_FPS","SOREN_DIRECT_STREAM_VIDEO_KBPS","SOREN_DIRECT_STREAM_AUDIO_KBPS","SOREN_DIRECT_STREAM_AUDIO_DELAY_MS"]){
    if(!/^[0-9]+$/.test(payload[k])){ toast(k+" は整数で入力してください"); return; }
  }
  const msg=$("#stream-settings-msg"); if(msg) msg.textContent="保存中...";
  try{
    const res=await api("/api/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({values:payload,expected_mtime:ENV_MTIME,confirm:true})});
    ENV_MTIME=res.env_mtime||ENV_MTIME;
    $("#env-mtime").textContent=`mtime=${ENV_MTIME} ${fmtTime(ENV_MTIME)}`;
    if(msg) msg.textContent="保存しました。配信の再起動 (stop → start) 後に反映されます。";
    toast("保存しました");
    await loadConfig();
  }catch(e){ if(msg) msg.textContent=String(e); toast(String(e),5000); }
}
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
      const st=w.paused?'<span class="badge warn">paused</span>':(w.alive?'<span class="badge ok">alive</span>':'<span class="badge bad">down</span>');
      tr.innerHTML=`<td class="mono">${esc(w.worker)}</td><td>${w.pid||"-"}</td><td>${st}</td>`;
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
async function loadPrompts(){
  try{
    const data=await api("/api/prompts");
    promptsState.list=data.prompts||[];
    const tb=$("#prompts-table");
    tb.innerHTML="";
    const rootsEl=$("#prompts-roots");
    if(rootsEl) rootsEl.textContent="roots: "+(data.roots||[]).join(" , ");
    for(const p of promptsState.list){
      const tr=document.createElement("tr");
      tr.style.cursor="pointer";
      if(promptsState.currentId===p.id) tr.style.background="rgba(110,168,254,0.15)";
      tr.innerHTML=`<td class="mono">${esc(p.id)}</td><td>${p.size}</td><td class="mono" style="font-size:11px">${fmtTime(p.mtime)}</td><td class="mono" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((p.preview||"").slice(0,120))}</td>`;
      tr.onclick=()=> loadPrompt(p.id);
      tb.appendChild(tr);
    }
    if(!promptsState.list.length) tb.innerHTML='<tr><td colspan="4" class="help">promptsなし</td></tr>';
  }catch(e){ toast(String(e),4000); }
}
async function loadPrompt(id){
  try{
    const data=await api(`/api/prompts/${encodeURIComponent(id)}`);
    promptsState.currentId=id; promptsState.expectedMtime=data.mtime||0;
    $("#prompts-edit-file").textContent=id;
    $("#prompts-edit-mtime").textContent=`${data.mtime} (${fmtTime(data.mtime)})`;
    $("#prompts-edit-size").textContent=`${data.size||0} bytes`;
    $("#prompts-edit-title").textContent=`編集: ${id}`;
    const ta=$("#prompts-content");
    ta.value=data.content||"";
    $("#prompts-msg").textContent="";
    updatePromptsPreview();
    // highlight selection
    loadPrompts();
  }catch(e){ toast(String(e),5000); $("#prompts-msg").textContent=String(e); }
}
function updatePromptsPreview(){
  const txt=$("#prompts-content").value||"";
  const pre=$("#prompts-preview");
  if(pre) pre.textContent=txt;
  const help=$("#prompts-size-help");
  if(help){
    const sz=new Blob([txt]).size;
    help.textContent=`${sz} bytes / ${200*1024} max ${sz>200*1024?"(超過)":""}`;
    help.style.color= sz>200*1024 ? "var(--bad)" : "";
  }
  const sizeEl=$("#prompts-edit-size");
  if(sizeEl) sizeEl.textContent=`${new Blob([txt]).size} bytes`;
}
async function savePrompt(){
  if(READ_ONLY){ toast("read-only モードのため保存できません"); return; }
  const id=promptsState.currentId;
  if(!id){ toast("ファイルを選択してください"); return; }
  const content=$("#prompts-content").value;
  const sz=new Blob([content]).size;
  if(sz>200*1024){ toast("サイズ超過 200KB"); return; }
  try{
    const res=await api(`/api/prompts/${encodeURIComponent(id)}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({content, expected_mtime: promptsState.expectedMtime})});
    promptsState.expectedMtime=res.mtime||promptsState.expectedMtime;
    $("#prompts-edit-mtime").textContent=`${res.mtime} (${fmtTime(res.mtime)})`;
    toast(`保存: ${id}`);
    $("#prompts-msg").textContent="";
    await loadPrompts();
  }catch(e){
    const msg=String(e);
    if(msg.includes("409")){
      toast("競合: 他で更新されました。再読込してマージしてください",5000);
      $("#prompts-msg").textContent=msg;
    } else {
      toast(msg,5000);
      $("#prompts-msg").textContent=msg;
    }
  }
}
async function loadAudioQueue(){
  try{
    const data=await api("/api/audio/queue");
    audioState.items=data.items||[];
    audioState.queueDir=data.queue_dir||"";
    audioState.dedupDir=data.dedup_dir||"";
    audioState.dedupCount=data.dedup_count||0;
    audioState.worker=data.worker||null;
    $("#audio-queue-dir").textContent=audioState.queueDir;
    $("#audio-dedup-dir").textContent=audioState.dedupDir;
    $("#audio-dedup-count").textContent=audioState.dedupCount;
    const w=audioState.worker;
    const wEl=$("#audio-worker-status");
    if(w) wEl.innerHTML=(w.alive?`<span class="badge ok">alive</span>`:`<span class="badge bad">down</span>`)+` <span class="mono">pid ${w.pid||"-"}</span>`;
    else wEl.textContent="-";
    const tb=$("#audio-queue-table");
    tb.innerHTML="";
    if(audioState.items.length===0){
      tb.innerHTML='<tr><td colspan="5" class="help">キュー空（再生待ちなし）</td></tr>';
    } else {
      for(const it of audioState.items){
        const tr=document.createElement("tr");
        const st=it.playing?'<span class="badge warn">playing</span>':'';
        tr.innerHTML=`<td class="mono" style="font-size:11px">${esc(it.filename)}</td><td class="mono" style="font-size:11px">${fmtTime(it.mtime)}</td><td class="mono">${esc(it.speaker||"-")}</td><td class="mono" style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${st} ${esc(it.preview)}</td><td>${it.playing?'':`<button class="btn danger" data-adel="${esc(it.filename)}" style="padding:4px 8px">×</button>`}</td>`;
        tb.appendChild(tr);
      }
    }
    for(const btn of $$("[data-adel]")){
      btn.onclick=async()=>{
        const fname=btn.getAttribute("data-adel");
        try{ await api(`/api/audio/queue/${encodeURIComponent(fname)}`,{method:"DELETE"}); toast(`削除: ${fname}`); await loadAudioQueue(); }catch(e){ toast(String(e)); }
      };
    }
  }catch(e){ toast(String(e)); }
}
function voiceStatusBadge(row){
  const st=row.status;
  if(st==="ready") return `<span class="badge ok">ready</span>`;
  if(st==="backoff") return `<span class="badge warn">backoff ${Math.round(row.retry_in_sec||0)}s</span>`;
  if(st==="disabled") return `<span class="badge">disabled</span>`;
  return `<span class="badge">${esc(st||"-")}</span>`;
}
function voiceAge(now, ts){
  if(!ts) return "-";
  const d=Math.max(0, Math.round(now-ts));
  if(d<60) return d+"s前";
  if(d<3600) return Math.floor(d/60)+"m前";
  return Math.floor(d/3600)+"h"+String(Math.floor((d%3600)/60)).padStart(2,"0")+"m前";
}
function renderVoiceChain(data){
  const now=data.now||(Date.now()/1000);
  const bo=data.backoff||{};
  $("#voice-backoff-desc").textContent=`${Math.round(bo.base_sec||0)}s × ${bo.mult||"-"} 乗 / 上限 ${Math.round(bo.max_sec||0)}s, probe ${bo.probe_timeout||"-"}s`;
  $("#voice-active").innerHTML=data.active_url?`<span class="badge ok">${esc(data.active_url)}</span> <span class="help">最終成功 ${esc(voiceAge(now,data.last_ok_at))}</span>`:"-";
  $("#voice-urls-source").textContent=data.urls_source||"-";
  $("#voice-state-file").textContent=data.state_file||"(未永続化)";
  const tb=$("#voice-table"); tb.innerHTML="";
  for(const r of (data.endpoints||[])){
    const tr=document.createElement("tr");
    const probe=r.probe?(r.probe.ok?` <span class="badge ok">probe ${r.probe.ms}ms</span>`:` <span class="badge bad">probe NG</span>`):"";
    const toggle=r.enabled?`<button class="btn danger" data-vact="disable" data-vurl="${esc(r.url)}" style="padding:4px 8px">無効化</button>`:`<button class="btn primary" data-vact="enable" data-vurl="${esc(r.url)}" style="padding:4px 8px">有効化</button>`;
    tr.innerHTML=`<td class="mono">${r.position}</td><td class="mono" style="font-size:12px">${esc(r.url)}${r.active?' <span class="badge ok">active</span>':''}</td><td>${voiceStatusBadge(r)}${probe}</td><td class="mono">${r.failures||0}</td><td class="mono">${r.ok_count||0}/${r.fail_count||0}</td><td class="mono">${r.last_ms!=null?r.last_ms:"-"} (${r.avg_ms!=null?r.avg_ms:"-"})</td><td class="mono" style="font-size:11px">${esc(voiceAge(now,r.last_ok_at))}</td><td class="mono" style="font-size:11px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.last_error||"")}">${esc(r.last_error?voiceAge(now,r.last_error_at)+" "+r.last_error:"-")}</td><td style="white-space:nowrap"><button class="btn" data-vact="probe" data-vurl="${esc(r.url)}" style="padding:4px 8px">疎通確認</button> <button class="btn" data-vact="reset" data-vurl="${esc(r.url)}" style="padding:4px 8px">backoff解除</button> ${toggle}</td>`;
    tb.appendChild(tr);
  }
  if(!(data.endpoints||[]).length) tb.innerHTML='<tr><td colspan="9" class="help">エンドポイント未設定</td></tr>';
  for(const btn of $$("[data-vact]")){
    btn.onclick=async()=>{
      const action=btn.getAttribute("data-vact"), url=btn.getAttribute("data-vurl");
      if(action==="disable" && !confirm(`${url} をチェーンから外しますか？（再び有効化するまで使われません）`)) return;
      btn.disabled=true;
      try{ await api("/api/voice/endpoints",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,url})}); toast(`${action}: ${url}`); await loadVoiceChain(); }
      catch(e){ toast(String(e)); btn.disabled=false; }
    };
  }
  const ev=(data.events||[]).slice(-20).reverse().map(e=>{
    const t=new Date((e.t||0)*1000).toLocaleTimeString();
    const extra=e.error?` ${e.error}`:(e.from?` from ${e.from}`:"")+(e.backoff_sec!=null?` backoff=${Math.round(e.backoff_sec)}s`:"")+(e.ms!=null?` ${e.ms}ms`:"");
    return `${t} ${e.event.toUpperCase().padEnd(8)} ${e.url||""}${extra}`;
  });
  const logTail=(data.log_tail||[]).slice(-15);
  $("#voice-log").textContent=(ev.length?ev.join("\n"):"(イベントなし)")+(logTail.length?"\n--- voicevox_chain.log ---\n"+logTail.join("\n"):"");
}
async function loadVoiceChain(probe=false){
  try{
    const data=await api("/api/voice/endpoints"+(probe?"?probe=1":""));
    renderVoiceChain(data);
  }catch(e){ toast(String(e)); }
}
function predictionStatusBadge(status){
  const s=String(status||"unknown").toUpperCase();
  const cls=(s==="ACTIVE"?"ok":(s==="LOCKED"?"warn":(s==="RESOLVED"?"ok":(s==="CANCELED"?"":"bad"))));
  return '<span class="badge '+cls+'">'+esc(s||"-")+'</span>';
}
function predictionOutcomeTitle(index, local, remote){
  const outcomes=(remote&&remote.outcomes)||[];
  const id=local&&local.outcome_ids&&local.outcome_ids[index];
  const found=outcomes.find(o=>o.id===id);
  return (found&&found.title)||PREDICTION_LABELS[index]||("index="+index);
}
function renderPredictionState(data){
  predictionState=data||{};
  const remote=data.remote||[];
  const local=data.local||null;
  const retry=data.retry||{};
  const worker=data.worker||{};
  const acc=data.accumulated||{};
  const enabled=$("#prediction-enabled");
  enabled.textContent=data.enabled?"有効":"無効";
  enabled.className="val "+(data.enabled?"badge ok":"badge warn");
  const configured=data.configured?"設定済み":"未設定";
  const http=data.http_code?("HTTP "+data.http_code):"HTTP -";
  $("#prediction-enabled-sub").textContent=configured+" / "+http+(data.explore_mode?" / explore mode":"");
  const active=remote.filter(p=>["ACTIVE","LOCKED"].includes(String(p.status||"").toUpperCase()));
  $("#prediction-remote-count").textContent=String(remote.length);
  $("#prediction-remote-sub").textContent=active.length?(active.length+"件が受付中/ロック中"):(remote.length?"受付中なし":"リモート履歴なし");
  $("#prediction-worker").textContent=worker.alive?"稼働中":"停止";
  $("#prediction-worker").className="val "+(worker.alive?"badge ok":"badge bad");
  $("#prediction-worker-sub").textContent=worker.pid?("pid "+worker.pid):"pid -";
  const count=Number(acc.count||0), max=(local&&local.game_num)?Number(local.game_num):0;
  $("#prediction-progress").textContent=local?("best "+Number(local.best_outcome||0)):"待機";
  $("#prediction-progress-sub").textContent=count?("蓄積 "+count+"ゲーム"+(max?" / 開始game "+max:"")):"蓄積状態なし";
  const msg=$("#prediction-status-msg");
  if(data.error) { msg.textContent="状態取得: "+data.error; msg.style.color="var(--bad)"; }
  else {
    const c=retry.create&&retry.create.active?("create retry "+retry.create.remaining+"s"):"";
    const r=retry.resolve&&retry.resolve.active?("resolve retry "+retry.resolve.remaining+"s"):"";
    msg.textContent=[c,r].filter(Boolean).join(" / ")||"状態を確認しました。";
    msg.style.color="";
  }
  const kv=$("#prediction-local-state"); kv.innerHTML="";
  const localRows=local?
    [["prediction_id",local.prediction_id],["game_num",local.game_num==null?"-":local.game_num],["created_at",fmtTime(local.created_at)],["best_outcome",PREDICTION_LABELS[Number(local.best_outcome||0)]||String(local.best_outcome||0)],["russia_created",String(!!local.russia_created)],["recovered",String(!!local.recovered)]]:
    [["state","なし"]];
  for(const [k,v] of localRows){ const dt=document.createElement("dt"); dt.textContent=k; const dd=document.createElement("dd"); dd.textContent=String(v==null?"-":v); dd.className="mono"; kv.appendChild(dt); kv.appendChild(dd); }
  const tb=$("#prediction-remote-table"); tb.innerHTML="";
  if(remote.length===0){ tb.innerHTML='<tr><td colspan="6" class="help">リモート予想なし</td></tr>'; }
  else for(const p of remote){
    const tr=document.createElement("tr");
    const points=p.channel_points_used==null?"-":p.channel_points_used;
    const users=p.users==null?"-":p.users;
    tr.innerHTML='<td>'+predictionStatusBadge(p.status)+'</td><td>'+esc(p.title||"-")+'</td><td class="mono">'+esc(p.created_at||"-")+'</td><td>'+esc(p.prediction_window==null?"-":String(p.prediction_window)+"秒")+'</td><td>'+esc(String(points))+" / "+esc(String(users))+'</td><td class="mono" style="font-size:11px">'+esc(p.id)+'</td>';
    tb.appendChild(tr);
  }
  const remoteActive=active.find(p=>local&&p.id===local.prediction_id)||active[0]||null;
  const actions=$("#prediction-actions"); actions.innerHTML="";
  if(local){
    for(let i=0;i<PREDICTION_LABELS.length;i++){
      const b=document.createElement("button"); b.className="btn"; b.textContent=i+": "+predictionOutcomeTitle(i,local,remoteActive); b.disabled=READ_ONLY;
      b.onclick=()=>runPredictionAction("resolve",{outcome_index:i}); actions.appendChild(b);
    }
    const cancel=document.createElement("button"); cancel.className="btn danger"; cancel.textContent="キャンセル"; cancel.disabled=READ_ONLY; cancel.onclick=()=>runPredictionAction("cancel"); actions.appendChild(cancel);
  } else {
    const hint=document.createElement("span"); hint.className="help"; hint.textContent=active.length?"ローカル状態がないため、先に同期してください。":"操作対象のローカル予想はありません。"; actions.appendChild(hint);
  }
  applyReadOnly();
}
async function loadPredictions(){
  try{ const data=await api("/api/predictions"); renderPredictionState(data); }
  catch(e){ toast(String(e),5000); const msg=$("#prediction-status-msg"); if(msg) msg.textContent=String(e); }
}
async function runPredictionAction(action, payload={}){
  if(READ_ONLY){ toast("read-only モードのため予想を操作できません"); return; }
  const labels={create:"予想を作成",sync:"リモート予想を同期",cancel:"予想をキャンセル",resolve:"予想を解決"};
  if(action==="create" && !confirm("Twitchに新しい予想を作成しますか？")) return;
  if(action==="cancel" && !confirm("現在の予想をキャンセルしますか？")) return;
  if(action==="resolve"){
    const i=Number(payload.outcome_index);
    if(!confirm("「"+(PREDICTION_LABELS[i]||i)+"」で予想を解決しますか？")) return;
  }
  try{
    const res=await api("/api/predictions/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(Object.assign({action},payload))});
    toast((labels[action]||action)+"を実行しました");
    const msg=$(action==="create"?"#prediction-create-msg":"#prediction-actions-msg"); if(msg) msg.textContent=res.message||"完了";
    await loadPredictions();
  }catch(e){ toast(String(e),5000); const msg=$(action==="create"?"#prediction-create-msg":"#prediction-actions-msg"); if(msg) msg.textContent=String(e); }
}
async function enqueueAudio(){
  if(READ_ONLY){ toast("read-only モードのため enqueue できません"); return; }
  const text=$("#audio-text").value;
  const source=$("#audio-source").value.trim()||"webui_manual";
  const speaker=$("#audio-speaker").value.trim();
  if(!text.trim()){ toast("text を入力してください"); return; }
  try{
    const res=await api("/api/audio/enqueue",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text, source, speaker})});
    if(res.dedup){ toast("dedup: 同一テキストが120秒以内にenqueue済みのためスキップ",4000); }
    else { toast("読み上げキューに追加しました"); $("#audio-text").value=""; $("#audio-speaker").value=""; }
    $("#audio-enqueue-msg").textContent=res.filename?`enqueued: ${res.filename}`:(res.dedup?"dedup スキップ":"");
    updateAudioTextCount();
    await loadAudioQueue();
  }catch(e){ toast(String(e),5000); $("#audio-enqueue-msg").textContent=String(e); }
}
function updateAudioTextCount(){
  const el=$("#audio-text");
  const cnt=$("#audio-text-count");
  if(el && cnt) cnt.textContent=`${el.value.length}/1000`;
}
document.addEventListener("DOMContentLoaded",()=>{
  // ログインフォーム: token 入力→sessionStorage 保存 (URL query token は廃止: issue #41)
  const loginInput = $("#login-token-input");
  const loginBtn = $("#login-submit");
  const submitLogin = ()=>{
    const val = (loginInput.value||"").trim();
    if(!val) return;
    sessionStorage.setItem("webui_token", val);
    hideLogin();
    location.reload();
  };
  if(loginBtn) loginBtn.onclick = submitLogin;
  if(loginInput) loginInput.addEventListener("keydown", (e)=>{ if(e.key==="Enter") submitLogin(); });
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
    if(tab==="stream") { loadStream(); loadWorkersControl(); if(streamTimer) clearInterval(streamTimer); streamTimer=setInterval(()=>{ loadStream(); loadWorkersControl(); },10000); }
    else { if(streamTimer) { clearInterval(streamTimer); streamTimer=null; } }
    if(tab==="overlay") { loadOverlayEvents(); loadWorkBanner(); loadTop(); loadPreview(); }
    if(tab==="audio") { loadAudioQueue(); loadVoiceChain(); if(audioTimer) clearInterval(audioTimer); audioTimer=setInterval(()=>{ loadVoiceChain(); },10000); }
    else { if(audioTimer) { clearInterval(audioTimer); audioTimer=null; } }
    if(tab==="predictions") loadPredictions();
    if(tab==="prompts") loadPrompts();
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
  const statsGroupCb = document.getElementById("stats-group-base");
  if(statsGroupCb) statsGroupCb.onchange=()=>{
    if(_lastStatsData){
      const gb = statsGroupCb.checked;
      renderStatsLabels(_lastStatsData, gb);
      renderStatsLabelAgents(_lastStatsData, gb);
    } else {
      loadStats();
    }
  };
  const statsSearch=document.getElementById("stats-search");
  if(statsSearch){
    let t; statsSearch.oninput=()=>{
      clearTimeout(t); t=setTimeout(()=>{
        if(_lastStatsData){
          const gb=document.getElementById("stats-group-base")?.checked ?? true;
          renderStatsLabels(_lastStatsData, gb);
          renderStatsLabelAgents(_lastStatsData, gb);
        }
      },200);
    };
  }
  const statsExpandAll=document.getElementById("stats-expand-all");
  if(statsExpandAll) statsExpandAll.onclick=()=>{
    $$("#stats-label-agents .accordion-item").forEach(el=>{ el.classList.add("open"); const b=el.querySelector(".accordion-body"); if(b) b.style.display="block"; });
  };
  const statsCollapseAll=document.getElementById("stats-collapse-all");
  if(statsCollapseAll) statsCollapseAll.onclick=()=>{
    $$("#stats-label-agents .accordion-item").forEach((el,idx)=>{ if(idx>=3){ el.classList.remove("open"); const b=el.querySelector(".accordion-body"); if(b) b.style.display="none"; } });
  };
  $("#health-refresh").onclick=()=>loadHealth();
  $("#do-reload").onclick=async()=>{
    try{ const r=await api("/api/reload",{method:"POST"}); toast(JSON.stringify(r.results)); await loadHealth(); }catch(e){ toast(String(e)); }
  };
  // status
  const sRefresh=document.getElementById("status-refresh");
  if(sRefresh) sRefresh.onclick=()=>loadStatus();
  // stream
  const stStart=document.getElementById("stream-start");
  if(stStart) stStart.onclick=()=>streamAction("start");
  const stStop=document.getElementById("stream-stop");
  if(stStop) stStop.onclick=()=>streamAction("stop");
  const stRefresh=document.getElementById("stream-refresh");
  if(stRefresh) stRefresh.onclick=()=>loadStream();
  const chStart=document.getElementById("chat-start");
  if(chStart) chStart.onclick=()=>chatAction("start");
  const chStop=document.getElementById("chat-stop");
  if(chStop) chStop.onclick=()=>chatAction("stop");
  const stSave=document.getElementById("stream-settings-save");
  if(stSave) stSave.onclick=()=>saveStreamSettings();
  const stReload=document.getElementById("stream-settings-reload");
  if(stReload) stReload.onclick=()=>loadConfig().catch(e=>toast(String(e)));
  const ccToggle=document.getElementById("stream-cc");
  if(ccToggle) ccToggle.onchange=(e)=>{ const lb=document.getElementById("stream-cc-label"); if(lb) lb.textContent=e.target.checked?"CC on":"CC off"; };
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
  // audio handlers
  const aRefresh=document.getElementById("audio-queue-refresh");
  if(aRefresh) aRefresh.onclick=()=>loadAudioQueue();
  const aClear=document.getElementById("audio-queue-clear");
  const vRefresh=$("#voice-refresh"), vProbe=$("#voice-probe-all"), vReset=$("#voice-reset-all");
  if(vRefresh) vRefresh.onclick=()=>loadVoiceChain();
  if(vProbe) vProbe.onclick=async()=>{ vProbe.disabled=true; try{ await loadVoiceChain(true); toast("疎通確認を記録しました"); } finally { vProbe.disabled=false; } };
  if(vReset) vReset.onclick=async()=>{ if(!confirm("全エンドポイントの backoff をリセットしますか？")) return; try{ await api("/api/voice/endpoints",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"reset"})}); toast("backoff 全リセット"); await loadVoiceChain(); }catch(e){ toast(String(e)); } };
  if(aClear) aClear.onclick=async()=>{ if(!confirm("読み上げキューを全クリアしますか？")) return; try{ await api("/api/audio/queue",{method:"DELETE"}); toast("キュー全クリア"); await loadAudioQueue(); }catch(e){ toast(String(e)); } };
  const aEnqueue=document.getElementById("audio-enqueue");
  if(aEnqueue) aEnqueue.onclick=()=>enqueueAudio();
  const aEnqClear=document.getElementById("audio-enqueue-clear");
  if(aEnqClear) aEnqClear.onclick=()=>{ $("#audio-text").value=""; $("#audio-speaker").value=""; $("#audio-enqueue-msg").textContent=""; updateAudioTextCount(); };
  const aText=document.getElementById("audio-text");
  if(aText) aText.addEventListener("input", updateAudioTextCount);
  for(const btn of $$("[data-audio-preset]")){
    btn.onclick=()=>{ $("#audio-text").value=btn.getAttribute("data-audio-preset"); updateAudioTextCount(); };
  }
  // predictions handlers
  const predRefresh=document.getElementById("prediction-refresh");
  if(predRefresh) predRefresh.onclick=()=>loadPredictions();
  const predSync=document.getElementById("prediction-sync");
  if(predSync) predSync.onclick=()=>runPredictionAction("sync");
  const predCreate=document.getElementById("prediction-create");
  if(predCreate) predCreate.onclick=()=>runPredictionAction("create",{game_num:Number(document.getElementById("prediction-game-num").value||0)});
  // prompts handlers
  const prRefresh=document.getElementById("prompts-refresh");
  if(prRefresh) prRefresh.onclick=()=>loadPrompts();
  const prSave=document.getElementById("prompts-save");
  if(prSave) prSave.onclick=()=>savePrompt();
  const prReload=document.getElementById("prompts-reload");
  if(prReload) prReload.onclick=()=>{ if(promptsState.currentId) loadPrompt(promptsState.currentId); };
  const prContent=document.getElementById("prompts-content");
  if(prContent) prContent.addEventListener("input", updatePromptsPreview);
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
    # issue #42: CSRF token 署名用の乱数 secret。run_webui() が BoundHandler ごとに
    # 起動時生成する (プロセス再起動で失効)。テストで直接 _Handler を使う場合は
    # _csrf_secret() が遅延生成してクラス属性へキャッシュする。
    csrf_secret: bytes | None = None

    def _runtime_backend(self) -> RuntimeBackend:
        """read-only worker/status route が使う RuntimeBackend を返す (issue #43)。

        現状 docich webui は soviet_now 専用ダッシュボードなので常に
        `SorenBackend` を返すが、route 側は `RuntimeBackend` interface だけを
        見るため、将来 soren 以外の game/runtime を追加しても route 変更は不要。
        """
        return SorenBackend(self.soren_root)

    def _set_cors(self):
        # issue #42: wildcard (*) と credentials を両立させない。Authorization
        # ヘッダは fetch の `credentials` モードに関係なく常に送られるため
        # ACAO:* でも読み取り自体は可能だが、allowlist 外の Origin には一切
        # CORS ヘッダを返さないことで「任意オリジンから読めてしまう」経路を塞ぐ。
        # Access-Control-Allow-Credentials は使わない (cookie 認証をしていないため
        # 不要。ACAO を specific origin にした上で追加すると危険なので送らない)。
        if not self.g.webui.allow_cors:
            return
        origin = self.headers.get("Origin", "")
        if origin and self._is_allowed_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET,PUT,DELETE,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-WebUI-Token, X-CSRF-Token, X-Docich-Confirm")
        self.send_header("Access-Control-Max-Age", "86400")

    def _supplied_token(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            val = auth[len("Bearer ") :].strip()
            if val:
                return val
        x = self.headers.get("X-WebUI-Token", "")
        return x.strip() if x else ""

    def _check_auth(self) -> bool:
        op_token = _effective_token(self.g)
        ro_token = _effective_read_only_token(self.g)
        if not op_token and not ro_token:
            return True
        supplied = self._supplied_token()
        if not supplied:
            return False
        # check header (timing-safe 比較。query token は受理しない: issue #41)
        if op_token and _tokens_match(supplied, op_token):
            return True
        if ro_token and _tokens_match(supplied, ro_token):
            return True
        return False

    def _identity(self) -> str:
        """_check_auth() が True である前提で呼ぶ。"operator" (読み書き可) または
        "viewer" (issue #42: read_only_token で認証。全 mutation 拒否)。
        両 token とも未設定 (既定) の場合は従来どおり "operator" として扱う。"""
        op_token = _effective_token(self.g)
        ro_token = _effective_read_only_token(self.g)
        supplied = self._supplied_token()
        if ro_token and supplied and not (op_token and _tokens_match(supplied, op_token)):
            if _tokens_match(supplied, ro_token):
                return "viewer"
        return "operator"

    # --- issue #42: Host/Origin allowlist ---------------------------------

    def _listening_port(self) -> int:
        try:
            return int(self.server.server_address[1])
        except Exception:
            return int(self.g.webui.port or 8787)

    def _host_allowlist(self) -> set[str]:
        port = self._listening_port()
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        bind = (self.g.webui.bind or "").strip()
        if bind and bind not in ("0.0.0.0", "::"):
            hosts.add(f"{bind}:{port}")
        for origin in self.g.webui.allowed_origins or []:
            parsed = _parse_origin_str(origin)
            if parsed:
                hosts.add(parsed[1])
        return hosts

    def _origin_allowlist(self) -> set[str]:
        port = self._listening_port()
        origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://[::1]:{port}"}
        bind = (self.g.webui.bind or "").strip()
        if bind and bind not in ("0.0.0.0", "::"):
            origins.add(f"http://{bind}:{port}")
        for origin in self.g.webui.allowed_origins or []:
            parsed = _parse_origin_str(origin)
            if parsed:
                origins.add(f"{parsed[0]}://{parsed[1]}")
        return origins

    def _is_allowed_origin(self, origin: str) -> bool:
        return origin.strip().lower().rstrip("/") in {o.lower() for o in self._origin_allowlist()}

    def _check_host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        return host in {h.lower() for h in self._host_allowlist()}

    def _check_content_type_ok(self) -> bool:
        """mutation の Content-Type を検証する (issue #42)。HTML <form> は
        application/json を送れない (x-www-form-urlencoded/multipart/text-plain
        しか送れない) ため、これを要求するだけで classic な form-based CSRF を防げる。
        Content-Length が不正/未指定な場合はここでは判定せず _read_body() に委ねる。"""
        raw_len = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(raw_len)
        except ValueError:
            return True
        if length <= 0:
            return True
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return ctype == "application/json"

    def _guard_mutation(self) -> int:
        """PUT/POST/DELETE 共通の防御ゲート (issue #42): Host allowlist → Origin
        allowlist (存在する場合のみ) → Content-Type → 認証 → CSRF token → read-only
        (server 全体 or viewer identity) の順に検証する。拒否時は応答送信済みで
        その status code を返す。通過なら 0。全ての拒否/許可を secret/body を含めない
        authorization audit event として記録する。"""
        method = self.command
        path = urllib.parse.urlparse(self.path).path
        if not self._check_host_allowed():
            self._send_error_json(400, "invalid_host", "許可されていない Host ヘッダです")
            _log_authz_event(self.soren_root, method, path, "unauthenticated", "deny", "invalid_host")
            return 400
        origin = self.headers.get("Origin", "")
        if origin and not self._is_allowed_origin(origin):
            self._send_error_json(403, "invalid_origin", "許可されていない Origin です")
            _log_authz_event(self.soren_root, method, path, "unauthenticated", "deny", "invalid_origin")
            return 403
        if not self._check_content_type_ok():
            self._send_error_json(415, "invalid_content_type", "Content-Type は application/json である必要があります")
            _log_authz_event(self.soren_root, method, path, "unauthenticated", "deny", "invalid_content_type")
            return 415
        if not self._check_auth():
            self._send_error_json(401, "unauthorized")
            _log_authz_event(self.soren_root, method, path, "unauthenticated", "deny", "unauthorized")
            return 401
        ok, reason = self._verify_csrf_token(self.headers.get("X-CSRF-Token", ""))
        if not ok:
            self._send_error_json(403, reason, "CSRF token が必要、または不正/期限切れです (GET /api/csrf で再取得してください)")
            _log_authz_event(self.soren_root, method, path, self._identity(), "deny", reason)
            return 403
        identity = self._identity()
        if self.read_only or identity == "viewer":
            self._send_error_json(403, "read_only", "read-only mode" if self.read_only else "read-only identity (viewer token)")
            _log_authz_event(self.soren_root, method, path, identity, "deny", "read_only")
            return 403
        _log_authz_event(self.soren_root, method, path, identity, "allow")
        return 0

    def _csrf_secret(self) -> bytes:
        secret = type(self).csrf_secret
        if not secret:
            secret = secrets.token_bytes(32)
            type(self).csrf_secret = secret
        return secret

    def _make_csrf_token(self) -> str:
        return _make_csrf_token(self._csrf_secret())

    def _verify_csrf_token(self, token: str) -> tuple[bool, str]:
        return _verify_csrf_token(self._csrf_secret(), token)

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
            if path == "/":
                # トップページは静的な SPA シェル (機密情報を含まない) であり、
                # token 入力フォームを表示する必要があるため認証不要で常に返す。
                # query token は廃止済み (issue #41): ログイン手順はフォーム入力→
                # sessionStorage 保存のみで、URL 経由の token 受け渡しは行わない。
                body = INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._set_cors()
                self.end_headers()
                self.wfile.write(body)
                status = 200
                return
            if not self._check_auth():
                status = 401
                self._send_error_json(401, "unauthorized", "token required")
                return
            if path == "/api/health":
                status = self._handle_health()
            elif path == "/api/csrf":
                status = self._handle_get_csrf()
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
            elif path == "/api/stream":
                status = self._handle_get_stream()
            elif path == "/api/voice/endpoints":
                status = self._handle_get_voice_endpoints(query.get("probe", ["0"])[0] in ("1", "true"))
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
            elif path == "/api/prompts":
                status = self._handle_list_prompts()
            elif path.startswith("/api/prompts/"):
                prompt_id = urllib.parse.unquote(path[len("/api/prompts/") :])
                status = self._handle_get_prompt(prompt_id)
            elif path == "/api/audio/queue":
                status = self._handle_get_audio_queue()
            elif path == "/api/predictions":
                status = self._handle_get_predictions()
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
            guard = self._guard_mutation()
            if guard:
                status = guard
                return
            if parsed.path == "/api/config":
                status = self._handle_put_config()
            elif parsed.path == "/api/overlay/work_banner":
                status = self._handle_put_work_banner()
            elif parsed.path == "/api/overlay/top":
                status = self._handle_put_top()
            elif parsed.path == "/api/overlay/events":
                status = self._handle_put_overlay_events()
            elif parsed.path.startswith("/api/prompts/"):
                prompt_id = urllib.parse.unquote(parsed.path[len("/api/prompts/") :])
                status = self._handle_put_prompt(prompt_id)
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
            guard = self._guard_mutation()
            if guard:
                status = guard
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
            elif parsed.path.startswith("/api/audio/queue/"):
                part = parsed.path[len("/api/audio/queue/") :]
                # decode file name
                fname = urllib.parse.unquote(part)
                if fname == "clear":
                    status = self._handle_clear_audio_queue()
                else:
                    status = self._handle_delete_audio_queue_item(fname)
            elif parsed.path == "/api/audio/queue":
                status = self._handle_clear_audio_queue()
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
            guard = self._guard_mutation()
            if guard:
                status = guard
                return
            if parsed.path == "/api/backoffs/clear":
                status = self._handle_clear_all_backoffs()
            elif parsed.path == "/api/reload":
                status = self._handle_reload()
            elif parsed.path == "/api/stream":
                status = self._handle_post_stream()
            elif parsed.path == "/api/voice/endpoints":
                status = self._handle_post_voice_endpoints()
            elif parsed.path == "/api/chat":
                status = self._handle_post_chat_toggle()
            elif parsed.path == "/api/workers":
                status = self._handle_post_workers()
            elif parsed.path == "/api/overlay/events":
                status = self._handle_post_overlay_event()
            elif parsed.path == "/api/overlay/events/bulk":
                status = self._handle_post_overlay_events_bulk()
            elif parsed.path == "/api/overlay/preview/refresh":
                status = self._handle_reload()  # alias
            elif parsed.path == "/api/audio/enqueue":
                status = self._handle_post_audio_enqueue()
            elif parsed.path == "/api/audio/queue/clear":
                status = self._handle_clear_audio_queue()
            elif parsed.path == "/api/predictions/action":
                status = self._handle_post_prediction_action()
            else:
                status = 404
                self._send_error_json(404, "not_found")
        finally:
            latency = int((time.monotonic() - t0) * 1000)
            _log_request(self.soren_root, "POST", parsed.path, status, latency)

    # ---- handlers ----

    def _handle_get_csrf(self) -> int:
        """issue #42: mutation 用 CSRF token を発行する (認証は _check_auth と同じ)。
        ブラウザからの直接 (form) 送信では取得不能なうえ Origin allowlist 外の
        cross-origin fetch はレスポンス本文を読めない (allow_cors=false が既定) ため、
        token 値そのものは漏れない。"""
        token = self._make_csrf_token()
        exp = int(token.split(".", 1)[0])
        self._send_json(200, {"csrf_token": token, "expires_at": exp})
        return 200

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
        # issue #42: config 変更は dangerous action として再確認 (confirm:true) を要求する。
        if not _is_confirmed(data, self.headers):
            self._send_error_json(428, "confirmation_required", "設定変更には confirm:true が必要です")
            return 428
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
        recent_errors: list[dict[str, Any]] = []
        # --- new: chain / stage breakdown ---
        by_label: dict[str, dict[str, Any]] = {}
        by_label_agent: dict[str, dict[str, dict[str, int]]] = {}
        by_base_label: dict[str, dict[str, Any]] = {}
        by_base_label_agent: dict[str, dict[str, dict[str, int]]] = {}
        pending_by_label: dict[str, int] = {}
        depth_winner_by_label: dict[str, dict[int, int]] = {}
        depth_failed_by_label: dict[str, dict[int, int]] = {}
        pending_by_base: dict[str, int] = {}
        depth_winner_by_base: dict[str, dict[int, int]] = {}
        depth_failed_by_base: dict[str, dict[int, int]] = {}

        def _ensure_label_entry(lbl: str) -> None:
            if lbl not in by_label:
                by_label[lbl] = {"attempt": 0, "ok": 0, "fail": 0, "winner": 0, "all_failed": 0, "chain_hint": _label_chain_hint(lbl)}
            if lbl not in by_label_agent:
                by_label_agent[lbl] = {}
            if lbl not in pending_by_label:
                pending_by_label[lbl] = 0
                depth_winner_by_label[lbl] = {}
                depth_failed_by_label[lbl] = {}

        def _ensure_base_entry(base: str) -> None:
            if base not in by_base_label:
                by_base_label[base] = {"attempt": 0, "ok": 0, "fail": 0, "winner": 0, "all_failed": 0, "chain_hint": _label_chain_hint(base)}
            if base not in by_base_label_agent:
                by_base_label_agent[base] = {}
            if base not in pending_by_base:
                pending_by_base[base] = 0
                depth_winner_by_base[base] = {}
                depth_failed_by_base[base] = {}

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
                    lbl = str(rec.get("label", "") or "")
                    lbl_display = lbl if lbl else "(empty)"
                    base = _normalize_stats_label(lbl) if lbl else "(empty)"
                    _ensure_label_entry(lbl_display)
                    _ensure_base_entry(base)
                    if ev == "attempt":
                        attempt += 1
                        by_label[lbl_display]["attempt"] += 1
                        by_base_label[base]["attempt"] += 1
                        pending_by_label[lbl_display] = pending_by_label.get(lbl_display, 0) + 1
                        pending_by_base[base] = pending_by_base.get(base, 0) + 1
                        if ag:
                            by_agent.setdefault(ag, {"attempt": 0, "winner": 0, "fail": 0})
                            by_agent[ag]["attempt"] += 1
                            by_label_agent[lbl_display].setdefault(ag, {"attempt": 0, "winner": 0, "ok": 0, "fail": 0})
                            by_label_agent[lbl_display][ag]["attempt"] += 1
                            by_base_label_agent[base].setdefault(ag, {"attempt": 0, "winner": 0, "ok": 0, "fail": 0})
                            by_base_label_agent[base][ag]["attempt"] += 1
                    elif ev == "ok":
                        ok += 1
                        by_label[lbl_display]["ok"] += 1
                        by_base_label[base]["ok"] += 1
                        if ag and ag in by_label_agent.get(lbl_display, {}):
                            by_label_agent[lbl_display][ag]["ok"] += 1
                        if ag and ag in by_base_label_agent.get(base, {}):
                            by_base_label_agent[base][ag]["ok"] += 1
                    elif ev == "fail":
                        fail += 1
                        by_label[lbl_display]["fail"] += 1
                        by_base_label[base]["fail"] += 1
                        if ag:
                            by_agent.setdefault(ag, {"attempt": 0, "winner": 0, "fail": 0})
                            by_agent[ag]["fail"] += 1
                            by_label_agent[lbl_display].setdefault(ag, {"attempt": 0, "winner": 0, "ok": 0, "fail": 0})
                            by_base_label_agent[base].setdefault(ag, {"attempt": 0, "winner": 0, "ok": 0, "fail": 0})
                        if ag and ag in by_label_agent.get(lbl_display, {}):
                            by_label_agent[lbl_display][ag]["fail"] += 1
                        if ag and ag in by_base_label_agent.get(base, {}):
                            by_base_label_agent[base][ag]["fail"] += 1
                        err_text = str(rec.get("error", "") or "")
                        if err_text:
                            recent_errors.append(
                                {
                                    "ts": rec.get("ts", 0),
                                    "day": day,
                                    "label": lbl_display,
                                    "agent": ag or str(rec.get("resolved_model", "") or ""),
                                    "error": err_text[:200],
                                }
                            )
                    elif ev == "winner":
                        winner += 1
                        by_label[lbl_display]["winner"] += 1
                        by_base_label[base]["winner"] += 1
                        if ag:
                            by_agent.setdefault(ag, {"attempt": 0, "winner": 0, "fail": 0})
                            by_agent[ag]["winner"] += 1
                            by_label_agent[lbl_display].setdefault(ag, {"attempt": 0, "winner": 0, "ok": 0, "fail": 0})
                            by_label_agent[lbl_display][ag]["winner"] += 1
                            by_base_label_agent[base].setdefault(ag, {"attempt": 0, "winner": 0, "ok": 0, "fail": 0})
                            by_base_label_agent[base][ag]["winner"] += 1
                        d = pending_by_label.get(lbl_display, 0)
                        if d <= 0:
                            d = 1
                        depth_winner_by_label[lbl_display][d] = depth_winner_by_label[lbl_display].get(d, 0) + 1
                        db = pending_by_base.get(base, 0)
                        if db <= 0:
                            db = 1
                        depth_winner_by_base[base][db] = depth_winner_by_base[base].get(db, 0) + 1
                        pending_by_label[lbl_display] = 0
                        pending_by_base[base] = 0
                    elif ev == "all_failed":
                        all_failed += 1
                        by_label[lbl_display]["all_failed"] += 1
                        by_base_label[base]["all_failed"] += 1
                        d = pending_by_label.get(lbl_display, 0)
                        if d <= 0:
                            d = 1
                        depth_failed_by_label[lbl_display][d] = depth_failed_by_label[lbl_display].get(d, 0) + 1
                        db = pending_by_base.get(base, 0)
                        if db <= 0:
                            db = 1
                        depth_failed_by_base[base][db] = depth_failed_by_base[base].get(db, 0) + 1
                        pending_by_label[lbl_display] = 0
                        pending_by_base[base] = 0
            except Exception:
                continue
            day_stats.append(
                {"day": day, "attempt": attempt, "ok": ok, "fail": fail, "winner": winner, "all_failed": all_failed}
            )
        by_label_depth: dict[str, dict[str, Any]] = {}
        for lbl in set(list(by_label.keys()) + list(depth_winner_by_label.keys()) + list(depth_failed_by_label.keys())):
            wd = depth_winner_by_label.get(lbl, {})
            fd = depth_failed_by_label.get(lbl, {})
            total_gen = sum(wd.values()) + sum(fd.values())
            avg_w = (sum(k * v for k, v in wd.items()) / sum(wd.values())) if wd else 0
            avg_f = (sum(k * v for k, v in fd.items()) / sum(fd.values())) if fd else 0
            by_label_depth[lbl] = {
                "winner_depth": {str(k): v for k, v in sorted(wd.items())},
                "failed_depth": {str(k): v for k, v in sorted(fd.items())},
                "total_generations": total_gen,
                "avg_winner_depth": round(avg_w, 2) if wd else 0,
                "avg_failed_depth": round(avg_f, 2) if fd else 0,
                "pending": pending_by_label.get(lbl, 0),
            }
        by_base_depth: dict[str, dict[str, Any]] = {}
        for base in set(list(by_base_label.keys()) + list(depth_winner_by_base.keys()) + list(depth_failed_by_base.keys())):
            wd = depth_winner_by_base.get(base, {})
            fd = depth_failed_by_base.get(base, {})
            total_gen = sum(wd.values()) + sum(fd.values())
            avg_w = (sum(k * v for k, v in wd.items()) / sum(wd.values())) if wd else 0
            avg_f = (sum(k * v for k, v in fd.items()) / sum(fd.values())) if fd else 0
            by_base_depth[base] = {
                "winner_depth": {str(k): v for k, v in sorted(wd.items())},
                "failed_depth": {str(k): v for k, v in sorted(fd.items())},
                "total_generations": total_gen,
                "avg_winner_depth": round(avg_w, 2) if wd else 0,
                "avg_failed_depth": round(avg_f, 2) if fd else 0,
                "pending": pending_by_base.get(base, 0),
            }
        recent_errors.sort(key=lambda r: r.get("ts", 0))
        recent_errors = recent_errors[-40:]
        recent_errors.reverse()
        self._send_json(
            200,
            {
                "days": day_stats,
                "by_agent": by_agent,
                "recent_errors": recent_errors,
                "by_label": by_label,
                "by_label_agent": by_label_agent,
                "by_label_depth": by_label_depth,
                "by_base_label": by_base_label,
                "by_base_label_agent": by_base_label_agent,
                "by_base_depth": by_base_depth,
                "stats_dir": str(sdir),
            },
        )
        return 200

    def _handle_get_game_state(self) -> int:
        # issue #43: RuntimeBackend.get_status() 経由 (Soren固有のpath/process名は
        # runtime_backend.SorenBackend 側に移設済み)。ロールバック用の legacy flag
        # のときだけ旧経路 (未配置チェックを挟まず直接読む、リファクタ前と同一の
        # 挙動) を使う。
        if _legacy_runtime_reads_enabled():
            self._send_json(200, _read_game_status(self.soren_root))
            return 200
        backend = self._runtime_backend()
        if not backend.capabilities().get(CAPABILITY_GET_STATUS):
            self._send_json(
                200,
                {
                    "exists": False,
                    "path": None,
                    "mtime": 0,
                    "data": None,
                    "state": "",
                    "score": None,
                    "unsupported": True,
                    "capability": CAPABILITY_GET_STATUS,
                    "reason": "soviet_now is not deployed at this soren_root",
                },
            )
            return 200
        self._send_json(200, backend.get_status())
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
        # issue #43: RuntimeBackend.list_workers() 経由 (Soren固有のworker名/pid
        # ファイルpathは runtime_backend.SorenBackend 側に移設済み)。ロールバック
        # 用の legacy flag のときだけ旧経路 (未配置チェックを挟まず直接読む、
        # リファクタ前と同一の挙動) を使う。
        if _legacy_runtime_reads_enabled():
            workers = _get_workers_status(self.soren_root)
            self._send_json(200, {"workers": workers, "now": int(time.time())})
            return 200
        backend = self._runtime_backend()
        if not backend.capabilities().get(CAPABILITY_LIST_WORKERS):
            self._send_json(
                200,
                {
                    "workers": [],
                    "now": int(time.time()),
                    "unsupported": True,
                    "capability": CAPABILITY_LIST_WORKERS,
                    "reason": "soviet_now is not deployed at this soren_root",
                },
            )
            return 200
        self._send_json(200, {"workers": backend.list_workers(), "now": int(time.time())})
        return 200

    def _handle_get_predictions(self) -> int:
        try:
            snapshot = _prediction_status_snapshot(self.soren_root)
        except Exception as exc:
            self._send_error_json(500, "prediction_status_failed", str(exc)[:300])
            return 500
        self._send_json(200, snapshot)
        return 200

    def _handle_post_prediction_action(self) -> int:
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
        action = str(data.get("action", "")).strip().lower()
        if action not in PREDICTION_ACTIONS:
            self._send_error_json(400, "invalid_action", "action must be create, resolve, cancel, or sync")
            return 400
        args: list[str]
        if action == "create":
            raw_game_num = data.get("game_num", 0)
            try:
                game_num = int(raw_game_num)
            except Exception:
                self._send_error_json(400, "validation_error", "game_num must be an integer", field="game_num")
                return 400
            if game_num < 0 or game_num > 1_000_000_000:
                self._send_error_json(400, "validation_error", "game_num must be between 0 and 1000000000", field="game_num")
                return 400
            args = ["create", str(game_num)]
        elif action == "resolve":
            raw_index = data.get("outcome_index")
            try:
                outcome_index = int(raw_index)
            except Exception:
                self._send_error_json(400, "validation_error", "outcome_index must be an integer", field="outcome_index")
                return 400
            if outcome_index not in range(len(PREDICTION_OUTCOME_LABELS)):
                self._send_error_json(400, "validation_error", "outcome_index must be 0..3", field="outcome_index")
                return 400
            args = ["resolve", str(outcome_index)]
        elif action == "cancel":
            args = ["cancel"]
        else:
            args = ["sync"]
        result = _run_prediction_command(self.soren_root, args)
        if not result.get("available"):
            self._send_error_json(503, "prediction_unavailable", str(result.get("message", "prediction script unavailable"))[:300])
            return 503
        if not result.get("ok"):
            self._send_error_json(502, "prediction_command_failed", str(result.get("message", "prediction command failed"))[:300])
            return 502
        if action in PREDICTION_ACTIONS and result.get("result") is None:
            self._send_error_json(409, "prediction_not_operable", str(result.get("message", "prediction command made no change"))[:300])
            return 409
        response: dict[str, Any] = {"ok": True, "action": action, "result": result.get("result")}
        if result.get("message"):
            response["message"] = str(result["message"])[:300]
        self._send_json(200, response)
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

    def _handle_get_voice_endpoints(self, probe: bool = False) -> int:
        try:
            report = _get_voice_endpoints(self.soren_root, probe=probe)
        except Exception as exc:
            self._send_error_json(500, "voice_endpoints_failed", str(exc)[:300])
            return 500
        self._send_json(200, report)
        return 200

    def _handle_post_voice_endpoints(self) -> int:
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
        try:
            result = _voice_endpoint_action(self.soren_root, str(data.get("action", "")), str(data.get("url", "")))
        except ValueError as exc:
            self._send_error_json(400, "invalid_action", str(exc))
            return 400
        except Exception as exc:
            self._send_error_json(500, "voice_endpoint_action_failed", str(exc)[:300])
            return 500
        self._send_json(200, result)
        return 200

    def _handle_get_stream(self) -> int:
        try:
            status = _get_stream_status(self.soren_root)
        except Exception as exc:
            self._send_error_json(500, "stream_status_failed", str(exc)[:300])
            return 500
        self._send_json(200, status)
        return 200

    def _handle_post_stream(self) -> int:
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
        # issue #42: 配信の停止/開始は dangerous action として再確認 (confirm:true) を要求する。
        if not _is_confirmed(data, self.headers):
            self._send_error_json(428, "confirmation_required", "配信操作には confirm:true が必要です")
            return 428
        action = str(data.get("action", "")).strip().lower()
        if action not in ("start", "stop"):
            self._send_error_json(400, "invalid_action", "action must be start or stop")
            return 400
        before = _get_stream_status(self.soren_root)
        if action == "stop":
            result = _stop_stream_runner(self.soren_root)
            after = _get_stream_status(self.soren_root)
            ok = bool(result.get("stopped")) or after.get("state") in ("paused", "off")
            self._send_json(
                200,
                {
                    "ok": ok,
                    "action": action,
                    "state": after.get("state"),
                    "stopped": result.get("stopped"),
                    "method": result.get("method"),
                    "escalated_kill": result.get("escalated_kill"),
                    "remaining_pids": result.get("remaining_pids"),
                },
            )
            return 200
        # start: pause マーカーを外し、supervisor による respawn を待つ
        _set_worker_paused(self.soren_root, STREAM_WORKER, False)
        after = _wait_for_stream_start(self.soren_root)
        hint = None
        if after.get("state") != "live":
            hint = "supervisor (start_all.sh / soren-runtime) が稼働していれば数秒〜数十秒で自動起動します。稼働していない場合は手動で起動してください。"
        self._send_json(
            200,
            {"ok": after.get("state") == "live", "action": action, "state": after.get("state"), "hint": hint},
        )
        return 200

    def _handle_post_chat_toggle(self) -> int:
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
        action = str(data.get("action", "")).strip().lower()
        if action not in ("start", "stop"):
            self._send_error_json(400, "invalid_action", "action must be start or stop")
            return 400
        paused = action == "stop"
        _set_worker_paused(self.soren_root, "chat_worker", paused)
        pid = _find_worker_pid(self.soren_root, "chat_worker")
        # chat_worker はマーカーを自身のループで検知して park / resume する
        # (_worker_is_paused → _park_while_paused)。プロセスへのシグナルは不要で、
        # 停止時は IRC daemon・コメント生成・queue 消費がループ周期内で止まり、
        # 再開時も marker 削除で自動復帰する。
        self._send_json(
            200,
            {
                "ok": True,
                "action": action,
                "chat_paused": _is_worker_paused(self.soren_root, "chat_worker"),
                "worker_pid": pid,
            },
        )
        return 200

    def _handle_post_workers(self) -> int:
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
        # issue #42: worker (process) 制御は dangerous action として再確認 (confirm:true) を要求する。
        if not _is_confirmed(data, self.headers):
            self._send_error_json(428, "confirmation_required", "worker操作には confirm:true が必要です")
            return 428
        worker = str(data.get("worker", "")).strip().lower()
        if worker not in WORKER_CONTROL_TARGETS:
            self._send_error_json(400, "invalid_worker", "worker must be prediction_worker or improve_daemon")
            return 400
        action = str(data.get("action", "")).strip().lower()
        if action not in ("start", "stop"):
            self._send_error_json(400, "invalid_action", "action must be start or stop")
            return 400
        if action == "stop":
            result = _stop_controlled_worker(self.soren_root, worker)
            job_stopped = True
            if worker == "improve_daemon":
                job_stopped = bool((result.get("job") or {}).get("stopped", True))
                _mark_improve_job_stopped(self.soren_root)
            response: dict[str, Any] = {
                "ok": bool(result.get("stopped")) and job_stopped,
                "worker": worker,
                "action": action,
                "paused": True,
                "stopped": result.get("stopped"),
                "term_sent": result.get("term_sent"),
                "remaining_pid": result.get("remaining_pid"),
            }
            if worker == "improve_daemon":
                response["job_continues_in_background"] = not job_stopped
                response["job_pid"] = result.get("job_pid")
                response["job_stop"] = result.get("job")
                # v710以降: markerで新規spawnを封じ、稼働中ジョブもツリーごと止める。
                # lockは次回start時に蓄積データから再作成されるため、停止応答時点で除去する。
                lock = _improve_lock_path(self.soren_root)
                lock_removed = False
                if lock.is_file():
                    try:
                        lock.unlink()
                        lock_removed = True
                    except OSError:
                        pass
                response["lock_removed"] = lock_removed
            self._send_json(200, response)
            return 200
        # start: pause マーカーを外し、supervisor による respawn を待つ
        _set_worker_paused(self.soren_root, worker, False)
        started = _wait_for_worker_start(self.soren_root, worker)
        hint = None
        if not started.get("running"):
            hint = "supervisor (start_all.sh / soren-runtime) が稼働していれば数秒〜数十秒で自動起動します。稼働していない場合は手動で起動してください。"
        self._send_json(
            200,
            {
                "ok": bool(started.get("running")),
                "worker": worker,
                "action": action,
                "paused": False,
                "pid": started.get("pid"),
                "hint": hint,
            },
        )
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

    def _handle_get_audio_queue(self) -> int:
        try:
            items = _list_audio_queue(self.soren_root, limit=50)
        except Exception as exc:
            self._send_error_json(500, "list_failed", str(exc))
            return 500
        qdir = _comment_queue_dir(self.soren_root)
        dedup_dir = _comment_audio_dedup_dir(self.soren_root)
        try:
            dedup_count = len(list(dedup_dir.iterdir())) if dedup_dir.is_dir() else 0
        except Exception:
            dedup_count = 0
        # also report audio_worker status
        worker_info = None
        try:
            # reuse _find_worker_pid; soren_root workers
            pid = _find_worker_pid(self.soren_root, "audio_worker")
            alive = pid is not None
            worker_info = {"worker": "audio_worker", "pid": pid, "alive": alive}
        except Exception:
            worker_info = {"worker": "audio_worker", "pid": None, "alive": False}
        self._send_json(
            200,
            {
                "queue_dir": str(qdir),
                "dedup_dir": str(dedup_dir),
                "dedup_count": dedup_count,
                "count": len(items),
                "items": items,
                "worker": worker_info,
            },
        )
        return 200

    def _handle_post_audio_enqueue(self) -> int:
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
        text = data.get("text", "")
        source = data.get("source", "webui_manual")
        speaker = data.get("speaker", "")
        # allow alternative keys: content, message
        if not text and "content" in data:
            text = data.get("content", "")
        if not text and "message" in data:
            text = data.get("message", "")
        try:
            result = _enqueue_audio_text(self.soren_root, str(text), str(source), str(speaker))
        except ValueError as exc:
            self._send_error_json(400, "validation_error", str(exc))
            return 400
        except Exception as exc:
            self._send_error_json(500, "enqueue_failed", str(exc))
            return 500
        # result includes dedup flag
        if result.get("dedup"):
            self._send_json(200, {"ok": True, "dedup": True, "message": "dedup: 同一テキストが120秒以内にenqueue済みのためスキップされました"})
            return 200
        self._send_json(200, {"ok": True, "dedup": False, "filename": result.get("filename"), "path": result.get("path")})
        return 200

    def _handle_delete_audio_queue_item(self, fname: str) -> int:
        if not fname or "/" in fname or "\\" in fname or ".." in fname:
            self._send_error_json(400, "invalid_filename", "path traversal not allowed")
            return 400
        # allow only known queue filenames
        if not (fname.endswith(".txt") or fname.endswith(".playing")):
            self._send_error_json(400, "invalid_filename", "only .txt or .playing allowed")
            return 400
        # extra safety: must match comment* pattern or comment_announce*
        if not (fname.startswith("comment_") or fname.startswith("comment_announce_")):
            self._send_error_json(400, "invalid_filename", "filename must start with comment_")
            return 400
        if len(fname) > 200:
            self._send_error_json(400, "invalid_filename", "filename too long")
            return 400
        qdir = _comment_queue_dir(self.soren_root)
        target = qdir / fname
        # ensure inside qdir
        try:
            target.resolve().relative_to(qdir.resolve())
        except Exception:
            self._send_error_json(400, "invalid_filename", "outside queue dir")
            return 400
        deleted = False
        try:
            if target.is_file():
                target.unlink()
                deleted = True
            # also remove sidecars
            for suf in [".speaker", ".mode", ".meta"]:
                side = Path(str(target) + suf)
                try:
                    if side.is_file():
                        side.unlink()
                except Exception:
                    pass
            # if deleted .txt, also check .playing counterpart? not needed
        except Exception as exc:
            self._send_error_json(500, "delete_failed", str(exc))
            return 500
        if not deleted:
            self._send_error_json(404, "not_found", f"{fname} not found")
            return 404
        self._send_json(200, {"ok": True, "deleted": fname})
        return 200

    def _handle_clear_audio_queue(self) -> int:
        qdir = _comment_queue_dir(self.soren_root)
        count = 0
        try:
            if qdir.is_dir():
                for p in list(qdir.glob("*.txt")) + list(qdir.glob("*.playing")):
                    if p.name.startswith("."):
                        continue
                    if p.name == "played_hashes.txt":
                        continue
                    if not (p.name.startswith("comment_") or p.name.startswith("comment_announce_")):
                        continue
                    try:
                        p.unlink()
                        count += 1
                        for suf in [".speaker", ".mode", ".meta"]:
                            side = Path(str(p) + suf)
                            try:
                                if side.is_file():
                                    side.unlink()
                            except Exception:
                                pass
                    except Exception:
                        pass
        except Exception as exc:
            self._send_error_json(500, "clear_failed", str(exc))
            return 500
        self._send_json(200, {"ok": True, "cleared": count})
        return 200

    def _handle_list_prompts(self) -> int:
        try:
            items = _list_prompts(self.soren_root)
        except Exception as exc:
            self._send_error_json(500, "list_failed", str(exc))
            return 500
        roots = [str(p) for p in _prompts_dirs(self.soren_root)]
        self._send_json(200, {"prompts": items, "count": len(items), "roots": roots})
        return 200

    def _handle_get_prompt(self, prompt_id: str) -> int:
        try:
            target = _resolve_prompt_path(self.soren_root, prompt_id)
        except ValueError as exc:
            self._send_error_json(400, "invalid_id", str(exc))
            return 400
        if not target.is_file():
            self._send_error_json(404, "not_found", f"prompt not found: {prompt_id}")
            return 404
        try:
            txt = target.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            self._send_error_json(500, "read_failed", str(exc))
            return 500
        # double-check size
        if len(txt.encode("utf-8")) > PROMPTS_MAX_BYTES:
            self._send_error_json(500, "too_large", "file exceeds max size")
            return 500
        self._send_json(
            200,
            {
                "id": prompt_id,
                "content": txt,
                "mtime": _prompt_mtime(target),
                "size": len(txt.encode("utf-8")),
                "path": str(target),
            },
        )
        return 200

    def _handle_put_prompt(self, prompt_id: str) -> int:
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
        content = data.get("content")
        if content is None:
            self._send_error_json(400, "validation_error", "content required")
            return 400
        if not isinstance(content, str):
            self._send_error_json(400, "validation_error", "content must be string")
            return 400
        expected_mtime = data.get("expected_mtime")
        if expected_mtime is not None:
            try:
                expected_mtime = int(expected_mtime)
            except Exception:
                self._send_error_json(400, "validation_error", "expected_mtime must be integer")
                return 400
        try:
            _validate_prompt_content(content)
        except ValueError as exc:
            self._send_error_json(400, "validation_error", str(exc))
            return 400
        try:
            target = _resolve_prompt_path(self.soren_root, prompt_id)
        except ValueError as exc:
            self._send_error_json(400, "invalid_id", str(exc))
            return 400
        try:
            new_mtime = _atomic_prompt_write(self.soren_root, target, content, expected_mtime)
        except FileExistsError as exc:
            self._send_error_json(409, "concurrent_edit", str(exc))
            return 409
        except ValueError as exc:
            msg = str(exc)
            if "mtime mismatch" in msg or "concurrent" in msg:
                self._send_error_json(409, "conflict", msg)
                return 409
            self._send_error_json(400, "validation_error", msg)
            return 400
        except Exception as exc:
            self._send_error_json(500, "write_failed", str(exc))
            return 500
        self._send_json(200, {"ok": True, "id": prompt_id, "mtime": new_mtime})
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
    token = _effective_token(g)
    # issue #41 (fail closed): 非loopback bind + read_only=false (writable) + token
    # 未設定という危険な組み合わせかどうか。config.py の load_global は config ファイル
    # 由来の値のみ検証するため、--bind / --read-only という CLI 上書き後の実効値を
    # ここでも見て、CLI 上書きで config.py の検証をすり抜けられないようにする。
    unsafe_combo = not is_loopback_bind(effective_bind) and not eff_read_only and not token

    if dry_run:
        print(f"docich webui dry-run")
        print(f"  bind: {effective_bind}:{effective_port}")
        print(f"  soren_root: {eff_soren_root}")
        print(f"  read_only: {eff_read_only}")
        print(f"  token: {'set' if token else '(none)'}")
        print(f"  allow_cors: {g.webui.allow_cors}")
        # validate soren_root
        if not (eff_soren_root / "eloop_lib.sh").is_file():
            print(f"  WARNING: {eff_soren_root}/eloop_lib.sh not found (soren_root may be wrong)")
        if not (eff_soren_root / ".env").is_file():
            print(f"  WARNING: {eff_soren_root}/.env not found (will be created on first save)")
        if unsafe_combo:
            print(
                "  WARNING: non-loopback bind + writable + no token/auth."
                " 実際の起動はこの組み合わせを error にして拒否します"
                " (webui.token を設定するか --read-only を付けてください)"
            )
        print("  endpoints: /, /api/health, /api/csrf, /api/config, /api/backoffs, /api/stats, /api/reload, /api/game_state, /api/improve_state, /api/workers (GET/POST), /api/stream (GET/POST), /api/voice/endpoints (GET/POST), /api/chat (POST), /api/peak_status, /api/predictions, /api/predictions/action, /api/prompts")
        return 0

    if unsafe_combo:
        print(
            "docich: エラー: 非loopback bind + 書き込み可 (read_only=false) + 認証なし"
            " (token 未設定) の組み合わせでは起動できません。"
            " webui.token (または token_env 環境変数) を設定するか、"
            " --read-only を付けて起動してください。",
            flush=True,
        )
        return 2

    # validate soren_root
    # issue #43: soviet_now が未配置 (soren_root が無い/eloop_lib.sh が無い) でも
    # WebUI 自体は起動する。worker/status は RuntimeBackend が capability
    # unsupported を明示して返すので、ここで起動を止める必要はない。
    if not eff_soren_root.is_dir():
        print(f"docich: 警告: soren_root が見つかりません: {eff_soren_root} (soviet_now 未配置として起動します)", flush=True)
    elif not (eff_soren_root / "eloop_lib.sh").is_file():
        print(f"docich: 警告: {eff_soren_root}/eloop_lib.sh が見つかりません (soren_rootの指定を確認してください)", flush=True)
    if eff_read_only:
        print(f"docich: webui read-only mode enabled")

    # warn if binding to 0.0.0.0 (writable+認証なしは上のガードで既に弾いているので、
    # ここに到達するのは read_only=true か token 設定済みのケース。到達性の注意喚起のみ)
    if effective_bind == "0.0.0.0":
        print(f"docich: 警告: 0.0.0.0 にバインドするとインターネットから到達可能です。Tailscale serve (127.0.0.1) を推奨します", flush=True)

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
    # issue #42: CSRF secret はプロセス起動ごとに新規生成する (再起動で全 CSRF token 失効)。
    BoundHandler.csrf_secret = secrets.token_bytes(32)

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
