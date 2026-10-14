"""Validation helpers for game-switch filesystem and tmux boundaries."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


GAME_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RUNTIME_ID_PATTERN = re.compile(r"^g([1-9][0-9]*)-([a-f0-9]{6,32})$")
TMUX_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
TMUX_TARGET_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9_-]{0,127}(?::[a-z0-9][a-z0-9_-]{0,127})?$"
)
TMUX_WINDOW_ID_PATTERN = re.compile(r"^@[0-9]+$")
TMUX_SESSION_ID_PATTERN = re.compile(r"^\$[0-9]+$")


class NameValidationError(ValueError):
    """Raised when user-controlled text is unsafe as a runtime identifier."""


def validate_game_name(name: str) -> str:
    if not isinstance(name, str) or GAME_NAME_PATTERN.fullmatch(name) is None:
        raise NameValidationError(
            "ゲーム名は英小文字または数字で始まる64文字以内の "
            "英小文字・数字・_・- で指定してください"
        )
    return name


def validate_runtime_id(runtime_id: str) -> str:
    if not isinstance(runtime_id, str) or RUNTIME_ID_PATTERN.fullmatch(runtime_id) is None:
        raise NameValidationError("runtime_id の形式が不正です")
    return runtime_id


def runtime_id_generation(runtime_id: str) -> int:
    runtime_id = validate_runtime_id(runtime_id)
    match = RUNTIME_ID_PATTERN.fullmatch(runtime_id)
    assert match is not None
    return int(match.group(1))


def validate_tmux_name(name: str) -> str:
    if not isinstance(name, str) or TMUX_NAME_PATTERN.fullmatch(name) is None:
        raise NameValidationError("tmux名の形式が不正です")
    return name


def validate_tmux_target(target: str) -> str:
    if not isinstance(target, str) or TMUX_TARGET_PATTERN.fullmatch(target) is None:
        raise NameValidationError("tmux target の形式が不正です")
    return target


def validate_tmux_window_id(window_id: str) -> str:
    if not isinstance(window_id, str) or TMUX_WINDOW_ID_PATTERN.fullmatch(window_id) is None:
        raise NameValidationError("tmux window IDの形式が不正です")
    return window_id


def validate_tmux_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or TMUX_SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise NameValidationError("tmux session IDの形式が不正です")
    return session_id


def validate_tmux_window_ref(target: str) -> str:
    if isinstance(target, str) and TMUX_WINDOW_ID_PATTERN.fullmatch(target) is not None:
        return validate_tmux_window_id(target)
    return validate_tmux_target(target)


def validate_tmux_session_ref(session: str) -> str:
    if isinstance(session, str) and TMUX_SESSION_ID_PATTERN.fullmatch(session) is not None:
        return validate_tmux_session_id(session)
    return validate_tmux_name(session)


def ensure_contained(base: Path, candidate: Path) -> Path:
    """Resolve ``candidate`` and require it to remain below ``base``.

    ``Path.resolve`` follows existing symlinks, so a game definition symlink
    pointing outside ``games_dir`` is rejected as well as ``..`` traversal.
    """

    base_resolved = Path(base).expanduser().resolve()
    candidate_resolved = Path(candidate).expanduser().resolve()
    try:
        candidate_resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise NameValidationError(
            f"パスが許可ディレクトリ外を指しています: {candidate_resolved}"
        ) from exc
    return candidate_resolved


@dataclass(frozen=True)
class RuntimeNames:
    game_window: str
    agent_window: str
    adapter_session: str


def runtime_names(generation: int) -> RuntimeNames:
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise NameValidationError("generation は1以上の整数である必要があります")
    suffix = f"g{generation}"
    return RuntimeNames(
        game_window=validate_tmux_name(f"game-{suffix}"),
        agent_window=validate_tmux_name(f"agent-{suffix}"),
        adapter_session=validate_tmux_name(f"docich-game-{suffix}"),
    )


def runtime_directory(state_dir: Path, runtime_id: str) -> Path:
    validate_runtime_id(runtime_id)
    root = Path(state_dir) / "runtimes"
    return ensure_contained(root, root / runtime_id)
