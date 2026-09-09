"""End-of-corner improvement for corner games.

Replaces the continuous daemon rhythm for corner games: during the corner
only match logs accumulate; when the corner ends, one improvement job runs.
The candidate comes from an LLM (sorengame-style delegation via
docich.ai_generate), is evaluated with real headless matches, and is
promoted only past the margin gate.  Promotion reuses
docich.resolver.improve history/rendering, so the next corner announces the
strategy diff automatically (see retro_corner.describe_strategy_change).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


class CornerImproveError(RuntimeError):
    """User-facing failure in the end-of-corner improvement job."""


def _safe_detail(value: BaseException | str) -> str:
    return str(value).replace("\n", " ")[:240]


def slice_corner_matches(log_path, game: str, start_ts: float, end_ts: float) -> list[dict]:
    """Match entries of one corner window from a scorelog jsonl file."""
    matches: list[dict] = []
    try:
        lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return matches
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("game") != game:
            continue
        try:
            ts = float(entry.get("ts", 0) or 0)
            score = int(entry.get("score"))
        except (TypeError, ValueError):
            continue
        if start_ts <= ts <= end_ts:
            matches.append({"ts": ts, "score": score})
    return matches


def summarize_matches(matches: list[dict]) -> dict:
    scores = [m["score"] for m in matches]
    if not scores:
        return {"n": 0, "mean": 0.0, "best": 0}
    return {"n": len(scores), "mean": sum(scores) / len(scores), "best": max(scores)}


def build_prompt(*, game: str, stats: dict, current: dict, previous: dict) -> str:
    return f"""あなたはレトロゲームコーナーの戦略改善担当です。
対象ゲーム: {game}
今回コーナーの実戦成績: {stats['n']}試合、平均{stats['mean']:.1f}点、最高{stats['best']}点
現在の戦略重み (JSON):
{json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True)}
前回の戦略重み (JSON):
{json.dumps(previous, ensure_ascii=False, indent=2, sort_keys=True)}

