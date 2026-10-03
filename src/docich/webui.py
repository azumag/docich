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
from typing import Any, Mapping

from . import speech
from . import overlay_queue as shared_overlay_queue
from .webui_resources_loader import ResourceLoader
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

# Chains タブが編集するチェーン (フロントの chainKeys() と一致させる)。
CHAIN_KEYS = (
    "AI_COMMON_AGENTS",
    "MODEL_IMPROVE_LIST",
    "RADIO_AGENTS",
    "RADIO_PREPASS_AGENTS",
    "COMMENT_AGENTS",
    "COMMENT_TRANSLATION_AGENTS",
)
# 一時停止の記録 (webui 専用の .env キー)。値は "index:agent" のカンマ区切りで、
# index は停止前のチェーン内位置 (再開時に同じ位置へ戻すために使う)。停止中の
# モデルは本体のチェーン値から除外して保存するため、ランタイムは *_PAUSED を
# 参照しない (除外は保存時のチェーン値そのもので完結する)。
CHAIN_PAUSE_KEYS = {key: f"{key}_PAUSED" for key in CHAIN_KEYS}
CHAIN_PAUSE_KEY_TO_CHAIN = {v: k for k, v in CHAIN_PAUSE_KEYS.items()}
CHAIN_PAUSE_MAX_ENTRIES = 64
CHAIN_PAUSE_MAX_INDEX = 999
# "index:agent" (agent 自体は AGENT_RE で別途検証する)
PAUSED_ENTRY_RE = re.compile(r"^(\d{1,3}):(.+)$")

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
    "TWITCH_ADS_ENABLED",
} | set(CHAIN_PAUSE_KEYS.values())  # <CHAIN>_PAUSED (webui の停止位置記録)

# Display defaults are loaded from webui_resources/defaults.json below.

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


def _effective_value(key: str, dotenv: dict[str, str], defaults: Mapping[str, str] | None = None) -> str:
    if defaults is None:
        defaults = _RESOURCES.refresh()[0].defaults
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
        return defaults["AI_COMMON_AGENTS"]
    if key == "COMMENT_TRANSLATION_AGENTS":
        # inherits COMMENT_AGENTS effective
        ca = _effective_value("COMMENT_AGENTS", dotenv, defaults)
        if ca:
            return ca
        return defaults["AI_COMMON_AGENTS"]
    if key == "MODEL_IMPROVE_PEAK_LIST":
        if "MODEL_IMPROVE_PEAK_LIST" in dotenv and dotenv["MODEL_IMPROVE_PEAK_LIST"]:
            return dotenv["MODEL_IMPROVE_PEAK_LIST"]
        # inherits MODEL_IMPROVE_LIST effective
        ml = _effective_value("MODEL_IMPROVE_LIST", dotenv, defaults)
        if ml:
            return ml
        return defaults["MODEL_IMPROVE_LIST"]
    return defaults.get(key, "")


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
    if key in CHAIN_PAUSE_KEY_TO_CHAIN:
        # 一時停止の位置記録 "index:agent" (空 = 停止なし)
        if not value.strip():
            return
        parts = [p.strip() for p in value.split(",")]
        if len(parts) > CHAIN_PAUSE_MAX_ENTRIES:
            raise ValueError(f"{key} の要素が多すぎます (最大 {CHAIN_PAUSE_MAX_ENTRIES})")
        # 同一 agent の複数エントリは、チェーン内に同じ agent が複数ある場合の
        # 位置記録として正当 (index が異なる)。重複は許容する。
        for part in parts:
            m = PAUSED_ENTRY_RE.match(part)
            if not m:
                raise ValueError(f"{key} の要素 {part!r} は index:agent 形式である必要があります")
            index = int(m.group(1))
            agent = m.group(2)
            if index > CHAIN_PAUSE_MAX_INDEX:
                raise ValueError(f"{key} の index {index} が大きすぎます")
            if not AGENT_RE.match(agent):
                raise ValueError(f"{key} に不正なエージェント {agent!r} が含まれます")
        return
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
    if key in ("PEAK_HOURS_AGENT_SWAP_ENABLED", "PEAK_HOURS_QUEUE_GATE_ENABLED", "IMPROVE_PEAK_CHAIN_ENABLED", "IMPROVE_PEAK_HOUR_DEFER_ENABLED", "TWITCH_ADS_ENABLED"):
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
    return shared_overlay_queue.validate_event(ev)

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
CONFIRM_REQUIRED_PATHS = {("POST", "/api/workers"), ("POST", "/api/stream"), ("PUT", "/api/config"), ("POST", "/api/corners")}


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


