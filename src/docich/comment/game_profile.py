"""Game-owned comment prompt profile: docich is the single source of truth.

The live comment worker (Soren ``broadcast/comment.sh``) builds its prompt for
the Soviet game. While another game owns the main screen, the Soviet persona
sentence, Soviet score statistics and Soviet UI memo are wrong for the viewer.
This module owns the game-specific replacements; the shell only applies them
through the narrow, fail-open launcher ``bin/docich-comment-profile``.

Only the existing prompt slots are replaced (persona sentence, game state,
UI memo, operational memo). The prompt templates are not touched here.

Every sentence below is either an existing production sentence moved here
(radio persona replacement, non-Soviet game state note) or the explanation of
how the Hanjuku bot works that the owner specified. Nothing about the Hanjuku
screen layout is stated, because it has not been measured.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import paper_corner_profile

SCHEMA = 1
HANJUKU = "hanjuku-hero"
HANJUKU_BLURB = "半熟英雄(SFCエミュレータ)"
PAPER = paper_corner_profile.PAPER_VIEW
DOUSHI_VIEW = "doushi-view"
DOUSHI_BLURB = "同志カード大賞のコーナー（同志カードの成績ランキング）"
DOUSHI_RULES_EN = (
    "There is no separate rules memo for this ranking corner. Use only the measured "
    "ranking facts in the game-state context; never invent card rules or rankings."
)
DOUSHI_RULES_JA = (
    "【同志カード大賞コーナー】\n"
    "このコーナーのカードルールのメモはありません。ゲーム状態メモにある実測ランキングだけを使い、"
    "カードのルールや順位を推測で補わないこと。"
)

# Same replacement lib/radio_game_context.py applies to radio prompts.
PERSONA_REPLACE = (
    ("自分自身がソ連ゲームをプレイしているプレイヤーでもあります。",
     "ゲームの担当は現在のメイン画面に従います。"),
)

# Meaning: the Soviet game's score statistics, board snapshot and reply rules
# must not be presented as the state of the current game.
NON_SOVIET_STATE_SUFFIX = (
    "ソ連ゲームのスコア・盤面・ピース・建国統計はこのゲームの状況ではありません。"
    "現在の戦闘・勝敗・操作の情報はこのメモにはありません。推測して補わないでください。"
)

# The prompt templates carry the Soviet game's rules in this slot by default. No Hanjuku rules text has been
# measured, so the replacement only says that and forbids explaining it with the Soviet rules.
HANJUKU_RULES_EN = (
    "【Current Game Rules (半熟英雄)】\n"
    "There is no rules memo for the current game. Never explain it with the Soviet game's rules "
    "(countries, merging, score). If a viewer asks about its rules and the memo has none, "
    "say you do not have that information instead of guessing."
)
HANJUKU_RULES_JA = (
    "\t【現在のゲームの基本ルール（半熟英雄）】\n"
    "\t※ 現在のゲームの基本ルールのメモはありません。ソ連ゲームの国・併合・得点のルールで説明しないこと。\n"
    "\t※ ルールを聞かれても、メモにないことは推測で答えず、分からないと伝えること。"
)

HANJUKU_HOW_IT_WORKS = (
    "- 【半熟英雄BOTの仕組み】視聴者が半熟のBOTの動き方・AIの使い方（どう動いてるの、AIが操作してるの等）を聞いたときだけ、"
    "次の要点を噛み砕いて答える。このあとに並ぶソ連ゲームの戦略改善・メリケンAIの行は半熟BOTの話ではない",
    "  ・基本は人が決めたルール(ヒューリスティック)で動くBOT。画面を読み取り、決まった手順でボタンを押す",
    "  ・AIはBOTの動作ログを見て定期的に改善するために使う。いきなり大きく変えず、少しずつ直す保守的なやり方",
    "  ・難しい場面ではLLMがその場で判断する仕組みも組み込んだハイブリッド構成。ただしLLMが必要な場面は少ない",
    "  ・判断の理由は最終的にアルゴリズムへ置き換えられるので、この方針にしている",
    "  ・LLMがリアルタイムに判断するとレイテンシ(待ち時間)が問題になるため、いまはこの方式がベター",
)
HANJUKU_LLM_ON = "  ・いまの状態: LLMの動的判断は有効"
HANJUKU_LLM_OFF = "  ・いまの状態: LLMの動的判断は一時的に止めていて、ルールだけで動いている"


REPO_HANJUKU_CONFIG = Path(__file__).resolve().parents[3] / "config" / "games" / "hanjuku-hero.toml"


def default_hanjuku_config() -> Path:
    """CLI default: the env override (tests), else this checkout's config. Callers that must not
    read ``os.environ`` (e.g. a provider with an allowlisted env) pass ``config=`` explicitly."""
    override = os.environ.get("DOCICH_HANJUKU_CONFIG_FILE")
    return Path(override) if override else REPO_HANJUKU_CONFIG


def hanjuku_llm_enabled(path: Path | None = None) -> bool | None:
    """Measured from ``[hanjuku.chart_adjust]``; ``None`` when unreadable (never guess)."""
    import tomllib

    try:
        with (path or default_hanjuku_config()).open("rb") as stream:
            doc = tomllib.load(stream)
        cfg = (doc.get("hanjuku") or {}).get("chart_adjust") or {}
        enabled = cfg.get("enabled", False)
        agents = cfg.get("agents", "")
        if cfg.get("direct_enabled", False) is True:
            agents = cfg.get("direct_agents", "")
        if not isinstance(enabled, bool) or not isinstance(agents, str):
            return None
        return enabled and bool(agents.strip())
    except Exception:
        return None


def hanjuku_how_it_works(config: Path | None = None) -> list[str]:
    lines = list(HANJUKU_HOW_IT_WORKS)
    llm = hanjuku_llm_enabled(config)
    if llm is True:
        lines.append(HANJUKU_LLM_ON)
    elif llm is False:
        lines.append(HANJUKU_LLM_OFF)
    return lines


def non_soviet_game_state(game: str, blurb: str) -> str:
    return f"現在のメイン画面: {game} — {blurb}。" + NON_SOVIET_STATE_SUFFIX


def doushi_profile_context(state_dir: Path | None) -> tuple[str, str]:
    """Return the same bounded ranking facts for game-state and chitchat UI memo."""
    fallback = non_soviet_game_state(DOUSHI_VIEW, DOUSHI_BLURB)
    if state_dir is None:
        return fallback, ""
    try:
        corner = json.loads((Path(state_dir) / "doushi_corner.json").read_text(encoding="utf-8"))
        identity = corner.get("doushi_runtime_identity") if isinstance(corner, dict) else None
        if not isinstance(identity, dict) or identity.get("game") != DOUSHI_VIEW:
            return fallback, ""
        runtime_id = identity.get("runtime_id")
        if not isinstance(runtime_id, str) or not runtime_id:
            return fallback, ""
        from ..naming import runtime_directory

        stored = json.loads(
            (runtime_directory(Path(state_dir), runtime_id) / "doushi_ranking.json").read_text(
                encoding="utf-8"))
    except Exception:
        return fallback, ""
    payload = stored.get("payload") if isinstance(stored, dict) else None
    if not isinstance(payload, dict):
        return fallback, ""

    def first_row(key: str) -> dict:
        rows = payload.get(key)
        return rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}

    def bounded(value, limit: int) -> str:
        return value.strip()[:limit] if isinstance(value, str) else ""

    ranking_lines = []
    top_count = first_row("by_count")
    name = bounded(top_count.get("username"), 24)
    count = top_count.get("redemption_count")
    if name and type(count) is int and count >= 0:
        ranking_lines.append(f"排出枚数トップは同志{name}（{count}枚）。")
    top_points = first_row("by_points")
    spender = bounded(top_points.get("username"), 24)
    points = top_points.get("total_points")
    if spender and type(points) is int and points >= 0:
        ranking_lines.append(f"使用ポイントトップは同志{spender}（{points}ポイント）。")
    top_card = first_row("popular_cards")
    card = bounded(top_card.get("card_name"), 24)
    drawn = top_card.get("actual_count")
    if card and type(drawn) is int and drawn >= 0:
        ranking_lines.append(f"人気カードは{card}（{drawn}枚）。")
    if not ranking_lines:
        return fallback, ""

    intro = "- いまは同志カード大賞のコーナー中です。同志カードの成績ランキングを発表しています。"
    game_state = "".join([intro, *ranking_lines, "画面に無い数字は創作しないこと。"])[:600]
    ui_memo = ("【同志カード大賞の実測ランキング】" + "".join(ranking_lines)
               + "このメモにない順位や数値は補わないこと。")[:300]
    return game_state, ui_memo


def doushi_game_state(state_dir: Path | None) -> str:
    """Return the bounded game-state projection used by the native provider."""
    return doushi_profile_context(state_dir)[0]


def build(game: str, *, config: Path | None = None, state_dir: Path | None = None,
          now: float | None = None) -> dict | None:
    """Return the replacement profile for ``game``; ``None`` when docich has none.

    ``state_dir`` is the docich state directory (holding ``game_switch.json``
    and ``trading/status.json``). PAPER and Doushi profiles read it; the Hanjuku
    profile stays config-only.
    """
    if game == PAPER:
        profile = paper_corner_profile.build(state_dir, now=now)
        profile["persona_replace"] = [list(pair) for pair in PERSONA_REPLACE]
        return profile
    if game == DOUSHI_VIEW:
        game_state, ui_memo = doushi_profile_context(state_dir)
        return {
            "schema": SCHEMA,
            "game": game,
            "persona_replace": [list(pair) for pair in PERSONA_REPLACE],
            "game_state": game_state,
            "ui_memo": ui_memo,
            "game_rules": DOUSHI_RULES_EN,
            "game_rules_ja": DOUSHI_RULES_JA,
            "ops_lines": [],
        }
    if game != HANJUKU:
        return None
    return {
        "schema": SCHEMA,
        "game": game,
        "persona_replace": [list(pair) for pair in PERSONA_REPLACE],
        "game_state": non_soviet_game_state(game, HANJUKU_BLURB),
        # The Soviet UI memo (graph/status windows, Meriken AI) is not this screen.
        "ui_memo": "",
        "game_rules": HANJUKU_RULES_EN,
        "game_rules_ja": HANJUKU_RULES_JA,
        "ops_lines": hanjuku_how_it_works(config),
    }


def default_state_dir() -> Path:
    """Resolve the docich state directory for the launcher.

    Prefer the operator's explicit state directory, then the canonical switch
    file's directory (the layout Soren's comment worker already uses), then the
    shared resolver's checkout/VM defaults.  Never invent a path from the game
    name.
    """
    configured = (os.environ.get("DOCICH_STATE_DIR") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else Path.cwd() / path
    canonical = (os.environ.get("DOCICH_GAME_SWITCH_CANONICAL_FILE") or "").strip()
    if canonical:
        path = Path(canonical).expanduser()
        return (path if path.is_absolute() else Path.cwd() / path).parent
    from docich.soren_corner_stats import resolve_state_dir

    return resolve_state_dir()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: docich-comment-profile <game>", file=sys.stderr)
        return 2
    profile = build(args[0], state_dir=default_state_dir())
    if profile is not None:  # an unsupported game prints nothing: the shell keeps its own prompt
        sys.stdout.write(json.dumps(profile, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