今回の成績を踏まえ、平均スコアを上げる方向に数値重みだけを調整した候補を
1つ提案してください。キー構成は変えず、既存キーの数値のみ変更すること。
出力はJSONオブジェクト1つのみ。説明文は書かず、```jsonフェンスで囲むこと。
"""


def parse_candidate(text: str, allowed_keys: set[str]) -> dict:
    """LLM出力から候補重みを取り出す。形式不正は CornerImproveError。"""
    match = JSON_FENCE_RE.search(text or "")
    payload = match.group(1) if match else (text or "")
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise CornerImproveError(f"LLM出力がJSONではありません: {_safe_detail(exc)}") from exc
    if not isinstance(data, dict) or not data:
        raise CornerImproveError("LLM出力が空でないJSONオブジェクトではありません")
    unknown = sorted(set(data) - set(allowed_keys))
    if unknown:
        raise CornerImproveError(f"未知の重みキーがあります: {', '.join(unknown[:5])}")
    candidate = {}
    for key, value in data.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CornerImproveError(f"重みは数値である必要があります: {key}")
        if not (0.001 <= float(value) <= 1e6):
            raise CornerImproveError(f"重みが範囲外です: {key}={value}")
        candidate[key] = value
    return candidate


def _default_llm(g, *, agents: str, prompt_text: str, timeout: int = 600) -> str:
    """sorengame と同じ dispatch 経路で1候補を生成する。本番実行のみ。"""
    if os.environ.get("DOCICH_ALLOW_REAL_AI") != "1":
        raise CornerImproveError(
            "LLM改善の実実行には DOCICH_ALLOW_REAL_AI=1 が必要です"
        )
    import tempfile

    from .ai_generate import build_ai_invocation
    from .procs import run

    with tempfile.TemporaryDirectory(prefix="docich-corner-improve-") as tmp:
        prompt_file = Path(tmp) / "prompt.txt"
        prompt_file.write_text(prompt_text, encoding="utf-8")
        inv = build_ai_invocation(
            g,
            game_name="sorengame",
            label="RADIO:retro-improve",
            agents=agents,
            prompt_file=prompt_file,
            timeout=timeout,
        )
        try:
            completed = run(
                inv.argv, cwd=str(inv.cwd), env_extra=inv.env,
                timeout=float(timeout + 60), capture=True,
            )
        except Exception as exc:
            raise CornerImproveError(f"LLM呼び出しに失敗しました: {_safe_detail(exc)}") from exc
    if completed.returncode != 0:
        raise CornerImproveError(
            f"LLM改善が失敗しました (rc={completed.returncode}): "
            f"{(completed.stderr or '').strip()[:200]}"
        )
    output = (completed.stdout or "").strip()
    if not output:
        raise CornerImproveError("LLM改善の出力が空でした")
    return output


def _corner_window(state: dict) -> tuple[float, float]:
    try:
        start = dt.datetime.fromisoformat(str(state["started_at"])).timestamp()
        end = dt.datetime.fromisoformat(str(state["ends_at"])).timestamp()
    except (KeyError, ValueError, TypeError, OverflowError, OSError) as exc:
        raise CornerImproveError(f"コーナー期間が不正です: {_safe_detail(exc)}") from exc
    if not end >= start:
        raise CornerImproveError("コーナー期間が不正です")
    return start, end


def run_corner_improve(
    g,
    *,
    game: str,
    date_str: str,
    agents: str,
    matches: int = 2,
    margin_pct: float = 10.0,
    dry_run: bool = False,
    llm=None,
    evaluator=None,
) -> dict:
    """指定日次コーナー終了後の改善を1回実行する。結果サマリ dict を返す。"""
    from .resolver import strategy_path
    from .resolver.improve import (
        _append_log,
        _game_defaults,
        _promote,
        evaluate_gnurobots,
        read_strategy_for_game,
    )

    if game != "gnurobots":
        return {"status": "skipped", "reason": f"unsupported-game:{game}"}
    state_path = Path(g.state_dir) / "retro_corner.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CornerImproveError(f"コーナー状態を読み込めません: {_safe_detail(exc)}") from exc
    if not isinstance(state, dict) or state.get("date") != date_str:
        return {"status": "skipped", "reason": "wrong-date"}
    if state.get("status") != "completed":
        return {"status": "skipped", "reason": f"wrong-status:{state.get('status')}"}
    start_ts, end_ts = _corner_window(state)

    log_env = os.environ.get("GNUROBOTS_SCORELOG", "").strip()
    log_path = Path(log_env) if log_env else (Path(g.state_dir) / "scores" / f"{game}.jsonl")
    corner_matches = slice_corner_matches(log_path, game, start_ts, end_ts)
    stats = summarize_matches(corner_matches)
    if stats["n"] == 0:
        return {"status": "skipped", "reason": "no-matches", "stats": stats}

    defaults = _game_defaults(game)
    current = read_strategy_for_game(game, strategy_path(g.state_dir, game))
    history_dir = Path(g.state_dir) / "resolver" / "history"
    try:
        snapshots = sorted(history_dir.glob("*.json"))
    except OSError:
        snapshots = []
    previous: dict = {}
    if snapshots:
        try:
            data = json.loads(snapshots[-1].read_text(encoding="utf-8"))
            previous = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            previous = {}
    prompt_text = build_prompt(game=game, stats=stats, current=current, previous=previous)
    if dry_run:
        return {"status": "dry-run", "stats": stats, "prompt_chars": len(prompt_text)}

    llm = llm or (lambda text: _default_llm(g, agents=agents, prompt_text=text))
    try:
        raw_output = llm(prompt_text)
        candidate_delta = parse_candidate(raw_output, set(defaults))
    except CornerImproveError:
        raise
    except Exception as exc:
        raise CornerImproveError(f"候補生成に失敗しました: {_safe_detail(exc)}") from exc
    candidate = dict(current)
    candidate.update(candidate_delta)

    evaluator = evaluator or (lambda strat: evaluate_gnurobots(strat, matches))
    try:
        ev = evaluator(candidate)
    except Exception as exc:
        raise CornerImproveError(f"候補評価に失敗しました: {_safe_detail(exc)}") from exc
    candidate_mean = float(ev.get("mean_score", 0.0) or 0.0)
    played = int(ev.get("played", 0) or 0)
    summary = {
        "game": game, "date": date_str, "corner_n": stats["n"],
        "corner_mean": round(stats["mean"], 1), "corner_best": stats["best"],
        "candidate_mean": round(candidate_mean, 1), "candidate_played": played,
        "matches": matches, "margin_pct": margin_pct,
    }
    if played > 0 and candidate_mean > stats["mean"] * (1 + margin_pct / 100.0):
        s_file = strategy_path(g.state_dir, game)
        try:
            old_raw = json.loads(Path(s_file).read_text(encoding="utf-8"))
            old = old_raw if isinstance(old_raw, dict) else dict(current)
        except (OSError, ValueError):
            old = dict(current)
        _promote(g, game, s_file, old, candidate)
        summary["promoted"] = True
        _append_log(g.state_dir, game, {**summary, "promoted": True})
        return {"status": "promoted", **summary}
    summary["promoted"] = False
    _append_log(g.state_dir, game, {**summary, "promoted": False})
    return {"status": "kept", **summary}
