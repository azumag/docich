"""End-of-corner improvement for corner games.

Replaces the continuous daemon rhythm for corner games: during the corner
only match logs accumulate; when the corner ends, one improvement job runs.
The candidate comes from an LLM (sorengame-style delegation via
docich.ai_generate). Generic games use a headless margin gate; Pac-Man compares
each candidate with an interleaved headless ABBA evaluation on the following
improvement cycle. Moon Buggy stages differing candidates for a fixed live ABBA
comparison. Promotion reuses docich.resolver.improve history/rendering, so the
next corner announces the strategy diff automatically (see
retro_corner.describe_strategy_change).
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import stat as statmod
import time
from contextlib import contextmanager
from pathlib import Path
from .resolver import latest_strategy_snapshot, strategy_path
from .resolver.improve import (
    _append_log,
    _game_defaults,
    _promote,
    evaluate_gnurobots,
    read_strategy_for_game,
)
from .resolver.bot_eval import bot_games, run_bot_matches

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)

# Durable failure metadata is an enum contract, not an exception serialization
# surface. Keep unknown/future/injected exception attributes from becoming
# free-text state; diagnostics applies the same allowlist at its read boundary.
CORNER_IMPROVE_REASON_CODES = frozenset({
    "state-read", "corner-window", "gate-disabled", "llm-call", "llm-rc",
    "llm-empty", "llm-format", "llm-keys", "llm-values", "llm-unexpected",
    "eval", "lane-busy", "policy-promoted", "policy-incomplete", "policy-faults",
    "policy-below-margin", "policy-not-significant", "policy-identical",
    "policy-invalid", "policy-kept", "policy-eval", "ab-pending",
    "ab-incomplete", "ab-adopted", "ab-rejected", "ab-stale", "ab-invalid",
    "ab-eval", "ab-state", "ab-baseline-changed", "unexpected",
})

PACMAN_AB_GAME = "pacman4console"
PACMAN_AB_TRIAL_NAME = "pacman4console_ab_trial.json"

# One improvement job at a time across every corner and the PAPER pipeline.
# Queue dispatch no longer waits for the previous job, so the lane is what
# keeps concurrent LLM/evaluation work bounded.  A job that cannot get the
# lane within the bounded wait records a visible skip instead of piling up.
IMPROVE_LANE_WAIT_S = 1800.0
IMPROVE_LANE_POLL_S = 5.0
CORNER_IMPROVE_PHASES = frozenset({"state", "llm", "eval", "unknown"})


def _fixed_enum(value, allowed: frozenset[str], fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


class CornerImproveError(RuntimeError):
    """User-facing failure in the end-of-corner improvement job."""

    def __init__(self, message, *, code="unexpected", phase="unknown"):
        super().__init__(message)
        self.code = code
        self.phase = phase


# Keep the improvement dispatch in lockstep with the headless evaluator.  Every
# retro game using a command brain gets the same candidate/evaluate/promote
# lifecycle; adding a game to only one of these lists silently disables its
# improvement path.
BOT_GAMES = bot_games()


def numeric_weights(weights: dict) -> set[str]:
    """bot_games の重みのうち LLM に提案させる数値キー (真偽値は固定方策)。"""
    return {
        key for key, value in weights.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _lane_path(state_dir) -> Path:
    return Path(state_dir) / "locks" / "corner-improve-lane.lock"


def improve_lane_free(state_dir) -> bool:
    """Probe the cross-corner improvement lane without keeping the lock."""
    path = _lane_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return True


@contextmanager
def improve_lane(state_dir, *, timeout: float = IMPROVE_LANE_WAIT_S, sleep=time.sleep):
    """Serialize improvement jobs across corners; yield False on bounded timeout."""
    path = _lane_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    yield False
                    return
                sleep(min(IMPROVE_LANE_POLL_S, remaining))
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@contextmanager
def _singleflight(state_dir, game: str):
    """改善ジョブの単一飛行。手動再実行と切り離しjobの二重 promote を防ぐ。"""
    path = Path(state_dir) / "locks" / f"corner-improve-{game}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


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


def _strategy_digest(strategy: dict) -> str:
    payload = json.dumps(
        strategy, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _pacman_ab_path(state_dir) -> Path:
    return Path(state_dir) / "resolver" / PACMAN_AB_TRIAL_NAME


def _valid_pacman_strategy(value, defaults: dict) -> bool:
    if not isinstance(value, dict) or set(value) != set(defaults):
        return False
    for key, default in defaults.items():
        item = value.get(key)
        if isinstance(default, bool):
            if type(item) is not bool:
                return False
        elif isinstance(default, (int, float)) and not isinstance(default, bool):
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return False
            try:
                if not math.isfinite(float(item)) or not 0.001 <= float(item) <= 1e6:
                    return False
            except (OverflowError, ValueError):
                return False
        elif type(item) is not type(default):
            return False
    return True


def _read_pacman_ab_trial(path: Path, defaults: dict) -> dict | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not statmod.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise ValueError("invalid trial file")
            payload = stream.read(65537)
            if len(payload) > 65536:
                raise ValueError("trial file too large")
        raw = json.loads(payload)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as exc:
        raise CornerImproveError(
            "Pac-Man A/B候補の状態を読み込めません",
            code="ab-invalid", phase="state",
        ) from exc
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw.get("schema_version") != 1
        or not isinstance(raw.get("status"), str)
        or raw.get("status") not in {"pending", "promoting", "rejected"}
        or raw.get("game") != PACMAN_AB_GAME
        or not _finite_number(raw.get("created_at"))
        or not _valid_pacman_strategy(raw.get("baseline"), defaults)
        or not _valid_pacman_strategy(raw.get("candidate"), defaults)
    ):
        raise CornerImproveError(
            "Pac-Man A/B候補の状態が不正です",
            code="ab-invalid", phase="state",
        )
    expected_fields = {
        "schema_version", "status", "created_at", "game", "baseline_sha256",
        "candidate_sha256", "baseline", "candidate",
    }
    if raw["status"] in {"promoting", "rejected"}:
        expected_fields.add("summary")
    if set(raw) != expected_fields:
        raise CornerImproveError(
            "Pac-Man A/B候補の状態に未知の項目があります",
            code="ab-invalid", phase="state",
        )
    try:
        if (raw.get("baseline_sha256") == raw.get("candidate_sha256")
                or raw.get("baseline_sha256") != _strategy_digest(raw["baseline"])
                or raw.get("candidate_sha256") != _strategy_digest(raw["candidate"])):
            raise ValueError("digest mismatch")
    except (TypeError, ValueError, OverflowError) as exc:
        raise CornerImproveError(
            "Pac-Man A/B候補のハッシュが一致しません",
            code="ab-invalid", phase="state",
        ) from exc
    if raw["status"] in {"promoting", "rejected"}:
        summary = raw.get("summary")
        if (not _valid_pacman_ab_summary(summary, raw)
                or summary["promoted"] != (raw["status"] == "promoting")):
            raise CornerImproveError(
                "Pac-Man A/B判定記録が不正です",
                code="ab-invalid", phase="state",
            )
    return raw


def _valid_pacman_ab_summary(value, trial: dict) -> bool:
    fields = {
        "game", "date", "corner_n", "corner_mean", "corner_best", "ab_pattern",
        "ab_matches_per_arm", "baseline_sha256", "candidate_sha256", "baseline_scores",
        "candidate_scores", "baseline_mean", "candidate_mean", "baseline_played",
        "candidate_played", "promoted",
    }
    if not isinstance(value, dict) or set(value) != fields:
        return False
    if (value.get("game") != PACMAN_AB_GAME
            or not isinstance(value.get("date"), str)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value["date"]) is None
            or value.get("ab_pattern") != "ABBA"
            or value.get("baseline_sha256") != trial.get("baseline_sha256")
            or value.get("candidate_sha256") != trial.get("candidate_sha256")
            or type(value.get("promoted")) is not bool):
        return False
    matches = value.get("ab_matches_per_arm")
    if type(matches) is not int or not 1 <= matches <= 10:
        return False
    if (type(value.get("corner_n")) is not int or value["corner_n"] < 0
            or type(value.get("corner_best")) is not int or value["corner_best"] < 0
            or type(value.get("baseline_played")) is not int
            or value["baseline_played"] != matches
            or type(value.get("candidate_played")) is not int
            or value["candidate_played"] != matches):
        return False
    for name in ("corner_mean", "baseline_mean", "candidate_mean"):
        number = value.get(name)
        if not _finite_number(number):
            return False
    for name in ("baseline_scores", "candidate_scores"):
        scores = value.get(name)
        if (not isinstance(scores, list) or len(scores) != matches
                or any(not _finite_number(score) for score in scores)):
            return False
    raw_a = sum(value["baseline_scores"]) / matches
    raw_b = sum(value["candidate_scores"]) / matches
    expected_a = round(raw_a, 1)
    expected_b = round(raw_b, 1)
    return (
        value["baseline_mean"] == expected_a
        and value["candidate_mean"] == expected_b
        and value["promoted"] == (raw_b > raw_a)
    )


def _pacman_ab_order(matches_per_arm: int) -> list[str]:
    """Build a balanced, interleaved ABBA order for the requested sample count."""
    if type(matches_per_arm) is not int or not 1 <= matches_per_arm <= 10:
        raise ValueError("Pac-Man A/B matches must be in 1..10 per arm")
    count = matches_per_arm
    pattern = ("A", "B", "B", "A")
    return [pattern[index % len(pattern)] for index in range(2 * count)]


def _pacman_ab_block(g, baseline: dict, candidate: dict, *, matches: int, evaluator=None):
    """Evaluate one Pac-Man ABBA block, requiring every bounded match to score."""
    evaluate = evaluator or _bot_evaluator(g, PACMAN_AB_GAME, 1)
    scores = {"A": [], "B": []}
    for arm in _pacman_ab_order(matches):
        try:
            result = evaluate(baseline if arm == "A" else candidate)
        except Exception as exc:
            raise CornerImproveError(
                "Pac-Man A/B評価に失敗しました", code="ab-eval", phase="eval",
            ) from exc
        if not isinstance(result, dict):
            return None
        played = result.get("played")
        raw_score = result.get("mean_score")
        if type(played) is not int or played != 1 or not _finite_number(raw_score):
            return None
        score = float(raw_score)
        scores[arm].append(score)
    if len(scores["A"]) != matches or len(scores["B"]) != matches:
        return None
    return scores


def _pacman_ab_summary(trial: dict, scores: dict[str, list[float]], *, date_str: str,
                       corner_stats: dict, matches: int) -> dict:
    baseline_mean = sum(scores["A"]) / len(scores["A"])
    candidate_mean = sum(scores["B"]) / len(scores["B"])
    return {
        "game": PACMAN_AB_GAME,
        "date": date_str,
        "corner_n": corner_stats["n"],
        "corner_mean": round(corner_stats["mean"], 1),
        "corner_best": corner_stats["best"],
        "ab_pattern": "ABBA",
        "ab_matches_per_arm": matches,
        "baseline_sha256": trial["baseline_sha256"],
        "candidate_sha256": trial["candidate_sha256"],
        "baseline_scores": scores["A"],
        "candidate_scores": scores["B"],
        "baseline_mean": round(baseline_mean, 1),
        "candidate_mean": round(candidate_mean, 1),
        "baseline_played": len(scores["A"]),
        "candidate_played": len(scores["B"]),
        "promoted": candidate_mean > baseline_mean,
    }


def _run_pacman_ab_trial(g, *, path: Path, trial: dict, current: dict, date_str: str,
                         corner_stats: dict, matches: int, evaluator=None) -> dict:
    from .game_switch import atomic_write_json

    current_digest = _strategy_digest(current)
    baseline_digest = trial["baseline_sha256"]
    candidate_digest = trial["candidate_sha256"]
    allowed_current = ({baseline_digest, candidate_digest}
                       if trial["status"] == "promoting" else {baseline_digest})
    if current_digest not in allowed_current:
        path.unlink(missing_ok=True)
        summary = {
            "game": PACMAN_AB_GAME, "date": date_str,
            "baseline_sha256": baseline_digest, "candidate_sha256": candidate_digest,
            "current_sha256": current_digest, "promoted": False,
        }
        _append_log(g.state_dir, PACMAN_AB_GAME,
                    {**summary, "reason_code": "ab-stale"})
        return {"status": "kept", "reason_code": "ab-stale", "phase": "state", **summary}

    strategy_file = strategy_path(g.state_dir, PACMAN_AB_GAME)
    if trial["status"] == "rejected":
        path.unlink(missing_ok=True)
        summary = trial.get("summary")
        if not isinstance(summary, dict):
            raise CornerImproveError("Pac-Man A/B見送り記録が不正です",
                                     code="ab-invalid", phase="state")
        _append_log(g.state_dir, PACMAN_AB_GAME,
                    {**summary, "reason_code": "ab-rejected"})
        return {"status": "kept", "reason_code": "ab-rejected", "phase": "eval", **summary}

    if trial["status"] == "promoting":
        if current_digest == baseline_digest:
            _promote(g, PACMAN_AB_GAME, strategy_file, trial["baseline"], trial["candidate"])
        else:
            from .resolver.bot_eval import bot_brain_weights_path

            live_path = bot_brain_weights_path(PACMAN_AB_GAME)
            live_path.parent.mkdir(parents=True, exist_ok=True)
            live_path.write_text(
                json.dumps(trial["candidate"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        path.unlink(missing_ok=True)
        summary = trial.get("summary")
        if not isinstance(summary, dict):
            raise CornerImproveError("Pac-Man A/B採用記録が不正です",
                                     code="ab-invalid", phase="state")
        _append_log(g.state_dir, PACMAN_AB_GAME,
                    {**summary, "promoted": True, "reason_code": "ab-adopted"})
        return {"status": "promoted", "reason_code": "ab-adopted", "phase": "eval", **summary}

    scores = _pacman_ab_block(
        g, trial["baseline"], trial["candidate"], matches=matches, evaluator=evaluator,
    )
    if scores is None:
        summary = {
            "game": PACMAN_AB_GAME, "date": date_str,
            "baseline_sha256": baseline_digest, "candidate_sha256": candidate_digest,
            "ab_pattern": "ABBA", "ab_matches_per_arm": matches,
            "promoted": False,
        }
        _append_log(g.state_dir, PACMAN_AB_GAME,
                    {**summary, "reason_code": "ab-incomplete"})
        return {"status": "kept", "reason_code": "ab-incomplete", "phase": "eval", **summary}

    summary = _pacman_ab_summary(
        trial, scores, date_str=date_str, corner_stats=corner_stats, matches=matches,
    )
    if summary["promoted"]:
        # Mark the decision durably before changing strategy files. If the job
        # is interrupted during promotion, the next cycle finishes the same
        # decision instead of rerunning the trial with a different outcome.
        trial.update(status="promoting", summary=summary)
        atomic_write_json(path, trial)
        _promote(g, PACMAN_AB_GAME, strategy_file, trial["baseline"], trial["candidate"])
        path.unlink(missing_ok=True)
        _append_log(g.state_dir, PACMAN_AB_GAME,
                    {**summary, "reason_code": "ab-adopted"})
        return {"status": "promoted", "reason_code": "ab-adopted", "phase": "eval", **summary}

    trial.update(status="rejected", summary=summary)
    atomic_write_json(path, trial)
    path.unlink(missing_ok=True)
    _append_log(g.state_dir, PACMAN_AB_GAME,
                {**summary, "reason_code": "ab-rejected"})
    return {"status": "kept", "reason_code": "ab-rejected", "phase": "eval", **summary}


def build_prompt(*, game: str, stats: dict, current: dict, previous: dict) -> str:
    basis = stats.get("basis", "live scorelog")
    return f"""あなたはレトロゲームコーナーの戦略改善担当です。
