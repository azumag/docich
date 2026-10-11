"""Operator-edited settings of the generic "special corner" (OBS/SRT view).

The special corner is the ``external-video-view`` program view: whatever the
streamer plays on their own PC, sent from OBS over SRT.  What it *is* (the
viewer-facing title, the Twitch category, the stream title suffix and the
prompts the hosts use) used to be hard-coded for Fly Me To The Home!.  It now
lives in one private JSON file in the state directory, edited from the WebUI.

Missing or unreadable settings fall back to the Fly Me To The Home! values, so
an existing deployment keeps its behaviour until an operator saves new ones.
Nothing here is secret; the Twitch category search runs the reviewed Soren
updater in its read-only ``--resolve`` mode and never forwards its stderr.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path

SETTINGS_FILE = "special_corner.json"
SCHEMA_VERSION = 1

# Backward-compatible defaults: the corner that existed before this module.
DEFAULT_TITLE = "Fly Me To The Home!"
DEFAULT_TITLE_SUFFIX = "全50面クリアに挑戦"
# Resolved read-only with the Soren updater's --resolve on 2026-10-11.
DEFAULT_CATEGORY_ID = "1202315854"
DEFAULT_CATEGORY_NAME = "Fly Me To The Home!"
DEFAULT_COMMENT_PROMPT = (
    "配信者が自分のPCで遊んでいる『Fly Me To The Home!』（家を目指して進むアクションゲーム）の映像を、OBSから受けて映している\n"
    "目標は全50面クリア。クリアしたらコーナーは終わりで、そのあと配信者が感想を話す"
)
DEFAULTS = {
    "title": DEFAULT_TITLE,
    "title_suffix": DEFAULT_TITLE_SUFFIX,
    "category_id": DEFAULT_CATEGORY_ID,
    "category_name": DEFAULT_CATEGORY_NAME,
    "comment_prompt": DEFAULT_COMMENT_PROMPT,
    "closing_prompt": "",
}

# field -> (min chars, max chars, multi-line allowed)
LIMITS = {
    "title": (1, 60, False),
    "title_suffix": (1, 60, False),
    "category_id": (1, 20, False),
    "category_name": (1, 100, False),
    "comment_prompt": (0, 800, True),
    "closing_prompt": (0, 400, True),
}
MAX_PROMPT_LINES = 20
QUERY_MAX_CHARS = 50
RESOLVE_TIMEOUT_S = 45
MAX_CANDIDATES = 10

# Shell/template metacharacters and markup never reach a prompt or argv.
FORBIDDEN_CHARS = frozenset("$`\\<>")
_CANDIDATE_LINE = re.compile(r"^(\d{1,20}) \| (.{1,100})$")


class SpecialCornerSettingsError(ValueError):
    """A settings value was rejected; ``field`` names the offending input."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


class CategorySearchError(RuntimeError):
    """The read-only category search could not produce candidates."""


def settings_path(state_dir) -> Path:
    return Path(state_dir) / SETTINGS_FILE


def _clean_text(field: str, value, *, minimum: int, maximum: int, multiline: bool) -> str:
    if not isinstance(value, str):
        raise SpecialCornerSettingsError(field, f"{field} は文字列で指定してください")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if multiline:
        lines = [line.rstrip() for line in text.split("\n")]
        while lines and not lines[-1]:
            lines.pop()
        while lines and not lines[0]:
            lines.pop(0)
        if len(lines) > MAX_PROMPT_LINES:
            raise SpecialCornerSettingsError(field, f"{field} は{MAX_PROMPT_LINES}行以内にしてください")
        text = "\n".join(lines)
    else:
        text = text.strip()
        if "\n" in text:
            raise SpecialCornerSettingsError(field, f"{field} に改行は使えません")
    for char in text:
        if char == "\n":
            continue
        if char in FORBIDDEN_CHARS or unicodedata.category(char) in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp"):
            raise SpecialCornerSettingsError(
                field, f"{field} に使えない文字が含まれています（$ ` \\ < > と制御文字は使えません）")
    if not minimum <= len(text) <= maximum:
        raise SpecialCornerSettingsError(field, f"{field} は{minimum}〜{maximum}文字にしてください")
    return text