def _get_peak_status(soren_root: Path, defaults: Mapping[str, str] | None = None) -> dict[str, Any]:
    if defaults is None:
        defaults = _RESOURCES.refresh()[0].defaults
    dotenv = _read_dotenv_dict(soren_root)
    windows = _effective_value("PEAK_HOURS_WINDOWS", dotenv, defaults)
    tz = _effective_value("PEAK_HOURS_TZ", dotenv, defaults)
    swap = _effective_value("PEAK_HOURS_AGENT_SWAP_ENABLED", dotenv, defaults)
    gate = _effective_value("PEAK_HOURS_QUEUE_GATE_ENABLED", dotenv, defaults)
    pref = _effective_value("PEAK_HOURS_AGENT_PREFERENCE", dotenv, defaults)
    prio = _effective_value("PEAK_HOURS_PRIORITY_AGENT", dotenv, defaults)
    improve_defer = _effective_value("IMPROVE_PEAK_HOUR_DEFER_ENABLED", dotenv, defaults)
    improve_peak_enabled = _effective_value("IMPROVE_PEAK_CHAIN_ENABLED", dotenv, defaults)
    improve_list = _effective_value("MODEL_IMPROVE_LIST", dotenv, defaults)
    improve_peak_list = _effective_value("MODEL_IMPROVE_PEAK_LIST", dotenv, defaults)
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
    return shared_overlay_queue.overlay_events_path(soren_root)

def _overlay_html_path(soren_root: Path) -> Path:
    return shared_overlay_queue.overlay_html_path(soren_root)

def _work_indicator_path(soren_root: Path) -> Path:
    return shared_overlay_queue.work_indicator_path(soren_root)

def _comment_gen_state_path(soren_root: Path) -> Path:
    return shared_overlay_queue.comment_gen_state_path(soren_root)

def _radio_state_path(soren_root: Path) -> Path:
    return shared_overlay_queue.radio_state_path(soren_root)

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


def _comment_audio_delivery_dir(soren_root: Path) -> Path:
    return _comment_queue_dir(soren_root) / "audio_delivery_dedup"


def _validate_audio_delivery_key(delivery_key: str) -> str:
    if not delivery_key:
        return ""
    value = str(delivery_key).strip()
    if not value or len(value) > 256:
        raise ValueError("delivery_key は1-256文字である必要があります")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError("delivery_key に制御文字は使用できません")
    return value


def _enqueue_audio_delivery(
    soren_root: Path, text: str, delivery: str, *, source: str = "crypto_paper", speaker: str = "",
    wait_for_lock: bool = True,
) -> dict[str, Any]:
    """Publish once by atomically moving a prepared payload into the queue.

    A complete receipt directory is installed before publication. Its payload
    exists while prepared; absence means the atomic queue rename committed,
    even if audio-worker has already consumed the queue file. Never expire
    receipts: text TTL and bounded marker eviction cannot deduplicate events.
    This guarantees process-crash recovery on one local filesystem, not
    exactly-once playback or recovery from filesystem/power loss.

    ``speaker`` (when non-empty) is published as a ``.speaker`` sidecar next
    to the queue file, mirroring the claim path. The Soren audio worker reads
    the sidecar to pick the voice. A redelivery of the same event reuses the
    same speaker because callers derive it deterministically from ``delivery``.
    """
    import fcntl
    import shutil

    queue = _comment_queue_dir(soren_root)
    receipts = _comment_audio_delivery_dir(soren_root)
    receipts.mkdir(parents=True, exist_ok=True)
    if source not in {"crypto_paper", "hanjuku_terminal"}:
        raise ValueError("unsupported durable audio source")
    # Keep existing PAPER receipt names stable; the new source is separately
    # namespaced so an identical caller key cannot alias across source types.
    receipt_key = delivery if source == "crypto_paper" else f"{source}\0{delivery}"
    key = hashlib.sha256(receipt_key.encode("utf-8")).hexdigest()
    marker = receipts / key
    # The lock inode is permanent; the kernel releases it when a process dies.
    with (receipts / ".publish.lock").open("a") as lock:
        lock_flags = fcntl.LOCK_EX if wait_for_lock else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(lock.fileno(), lock_flags)
        if not marker.exists():
            stage = Path(tempfile.mkdtemp(prefix=".prepared.", dir=receipts))
            try:
                filename = f"comment_announce_{time.time_ns()}_{key}_{source}.txt"
                for name, content in (("receipt.json", json.dumps({
                    "version": 1, "event_id": delivery, "source": source,
                    "filename": filename,
                })), ("payload", text + "\n")):
                    with (stage / name).open("w", encoding="utf-8") as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                (stage / "payload").chmod(0o644)
                os.replace(stage, marker)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        # Legacy marker-only reservations have no evidence of publication.
        # Fail rather than silently ACK or risk replaying an old announcement.
        try:
            record = json.loads((marker / "receipt.json").read_text(encoding="utf-8"))
            filename = record["filename"]
            recorded_source = record.get("source", "crypto_paper")
            if (record.get("version") != 1 or record.get("event_id") != delivery
                    or recorded_source != source
                    or not isinstance(filename, str)
                    or re.fullmatch(rf"comment_announce_[0-9]+_{key}_{re.escape(source)}\.txt", filename) is None):
                raise ValueError("invalid receipt")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("audio delivery receipt is ambiguous; manual reconciliation required") from exc
        payload = marker / "payload"
        if not payload.exists():
            return {"ok": True, "dedup": True, "filename": None}
        dest = queue / filename
        # This rename is both publication and the durable committed state.
        # Do not recreate/remove the receipt on any exception after this point.
        os.replace(payload, dest)
        if speaker:
            try:
                (Path(str(dest) + ".speaker")).write_text(speaker, encoding="utf-8")
            except Exception:
                pass
        return {"ok": True, "dedup": False, "filename": filename, "path": str(dest)}


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


