"""End-of-corner StrategyPolicy improvement for the PAPER corner (Stage 4).

After the corner restores, one job asks an AI model to adjust only the numeric
strategy parameters, validates the candidate against the same
``StrategyPolicy`` domain rules, and persists it atomically. Failures are
reported, never raised, and never write a partial/invalid policy.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import fcntl
import json
from pathlib import Path
import re
import time
from typing import Mapping

from .ai_text import AiTextError, extract_json_object, generate_text
from .corner_script import build_facts
from .models import TradingValidationError
from .strategy_store import (
    POLICY_KEYS,
    load_strategy_policy,
    policy_to_payload,
    save_strategy_policy,
)
from .strategies import StrategyPolicy


IMPROVE_LABEL = "RADIO:paper-improve"
DEFAULT_TIMEOUT = 600
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class PaperImproveError(RuntimeError):
    """Raised when the improvement candidate or AI call is invalid."""


def _safe_reason(value: BaseException | str) -> str:
    detail = str(value).replace("\n", " ")[:240]
    return detail


@contextmanager
def _singleflight(state_dir):
    """Single-flight lock so a manual retry and the detached job never race."""
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
        "あなたはPAPER暗号資産コーナーの戦略改善担当です。\n"
        "以下は今回コーナーの実データ(facts)です。事実だけを根拠にしてください。\n"
        f"{facts_json}\n\n"
        "次の5キーだけを持つJSONオブジェクト1つを出力してください。"
        "キーは変更せず、数値だけを調整します。\n"
        "- momentum_lookback: 2以上の整数\n"
        "- momentum_threshold_bps: 0より大きい数値\n"
        "- mean_reversion_lookback: 3以上の整数\n"
        "- mean_reversion_z: 0より小さい数値\n"
        "- max_notional_fraction: 0より大きく1以下の数値\n"
        "説明文・マークダウン・コードフェンスは書かないこと。"
    )


def _coerce_lookback(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool):
        raise PaperImproveError(f"{name} は整数である必要があります")
    if isinstance(value, float):
        if not value.is_integer():
            raise PaperImproveError(f"{name} は整数である必要があります")
        value = int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise PaperImproveError(f"{name} は整数である必要があります")
        try:
            value = int(text)
        except ValueError as exc:
            raise PaperImproveError(f"{name} は整数である必要があります") from exc
    if type(value) is not int:
        raise PaperImproveError(f"{name} は整数である必要があります")
    if value < minimum:
        raise PaperImproveError(f"{name} は{minimum}以上である必要があります")
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
    """Parse and range-validate a candidate policy. Malformed input raises."""
    data = extract_json_object(text)
    if not isinstance(data, dict):
        raise PaperImproveError("候補のJSONオブジェクトを抽出できません")
    missing = sorted(set(POLICY_KEYS) - set(data))
    if missing:
        raise PaperImproveError(f"候補に必要なキーがありません: {', '.join(missing)}")
    # Extra keys are ignored; the required five are still range-validated below.

    candidate = {
        "momentum_lookback": _coerce_lookback(data["momentum_lookback"], "momentum_lookback", 2),
        "momentum_threshold_bps": _coerce_decimal(
            data["momentum_threshold_bps"], "momentum_threshold_bps"
        ),
        "mean_reversion_lookback": _coerce_lookback(
            data["mean_reversion_lookback"], "mean_reversion_lookback", 3
        ),
        "mean_reversion_z": _coerce_decimal(data["mean_reversion_z"], "mean_reversion_z"),
        "max_notional_fraction": _coerce_decimal(
            data["max_notional_fraction"], "max_notional_fraction"
        ),
    }
    try:
        StrategyPolicy(**candidate)
    except TradingValidationError as exc:
        raise PaperImproveError(_safe_reason(exc)) from exc
    return candidate


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
    """Run one improvement. Never raises: returns a status summary dict."""
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
    try:
        current = load_strategy_policy(target)
        facts = build_facts(target, now=moment, policy=current)
        prompt = build_improve_prompt(facts)
    except Exception as exc:
        return {"status": "failed", "reason": f"facts:{_safe_reason(exc)}"}

    if dry_run:
        return {
            "status": "dry-run",
            "prompt_chars": len(prompt),
            "policy": policy_to_payload(current),
        }

    cleaned_agents = (agents or "").strip()
    if not cleaned_agents:
        return {"status": "skipped", "reason": "no-agents"}

    if llm is None:
        def llm(prompt_text):
            return generate_text(
                g, label=IMPROVE_LABEL, agents=cleaned_agents,
                prompt_text=prompt_text, timeout=timeout,
            )

    try:
        raw_output = llm(prompt)
        candidate = parse_policy_candidate(raw_output)
        new_policy = StrategyPolicy(**candidate)
    except AiTextError:
        return {"status": "failed", "reason": "ai-error"}
    except (PaperImproveError, TradingValidationError) as exc:
        return {"status": "failed", "reason": _safe_reason(exc)}
    except Exception as exc:
        return {"status": "failed", "reason": type(exc).__name__}

    try:
        save_strategy_policy(target, new_policy)
    except Exception as exc:
        return {"status": "failed", "reason": f"save:{_safe_reason(exc)}"}

    return {
        "status": "improved",
        "policy": policy_to_payload(new_policy),
        "changed": new_policy != current,
    }
