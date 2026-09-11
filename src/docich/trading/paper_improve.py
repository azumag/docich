"""End-of-corner PAPER strategy improvement.

The preferred path lets the AI synthesize a validated declarative PAPER-only
strategy experiment from allowlisted features. Legacy five-parameter policy
output remains accepted for backward compatibility. No generated code is ever
executed and no experiment is promoted to live trading automatically.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Mapping

from .ai_text import AiTextError, extract_json_object, generate_text
from .corner_script import build_facts
from .models import TradingValidationError
from .strategy_lab import (
    StrategyExperiment,
    StrategyLabError,
    evaluate_experiment,
    experiment_from_mapping,
    experiment_to_payload,
    load_strategy_experiment,
    persist_evaluation,
    save_pending_experiment,
    save_strategy_experiment,
)
from .strategy_store import (
    POLICY_KEYS,
    load_strategy_policy,
    policy_to_payload,
    save_strategy_policy,
)
from .strategies import StrategyPolicy


IMPROVE_LABEL = "RADIO:paper-improve"
DEFAULT_TIMEOUT = 600
STATUS_FILENAME = "paper_improve_status.json"
MIN_EXPERIMENT_CLOSED_SELLS = 8
MIN_EXPERIMENT_AGE_S = 24 * 3600


class PaperImproveError(RuntimeError):
    """Raised when an improvement candidate or AI call is invalid."""


def _safe_reason(value: BaseException | str) -> str:
    return str(value).replace("\n", " ")[:240]


def _write_improve_status(
    trading_dir,
    *,
    status: str,
    phase: str,
    progress: int,
    started_at: float,
    updated_at: float,
    detail: str = "",
    changed: bool | None = None,
) -> Path:
    target = Path(trading_dir) / STATUS_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    payload: dict[str, object] = {
        "schema_version": 1,
        "source": "paper",
        "status": str(status)[:32],
        "phase": str(phase)[:32],
        "progress": max(0, min(100, int(progress))),
        "detail": _safe_reason(detail) if detail else "",
        "started_at": float(started_at),
        "updated_at": float(updated_at),
    }
    if changed is not None:
        payload["changed"] = bool(changed)
    if status in {"improved", "failed", "skipped", "dry-run"}:
        payload["completed_at"] = float(updated_at)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    finally:
        tmp.unlink(missing_ok=True)
    return target


@contextmanager
def _singleflight(state_dir):
    path = Path(state_dir) / "locks" / "paper-improve.lock"
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


def build_improve_prompt(facts: Mapping[str, object]) -> str:
    facts_json = json.dumps(dict(facts), ensure_ascii=False, sort_keys=True)
    return (
        "あなたはPAPER暗号資産BOTの戦略研究者です。実運用ではなくPAPERなので、"
        "既存戦略の微調整に閉じず、仮説を大胆に試してください。ただし生成コードは使わず、"
        "以下の宣言的な戦略実験JSONだけを作ります。事実はfactsだけを根拠にします。\n"
        f"facts={facts_json}\n\n"
        "research.improvement_hints があれば有力な仮説として検討しますが、ニュース単発で"
        "因果を断定せず、取引結果と市場データに照らして反証可能なルールにしてください。\n"
        "次の形のJSONオブジェクト1つだけを返してください。\n"
        "{\"strategy_experiment\":{\n"
        "  \"experiment_id\":\"短いASCII識別子\",\n"
        "  \"name\":\"戦略名\",\n"
        "  \"thesis\":\"何を狙い、何なら失敗とみなすか\",\n"
        "  \"entry_rules\":[{\"rule_id\":\"entry-1\",\"combine\":\"all|any\","
        "\"max_notional_fraction\":0.01〜0.30,\"conditions\":[...] }],\n"
        "  \"exit_rules\":[{\"rule_id\":\"exit-1\",\"combine\":\"all|any\","
        "\"conditions\":[...] }],\n"
        "  \"max_pair_correlation\":0〜1\n"
        "}}\n"
        "condition は {\"feature\":...,\"lookback\":2〜24,\"op\":\">=|<=|>|<\","
        "\"threshold\":数値}。entryで使えるfeatureは return_bps, zscore, rsi, "
        "sma_gap_bps, volatility_bps, breakout_bps, drawdown_bps。exitではこれらに加えて "
        "pnl_bps, hold_minutes が使えます。pnl_bps と hold_minutes にlookbackは不要です。\n"
        "複数条件のAND/ORを積極的に使ってよいです。例えば『含み益がある AND モメンタム反転』、"
        "『高ボラ OR 最大保有時間』のような退出も可能です。1つの指標だけに固定しないでください。\n"
        "既存互換キー momentum_lookback, momentum_threshold_bps, mean_reversion_lookback, "
        "mean_reversion_z, max_notional_fraction は旧形式として引き続き受理されますが、"
        "原則はstrategy_experimentを返してください。説明文やMarkdownは不要です。"
    )


def _coerce_lookback(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool):
        raise PaperImproveError(f"{name} は整数である必要があります")
    if isinstance(value, float):
        if not value.is_integer():
            raise PaperImproveError(f"{name} は整数である必要があります")
        value = int(value)
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except (TypeError, ValueError) as exc:
            raise PaperImproveError(f"{name} は整数である必要があります") from exc
    if type(value) is not int or value < minimum:
        raise PaperImproveError(f"{name} は{minimum}以上の整数である必要があります")
    return value


def _coerce_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise PaperImproveError(f"{name} は数値である必要があります")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PaperImproveError(f"{name} は数値である必要があります") from exc
    if not result.is_finite():
        raise PaperImproveError(f"{name} は有限の数値である必要があります")
    return result


def parse_policy_candidate(text: str) -> dict:
    """Backward-compatible parser for the old five-parameter response."""
    data = extract_json_object(text)
    if not isinstance(data, dict):
        raise PaperImproveError("候補のJSONオブジェクトを抽出できません")
    missing = sorted(set(POLICY_KEYS) - set(data))
    if missing:
        raise PaperImproveError(f"候補に必要なキーがありません: {', '.join(missing)}")
    candidate = {
        "momentum_lookback": _coerce_lookback(data["momentum_lookback"], "momentum_lookback", 2),
        "momentum_threshold_bps": _coerce_decimal(data["momentum_threshold_bps"], "momentum_threshold_bps"),
        "mean_reversion_lookback": _coerce_lookback(data["mean_reversion_lookback"], "mean_reversion_lookback", 3),
        "mean_reversion_z": _coerce_decimal(data["mean_reversion_z"], "mean_reversion_z"),
        "max_notional_fraction": _coerce_decimal(data["max_notional_fraction"], "max_notional_fraction"),
    }
    try:
        StrategyPolicy(**candidate)
    except TradingValidationError as exc:
        raise PaperImproveError(_safe_reason(exc)) from exc
    return candidate


def parse_experiment_candidate(text: str) -> StrategyExperiment:
    data = extract_json_object(text)
    if not isinstance(data, Mapping):
        raise PaperImproveError("戦略実験JSONを抽出できません")
    raw = data.get("strategy_experiment")
    if not isinstance(raw, Mapping):
        raise PaperImproveError("strategy_experiment がありません")
    try:
        return experiment_from_mapping(raw, activated_at=0.0)
    except (StrategyLabError, TradingValidationError, TypeError, ValueError) as exc:
        raise PaperImproveError(_safe_reason(exc)) from exc


def _should_rotate_experiment(
    active: StrategyExperiment | None,
    evaluation: Mapping[str, object] | None,
    *,
    now: float,
) -> bool:
    if active is None:
        return True
    try:
        closed = int((evaluation or {}).get("closed_sells", 0) or 0)
    except (TypeError, ValueError):
        closed = 0
    age = max(0.0, float(now) - float(active.activated_at))
    return closed >= MIN_EXPERIMENT_CLOSED_SELLS or age >= MIN_EXPERIMENT_AGE_S


def run_paper_improve(
    g,
    *,
    trading_dir,
    agents: str,
    dry_run: bool = False,
    llm=None,
    now=None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    with _singleflight(g.state_dir) as single:
        if not single:
            return {"status": "skipped", "reason": "already-running"}
        return _run_paper_improve(
            g, trading_dir=trading_dir, agents=agents, dry_run=dry_run,
            llm=llm, now=now, timeout=timeout,
        )


def _run_paper_improve(
    g,
    *,
    trading_dir,
    agents: str,
    dry_run: bool,
    llm,
    now,
    timeout: int,
) -> dict:
    target = Path(trading_dir)
    moment = time.time() if now is None else float(now)
    started_at = moment

    def publish(status: str, phase: str, progress: int, detail: str = "", changed=None) -> None:
        try:
            stamp = time.time() if now is None else float(now)
            _write_improve_status(
                target, status=status, phase=phase, progress=progress,
                started_at=started_at, updated_at=stamp, detail=detail, changed=changed,
            )
        except Exception:
            pass

    publish("running", "facts", 10, "今回データと戦略実験を集計中")
    try:
        current = load_strategy_policy(target)
        facts = build_facts(target, now=moment, policy=current)
        active_experiment = load_strategy_experiment(target)
        evaluation: dict[str, object] | None = None
        if active_experiment is not None:
            evaluation = evaluate_experiment(
                target, active_experiment, capital_jpy=facts.get("capital_jpy", "0")
            )
            facts["active_strategy_experiment"] = experiment_to_payload(active_experiment)
            facts["experiment_evaluation"] = evaluation
            if not dry_run:
                persist_evaluation(target, evaluation, active_experiment)
        prompt = build_improve_prompt(facts)
    except Exception as exc:
        reason = f"facts:{_safe_reason(exc)}"
        publish("failed", "facts", 10, reason)
        return {"status": "failed", "reason": reason}

    if dry_run:
        publish("dry-run", "done", 100, "dry-run")
        return {
            "status": "dry-run",
            "prompt_chars": len(prompt),
            "policy": policy_to_payload(current),
            "experiment": None if active_experiment is None else experiment_to_payload(active_experiment),
        }

    cleaned_agents = (agents or "").strip()
    if not cleaned_agents:
        publish("skipped", "done", 100, "no-agents")
        return {"status": "skipped", "reason": "no-agents"}

    if llm is None:
        def llm(prompt_text):
            return generate_text(
                g, label=IMPROVE_LABEL, agents=cleaned_agents,
                prompt_text=prompt_text, timeout=timeout,
            )

    publish("running", "generate", 35, "AIに次の戦略実験を設計させています")
    try:
        raw_output = llm(prompt)
    except AiTextError:
        publish("failed", "generate", 35, "ai-error")
        return {"status": "failed", "reason": "ai-error"}
    except Exception as exc:
        reason = type(exc).__name__
        publish("failed", "generate", 35, reason)
        return {"status": "failed", "reason": reason}

    publish("running", "validate", 75, "戦略実験を検証中")
    experiment: StrategyExperiment | None = None
    legacy_policy: StrategyPolicy | None = None
    experiment_error: PaperImproveError | None = None
    try:
        experiment = parse_experiment_candidate(raw_output)
    except PaperImproveError as exc:
        experiment_error = exc
        try:
            legacy_policy = StrategyPolicy(**parse_policy_candidate(raw_output))
        except (PaperImproveError, TradingValidationError) as legacy_exc:
            reason = _safe_reason(experiment_error or legacy_exc)
            publish("failed", "validate", 75, reason)
            return {"status": "failed", "reason": reason}

    publish("running", "save", 90, "検証済み戦略をPAPERへ反映中")
    try:
        if experiment is not None:
            if _should_rotate_experiment(active_experiment, evaluation, now=moment):
                save_strategy_experiment(target, experiment, activated_at=moment)
                changed = active_experiment is None or experiment_to_payload(experiment) != experiment_to_payload(active_experiment)
                detail = "新しいPAPER戦略実験を開始"
                result = {
                    "status": "improved",
                    "kind": "strategy-experiment",
                    "experiment": experiment_to_payload(experiment),
                    "changed": changed,
                    "activated": True,
                }
            else:
                save_pending_experiment(target, experiment, proposed_at=moment)
                changed = True
                detail = "現行実験の評価中のため次候補を保存"
                result = {
                    "status": "improved",
                    "kind": "strategy-experiment",
                    "experiment": experiment_to_payload(experiment),
                    "changed": True,
                    "activated": False,
                    "pending": True,
                }
        else:
            assert legacy_policy is not None
            save_strategy_policy(target, legacy_policy)
            changed = legacy_policy != current
            detail = "従来パラメータを更新" if changed else "候補は現行パラメータと同一"
            result = {
                "status": "improved",
                "kind": "legacy-policy",
                "policy": policy_to_payload(legacy_policy),
                "changed": changed,
            }
    except Exception as exc:
        reason = f"save:{_safe_reason(exc)}"
        publish("failed", "save", 90, reason)
        return {"status": "failed", "reason": reason}

    publish("improved", "done", 100, detail, changed=changed)
    return result
