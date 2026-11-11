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

SCHEMA = 1
HANJUKU = "hanjuku-hero"
HANJUKU_BLURB = "半熟英雄(SFCエミュレータ)"

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


def build(game: str, *, config: Path | None = None) -> dict | None:
    """Return the replacement profile for ``game``; ``None`` when docich has none."""
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


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: docich-comment-profile <game>", file=sys.stderr)
        return 2
    profile = build(args[0])
    if profile is not None:  # an unsupported game prints nothing: the shell keeps its own prompt
        sys.stdout.write(json.dumps(profile, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