対象ゲーム: {game}
今回コーナーの実戦成績: {stats['n']}試合、平均{stats['mean']:.1f}点、最高{stats['best']}点
今回の改善データ: {basis}
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
        raise CornerImproveError(
            f"LLM出力がJSONではありません: {_safe_detail(exc)}",
            code="llm-format", phase="llm",
        ) from exc
    if not isinstance(data, dict) or not data:
        raise CornerImproveError(
            "LLM出力が空でないJSONオブジェクトではありません",
            code="llm-format", phase="llm",
        )
    unknown = sorted(set(data) - set(allowed_keys))
    if unknown:
        raise CornerImproveError(
            f"未知の重みキーがあります: {', '.join(unknown[:5])}",
            code="llm-keys", phase="llm",
        )
    candidate = {}
    for key, value in data.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CornerImproveError(
                f"重みは数値である必要があります: {key}",
                code="llm-values", phase="llm",
            )
        if not (0.001 <= float(value) <= 1e6):
            raise CornerImproveError(
                f"重みが範囲外です: {key}={value}",
                code="llm-values", phase="llm",
            )
        candidate[key] = value
    return candidate


def _default_llm(g, *, agents: str, prompt_text: str, timeout: int = 600) -> str:
    """Native docich dispatchで1候補を生成する。本番実行のみ。"""
    if os.environ.get("DOCICH_ALLOW_REAL_AI") != "1":
        raise CornerImproveError(
            "LLM改善の実実行には DOCICH_ALLOW_REAL_AI=1 が必要です",
            code="gate-disabled", phase="llm",
        )
    from .ai_generate import AiError, run_prompt

    try:
        result = run_prompt(
            g,
            label="RADIO:retro-improve",
            agents=agents,
            prompt_text=prompt_text,
            timeout=timeout,
            timeout_sec=float(timeout + 60),
        )
    except (AiError, OSError) as exc:
        raise CornerImproveError(
            f"LLM呼び出しに失敗しました: {_safe_detail(exc)}",
            code="llm-call", phase="llm",
        ) from exc
    if result.returncode != 0:
        raise CornerImproveError(
            f"LLM改善が失敗しました (rc={result.returncode}, kind={result.failure_kind or 'unknown'})",
            code="llm-rc", phase="llm",
        )
    output = result.output.strip()
    if not output:
        raise CornerImproveError("LLM改善の出力が空でした", code="llm-empty", phase="llm")
    return output


