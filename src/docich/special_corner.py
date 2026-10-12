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
# Rendered comment-reply note, re-read by Soren's comment.sh on every reply and
# used only while the canonical active game is the special view.
MEMO_FILE = "special_corner_memo.md"
VIEW_NAME = "external-video-view"
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
    write_comment_memo(state_dir, clean)
    return dict(clean, is_default=False, updated_at=payload["updated_at"])


def render_comment_memo(settings) -> str:
    """The comment-reply note (a ``comment_ui_memo`` style bullet).

    Only validated settings text is embedded (no ``$``/backquote/markup), and
    the gate sentence keeps it inert if it were ever shown on another screen.
    """
    lines = [f"- 【いまのメイン画面が {VIEW_NAME}（特別コーナー）のときだけ有効】"
             f"特別コーナー「{settings['title']}」の説明"]
    lines += ["  " + line for line in settings["comment_prompt"].split("\n") if line.strip()]
    lines += [
        "  ソ連ゲームのスコア・盤面・戦略改善の話ではない（その話題を聞かれたら、ソ連ゲームの話として別に答えてよい）",
        "  このメモに書いていないこと（何面か・操作・仕掛け・残り時間など）は推測で補わず、「ちょっと分からないな」と正直に答える。"
        "そのうえで「画面を見ていてね」「応援しているよ」と視聴者と一緒に見守る言い方にする",
        f"  いまのメイン画面が {VIEW_NAME} 以外のときは、この節を無視する",
    ]
    return "\n".join(lines) + "\n"


def write_comment_memo(state_dir, settings=None) -> Path:
    """Atomically (re)write the hot-read memo from the given or saved settings."""
    import tempfile

    settings = settings or load_settings(state_dir)
    path = Path(state_dir) / MEMO_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{MEMO_FILE}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(render_comment_memo(settings))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


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


# --- Manual start from the WebUI (SRT receiver + FIFO reservation) ---------
#
# The WebUI only drives the reviewed ``external-video-corner`` CLI: ``prepare``
# opens the SRT listener, and the runner (``start --wait-for-idle``) waits in
# the shared program FIFO.  Both run as independent ``systemd-run --user``
# units so a WebUI restart (every deploy) never kills them.

CORNER_FILE = "external_video_corner.json"
PAUSE_MARKER = Path("corners") / "external-video.paused"
# Same values as the hand-run commands: the operator's End button is the real
# finish; these are the technical ceilings (receiver 25 h, display 24 h).
RECEIVER_WAIT_MINUTES = 25 * 60
RUNNER_DURATION_MINUTES = 24 * 60
RUNNER_WAIT_MINUTES = 120
# waiting + display + restore budget for the runner unit's RuntimeMaxSec.
RUNNER_RUNTIME_MAX_SEC = (RUNNER_WAIT_MINUTES + RUNNER_DURATION_MINUTES + 30) * 60
CLI_TIMEOUT_S = 30
CORNER_BUSY = frozenset({"waiting", "starting", "active", "restoring", "failed"})


