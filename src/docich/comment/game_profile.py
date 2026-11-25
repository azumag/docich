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
import stat
import sys
import time
from pathlib import Path

from .. import hanjuku_family
from . import paper_corner_profile

SCHEMA = 1
HANJUKU = "hanjuku-hero"
HANJUKU_BLURB = "半熟英雄(SFCエミュレータ)"
SPEEDRUN = hanjuku_family.SPEEDRUN
SPEEDRUN_BLURB = "半熟英雄スピードラン(SFCエミュレータ、BOTがクリアタイムの短縮に挑戦中)"
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

# The speedrun corner has its own rules memo (design §6).  Every sentence is a
# measured rule of the timer or the game flow; nothing about the screen layout
# is claimed.
SPEEDRUN_RULES_EN = (
    "【Current Game Rules (半熟英雄スピードラン)】\n"
    "This is a 12-chapter speedrun of 半熟英雄. Each chapter ends at the chapter-end save "
    "confirmation, and the run ends when the clear ending 「めでたし」 is read.\n"
    "PB is the fastest finished run. A segment best is the fastest measured time for one "
    "chapter (shown in gold), and Sum of Best is the sum of the 12 segment bests, a "
    "theoretical limit rather than an achieved time.\n"
    "Timer colors: yellow = segment best (a new PB on the total is also yellow), blue = not "
    "a segment best but faster than the same chapter in the PB, red = slower than the PB, "
    "white = equal to the PB or no comparison yet.\n"
    "A game over or a stalled screen ends the attempt, and the next corner starts a new run "
    "from the beginning. Timing precision depends on the observation interval (about 1 "
    "second), so times are not frame-accurate.\n"
    "Never explain this with the Soviet game's rules (countries, merging, score). If a "
    "viewer asks something this memo does not cover, say you do not have that information "
    "instead of guessing."
)
SPEEDRUN_RULES_JA = (
    "\t【現在のゲームの基本ルール（半熟英雄スピードラン）】\n"
    "\t- 全12話のタイムを競うスピードラン。各話の終わりの章末セーブ確認で区切り、最後は「めでたし」を読んだ時点で計測終了\n"
    "\t- PBは完走した試走のうち最速の合計タイム。区間ベストは1話ごとの最速タイム。Sum of Bestは12話の区間ベストを合計した理論値で、実際に出たタイムではない\n"
    "\t- 色の意味: 黄=区間ベスト更新(PB更新も黄)、青=区間ベストではないがPB時の区間より速い、赤=PB時の区間より遅い、白=PB時と同じか比較なし\n"
    "\t- ゲームオーバーや画面停滞でその試走は終わり、次回は最初から走り直す\n"
    "\t- 計測精度は観測間隔(約1秒)に依存する\n"
    "\t- ソ連ゲームの国・併合・得点のルールで説明しないこと。ルールを聞かれても、メモにないことは推測で答えず、分からないと伝えること"
)
SPEEDRUN_OPS_LINE = (
    "- このコーナーは元の半熟BOTを複製し、クリアタイムを縮めるために改善していく計測コーナー"
)

# Same liveness bound as the overlay's ``speedrunTimerState``: a running timer
# whose heartbeat is older than this is not presented as current.
SPEEDRUN_STALE_MS = 30_000
SPEEDRUN_NO_INFO = "いまは半熟英雄スピードランのコーナー中。計測情報なし"
_SPEEDRUN_VERDICTS = {
    "gold": "区間ベスト更新",
    "blue": "PB時の区間より速い",
    "red": "PB時の区間より遅い",
    "white": "PB時と同タイム",
}
_SPEEDRUN_DNF_REASONS = {
    "game_over": "ゲームオーバー",
    "stalled": "画面停滞",
    "stopped": "運用停止",
}


REPO_HANJUKU_CONFIG = Path(__file__).resolve().parents[3] / "config" / "games" / "hanjuku-hero.toml"
REPO_SPEEDRUN_CONFIG = (
    Path(__file__).resolve().parents[3] / "config" / "games" / "hanjuku-speedrun.toml"
)


def default_hanjuku_config() -> Path:
    """CLI default: the env override (tests), else this checkout's config. Callers that must not
    read ``os.environ`` (e.g. a provider with an allowlisted env) pass ``config=`` explicitly."""
    override = os.environ.get("DOCICH_HANJUKU_CONFIG_FILE")
    return Path(override) if override else REPO_HANJUKU_CONFIG


def default_speedrun_config() -> Path:
    """CLI default for the speedrun corner; the original corner keeps its own env key."""
    override = os.environ.get("DOCICH_HANJUKU_SPEEDRUN_CONFIG_FILE")
    return Path(override) if override else REPO_SPEEDRUN_CONFIG


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