def validated_window(window) -> tuple[float, float]:
    """Validate an explicit corner window passed by the spawner.

    Queue dispatch can start the next corner before this job reads the shared
    corner state, so the completion hands over (started_at, ends_at) directly
    instead of racing the next state write.
    """
    try:
        start, end = (float(window[0]), float(window[1]))
    except (TypeError, ValueError, IndexError) as exc:
        raise CornerImproveError(
            f"コーナー期間が不正です: {_safe_detail(exc)}",
            code="corner-window", phase="state",
        ) from exc
    if not (math.isfinite(start) and math.isfinite(end)) or not end >= start:
        raise CornerImproveError("コーナー期間が不正です", code="corner-window", phase="state")
    return start, end


def _corner_window(state: dict) -> tuple[float, float]:
    try:
        start = dt.datetime.fromisoformat(str(state["started_at"])).timestamp()
        end = dt.datetime.fromisoformat(str(state["ends_at"])).timestamp()
    except (KeyError, ValueError, TypeError, OverflowError, OSError) as exc:
        raise CornerImproveError(
            f"コーナー期間が不正です: {_safe_detail(exc)}",
            code="corner-window", phase="state",
        ) from exc
    if not end >= start:
        raise CornerImproveError("コーナー期間が不正です", code="corner-window", phase="state")
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
    window: tuple[float, float] | None = None,
) -> dict:
    """指定日次コーナー終了後の改善を1回実行する。結果サマリ dict を返す。

    ``window`` は終了時に確定した (started_at, ends_at) のepoch秒。queue
    dispatchでは次コーナーが共有stateを上書きし得るため、spawn時に明示して
    stateファイルとの競合を避ける。
    """

    if game != "gnurobots" and game not in BOT_GAMES:
        return {"status": "skipped", "reason": f"unsupported-game:{game}"}
    with _singleflight(g.state_dir, game) as single:
        if not single:
            return {"status": "skipped", "reason": "already-running"}
        from .game_switch import atomic_write_json
        status_path = Path(g.state_dir) / f"corner_improve_{game}.json"
        started = time.time()
        with improve_lane(g.state_dir) as lane:
            if not lane:
                # Another improvement job holds the lane for longer than the
                # bounded wait. Keep the record visible instead of stacking
                # concurrent LLM/evaluation work.
                atomic_write_json(status_path, {
                    "status": "skipped", "started_at": started, "completed_at": time.time(),
                    "reason_code": "lane-busy", "phase": "unknown",
                })
                return {"status": "skipped", "reason": "lane-busy"}
            return _run_corner_improve_locked(
                g, status_path=status_path, started=started, game=game, date_str=date_str,
                agents=agents, matches=matches, margin_pct=margin_pct, dry_run=dry_run,
                llm=llm, evaluator=evaluator, window=window,
            )