class SpecialCornerControlError(RuntimeError):
    """A manual start/receiver action was refused; ``code`` is a fixed reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def configured_listen_ip(g) -> str:
    """``[external_video_corner] listen_ip`` (a validated Tailscale IPv4)."""
    import tomllib

    from .external_video_receiver import ExternalVideoError, listen_ip

    try:
        raw = tomllib.loads(Path(g.config_path).read_text(encoding="utf-8"))
        value = raw.get("external_video_corner", {}).get("listen_ip", "")
        return listen_ip(value)
    except (OSError, ValueError, AttributeError, ExternalVideoError):
        raise SpecialCornerControlError(
            "listen_ip_not_configured",
            "config の [external_video_corner] listen_ip（Tailscale IPv4）が未設定です") from None


def corner_status(g) -> str:
    try:
        data = json.loads((Path(g.state_dir) / CORNER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "idle"
    status = data.get("status") if isinstance(data, dict) else None
    return status[:32] if isinstance(status, str) and status else "idle"


def receiver_view(g, *, now=None) -> dict:
    """Receiver projection for the WebUI: no ids, IPs, pids or paths."""
    from .external_video_receiver import ExternalVideoError, read_receiver

    clock = time.time() if now is None else now
    try:
        state = read_receiver(g, now=clock)
    except (ExternalVideoError, OSError):
        return {"state": "none", "audio": False, "expires_in_s": None}
    status = state.get("status")
    expires_in = max(0, int(state["expires_at"] - clock))
    if state["alive"] and status == "receiving":
        if state["fresh"]:
            label = "receiving_audio" if state.get("audio_present") else "receiving"
        else:
            label = "stale"
    elif state["alive"]:
        label = "waiting"
    elif status == "launching" and clock < state["expires_at"]:
        label = "launching"
    else:
        label, expires_in = "stopped", None
    return {"state": label, "audio": bool(state.get("audio_present")), "expires_in_s": expires_in}


def control_view(g, *, now=None) -> dict:
    """What the WebUI may offer right now (the CLI still re-checks everything)."""
    status = corner_status(g)
    receiver = receiver_view(g, now=now)
    busy = status in CORNER_BUSY
    paused = (Path(g.state_dir) / PAUSE_MARKER).exists()
    return {
        "receiver": receiver,
        "paused": paused,
        "can_prepare": not busy and not paused and receiver["state"] in ("none", "stopped"),
        "can_start": not busy and not paused and receiver["state"] == "receiving_audio",
        "can_stop_receiver": not busy and receiver["state"] in ("launching", "waiting", "receiving",
                                                                 "receiving_audio", "stale"),
    }


def _cli(g) -> list[str]:
    repo = Path(g.repo_root).resolve()
    # Absolute config: a relative one resolves against the unit's cwd and has
    # already failed once in production.
    return [str(repo / "bin" / "docich"), "--config", str(Path(g.config_path).resolve()),
            "external-video-corner"]


def _run_cli(g, args, *, run):
    from .procs import user_bus_env

    try:
        result = run([*_cli(g), *args], cwd=str(Path(g.repo_root).resolve()), env=user_bus_env(),
                     stdin=subprocess.DEVNULL, capture_output=True, text=True,
                     timeout=CLI_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise SpecialCornerControlError("cli_timeout", "受信の操作がタイムアウトしました") from None
    except OSError:
        raise SpecialCornerControlError("cli_failed", "受信の操作を実行できません") from None
    if result.returncode != 0:
        lines = (result.stderr or "").strip().splitlines()
        raise SpecialCornerControlError("refused", (lines[-1] if lines else f"rc={result.returncode}")[:200])
    return result


def prepare_receiver(g, *, run=subprocess.run, now=None) -> dict:
    """Open the SRT listener (``prepare``) for OBS; returns the new projection."""
    view = control_view(g, now=now)
    if view["paused"]:
        raise SpecialCornerControlError("paused", "外部映像コーナーが休止設定です")
    if not view["can_prepare"]:
        raise SpecialCornerControlError("not_ready", "受信を新しく開始できる状態ではありません")
    ip = configured_listen_ip(g)
    _run_cli(g, ["prepare", "--listen-ip", ip, "--wait-minutes", str(RECEIVER_WAIT_MINUTES)], run=run)
    return receiver_view(g)


def stop_receiver(g, *, run=subprocess.run, now=None) -> dict:
    """Stop a prepared receiver that no corner is using (or will use)."""
    if not control_view(g, now=now)["can_stop_receiver"]:
        raise SpecialCornerControlError("not_ready", "止められる受信がありません（コーナー実行中・予約中は止めません）")
    _run_cli(g, ["receiver-stop"], run=run)
    return receiver_view(g)


def launch_corner(g, *, run=subprocess.run, now=None, unit_suffix=None) -> str:
    """Reserve the corner: ``start --wait-for-idle`` in its own user unit.

    Refused unless OBS video *and* audio are being decoded now, so a forgotten
    OBS never holds a FIFO turn (the runner also cancels at its turn when the
    source is gone for 30 s).  Returns the unit name.
    """
    from .procs import user_bus_env

    view = control_view(g, now=now)
    if view["paused"]:
        raise SpecialCornerControlError("paused", "外部映像コーナーが休止設定です")
    if corner_status(g) in CORNER_BUSY:
        raise SpecialCornerControlError("busy", "特別コーナーはすでに予約・実行中です")
    if not view["can_start"]:
        raise SpecialCornerControlError("receiver_not_ready",
                                        "OBS の映像と音声を受信中になってから開始してください")
    import uuid

    try:
        # The memo must exist even when the operator never saved settings.
        write_comment_memo(g.state_dir)
    except OSError:
        pass
    unit = "docich-external-video-corner-" + (unit_suffix or str(uuid.uuid4()))
    repo = Path(g.repo_root).resolve()
    command = ["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
               f"--property=RuntimeMaxSec={RUNNER_RUNTIME_MAX_SEC}",
               "--property=KillMode=control-group", f"--working-directory={repo}",
               *_cli(g), "start", "--duration-minutes", str(RUNNER_DURATION_MINUTES),
               "--wait-for-idle", "--wait-minutes", str(RUNNER_WAIT_MINUTES)]
    try:
        result = run(command, env=user_bus_env(), stdin=subprocess.DEVNULL, capture_output=True,
                     text=True, timeout=15, check=False)
    except (subprocess.TimeoutExpired, OSError):
        raise SpecialCornerControlError("launch_failed", "特別コーナーの起動を依頼できませんでした") from None
    if result.returncode != 0:
        raise SpecialCornerControlError("launch_failed",
                                        f"特別コーナーの起動を依頼できませんでした (rc={result.returncode})")
    return unit
