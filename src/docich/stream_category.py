"""Keep the stream's category and viewer title on the running game or view.

The reviewed Soren-side script ``update_stream_game.sh`` owns every Twitch
call; this module only decides *when* to run it and with which fixed,
viewer-facing arguments.  No token, channel id, or other secret is read or
passed here: the script loads its own ``.env`` from the Soren root.

Failure is always non-fatal.  A stale category is a cosmetic problem; a game
switch that gets rolled back because Twitch was unreachable is a real one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import GlobalConfig, load_game
from .naming import NameValidationError, validate_game_name
from .procs import user_bus_env
from .special_corner import (
    DEFAULT_CATEGORY_ID as FLYHOME_CATEGORY_ID,
    DEFAULT_CATEGORY_NAME as FLYHOME_CATEGORY_NAME,
    DEFAULT_TITLE_SUFFIX as FLYHOME_TITLE_SUFFIX,
)

SCRIPT_NAME = "update_stream_game.sh"
LOG_NAME = "stream-game.log"
TITLE_SYNC_SKIP_REASONS = frozenset({
    "category_not_configured", "updater_missing", "dispatch_failed",
})

# Reconciliation of metadata that no switch commit owns (#2673).  A game
# switch announces post-commit, so a path that never commits -- a failed
# restore, a manual ``failed -> idle`` repair, a re-started background game,
# or a corner stop that returns to no active seat -- leaves the previous
# corner's title and category on air.  The rotation tick re-announces only
# when the runtime it can see differs from the last one it asked for.
RECONCILE_STATE_NAME = "stream_metadata_last.json"
RECONCILE_SCHEMA = 1
# A background game counts as running only while its state file is being
# rewritten; a parked loop stops updating it.
BACKGROUND_STATE_FRESH_S = 120.0
BACKGROUND_GAME = "sorengame"
# A failed announce is retried only after this backoff, so an unreachable
# platform cannot turn a one-minute tick into a request storm.
RECONCILE_RETRY_BACKOFF_S = 300.0

# ``paper-view`` is a synthetic program view, not a game in ``config/games``.
# Use Twitch's technology category instead of leaving the category of the game
# that was displaced behind.
PAPER_CATEGORY_ID = "509670"
PAPER_CATEGORY_NAME = "Science & Technology"

# ``mystery-view`` (AI探偵ミステリー, #684) is likewise synthetic.  The corner is
# a participatory talk show rather than a playable game, so it follows the
# Just Chatting category for the 30 minutes it owns the stream (2026-10-21
# owner request: the title/category must follow the corner).  Never leave the
# displaced game's metadata on air while the case board is showing.
MYSTERY_VIEW_NAME = "mystery-view"
MYSTERY_CATEGORY_ID = "509658"
MYSTERY_CATEGORY_NAME = "Just Chatting"

# ``external-video-view`` (the OBS-sourced special corner) is likewise
# synthetic.  Its title, suffix and category are operator settings
# (``special_corner.json``, edited in the WebUI); the FLYHOME_* values are the
# backward-compatible defaults used until settings are saved.
SPECIAL_VIEW_NAME = FLYHOME_VIEW_NAME = "external-video-view"

# Public viewer labels only: never use operational briefs, chart versions,
# paths or generated/private text to compose an automatic broadcast title.
VIEWER_GAME_NAMES = {
    "sorengame": "ソ連ゲーム", "soren91": "ソ連ゲーム91",
    "hanjuku-hero": "半熟英雄", "nethack": "NetHack",
    "bastet": "Bastet", "gnurobots": "GNU Robots", "moon-buggy": "Moon Buggy",
    "ninvaders": "Space Invaders", "nsnake": "Snake",
    "pacman4console": "Pac-Man", "robots": "Robots",
    "paper-view": "ペーパートレード",
    MYSTERY_VIEW_NAME: "AI探偵ミステリー",
    FLYHOME_VIEW_NAME: "Fly Me To The Home!",
}


def viewer_title_args(game: str, g: GlobalConfig | None = None) -> list[str]:
    """Explicit nonempty text prevents updater ops-brief/env fallbacks.

    The reviewed Soren updater still owns the date-based [dayN] prefix and
    Twitch's length limit. Unknown catalog games use only a validated id.
    """
    game = validate_game_name(game)
    label = VIEWER_GAME_NAMES.get(game, game)
    from .stream_title_context import challenge_phrase
    suffix = FLYHOME_TITLE_SUFFIX if game == SPECIAL_VIEW_NAME else challenge_phrase(game)
    if g is not None and game == SPECIAL_VIEW_NAME:
        settings = _special_settings(g)
        return ["--activity", settings["title"], "--strategy", settings["title_suffix"]]
    if g is not None:
        from .stream_title_context import progress_phrase
        from .trading.soren_output import resolve_soren_root

        suffix = progress_phrase(game, Path(g.state_dir), resolve_soren_root(g)) or suffix
    return ["--activity", label, "--strategy", suffix]


def _special_settings(g: GlobalConfig) -> dict:
    from .special_corner import load_settings

    return load_settings(g.state_dir)


class StreamCategoryError(RuntimeError):
    """The category update could not even be attempted."""


def script_path(g: GlobalConfig) -> Path:
    """Path of the reviewed Soren-side updater (may not exist)."""
    from .trading.soren_output import resolve_soren_root

    return resolve_soren_root(g) / SCRIPT_NAME


def twitch_category(g: GlobalConfig, game: str) -> str | None:
    """The game's declared Twitch category id, or ``None`` when it has none.

    Games without a ``[twitch]`` table (or with an empty id) are simply not
    announced; the current category is left alone rather than guessed at.
    """
    try:
        loaded = load_game(g, game)
    except Exception:
        return None
    raw = loaded.raw.get("twitch", {}) if isinstance(loaded.raw, dict) else {}
    if not isinstance(raw, dict):
        return None
    category_id = raw.get("category_id")
    if not isinstance(category_id, str) or not category_id.strip():
        return None
    return category_id.strip()


def _submit_transient(child_argv: list[str], *, cwd: Path, log_path: Path) -> None:
    """Submit the updater as an independent user unit.

    The corner services are ``KillMode=control-group`` oneshots: a plain child
    is killed when the tick exits, which is exactly when the end-of-corner
    restore runs (the start-of-corner update survives because the tick keeps
    running for the whole corner).  An independent transient unit outlives the
    tick, like the improvement job's submission.
    """
    import uuid

    command = [
        "systemd-run", "--user", "--quiet", "--collect",
        f"--unit=docich-stream-category-{uuid.uuid4().hex}",
        "--property=Type=exec",
        "--property=RuntimeMaxSec=120",
        "--property=TimeoutStopSec=15",
        "--property=UMask=0077",
        f"--working-directory={Path(cwd).resolve()}",
        f"--property=StandardOutput=append:{Path(log_path).resolve()}",
        f"--property=StandardError=append:{Path(log_path).resolve()}",
    ]
    if os.environ.get("PATH"):
        command.append(f"--setenv=PATH={os.environ['PATH']}")
    try:
        submitted = subprocess.run(
            [*command, "--", *child_argv],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            # The tick unit does not reliably carry the user-bus environment a
            # timer-launched service needs for systemd-run --user (#947).
            env=user_bus_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise StreamCategoryError(f"{SCRIPT_NAME} の投入がタイムアウトしました") from exc
    except OSError as exc:
        raise StreamCategoryError(f"{SCRIPT_NAME} を投入できません: {exc}") from exc
    if submitted.returncode != 0:
        detail = (submitted.stderr or "").strip().replace("\n", " ")[:200]
        raise StreamCategoryError(
            f"{SCRIPT_NAME} の投入に失敗しました (rc={submitted.returncode}): {detail}"
        )


def _spawn(argv: list[str], *, cwd: Path, log_path: Path) -> None:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(log_path.parent, 0o700)
    # The child is detached and its output kept in a private log: the switch
    # must not wait on a Twitch API round trip, and the log can contain the
    # stream title, which is not something to put on a shared stream.  Keep
    # detached updates FIFO-ordered so a slow Snake request cannot overwrite a
    # later Soren restore.
    runner = Path(__file__).with_name("stream_category_runner.py")
    child_argv = list(argv)
    if runner.is_file():
        child_argv = [
            sys.executable,
            str(runner),
            str(log_path.parent / "stream-category.lock"),
            "--",
            *child_argv,
        ]
    if sys.platform == "linux" and os.environ.get("INVOCATION_ID"):
        _submit_transient(child_argv, cwd=Path(cwd), log_path=log_path)
        return
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "ab", closefd=True) as log:
            subprocess.Popen(
                child_argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(cwd),
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        raise StreamCategoryError(f"{SCRIPT_NAME} を起動できません: {exc}") from exc


def _record_title_sync_skip(g: GlobalConfig, reason: str) -> None:
    """Best-effort fixed-reason event for the owner-only Soren journal."""
    if reason not in TITLE_SYNC_SKIP_REASONS:
        return
    try:
        root = script_path(g).parent
        helper = root / "lib" / "stream_title_sync.py"
        if not helper.is_file():
            return
        subprocess.run(
            [sys.executable, str(helper), "--record-skip", reason],
            cwd=str(root),
            env={"PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except Exception:
        # Diagnostics must never affect a committed game/category transition.
        return


def _announce_explicit_category(
    g: GlobalConfig,
    *,
    category_id: str,
    category_name: str = "",
    view: str = "paper-view",
    title_args: list[str] | None = None,
    spawn=None,
) -> bool:
    """Ask the Soren updater to use a category without a game TOML.

    Synthetic program views such as ``paper-view`` intentionally do not live
    in the game catalog.  The reviewed updater already supports explicit
    category arguments, so keep that boundary instead of adding a fake game
    definition that could be selected by the game switcher.
    """
    if not isinstance(category_id, str) or not category_id.strip().isdigit():
        raise StreamCategoryError("TwitchカテゴリIDが不正です")
    script = script_path(g)
    if not script.is_file() or not os.access(script, os.X_OK):
        _record_title_sync_skip(g, "updater_missing")
        raise StreamCategoryError(f"{SCRIPT_NAME} が見つかりません: {script}")
    argv = [str(script), "--category-id", category_id.strip()]
    if category_name:
        argv.extend(["--category-name", str(category_name)])
    argv.extend(viewer_title_args(view, g) if title_args is None else title_args)
    try:
        (spawn or _spawn)(
            argv,
            cwd=script.parent,
            log_path=Path(g.state_dir) / "logs" / LOG_NAME,
        )
    except Exception:
        _record_title_sync_skip(g, "dispatch_failed")
        raise
    return True


def announce_stream_game(g: GlobalConfig, game: str, *, spawn=None) -> bool:
    """Ask the Soren updater to follow ``game``; ``False`` when skipped.

    Fixed arguments only: the game name (validated) and the reviewed games
    directory.  ``--games-dir`` is required because the script defaults to the
    Soren checkout, while the game definitions live in this repository.
    """
    try:
        game = validate_game_name(game)
    except NameValidationError as exc:
        raise StreamCategoryError(f"ゲーム名が不正です: {exc}") from exc
    if twitch_category(g, game) is None:
        _record_title_sync_skip(g, "category_not_configured")
        return False
    script = script_path(g)
    if not script.is_file() or not os.access(script, os.X_OK):
        _record_title_sync_skip(g, "updater_missing")
        raise StreamCategoryError(f"{SCRIPT_NAME} が見つかりません: {script}")
    argv = [
        str(script),
        "--game",
        game,
        "--games-dir",
        str(Path(g.games_dir).resolve()),
        *viewer_title_args(game, g),
    ]
    try:
        (spawn or _spawn)(
            argv,
            cwd=script.parent,
            log_path=Path(g.state_dir) / "logs" / LOG_NAME,
        )
    except Exception:
        _record_title_sync_skip(g, "dispatch_failed")
        raise
    _record_announced(g, game)
    return True


def _announced_state_path(g: GlobalConfig) -> Path:
    return Path(g.state_dir) / RECONCILE_STATE_NAME


def _record_announced(g: GlobalConfig, game: str) -> None:
    """Remember which runtime the stream metadata was last pointed at.

    Best-effort: this record only dedupes reconciliation, so a failed write
    must never disturb the announce that already happened.
    """
    _write_announce_record(g, game, "announced")


def _record_dispatch_failure(g: GlobalConfig, game: str) -> None:
    """Remember a failed attempt so reconciliation backs off instead of spamming."""
    _write_announce_record(g, game, "failed")


def _write_announce_record(g: GlobalConfig, game: str, status: str) -> None:
    if not isinstance(game, str) or not game:
        return
    try:
        from .game_switch import atomic_write_json

        atomic_write_json(
            _announced_state_path(g),
            {"schema": RECONCILE_SCHEMA, "game": game, "status": status, "at": time.time()},
        )
    except Exception:
        return


def _announce_record(g: GlobalConfig) -> dict | None:
    try:
        data = json.loads(_announced_state_path(g).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != RECONCILE_SCHEMA:
        return None
    return data


def last_announced_game(g: GlobalConfig) -> str | None:
    """The last runtime announced to the platforms, or ``None`` when unknown."""
    data = _announce_record(g)
    if data is None:
        return None
    game = data.get("game")
    return game if isinstance(game, str) and game else None


def _plane_active_game(state: dict) -> str | None:
    """The committed game/view of a ``ready`` canonical state."""
    active = state.get("active")
    if not isinstance(active, dict):
        return None
    game = active.get("game")
    return game if isinstance(game, str) and game else None


def _live_background_game(g: GlobalConfig) -> str | None:
    """The Soren game that keeps writing its state file, if any.

    Only consulted while the plane owns no seat: a running corner parks the
    loop, so a fresh state file there would otherwise be misattributed.
    """
    try:
        from .trading.soren_output import resolve_soren_root

        root = resolve_soren_root(g)
    except Exception:
        return None
    now = time.time()
    for candidate in (root / "game_state.json", root / "tmp" / "state" / "game_state.json"):
        try:
            fresh = now - candidate.stat().st_mtime <= BACKGROUND_STATE_FRESH_S
        except OSError:
            continue
        if fresh:
            return BACKGROUND_GAME
    return None


def intended_stream_game(g: GlobalConfig) -> str | None:
    """The runtime the stream should currently describe, or ``None``.

    ``ready`` defers to the committed seat.  ``idle`` is the state a failed
    restore or a corner stop leaves behind, and it is exactly where a live
    background game has no announcement path (#2673).  Every other phase is a
    transition in flight, so reconciliation stays out of the way.
    """
    try:
        state = json.loads(
            (Path(g.state_dir) / "game_switch.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    phase = state.get("phase")
    if phase == "ready":
        return _plane_active_game(state)
    if phase == "idle":
        return _live_background_game(g)
    return None


def reconcile_stream_metadata(g: GlobalConfig, *, spawn=None) -> bool:
    """Re-announce metadata when the visible runtime diverged from the last one.

    Runs from the corner-rotation tick.  Returns ``True`` only when a new
    announce was dispatched.  Never raises: a cosmetic mismatch must not take
    the rotation down.  A failed attempt is remembered so a broken updater is
    retried after a backoff instead of once per tick.
    """
    try:
        intended = intended_stream_game(g)
        if intended is None:
            return False
        record = _announce_record(g)
        if record is not None and record.get("game") == intended:
            if record.get("status") != "failed":
                return False
            last = record.get("at")
            if (isinstance(last, (int, float)) and not isinstance(last, bool)
                    and time.time() - float(last) < RECONCILE_RETRY_BACKOFF_S):
                return False
        if twitch_category(g, intended) is None:
            return False
        try:
            announced = announce_stream_game(g, intended, spawn=spawn)
        except Exception:
            _record_dispatch_failure(g, intended)
            return False
        return announced
    except Exception:
        return False


def announce_stream_paper(g: GlobalConfig, *, spawn=None) -> bool:
    """Move the stream to the non-game category used by the PAPER view."""
    return _announce_explicit_category(
        g,
        category_id=PAPER_CATEGORY_ID,
        category_name=PAPER_CATEGORY_NAME,
        spawn=spawn,
    )


def announce_stream_mystery(g: GlobalConfig, *, spawn=None) -> bool:
    """Move the stream to the AI探偵ミステリー title and Just Chatting category.

    The case board is a participatory talk show, not a playable game, so it has
    no ``config/games`` row to resolve.  The explicit-category path keeps the
    displaced game's metadata from staying on air and the normal restore hook
    announces the previous game again when the corner ends.
    """
    return _announce_explicit_category(
        g,
        category_id=MYSTERY_CATEGORY_ID,
        category_name=MYSTERY_CATEGORY_NAME,
        view=MYSTERY_VIEW_NAME,
        spawn=spawn,
    )


def announce_stream_special(g: GlobalConfig, *, spawn=None, settings=None) -> bool:
    """Move the stream to the special corner's configured category and title.

    Read at commit time, so settings saved while waiting apply to this start;
    the restore goes back through the previous game's own announcement.
    """
    if settings is None:
        settings = _special_settings(g)
    return _announce_explicit_category(
        g,
        category_id=settings["category_id"],
        category_name=settings["category_name"],
        view=SPECIAL_VIEW_NAME,
        title_args=["--activity", settings["title"], "--strategy", settings["title_suffix"]],
        spawn=spawn,
    )


# Backward-compatible name (the corner was Fly Me To The Home! only).
announce_stream_flyhome = announce_stream_special


def announce_running_view(g: GlobalConfig, game: str, *, spawn=None) -> bool:
    """Follow whatever a committed game switch put on screen.

    The ``GameSwitchCoordinator`` commits a real game or a synthetic program
    view such as ``paper-view``.  Routing both through one function lets the
    coordinator own the *when* and keeps the category from being left on the
    game a killed corner displaced.
    """
    from .adapters.program import PAPER_VIEW_NAME

    if game == PAPER_VIEW_NAME:
        announced = announce_stream_paper(g, spawn=spawn)
    elif game == MYSTERY_VIEW_NAME:
        announced = announce_stream_mystery(g, spawn=spawn)
    elif game == SPECIAL_VIEW_NAME:
        announced = announce_stream_special(g, spawn=spawn)
    else:
        announced = announce_stream_game(g, game, spawn=spawn)
    if announced:
        # The reconciliation hook compares against this record, so every
        # commit-driven announce must update it too (view paths record here;
        # games already recorded inside ``announce_stream_game``).
        _record_announced(g, game)
    return announced


def commit_hook(g: GlobalConfig, *, spawn=None):
    """Build the ``GameSwitchCoordinator`` post-commit hook for this config.

    The coordinator calls the returned callable with the game it just made
    active.  An error here is non-fatal by construction: the coordinator logs
    it and keeps the committed switch.
    """

    def hook(game: str) -> None:
        announce_running_view(g, game, spawn=spawn)

    return hook
