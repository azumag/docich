"""Keep the stream's category on whichever game or view is actually running.

The reviewed Soren-side script ``update_stream_game.sh`` owns every Twitch
call; this module only decides *when* to run it and with which fixed,
category-only arguments.  No token, channel id, or other secret is read or
passed here: the script loads its own ``.env`` from the Soren root.

Failure is always non-fatal.  A stale category is a cosmetic problem; a game
switch that gets rolled back because Twitch was unreachable is a real one.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import GlobalConfig, load_game
from .naming import NameValidationError, validate_game_name

SCRIPT_NAME = "update_stream_game.sh"
LOG_NAME = "stream-game.log"

# ``paper-view`` is a synthetic program view, not a game in ``config/games``.
# Use Twitch's technology category instead of leaving the category of the game
# that was displaced behind.  The title is intentionally left unchanged.
PAPER_CATEGORY_ID = "509670"
PAPER_CATEGORY_NAME = "Science & Technology"


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


def _spawn(argv: list[str], *, cwd: Path, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(log_path.parent, 0o700)
    # The child is detached and its output kept in a private log: the switch
    # must not wait on a Twitch API round trip, and the log can contain the
    # stream title, which is not something to put on a shared stream.
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "ab", closefd=True) as log:
            subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(cwd),
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        raise StreamCategoryError(f"{SCRIPT_NAME} を起動できません: {exc}") from exc


def _announce_explicit_category(
    g: GlobalConfig,
    *,
    category_id: str,
    category_name: str = "",
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
        raise StreamCategoryError(f"{SCRIPT_NAME} が見つかりません: {script}")
    argv = [str(script), "--category-id", category_id.strip(), "--category-only"]
    if category_name:
        argv.extend(["--category-name", str(category_name)])
    (spawn or _spawn)(
        argv,
        cwd=script.parent,
        log_path=Path(g.state_dir) / "logs" / LOG_NAME,
    )
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
        return False
    script = script_path(g)
    if not script.is_file() or not os.access(script, os.X_OK):
        raise StreamCategoryError(f"{SCRIPT_NAME} が見つかりません: {script}")
    argv = [
        str(script),
        "--game",
        game,
        "--games-dir",
        str(Path(g.games_dir).resolve()),
        "--category-only",
    ]
    (spawn or _spawn)(
        argv,
        cwd=script.parent,
        log_path=Path(g.state_dir) / "logs" / LOG_NAME,
    )
    return True


def announce_stream_paper(g: GlobalConfig, *, spawn=None) -> bool:
    """Move the stream to the non-game category used by the PAPER view."""
    return _announce_explicit_category(
        g,
        category_id=PAPER_CATEGORY_ID,
        category_name=PAPER_CATEGORY_NAME,
        spawn=spawn,
    )