def _run_corner_improve_locked(
    g,
    *,
    status_path,
    started: float,
    game: str,
    date_str: str,
    agents: str,
    matches: int,
    margin_pct: float,
    dry_run: bool,
    llm,
    evaluator,
    window=None,
) -> dict:
    from .game_switch import atomic_write_json

    try:
        result = _run_corner_improve(
            g, game=game, date_str=date_str, agents=agents,
            matches=matches, margin_pct=margin_pct, dry_run=dry_run,
            llm=llm, evaluator=evaluator, window=window,
        )
    except BaseException as exc:
        atomic_write_json(status_path, {
            "status": "failed", "started_at": started, "completed_at": time.time(),
            "reason_code": _fixed_enum(
                getattr(exc, "code", None), CORNER_IMPROVE_REASON_CODES, "unexpected"
            ),
            "phase": _fixed_enum(
                getattr(exc, "phase", None), CORNER_IMPROVE_PHASES, "unknown"
            ),
        })
        raise
    record = {"status": result["status"], "started_at": started,
              "completed_at": time.time()}
    reason_code = _fixed_enum(
        result.get("reason_code"), CORNER_IMPROVE_REASON_CODES, "unknown"
    )
    phase = _fixed_enum(result.get("phase"), CORNER_IMPROVE_PHASES, "unknown")
    if reason_code != "unknown":
        record["reason_code"] = reason_code
    if phase != "unknown":
        record["phase"] = phase
    atomic_write_json(status_path, record)
    return result