def _comment_audio_release_enqueue_key(soren_root: Path, text: str) -> None:
    """Release this text's dedup claim after a failed queue write."""
    key = _comment_audio_hash(text)
    if not key:
        return
    marker = _comment_audio_dedup_dir(soren_root) / key
    try:
        import shutil
        shutil.rmtree(marker, ignore_errors=True)
    except Exception:
        pass


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


def _enqueue_audio_text(
    soren_root: Path,
    text: str,
    source: str = "webui_manual",
    speaker: str = "",
    *,
    delivery_key: str = "",
) -> dict[str, Any]:
    cleaned = _validate_audio_text(text)
    src = _validate_audio_source(source)
    spk = _validate_audio_speaker(speaker)
    delivery = _validate_audio_delivery_key(delivery_key)
    if src == "hanjuku_terminal" and not delivery:
        raise ValueError("hanjuku_terminal requires a durable delivery_key")
    if delivery:
        if src not in {"crypto_paper", "hanjuku_terminal"}:
            raise ValueError("delivery_key is reserved for durable audio sources")
        # Each durable source keeps its own semantic queue classification.
        return _enqueue_audio_delivery(
            soren_root, cleaned, delivery, source=src, speaker=spk,
            wait_for_lock=(src == "crypto_paper"),
        )
    claimed = _comment_audio_claim_enqueue_key(soren_root, cleaned)
    if not claimed:
        return {"ok": True, "dedup": True, "filename": None}
    queue_dir = _comment_queue_dir(soren_root)
    try:
        queue_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        _comment_audio_release_enqueue_key(soren_root, cleaned)
        raise
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
    except Exception:
        _comment_audio_release_enqueue_key(soren_root, cleaned)
        raise
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
    return shared_overlay_queue.load_events(soren_root, keep=keep, strict=False)

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
    return shared_overlay_queue.get_keep_visible(soren_root)

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
    try:
        shared_overlay_queue.atomic_write(soren_root, rel_path, data, mode)
    except shared_overlay_queue.OverlayQueueBusyError as exc:
        # Existing bulk/clear/banner handlers return HTTP 409 for this class.
        raise FileExistsError(str(exc)) from exc

def _regenerate_event_overlay(soren_root: Path) -> bool:
    return shared_overlay_queue.regenerate_overlay(soren_root)

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


# --- Corners / corner rotation ---------------------------------------------
# webui は既存の reviewed one-off manual runner (bin/*-corner-manual) と固定 CLI
# だけを呼ぶ。rotation の自動選択・24h rolling cooldown・latch の fail-closed
# 契約は変更しない。手動 runner は別 state/lock の one-off 実行で、稼働中はその
# state が BUSY として観測されるため rotation の次の自動発火は program slot 待ち
# になる。読み取り側は seed・request UUID・自由文を出さない固定投影とする。
CORNER_MANUAL_DURATION_DEFAULT = 5
CORNER_MANUAL_DURATION_MIN = 1
CORNER_MANUAL_DURATION_MAX = 60
CORNER_RECOVER_TIMEOUT_SEC = 60
CORNER_MANUAL_STATE_FILES = (
    "retro_corner", "retro_corner_manual", "paper_corner", "paper_corner_manual",
    "soren91_corner", "soren91_corner_manual", "nethack_corner", "nethack_corner_manual",
)
_ROTATION_STATUS_ENUM = frozenset({"ready", "waiting", "running", "recovery_required"})
_ROTATION_ERROR_KIND_ENUM = frozenset({
    "adapter-state", "adapter-timestamp", "catalog-mismatch", "execution-error",
    "execution-unverified", "invalid-state", "unexpected",
})
_PENDING_PHASE_ENUM = frozenset({"selected", "dispatched"})
_CORNER_STATUS_ENUM = frozenset({
    "idle", "waiting", "starting", "active", "restoring", "preparing",
    "recovery_required", "failed", "completed", "interrupted", "expired",
    "running", "promoted", "kept", "improved", "dry-run", "skipped",
})
# adapter -> (one-off manual runner in bin/, --duration-minutes 有無, --game 必須)
_CORNER_MANUAL_LAUNCHERS = {
    "game": ("docich-retro-corner-manual", True, True),
    "meriken": ("docich-soren91-corner-manual", True, False),
    "nethack": ("docich-nethack-corner-manual", True, False),
    "paper": ("docich-paper-corner-manual", False, False),
}
# rotation / 自動起動が書く base state の停止経路（webui stop は manual だけを見ない）。
# paper は専用 restore CLI が scheduled service を止めて表示を復帰するため、
# bin/docich の paper-corner stop ではなく専用 launcher を使う。
_CORNER_BASE_STOP_CLI = {
    "game": ("docich", "retro-corner"),
    "meriken": ("docich-soren91-corner",),
    "nethack": ("docich-nethack-corner",),
    "paper": ("docich-paper-corner-restore",),
}
# 停止可能 status（画面のボタン有効化と POST stop の振り分けで共通）。
# manual: 既存の手動停止契約 + restoring（中断からの復帰中も手動で終わらせる）。
# base: RetroCornerManager._stop_direct / stop_manual が実際に stop できるもの。
_CORNER_MANUAL_STOP_STATUSES = frozenset(
    {"starting", "active", "restoring", "failed", "recovery_required"}
)
_CORNER_MANUAL_ACTIVE_STOP_STATUSES = frozenset({"starting", "active", "restoring"})
_CORNER_BASE_STOP_STATUSES = frozenset({"starting", "active", "restoring"})


