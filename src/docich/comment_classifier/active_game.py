"""Optional active-game hint for comment classification (#1243 PR-1).

Jev never receives raw game state, screenshots, scores, ranks, personal data,
prompts, history, or arbitrary game-owned strings. When the host knows an
active game session is running, it may attach a fixed-vocabulary hint
(reviewed enum kind + interaction kind) so short deictic game remarks
(``右じゃない?``) are not misread as general chitchat.

Body-only classification wins whenever the text alone suffices, and explicit
non-game topics (weather, politics, songs, stream bugs) are never pulled into
game categories. A missing, stale, or unknown hint degrades to body-only
classification; it is never an error.
"""
from __future__ import annotations

import re

ENABLE_ENV = "COMMENT_ACTIVE_GAME_ENABLED"
KIND_ENV = "COMMENT_ACTIVE_GAME_KIND"
INTERACTION_ENV = "COMMENT_ACTIVE_GAME_INTERACTION"

# Reviewed allowlist. Public display names live with the game-owned adapter
# (``docich.comment.sorengame_context.GAME_BLURBS``); only these keys may ever
# reach the classifier. Anything else is treated as stale/unknown.
GAME_KINDS = frozenset({"sorengame", "robots", "nethack", "hanjuku-hero"})
INTERACTION_KINDS = frozenset({"board", "action", "menu", "unknown"})

# Explicit non-game topics that must never be reinterpreted as gameplay,
# checked before any deictic/direction rule. ``右派/左派`` contain direction
# characters but are politics, not board positions.
_NON_GAME_RE = re.compile(
    r"天気|気温|雨|雪|晴|台風|地震|"
    r"右派|左派|政党|政治|選挙|内閣|総理|大統領|議員|"
    r"歌|うた|カラオケ|"
    r"配信|映像|音|声が|フリーズ|止まっ|固ま|バグ|不具合|OBS|"
    r"ワーカー|worker|分類器|classifier"
)
# Short deictic / direction / placement / operation references. Long texts are
# left to body-only classification so general questions merely containing 右
# are not hijacked.
_DEICTIC_QUESTION_RE = re.compile(r"右|左|上|下|そこ|あそこ|こっち|そっち|あっち|今の|これ|それ")
_PLACEMENT_RE = re.compile(r"置|動か|狙|避け|守|攻め|合体|連鎖|積")
_CORRECTION_RE = re.compile(r"違う|ちがう|ダメ|だめ|間違")
_QUESTION_RE = re.compile(r"[?？]")

MAX_HINT_TEXT_CHARS = 80


def settings(env) -> tuple[bool, str | None, str]:
    """Read the host-supplied hint; unknown kinds degrade to inactive.

    Returns ``(enabled, kind_or_None, interaction)``. Only an explicit ``1``
    enables the hint; an unlisted kind (stale/unknown game) returns inactive
    instead of raising. An unlisted interaction degrades to ``unknown``.
    """
    flag = env.get(ENABLE_ENV, "0")
    if type(flag) is not str or flag not in {"0", "1"}:
        raise ValueError("invalid_config")
    if flag == "0":
        return False, None, "unknown"
    kind = env.get(KIND_ENV, "")
    if type(kind) is not str or kind not in GAME_KINDS:
        return False, None, "unknown"
    interaction = env.get(INTERACTION_ENV, "unknown")
    if type(interaction) is not str or interaction not in INTERACTION_KINDS:
        interaction = "unknown"
    return True, kind, interaction


def hint(kind: str, interaction: str = "unknown") -> dict | None:
    """Build a validated hint mapping, or None for stale/unknown input."""
    if type(kind) is not str or kind not in GAME_KINDS:
        return None
    if type(interaction) is not str or interaction not in INTERACTION_KINDS:
        interaction = "unknown"
    return {"game_active": True, "active_game_kind": kind,
            "interaction_kind": interaction}


def check_hint(value) -> dict | None:
    """Strictly validate a caller-supplied hint mapping; reject anything else."""
    if not isinstance(value, dict) or set(value) != {
            "game_active", "active_game_kind", "interaction_kind"}:
        raise ValueError("invalid_config")
    if value["game_active"] is not True:
        raise ValueError("invalid_config")
    built = hint(value["active_game_kind"], value["interaction_kind"])
    if built is None:
        raise ValueError("invalid_config")
    return built


def sentence(active_hint: dict) -> str:
    """Fixed-vocabulary Jev instruction fragment for an active hint."""
    return (
        f'An active game session is running (kind: {active_hint["active_game_kind"]}, '
        f'interaction: {active_hint["interaction_kind"]}). Short deictic, direction, '
        'placement, or operation references (there/that/right/left/place) may refer '
        'to the current game even without an explicit game name. Topics that are '
        'explicitly non-game (weather, politics, songs, stream bugs) stay non-game. '
        'Do not invent game facts from an ambiguous referent.'
    )


def suggest_category(text: str) -> str | None:
    """Heuristic game-intent suggestion under an active hint, else None.

    Only called when body-only classification landed on a non-game category
    (``general_question`` / ``chitchat`` / ``other``); it never overrides an
    explicit game, notification, bug-report, or advice category.
    """
    if not isinstance(text, str):
        return None
    if len(text) > MAX_HINT_TEXT_CHARS:
        return None
    if _NON_GAME_RE.search(text):
        return None
    if _PLACEMENT_RE.search(text) and _QUESTION_RE.search(text):
        return "strategy_advice"
    if _QUESTION_RE.search(text) and _DEICTIC_QUESTION_RE.search(text):
        return "game_question"
    if _CORRECTION_RE.search(text) and re.search(r"そこ|それ|これ|あそこ", text):
        return "game_status"
    return None