def _bot_evaluator(g, game: str, matches: int):
    """bot_eval 経由の headless evaluator (一時weightsをenvで注入、並行安全)。

    評価は生バイナリを bot_eval 自身の start/retry キーで駆動し、本番の
    wrapper セッション・game-switch state・配信には触れない。
    turn cap 到達 (maxed) でも、bounded evaluation を許可したプリセットが
    有効なスコアを返した場合は、その固定上限までの比較結果として採用する。
    それ以外のゲームでは従来どおり fail closed にする。
    """

    def evaluate(strategy: dict) -> dict:
        import tempfile

        from .resolver.bot_eval import bot_preset

        preset = bot_preset(g, game)
        with tempfile.TemporaryDirectory(prefix="docich-bot-weights-") as tmp:
            weights_file = Path(tmp) / "weights.json"
            weights_file.write_text(
                json.dumps(strategy, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            summary = run_bot_matches(
                label=game,
                binary=preset["binary"](game),
                bot_cmd=preset["bot_cmd"],
                cwd=str(Path(__file__).resolve().parents[2]),
                cols=preset["cols"], rows=preset["rows"],
                matches=matches,
                env={"DOCICH_BRAIN_WEIGHTS": str(weights_file)},
                **preset["run_kwargs"],
            )
        accept_maxed = bool(preset.get("accept_maxed", False))
        completed = [
            m for m in summary.get("matches", [])
            if not m.get("maxed")
            or (accept_maxed and isinstance(m.get("score"), int))
        ]
        scores = [m["score"] for m in completed if isinstance(m.get("score"), int)]
        return {
            "matches": summary.get("matches", []),
            "mean_score": (sum(scores) / len(scores)) if scores else 0.0,
            "played": len(scores),
        }

    return evaluate


def _ninvaders_policy_improve(g, *, agents: str, margin_pct: float,
                              live_stats: dict, llm=None, dry_run: bool = False) -> dict:
    """Rewrite and measure the live NInvaders policy with six paired samples.

    The generic retro setting is two matches and only tunes two numeric
    weights. NInvaders needs repeated samples for its structural policy gate;
    keep that cost and behavior game-local instead of changing other corners.
    """
    from .ninvaders.improve import (
        CORNER_EVAL_MATCHES,
        CORNER_EVAL_MAX_SECONDS,
        CORNER_EVAL_PARALLEL,
        ImproveError,
        improve_once,
    )
    from .ninvaders.store import PolicyStore

    policy_dir = Path(g.state_dir) / "resolver" / "ninvaders"
    store = PolicyStore(policy_dir)
    llm_call = llm or (lambda prompt: _default_llm(g, agents=agents, prompt_text=prompt))
    try:
        result = improve_once(
            store,
            llm=llm_call,
            matches=CORNER_EVAL_MATCHES,
            parallel=CORNER_EVAL_PARALLEL,
            margin_pct=margin_pct,
            max_seconds=CORNER_EVAL_MAX_SECONDS,
            live_stats=live_stats,
            dry_run=dry_run,
        )
    except CornerImproveError:
        raise
    except (ImproveError, OSError, RuntimeError) as exc:
        # Do not persist exception text: provider output and process details
        # stay out of the fixed diagnostics projection.
        raise CornerImproveError(
            "NInvaders構造方策の評価に失敗しました",
            code="policy-eval", phase="eval",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - persist only fixed failure metadata
        raise CornerImproveError(
            "NInvaders構造方策の評価に失敗しました",
            code="policy-eval", phase="eval",
        ) from exc

    status = result.get("status")
    reason_code = None
    phase = "eval"
    if status == "promoted":
        reason_code = "policy-promoted"
    elif status == "rejected":
        phase = "llm"
        reason = result.get("reason", "")
        if reason == "identical-to-incumbent":
            reason_code = "policy-identical"
        elif "static-gate" in str(reason) or "スモーク" in str(reason):
            reason_code = "policy-invalid"
        else:
            reason_code = "policy-kept"
    elif status == "skipped":
        reason_code = "policy-incomplete"
    elif status == "kept":
        reasons = result.get("reasons", [])
        joined = " ".join(str(item) for item in reasons)
        if "too few" in joined:
            reason_code = "policy-incomplete"
        elif "faults" in joined:
            reason_code = "policy-faults"
        elif "margin" in joined:
            reason_code = "policy-below-margin"
        elif "significant" in joined:
            reason_code = "policy-not-significant"
        else:
            reason_code = "policy-kept"
    elif status == "dry-run":
        phase = "unknown"
    if reason_code:
        result["reason_code"] = reason_code
        result["phase"] = phase
    result.setdefault("stats", live_stats)
    return result


def _run_corner_improve(
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
    window=None,
) -> dict:
    if window is not None:
        # The spawner confirmed this run's window at completion; do not read
        # the shared state file, which the next queued corner may already own.
        start_ts, end_ts = validated_window(window)
    else:
        state_path = Path(g.state_dir) / "retro_corner.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CornerImproveError(
                f"コーナー状態を読み込めません: {_safe_detail(exc)}",
                code="state-read", phase="state",
            ) from exc
        if not isinstance(state, dict) or state.get("date") != date_str:
            return {"status": "skipped", "reason": "wrong-date"}
        if state.get("status") != "completed":
            return {"status": "skipped", "reason": f"wrong-status:{state.get('status')}"}
        start_ts, end_ts = _corner_window(state)

    if game == "moon-buggy":
        from .moon_buggy_ab import (
            MoonBuggyABError,
            finish as finish_moon_buggy_ab,
            read_experiment,
            weights_sha256,
        )

        try:
            experiment = read_experiment(g.state_dir)
        except MoonBuggyABError as exc:
            raise CornerImproveError(
                "Moon Buggy A/B state is invalid", code="ab-state", phase="state"
            ) from exc
        if dry_run and experiment:
            return {
                "status": "dry-run",
                "ab_status": experiment["status"],
                "ab_matches": len(experiment["results"]),
                "ab_winner": experiment.get("winner"),
            }
        if experiment and experiment["status"] in {"staged", "running"}:
            return {
                "status": "skipped", "reason": "ab-pending",
                "reason_code": "ab-pending", "phase": "state",
            }
        if experiment and experiment["status"] == "completed":
            winner = experiment["winner"]
            means = experiment["means"]
            promoted = False
            reason_code = None
            if winner == "B":
                s_file = strategy_path(g.state_dir, game)
                current = read_strategy_for_game(game, s_file)
                current_hash = weights_sha256(current)
                if current_hash not in {
                    experiment["baseline_sha256"], experiment["candidate_sha256"]
                }:
                    reason_code = "ab-baseline-changed"
                    finish_moon_buggy_ab(g.state_dir, status="kept")
                else:
                    try:
                        old_raw = json.loads(Path(s_file).read_text(encoding="utf-8"))
                        old = old_raw if isinstance(old_raw, dict) else dict(current)
                    except (OSError, ValueError):
                        old = dict(current)
                    # Reapplying is safe if a previous run stopped between
                    # writing the strategy file and updating the live brain.
                    _promote(g, game, s_file, old, experiment["candidate"])
                    finish_moon_buggy_ab(g.state_dir, status="promoted")
                    promoted = True
            else:
                finish_moon_buggy_ab(g.state_dir, status="kept")
            summary = {
                "game": game,
                "ab_pattern": "ABBA",
                "ab_winner": winner,
                "ab_baseline_mean": means["A"],
                "ab_candidate_mean": means["B"],
                "ab_matches": len(experiment["results"]),
                "promoted": promoted,
            }
            if reason_code:
                summary["reason_code"] = reason_code
                summary["phase"] = "state"
            _append_log(g.state_dir, game, summary)
            return {"status": "promoted" if promoted else "kept", **summary}

    log_env = os.environ.get("GNUROBOTS_SCORELOG", "").strip()
    log_path = Path(log_env) if log_env else (Path(g.state_dir) / "scores" / f"{game}.jsonl")
    corner_matches = slice_corner_matches(log_path, game, start_ts, end_ts)
    stats = summarize_matches(corner_matches)
    if stats["n"] == 0 and game not in BOT_GAMES:
        return {"status": "skipped", "reason": "no-matches", "stats": stats}
    if stats["n"] == 0:
        # A bounded command-brain evaluation is the primary comparable signal
        # for survival-style games such as Snake.  Requiring a natural Game
        # Over in the live corner made a safe run produce no improvement job
        # even though the strategy could be evaluated safely headlessly.
        stats["basis"] = "bounded headless evaluation (live corner had no completed match)"
    else:
        stats["basis"] = "live scorelog plus bounded headless evaluation"

    if game == "ninvaders":
        return _ninvaders_policy_improve(
            g,
            agents=agents,
            margin_pct=margin_pct,
            live_stats=stats,
            llm=llm,
            dry_run=dry_run,
        )

    defaults = _game_defaults(game)
    proposable = numeric_weights(defaults) if game in BOT_GAMES else set(defaults)
    current = read_strategy_for_game(game, strategy_path(g.state_dir, game))
    if game == PACMAN_AB_GAME:
        trial_path = _pacman_ab_path(g.state_dir)
        trial = _read_pacman_ab_trial(trial_path, defaults)
        if trial is not None:
            if dry_run:
                return {"status": "dry-run", "ab_pending": True,
                        "baseline_sha256": trial["baseline_sha256"],
                        "candidate_sha256": trial["candidate_sha256"]}
            return _run_pacman_ab_trial(
                g, path=trial_path, trial=trial, current=current, date_str=date_str,
                corner_stats=stats, matches=matches, evaluator=evaluator,
            )
    previous = latest_strategy_snapshot(g.state_dir, game) or {}
    # Show only the weights the candidate may change. The full strategy also
    # carries fixed flags (for example nsnake's tail_passable boolean); showing
    # them as tunable weights invited the model to return an unknown key, which
    # the strict parser then rejected and failed the whole job.
    prompt_current = {key: value for key, value in current.items() if key in proposable}
    prompt_previous = {key: value for key, value in previous.items() if key in proposable}
    prompt_text = build_prompt(game=game, stats=stats, current=prompt_current, previous=prompt_previous)
    if dry_run:
        return {"status": "dry-run", "stats": stats, "prompt_chars": len(prompt_text)}

    llm = llm or (lambda text: _default_llm(g, agents=agents, prompt_text=text))
    try:
        raw_output = llm(prompt_text)
        candidate_delta = parse_candidate(raw_output, proposable)
    except CornerImproveError:
        raise
    except Exception as exc:
        raise CornerImproveError(
            f"候補生成に失敗しました: {_safe_detail(exc)}",
            code="llm-unexpected", phase="llm",
        ) from exc
    candidate = dict(current)
    candidate.update(candidate_delta)

    if game == PACMAN_AB_GAME:
        if candidate == current:
            summary = {
                "game": game, "date": date_str, "corner_n": stats["n"],
                "corner_mean": round(stats["mean"], 1), "corner_best": stats["best"],
                "baseline_sha256": _strategy_digest(current),
                "candidate_sha256": _strategy_digest(candidate), "promoted": False,
            }
            _append_log(g.state_dir, game, {**summary, "reason_code": "policy-identical"})
            return {"status": "kept", "reason_code": "policy-identical",
                    "phase": "llm", **summary}
        trial = {
            "schema_version": 1,
            "status": "pending",
            "created_at": time.time(),
            "game": game,
            "baseline_sha256": _strategy_digest(current),
            "candidate_sha256": _strategy_digest(candidate),
            "baseline": current,
            "candidate": candidate,
        }
        from .game_switch import atomic_write_json

        atomic_write_json(_pacman_ab_path(g.state_dir), trial)
        summary = {
            "game": game, "date": date_str, "corner_n": stats["n"],
            "corner_mean": round(stats["mean"], 1), "corner_best": stats["best"],
            "ab_pattern": "ABBA", "ab_matches_per_arm": matches,
            "baseline_sha256": trial["baseline_sha256"],
            "candidate_sha256": trial["candidate_sha256"], "promoted": False,
        }
        _append_log(g.state_dir, game, {**summary, "reason_code": "ab-pending"})
        return {"status": "kept", "reason_code": "ab-pending",
                "phase": "state", **summary}

    if game in BOT_GAMES:
        evaluator = evaluator or _bot_evaluator(g, game, matches)
    else:
        evaluator = evaluator or (lambda strat: evaluate_gnurobots(strat, matches))
    try:
        baseline_ev = evaluator(current)
        candidate_ev = evaluator(candidate)
    except Exception as exc:
        raise CornerImproveError(
            f"候補評価に失敗しました: {_safe_detail(exc)}",
            code="eval", phase="eval",
        ) from exc
    baseline_mean = float(baseline_ev.get("mean_score", 0.0) or 0.0)
    baseline_played = int(baseline_ev.get("played", 0) or 0)
    candidate_mean = float(candidate_ev.get("mean_score", 0.0) or 0.0)
    candidate_played = int(candidate_ev.get("played", 0) or 0)
    summary = {
        "game": game, "date": date_str, "corner_n": stats["n"],
        "corner_mean": round(stats["mean"], 1), "corner_best": stats["best"],
        "baseline_mean": round(baseline_mean, 1), "baseline_played": baseline_played,
        "candidate_mean": round(candidate_mean, 1), "candidate_played": candidate_played,
        "matches": matches, "margin_pct": margin_pct,
    }
    if game == "moon-buggy":
        summary.pop("margin_pct", None)
        summary["promotion_method"] = "live-abba"
    if game != "moon-buggy":
        # All other games keep the existing headless promotion gate.
        threshold = baseline_mean * (1 + margin_pct / 100.0) if baseline_mean > 0 else 0.0
        if baseline_played > 0 and candidate_played > 0 and candidate_mean > threshold:
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

    # Headless evaluation remains a validity check and context for the next
    # experiment. A lower candidate is not discarded here: the next live
    # corner compares immutable A/B snapshots at game boundaries and chooses
    # the higher-scoring arm from one complete ABBA block.
    if baseline_played <= 0 or candidate_played <= 0:
        summary.update(promoted=False, ab_staged=False)
        _append_log(g.state_dir, game, summary)
        return {"status": "kept", **summary}
    from .moon_buggy_ab import (
        MoonBuggyABError,
        stage as stage_moon_buggy_ab,
        weights_sha256,
    )

    try:
        if weights_sha256(current) == weights_sha256(candidate):
            summary.update(promoted=False, ab_staged=False, reason_code="policy-identical")
            _append_log(g.state_dir, game, summary)
            return {"status": "kept", **summary}
        experiment = stage_moon_buggy_ab(
            g.state_dir,
            current,
            candidate,
            source_date=date_str,
            headless_baseline_mean=baseline_mean,
            headless_candidate_mean=candidate_mean,
        )
    except MoonBuggyABError as exc:
        raise CornerImproveError(
            "Moon Buggy A/B candidate could not be staged", code="ab-state", phase="state"
        ) from exc
    summary.update(
        promoted=False,
        ab_staged=True,
        ab_pattern="ABBA",
        ab_experiment_id=experiment["experiment_id"],
        ab_candidate_sha256=experiment["candidate_sha256"],
    )
    _append_log(g.state_dir, game, summary)
    return {"status": "ab-staged", **summary}