def _read_json_bounded(path: Path, limit: int):
    """Read one bounded regular JSON file; never mutate or quarantine it."""
    try:
        meta = os.lstat(path)
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > limit:
            return None
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _non_negative_int(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _hms(milliseconds: int) -> str:
    total = max(0, int(milliseconds)) // 1000
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def _signed_short(milliseconds: int) -> str:
    sign = "+" if milliseconds >= 0 else "−"
    total = abs(int(milliseconds)) // 1000
    minutes, seconds = divmod(total, 60)
    return f"{sign}{minutes}:{seconds:02d}"


def _last_speedrun_split(splits, segment_count: int):
    """The newest fully measured split, or ``None`` (a bad row is never guessed)."""
    if not isinstance(splits, list):
        return None
    for item in reversed(splits):
        if not isinstance(item, dict):
            continue
        index = _non_negative_int(item.get("index"))
        end_index = _non_negative_int(item.get("end_index"))
        segment = _non_negative_int(item.get("segment_ms"))
        cumulative = _non_negative_int(item.get("cumulative_ms"))
        verdict = item.get("verdict")
        if index is None or end_index is None or segment is None or cumulative is None:
            continue
        if index >= segment_count or end_index >= segment_count or end_index < index:
            continue
        if verdict not in _SPEEDRUN_VERDICTS:
            continue
        delta = item.get("delta_ms")
        if type(delta) is not int:
            delta = None
        return index, end_index, segment, cumulative, verdict, delta
    return None


def speedrun_state(state_dir: Path | None, now: float | None = None) -> str:
    """Measured speedrun facts only, bounded to 300 characters.

    The timer is shown only while ``game_switch.json`` names this game and the
    same runtime.  A missing, corrupt, stale (running heartbeat over 30s), or
    mismatched timer returns ``計測情報なし`` with no numbers at all.  The RTA
    package is imported only on this path; the ``hanjuku-hero`` profile never
    touches it.
    """
    if state_dir is None:
        return SPEEDRUN_NO_INFO
    state_dir = Path(state_dir)
    now_ms = int((time.time() if now is None else now) * 1000)

    switch = _read_json_bounded(state_dir / "game_switch.json", 256 * 1024)
    active = switch.get("active") if isinstance(switch, dict) else None
    if not isinstance(active, dict) or active.get("game") != SPEEDRUN:
        return SPEEDRUN_NO_INFO
    active_runtime = active.get("runtime_id")
    if not isinstance(active_runtime, str) or not active_runtime:
        return SPEEDRUN_NO_INFO

    try:
        from ..hanjuku_speedrun.rta import store, timer as rta_timer
    except Exception:
        return SPEEDRUN_NO_INFO

    data_dir = state_dir.joinpath(*store.DATA_PARTS)
    raw = _read_json_bounded(data_dir / store.TIMER_FILE, store.MAX_TIMER_BYTES)
    if not isinstance(raw, dict):
        return SPEEDRUN_NO_INFO
    if raw.get("schema") != rta_timer.SCHEMA or raw.get("game") != rta_timer.GAME:
        return SPEEDRUN_NO_INFO
    status = raw.get("status")
    if status not in rta_timer.STATUSES:
        return SPEEDRUN_NO_INFO
    runtime_id = raw.get("runtime_id")
    if not isinstance(runtime_id, str) or runtime_id != active_runtime:
        return SPEEDRUN_NO_INFO

    started = _non_negative_int(raw.get("started_at_ms"))
    ended = _non_negative_int(raw.get("ended_at_ms"))
    if status != "pending" and started is None:
        return SPEEDRUN_NO_INFO
    if status in ("finished", "dnf") and ended is None:
        return SPEEDRUN_NO_INFO
    heartbeat = _non_negative_int(raw.get("heartbeat_at_ms"))
    if status == "running" and (heartbeat is None
                                or now_ms - heartbeat > SPEEDRUN_STALE_MS):
        return SPEEDRUN_NO_INFO
    current = raw.get("current_index", 0)
    if type(current) is not int or not 0 <= current <= rta_timer.SEGMENT_COUNT:
        return SPEEDRUN_NO_INFO
    if status in ("pending", "running") and current >= rta_timer.SEGMENT_COUNT:
        return SPEEDRUN_NO_INFO

    comparison = raw.get("comparison") if isinstance(raw.get("comparison"), dict) else {}
    records = None
    records_raw = _read_json_bounded(data_dir / store.RECORDS_FILE, store.MAX_RECORDS_BYTES)
    if isinstance(records_raw, dict) and records_raw.get("game") in (None, rta_timer.GAME):
        try:
            records = rta_timer.normalize_records(records_raw)
        except Exception:
            records = None

    pb_total = None
    pb_cumulative = None
    sum_of_best = None
    pb_known = False
    if records is not None:
        pb_known = True
        pb = records.get("pb") if isinstance(records.get("pb"), dict) else None
        if pb is not None:
            pb_total = _non_negative_int(pb.get("total_ms"))
            values = pb.get("cumulative_ms")
            pb_cumulative = values if isinstance(values, list) else None
        sum_of_best = _non_negative_int(records.get("sum_of_best_ms"))
    else:
        if "pb_total_ms" in comparison:
            pb_known = True
            pb_total = _non_negative_int(comparison.get("pb_total_ms"))
        values = comparison.get("pb_cumulative_ms")
        pb_cumulative = values if isinstance(values, list) else None
        sum_of_best = _non_negative_int(comparison.get("sum_of_best_ms"))

    parts = ["いまは半熟英雄スピードランのコーナー中。"]
    attempt_no = _non_negative_int(raw.get("attempt_no"))
    attempt = f"第{attempt_no}試走" if attempt_no else "試走"
    if status == "pending":
        parts.append(f"{attempt}の開始前。")
    elif status == "running":
        parts.append(
            f"{attempt}、第{current + 1}話を走行中、経過 {_hms(now_ms - (started or 0))}。")
    elif status == "finished":
        elapsed = (ended or 0) - (started or 0)
        if raw.get("pb_updated") is True:
            parts.append(f"{attempt}は完走してPB更新、記録 {_hms(elapsed)}。")
        elif raw.get("total_verdict") == "red":
            parts.append(f"{attempt}は完走したがPBより遅い、記録 {_hms(elapsed)}。")
        else:
            parts.append(f"{attempt}は完走、記録 {_hms(elapsed)}。")
    else:
        reason = _SPEEDRUN_DNF_REASONS.get(raw.get("dnf_reason"))
        elapsed = _hms((ended or 0) - (started or 0))
        parts.append(f"{attempt}は{reason}で打ち切り、経過 {elapsed}。" if reason
                     else f"{attempt}は打ち切り、経過 {elapsed}。")

    if pb_total is not None:
        parts.append(f"PB {_hms(pb_total)}。")
    elif pb_known:
        parts.append("PBはまだ無い。")

    last = _last_speedrun_split(raw.get("splits"), rta_timer.SEGMENT_COUNT)
    if last is not None:
        index, end_index, segment, cumulative, verdict, delta = last
        chapter = (f"第{index + 1}話" if index == end_index
                   else f"第{index + 1}〜{end_index + 1}話")
        if pb_cumulative is not None and end_index < len(pb_cumulative):
            pb_cum = _non_negative_int(pb_cumulative[end_index])
            if pb_cum is not None:
                parts.append(f"{chapter}終了時点でPB比 "
                             f"{_signed_short(cumulative - pb_cum)}。")
        detail = f"直前の{chapter}は {_hms(segment)}、"
        if verdict == "white" and delta is None:
            detail += "比較なし"
        else:
            detail += _SPEEDRUN_VERDICTS[verdict]
        if delta is not None:
            detail += f"（PB比 {_signed_short(delta)}）"
        parts.append(detail + "。")

    if sum_of_best is not None:
        parts.append(f"Sum of Best {_hms(sum_of_best)}。")

    output = ""
    for part in parts:
        if len(output) + len(part) > 300:
            break
        output += part
    return output or SPEEDRUN_NO_INFO


def build(game: str, *, config: Path | None = None, state_dir: Path | None = None,
          now: float | None = None) -> dict | None:
    """Return the replacement profile for ``game``; ``None`` when docich has none.

    ``state_dir`` is the docich state directory (holding ``game_switch.json``
    and ``trading/status.json``). PAPER, Doushi and the speedrun profiles read
    it; the original Hanjuku profile stays config-only.
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
    if game == SPEEDRUN:
        if config is None:
            config = default_speedrun_config()
        ops_lines = hanjuku_how_it_works(config)
        # The shared explanation comes first; this line says what the corner is.
        ops_lines.insert(1, SPEEDRUN_OPS_LINE)
        return {
            "schema": SCHEMA,
            "game": game,
            "persona_replace": [list(pair) for pair in PERSONA_REPLACE],
            "game_state": speedrun_state(state_dir, now=now),
            # The Soviet UI memo (graph/status windows, Meriken AI) is not this screen.
            "ui_memo": "",
            "game_rules": SPEEDRUN_RULES_EN,
            "game_rules_ja": SPEEDRUN_RULES_JA,
            "ops_lines": ops_lines,
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
