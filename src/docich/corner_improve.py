"""End-of-corner improvement for corner games.

Replaces the continuous daemon rhythm for corner games: during the corner
only match logs accumulate; when the corner ends, one improvement job runs.
The candidate comes from an LLM (sorengame-style delegation via
docich.ai_generate), is evaluated against the current strategy with the same
headless evaluator, and is promoted only past the margin gate.  Promotion
reuses docich.resolver.improve history/rendering, so the next corner announces
the strategy diff automatically (see retro_corner.describe_strategy_change).
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import math
import os
import re
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
MIN_CANDIDATE_WEIGHT = 0.001
MAX_CANDIDATE_WEIGHT = 1e6

# Durable failure metadata is an enum contract, not an exception serialization
# surface. Keep unknown/future/injected exception attributes from becoming
# free-text state; diagnostics applies the same allowlist at its read boundary.
CORNER_IMPROVE_REASON_CODES = frozenset({
    "state-read", "corner-window", "gate-disabled", "llm-call", "llm-rc",
    "llm-empty", "llm-format", "llm-keys", "llm-values", "llm-unexpected",
    "eval", "lane-busy", "policy-promoted", "policy-incomplete", "policy-faults",
    "policy-below-margin", "policy-not-significant", "policy-identical",
    "policy-invalid", "policy-kept", "policy-eval", "unexpected",
})

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


def _minimum_candidate_weight(game: str) -> float:
    """Bastet の hard_drop は 0 で soft drop を選べる。"""
    return 0.0 if game == "bastet" else MIN_CANDIDATE_WEIGHT


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


def build_prompt(*, game: str, stats: dict, current: dict, previous: dict) -> str:
    basis = stats.get("basis", "live scorelog")
    minimum = _minimum_candidate_weight(game)
    game_guidance = ""
    if game == "bastet":
        game_guidance = (
            "\nBastet の hard_drop は 0.5 以上で Enter によるハードドロップ、"
            "0.5 未満で Down によるソフトドロップです。0.0 は有効な候補です。"
            "評価では 0.0 と 1.0 の方策を比較してください。\n"
        )
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
各値は有限なJSON数値で {minimum:g} 以上 {MAX_CANDIDATE_WEIGHT:g} 以下にしてください。{game_guidance}
出力はJSONオブジェクト1つのみ。説明文は書かず、```jsonフェンスで囲むこと。
"""


def parse_candidate(
    text: str,
    allowed_keys: set[str],
    *,
    minimum: float = MIN_CANDIDATE_WEIGHT,
) -> dict:
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
        try:
            numeric_value = float(value)
        except (OverflowError, ValueError):
            numeric_value = float("inf")
        outside_range = not minimum <= numeric_value <= MAX_CANDIDATE_WEIGHT
        if not math.isfinite(numeric_value) or outside_range:
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
        minimum_weight = _minimum_candidate_weight(game)
        candidate_delta = parse_candidate(raw_output, proposable, minimum=minimum_weight)
    except CornerImproveError:
        raise
    except Exception as exc:
        raise CornerImproveError(
            f"候補生成に失敗しました: {_safe_detail(exc)}",
            code="llm-unexpected", phase="llm",
        ) from exc
    candidate = dict(current)
    candidate.update(candidate_delta)

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
    # Promotion gate は current/candidate を同じ headless evaluator で比較する。
    # 実配信ログは候補生成の文脈・外部品質の観測値として保持するが、異なる
    # 実行条件のスコアを直接 promotion threshold に混ぜない。
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