def _view_str(value: Any, limit: int = 64) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value[:limit] or None


def _view_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _view_time(value: Any) -> float | None:
    """Epoch seconds for the UI. ISO strings go through the reviewed parser."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if v > 0 else None
    if isinstance(value, str):
        try:
            from .corner_rotation import timestamp

            v = float(timestamp(value))
            return v if v > 0 else None
        except Exception:
            return None
    return None


def _rotation_view(g: GlobalConfig) -> dict[str, Any]:
    """Bounded, read-only projection of corner_rotation.json (#seed/uuid は出さない)."""
    from .corner_catalog import cooldown_seconds, rotation_config, rotation_enabled

    path = Path(g.state_dir) / "corner_rotation.json"
    raw = _load_json_file(path)
    out: dict[str, Any] = {"present": path.is_file(), "readable": isinstance(raw, dict)}
    try:
        out["enabled"] = bool(rotation_enabled(g))
    except Exception:
        out["enabled"] = False
    try:
        cfg = rotation_config(g)
        out["schedule_mode"] = cfg.get("schedule_mode", "interval")
        out["cooldown_seconds"] = float(cooldown_seconds(g))
    except Exception:
        out["schedule_mode"] = None
        out["cooldown_seconds"] = None
    if not isinstance(raw, dict):
        return out
    status = raw.get("status")
    status = status if status in _ROTATION_STATUS_ENUM else "unknown"
    pending = raw.get("pending")
    pending_view: dict[str, Any] | None = None
    if isinstance(pending, dict):
        selected = _view_time(pending.get("selected_at"))
        pending_view = {
            "corner": _view_str(pending.get("corner")),
            "phase": (pending.get("phase")
                      if pending.get("phase") in _PENDING_PHASE_ENUM else "unknown"),
            "age_sec": max(0, int(time.time() - selected)) if selected else None,
        }
    eligible: list[str] = []
    if isinstance(raw.get("eligible"), list):
        eligible = [item[:64] for item in raw.get("eligible") if isinstance(item, str)][:20]
    error_kind = raw.get("error_kind")
    out.update(
        status=status,
        reason=_view_str(raw.get("reason"), 64),
        error_kind=(error_kind if error_kind in _ROTATION_ERROR_KIND_ENUM
                    else (None if error_kind is None else "unknown")),
        slot=_view_int(raw.get("slot")),
        latch=(status == "recovery_required"),
        can_recover=bool(out["enabled"]) and status == "recovery_required",
        pending=pending_view,
        last_slot_at=_view_time(raw.get("last_slot_at")),
        next_due_at=_view_time(raw.get("next_due_at")),
        last_seen_at=_view_time(raw.get("last_seen_at")),
        eligible_count=len(eligible),
        eligible=eligible,
    )
    return out


def _game_switch_view(g: GlobalConfig) -> dict[str, Any]:
    path = Path(g.state_dir) / "game_switch.json"
    raw = _load_json_file(path)
    if not isinstance(raw, dict):
        return {"present": path.is_file(), "readable": False}
    active = raw.get("active") if isinstance(raw.get("active"), dict) else {}
    last = raw.get("last_result") if isinstance(raw.get("last_result"), dict) else {}
    return {
        "present": True,
        "readable": True,
        "phase": _view_str(raw.get("phase"), 32),
        "operation": _view_str(raw.get("operation"), 32),
        "active_game": _view_str(active.get("game")),
        "active_generation": _view_int(active.get("generation")),
        "last_status": _view_str(last.get("status"), 32),
        "last_error_code": _view_str(last.get("error_code"), 64),
        "last_to_game": _view_str(last.get("to_game")),
        "updated_at": _view_str(raw.get("updated_at"), 40),
    }


def _soren91_renderer_view(g: GlobalConfig) -> dict[str, Any]:
    """メリケン描画ホストの方針と直近の選択。URL/token は出さず有無だけ返す。"""
    from . import soren91_renderer
    from .corner_adapters import MerikenCornerAdapter

    configured: dict[str, bool | None] = {host: None for host in soren91_renderer.HOSTS}
    env_path = Path(os.environ.get("DOCICH_SOREN91_ENV_FILE",
                                   str(MerikenCornerAdapter.DEFAULT_ENV_FILE)))
    try:
        values = MerikenCornerAdapter._read_env_file(env_path) if env_path.is_file() else {}
        keys = {
            "windows": ("SOREN91_WINDOWS_AGENT_BASE_URL", "SOREN91_WINDOWS_AGENT_TOKEN"),
            "mac": ("SOREN91_MACOS_AGENT_BASE_URL", "SOREN91_LOCAL_AGENT_TOKEN"),
        }
        configured = {host: all(values.get(k) for k in pair) for host, pair in keys.items()}
    except Exception:
        pass
    selection = soren91_renderer.load_selection(g.state_dir)
    if selection:
        attempts = selection.get("attempts") if isinstance(selection.get("attempts"), list) else []
        selection = {
            "host": selection["host"],
            "selected_at": _view_time(selection.get("selected_at")),
            "attempts": [
                {"host": a.get("host") if a.get("host") in soren91_renderer.HOSTS else None,
                 "ok": a.get("error") is None,
                 "error": _view_str(a.get("error"), 200)}
                for a in attempts[:4] if isinstance(a, dict)
            ],
        }
    return {
        "mode": soren91_renderer.load_mode(g.state_dir),
        "modes": list(soren91_renderer.MODES),
        "order": list(soren91_renderer.host_order(g.state_dir)),
        "configured": configured,
        "selection": selection,
    }


def _corners_view(g: GlobalConfig) -> dict[str, Any]:
    """Read-only page model: rotation ledger + game switch + catalog + corner states."""
    from .corner_catalog import load_catalog

    state_dir = Path(g.state_dir)
    catalog: list[dict[str, Any]] = []
    ledger = _load_json_file(state_dir / "corner_rotation.json")
    last_run = _rotation_last_runs(ledger)
    eligible_ids = set()
    if isinstance(ledger, dict) and isinstance(ledger.get("eligible"), list):
        eligible_ids = {item for item in ledger["eligible"] if isinstance(item, str)}
    try:
        from .corner_catalog import cooldown_seconds

        cooldown = float(cooldown_seconds(g))
    except Exception:
        cooldown = None
    now = time.time()
    try:
        for row in load_catalog(g):
            last = last_run.get(row.id)
            until = (last + cooldown) if (last is not None and cooldown) else None
            catalog.append({
                "id": row.id,
                "adapter": row.adapter,
                "game": row.game,
                "enabled": bool(row.enabled),
                "paused": bool(row.paused),
                "manual": row.adapter in _CORNER_MANUAL_LAUNCHERS,
                "eligible": row.id in eligible_ids,
                "last_run_at": last,
                "cooldown_until": until if (until is not None and until > now) else None,
                "state_file": _CORNER_STATE_FILE_BY_ADAPTER.get(row.adapter),
            })
    except Exception:
        catalog = []
    corners: dict[str, dict[str, Any]] = {}
    for name in CORNER_MANUAL_STATE_FILES:
        raw = _load_json_file(state_dir / f"{name}.json")
        entry: dict[str, Any] = {"present": isinstance(raw, dict), "readable": isinstance(raw, dict)}
        if isinstance(raw, dict):
            status = raw.get("status")
            target = raw.get("target_matches")
            entry.update(
                status=status if status in _CORNER_STATUS_ENUM else "unknown",
                game=_view_str(raw.get("game")),
                previous_game=_view_str(raw.get("previous_game")),
                started_at=_view_time(raw.get("started_at")),
                ends_at=_view_time(raw.get("ends_at")),
                completed_at=_view_time(raw.get("completed_at")),
                recovery_required=(raw.get("recovery_required")
                                   if isinstance(raw.get("recovery_required"), bool) else None),
                target_matches=(target if isinstance(target, int)
                                and not isinstance(target, bool) and 1 <= target <= 100 else None),
                stop_retryable=_hanjuku_stop_retryable(g, raw),
                end_reason=(raw.get("end_reason") if raw.get("end_reason") in
                            {"game_over", "screen_stalled", "manual_saved_stop",
                             "manual_forced_stop",
                             "switch-terminal-before-corner-active"} else None),
                last_error_code=_view_str(raw.get("last_error_code")),
            )
        corners[name] = entry
    return {
        "generated_at": int(time.time()),
        # どの config / state を読んでいるかを出す。本番は docich.soren-live.toml /
        # run-soren-live。既定 config のまま起動すると空の state を見て「動いていない」
        # ように見えるため、画面側で警告に使う（パスは basename のみ）。
        "source": {
            "config": Path(str(g.config_path)).name if g.config_path else None,
            "state_dir": state_dir.name,
        },
        "rotation": _rotation_view(g),
        "game_switch": _game_switch_view(g),
        "catalog": catalog,
        "corners": corners,
    }


_CORNER_STATE_FILE_BY_ADAPTER = {
    "game": "retro_corner",
    "meriken": "soren91_corner",
    "nethack": "nethack_corner",
    "paper": "paper_corner",
}


def _rotation_last_runs(ledger: Any) -> dict[str, float]:
    """Latest history timestamp per corner id (selection/execution/manual)."""
    out: dict[str, float] = {}
    if not isinstance(ledger, dict) or not isinstance(ledger.get("history"), list):
        return out
    for row in ledger["history"][-500:]:
        if not isinstance(row, dict) or not isinstance(row.get("corner"), str):
            continue
        at = _view_time(row.get("at"))
        if at is None:
            continue
        key = row["corner"][:64]
        if at > out.get(key, 0.0):
            out[key] = at
    return out


def _corner_catalog_row(g: GlobalConfig, corner_id: str) -> Any:
    from .corner_catalog import load_catalog

    try:
        rows = {row.id: row for row in load_catalog(g)}
    except Exception:
        rows = {}
    row = rows.get(corner_id)
    if row is None:
        raise ValueError("unknown corner")
    return row


def _corner_state_present(g: GlobalConfig, state_file: str, row: Any) -> dict[str, Any] | None:
    """base/manual state のうち、このカタログ行に属する present な状態のみ返す。"""
    if not state_file:
        return None
    raw = _load_json_file(Path(g.state_dir) / f"{state_file}.json")
    if not isinstance(raw, dict):
        return None
    # game adapter は複数コーナーで state を共有。game が読めるときは一致行だけが対象。
    # game 欄が欠落/unknown の古い手動記録は対象外（全 game 行へ誤表示しない）。
    if getattr(row, "adapter", None) == "game":
        game = raw.get("game")
        if not isinstance(game, str) or not game or game == "unknown" or game != row.game:
            return None
    return raw


def _corner_manual_argv(g: GlobalConfig, corner_id: str, action: str,
                        duration: int) -> tuple[list[str], dict[str, Any]]:
    """Reviewed one-off runner argv. corner id is resolved through the catalog
    (config-validated, game-name validated) and passed as an exec list -- never
    through a shell."""
    row = _corner_catalog_row(g, corner_id)
    spec = _CORNER_MANUAL_LAUNCHERS.get(row.adapter)
    if spec is None:
        raise ValueError("corner has no manual runner")
    launcher, supports_duration, needs_game = spec
    argv = [str(Path(g.repo_root) / "bin" / launcher)]
    # runner は既定で config/docich.toml を読む。webui と同じ config（本番は
    # docich.soren-live.toml）を明示しないと別の state_dir を操作してしまう。
    if g.config_path:
        argv += ["--config", str(g.config_path)]
    argv.append(action)
    if needs_game:
        argv += ["--game", row.game]
    if action == "start" and supports_duration:
        argv += ["--duration-minutes", str(duration)]
    return argv, {"id": row.id, "adapter": row.adapter, "game": row.game}


def _corner_base_stop_argv(g: GlobalConfig, corner_id: str) -> tuple[list[str], dict[str, Any]]:
    """rotation / 自動起動が書く base state 用の停止 argv（exec list、shell 不使用）。"""
    row = _corner_catalog_row(g, corner_id)
    spec = _CORNER_BASE_STOP_CLI.get(row.adapter)
    if spec is None:
        raise ValueError("corner has no base runner")
    bin_dir = Path(g.repo_root) / "bin"
    if row.adapter == "paper":
        if not g.config_path:
            raise ValueError("PAPER restore requires an explicit config")
        argv = [str(bin_dir / spec[0])]
        argv += ["--config", str(g.config_path)]
        return argv, {
            "id": row.id, "adapter": row.adapter, "game": row.game, "target": "base"
        }
    if len(spec) == 1:
        argv = [str(bin_dir / spec[0])]
    else:
        # bin/docich --config PATH <sub> stop
        argv = [str(bin_dir / spec[0])]
        if g.config_path:
            argv += ["--config", str(g.config_path)]
        argv.append(spec[1])
        argv.append("stop")
        return argv, {"id": row.id, "adapter": row.adapter, "game": row.game, "target": "base"}
    if g.config_path:
        argv += ["--config", str(g.config_path)]
    argv.append("stop")
    return argv, {"id": row.id, "adapter": row.adapter, "game": row.game, "target": "base"}


def _hanjuku_stop_retryable(g: GlobalConfig, state: dict) -> bool:
    if state.get("game") != "hanjuku-hero" or state.get("status") != "failed":
        return False
    canonical = _load_json_file(Path(g.state_dir) / "game_switch.json") or {}
    active = canonical.get("active") or {}
    expected = state.get("bot_identity")
    return (canonical.get("phase") == "ready" and isinstance(active, dict)
            and active.get("game") == "hanjuku-hero" and isinstance(expected, dict)
            and all(active.get(k) is not None and active.get(k) == expected.get(k)
                    for k in ("game", "runtime_id", "generation", "lease_id")))


def _corner_stop_argv(g: GlobalConfig, corner_id: str,
                      duration: int) -> tuple[list[str], dict[str, Any]]:
    """進行中の手動実行、次に base 実行へ stop を振り分ける。

    手動側の failed / recovery_required は base の進行を隠さない。
    実行中の対象がない場合は、既存の manual stop 契約を維持する。
    """
    try:
        row = _corner_catalog_row(g, corner_id)
    except ValueError:
        raise
    base_file = _CORNER_STATE_FILE_BY_ADAPTER.get(row.adapter)
    manual = _corner_state_present(g, f"{base_file}_manual" if base_file else "", row)
    base = _corner_state_present(g, base_file or "", row)
    # A failed/recovery-required manual record can be stale while the scheduled
    # runner owns the current PAPER view. Prefer an actually progressing owner;
    # retry the manual failure only when no base run is active.
    if manual and manual.get("status") in _CORNER_MANUAL_ACTIVE_STOP_STATUSES:
        argv, meta = _corner_manual_argv(g, corner_id, "stop", duration)
        meta = {**meta, "target": "manual"}
        return argv, meta
    if base and (base.get("status") in _CORNER_BASE_STOP_STATUSES or _hanjuku_stop_retryable(g, base)):
        return _corner_base_stop_argv(g, corner_id)
    if manual and manual.get("status") in _CORNER_MANUAL_STOP_STATUSES:
        argv, meta = _corner_manual_argv(g, corner_id, "stop", duration)
        meta = {**meta, "target": "manual"}
        return argv, meta
    argv, meta = _corner_manual_argv(g, corner_id, "stop", duration)
    meta = {**meta, "target": "manual"}
    return argv, meta


def _rotation_recover_argv(g: GlobalConfig) -> list[str]:
    """Fixed latch-recovery CLI (same contract as the owner-only operator)."""
    return [str(Path(g.repo_root) / "bin" / "docich"), "--config", str(g.config_path),
            "corner-rotation", "recover"]


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

_RESOURCES = ResourceLoader(Path(__file__).with_name("webui_resources"), WEBUI_ALLOWLIST, _validate_value)
# Compatibility exports for callers inspecting the packaged initial snapshot.
DEFAULTS = _RESOURCES.refresh()[0].defaults
INDEX_HTML = _RESOURCES.refresh()[0].html.decode("utf-8")


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
    resources = _RESOURCES

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
                snapshot, resource_status = self.resources.refresh()
                body = snapshot.html
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Docich-Resources", resource_status["status"])
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
            elif path == "/api/corners":
                status = self._handle_get_corners()
            elif path == "/api/soren91/renderer":
                status = self._handle_get_soren91_renderer()
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
            elif parsed.path == "/api/corners":
                status = self._handle_post_corners()
            elif parsed.path == "/api/soren91/renderer":
                status = self._handle_post_soren91_renderer()
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
        _, resource_status = self.resources.refresh()
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
                "resources": resource_status,
            },
        )
        return 200

    def _handle_get_config(self) -> int:
        snapshot, resource_status = self.resources.refresh()
        defaults = snapshot.defaults
        dotenv = _read_dotenv_dict(self.soren_root)
        mtime = _dotenv_mtime(self.soren_root)
        entries = []
        for key in sorted(WEBUI_ALLOWLIST):
            val = dotenv.get(key, "")
            eff = _effective_value(key, dotenv, defaults)
            default = defaults.get(key, "")
            # inherit defaults for empty inherited keys
            if key in ("RADIO_AGENTS", "RADIO_PREPASS_AGENTS") and not val:
                default = defaults["AI_COMMON_AGENTS"]
            if key == "COMMENT_TRANSLATION_AGENTS" and not val:
                # default is COMMENT_AGENTS effective
                default = defaults["AI_COMMON_AGENTS"]
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
                "resources": resource_status,
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
        defaults = self.resources.refresh()[0].defaults
        bdir = _backoff_dir(self.soren_root)
        now = int(time.time())
        dotenv = _read_dotenv_dict(self.soren_root)
        # collect known agents from effective configs
        known_agents: set[str] = set()
        for key in ("AI_COMMON_AGENTS", "MODEL_IMPROVE_LIST", "RADIO_AGENTS", "RADIO_PREPASS_AGENTS", "COMMENT_AGENTS", "COMMENT_TRANSLATION_AGENTS"):
            eff = _effective_value(key, dotenv, defaults)
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
            info = _get_peak_status(self.soren_root, self.resources.refresh()[0].defaults)
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

    def _handle_get_corners(self) -> int:
        """Read-only rotation/game-switch/corner projection (seed/uuid は出さない)."""
        try:
            view = _corners_view(self.g)
        except Exception as exc:
            self._send_error_json(500, "corners_unavailable", str(exc)[:200])
            return 500
        self._send_json(200, view)
        return 200

    def _handle_get_soren91_renderer(self) -> int:
        try:
            view = _soren91_renderer_view(self.g)
        except Exception as exc:
            self._send_error_json(500, "renderer_unavailable", str(exc)[:200])
            return 500
        self._send_json(200, view)
        return 200

    def _handle_post_soren91_renderer(self) -> int:
        """描画ホスト方針 (auto/windows/mac) を保存する。次のコーナー開始から効く。"""
        from . import soren91_renderer

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
        mode = str(data.get("mode", "")).strip().lower()
        try:
            soren91_renderer.save_mode(self.g.state_dir, mode)
        except ValueError as exc:
            self._send_error_json(400, "invalid_mode", str(exc))
            return 400
        except OSError as exc:
            self._send_error_json(500, "renderer_save_failed", str(exc)[:200])
            return 500
        self._send_json(200, _soren91_renderer_view(self.g))
        return 200

    def _handle_post_corners(self) -> int:
        """One-off manual start/stop and the fixed latch recovery (issue #42 の
        dangerous action: confirm:true 必須)。rotation の cooldown/latch 契約は
        runner/CLI 側の既存 fail-closed に委譲する。"""
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
        if not _is_confirmed(data, self.headers):
            self._send_error_json(428, "confirmation_required", "corner操作には confirm:true が必要です")
            return 428
        action = str(data.get("action", "")).strip().lower()
        if action not in ("start", "stop", "recover"):
            self._send_error_json(400, "invalid_action", "action must be start, stop or recover")
            return 400
        if action == "recover":
            argv = _rotation_recover_argv(self.g)
            try:
                proc = subprocess.run(
                    argv,
                    cwd=str(self.g.repo_root),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=CORNER_RECOVER_TIMEOUT_SEC,
                )
            except subprocess.TimeoutExpired:
                self._send_error_json(504, "recover_timeout",
                                      f"{CORNER_RECOVER_TIMEOUT_SEC}s を超えて復旧が完了しませんでした")
                return 504
            except OSError as exc:
                self._send_error_json(500, "recover_start_failed", str(exc)[:200])
                return 500
            after = _load_json_file(Path(self.g.state_dir) / "corner_rotation.json")
            latched = isinstance(after, dict) and after.get("status") == "recovery_required"
            outcome: dict[str, Any] = {}
            stdout = (proc.stdout or "").strip()
            if stdout:
                try:
                    parsed_out = json.loads(stdout.splitlines()[-1])
                    if isinstance(parsed_out, dict):
                        outcome = {key: parsed_out.get(key) for key in
                                   ("status", "reason", "corner", "result", "latch_resolved")
                                   if key in parsed_out}
                except Exception:
                    outcome = {}
            detail = ((proc.stderr or "").strip() or stdout)[-200:]
            self._send_json(200, {
                # 成否は「試した」ではなく「ledger の latch が消えたか」で決める
                # (corner-rotation recover 自身の exit 契約と同じ)。
                "ok": proc.returncode == 0 and not latched,
                "action": "recover",
                "exit_code": proc.returncode,
                "latch_resolved": not latched,
                "outcome": outcome,
                "detail": detail,
            })
            return 200
        duration = data.get("duration_minutes", CORNER_MANUAL_DURATION_DEFAULT)
        if isinstance(duration, bool) or not isinstance(duration, int):
            self._send_error_json(400, "invalid_duration", "duration_minutes must be an integer")
            return 400
        if not (CORNER_MANUAL_DURATION_MIN <= duration <= CORNER_MANUAL_DURATION_MAX):
            self._send_error_json(
                400, "invalid_duration",
                f"duration_minutes must be {CORNER_MANUAL_DURATION_MIN}..{CORNER_MANUAL_DURATION_MAX}",
            )
            return 400
        corner_id = str(data.get("corner", "")).strip()
        try:
            if action == "stop":
                argv, meta = _corner_stop_argv(self.g, corner_id, duration)
            else:
                argv, meta = _corner_manual_argv(self.g, corner_id, action, duration)
                meta = {**meta, "target": "manual"}
        except ValueError as exc:
            self._send_error_json(400, "invalid_corner", str(exc))
            return 400
        # one-off runner はコーナー終了まで生きて良い。webui の寿命や HTTP 応答に
        # 結びつけない (detached, no shell)。
        log_dir = self.soren_root / "tmp/logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"corner_manual_{meta['id']}.log"
            with log_path.open("ab") as log_file:
                proc = subprocess.Popen(
                    argv,
                    cwd=str(self.g.repo_root),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
        except OSError as exc:
            self._send_error_json(500, "corner_dispatch_failed", str(exc)[:200])
            return 500
        self._send_json(200, {
            "ok": True,
            "action": action,
            "corner": meta,
            "duration_minutes": duration if action == "start" else None,
            "pid": proc.pid,
            "log": f"tmp/logs/corner_manual_{meta['id']}.log",
        })
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
        try:
            shared_overlay_queue.append_event(
                self.soren_root, ev, keep=keep, strict=False, regenerate=False
            )
        except shared_overlay_queue.OverlayQueueError as exc:
            if "another overlay edit in progress" in str(exc):
                self._send_error_json(409, "concurrent_edit", str(exc))
                return 409
            self._send_error_json(500, "write_failed", str(exc))
            return 500
        except Exception as exc:
            self._send_error_json(500, "write_failed", str(exc))
            return 500
        _regenerate_event_overlay(self.soren_root)
        events = _load_overlay_events(self.soren_root)
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
        try:
            count = shared_overlay_queue.delete_event(self.soren_root, idx)
        except IndexError:
            self._send_error_json(404, "not_found", f"index {idx} out of range")
            return 404
        except shared_overlay_queue.OverlayQueueBusyError as exc:
            self._send_error_json(409, "concurrent_edit", str(exc))
            return 409
        except Exception as exc:
            self._send_error_json(500, "write_failed", str(exc))
            return 500
        _regenerate_event_overlay(self.soren_root)
        self._send_json(200, {"ok": True, "deleted": idx, "count": count})
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

    BoundHandler.resources = ResourceLoader(Path(__file__).with_name("webui_resources"), WEBUI_ALLOWLIST, _validate_value)
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