def validate_settings(data) -> dict:
    """Return normalized settings or raise ``SpecialCornerSettingsError``."""
    if not isinstance(data, dict):
        raise SpecialCornerSettingsError("settings", "settings はオブジェクトで指定してください")
    unknown = sorted(set(data) - set(LIMITS))
    if unknown:
        raise SpecialCornerSettingsError(unknown[0], f"未知の項目です: {unknown[0]}")
    missing = sorted(set(LIMITS) - set(data))
    if missing:
        raise SpecialCornerSettingsError(missing[0], f"{missing[0]} がありません")
    clean = {}
    for field, (minimum, maximum, multiline) in LIMITS.items():
        clean[field] = _clean_text(field, data[field], minimum=minimum, maximum=maximum,
                                   multiline=multiline)
    if not clean["category_id"].isascii() or not clean["category_id"].isdigit():
        raise SpecialCornerSettingsError("category_id", "category_id は数字だけで指定してください")
    return clean


def load_settings(state_dir) -> dict:
    """Saved settings, or the defaults when absent/invalid. Never raises.

    The result always carries ``is_default`` (nothing valid saved) so callers
    can keep the historic Fly Me To The Home! fixed texts for that case.
    """
    path = settings_path(state_dir)
    try:
        raw = path.read_bytes()
        if len(raw) > 65536:
            raise ValueError
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError
        clean = validate_settings({key: data.get(key) for key in LIMITS})
    except (OSError, ValueError):
        return dict(DEFAULTS, is_default=True, updated_at=None)
    updated = data.get("updated_at")
    return dict(clean, is_default=False,
                updated_at=updated if isinstance(updated, (int, float)) else None)


def save_settings(state_dir, data, *, clock=time.time) -> dict:
    """Validate and atomically persist the settings; returns the stored view."""
    from .game_switch import atomic_write_json

    clean = validate_settings(data)
    payload = dict(clean, schema_version=SCHEMA_VERSION, updated_at=clock())
    atomic_write_json(settings_path(state_dir), payload)
    return dict(clean, is_default=False, updated_at=payload["updated_at"])


def validate_query(query) -> str:
    if not isinstance(query, str):
        raise SpecialCornerSettingsError("query", "検索語は文字列で指定してください")
    text = query.strip()
    if not 1 <= len(text) <= QUERY_MAX_CHARS:
        raise SpecialCornerSettingsError("query", f"検索語は1〜{QUERY_MAX_CHARS}文字にしてください")
    if text.startswith("-"):
        raise SpecialCornerSettingsError("query", "検索語は - で始められません")
    for char in text:
        if char in FORBIDDEN_CHARS or unicodedata.category(char) in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp"):
            raise SpecialCornerSettingsError("query", "検索語に使えない文字が含まれています")
    return text


def parse_candidates(stdout: str) -> list[dict]:
    """``<id> | <name>`` lines of the updater's --resolve; anything else is ignored."""
    rows = []
    for line in (stdout or "").splitlines():
        match = _CANDIDATE_LINE.match(line.strip())
        if not match:
            continue
        name = match.group(2).strip()
        if not name or any(char in FORBIDDEN_CHARS or unicodedata.category(char).startswith("C")
                           for char in name):
            continue
        rows.append({"id": match.group(1), "name": name})
        if len(rows) >= MAX_CANDIDATES:
            break
    return rows


def search_categories(g, query, *, run=subprocess.run) -> list[dict]:
    """Twitch category candidates via the reviewed updater's read-only mode.

    Fixed argv (``--resolve <validated query>``), a minimal environment (the
    updater loads its own ``.env``), no shell, bounded time.  stderr is
    discarded: it may echo account details that do not belong in the WebUI.
    """
    from .stream_category import script_path

    text = validate_query(query)
    script = script_path(g)
    if not script.is_file() or not os.access(script, os.X_OK):
        raise CategorySearchError("カテゴリ検索スクリプトが見つかりません")
    env = {"PATH": os.environ.get("PATH") or "/usr/bin:/bin", "LANG": "C.UTF-8"}
    if os.environ.get("HOME"):
        env["HOME"] = os.environ["HOME"]
    try:
        result = run([str(script), "--resolve", text], cwd=str(script.parent), env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                     text=True, timeout=RESOLVE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise CategorySearchError("カテゴリ検索がタイムアウトしました") from None
    except OSError:
        raise CategorySearchError("カテゴリ検索を実行できません") from None
    if result.returncode != 0:
        raise CategorySearchError(f"カテゴリ検索に失敗しました (rc={result.returncode})")
    return parse_candidates(result.stdout)
