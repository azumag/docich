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
import os
from pathlib import Path
import re
import tempfile
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
STATUS_FILENAME = "paper_improve_status.json"
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class PaperImproveError(RuntimeError):
    """Raised when the improvement candidate or AI call is invalid."""


def _safe_reason(value: BaseException | str) -> str:
    detail = str(value).replace("\n", " ")[:240]
    return detail


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
    """Atomically publish a small, non-sensitive progress document for overlays."""
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
    # `research` contains public web material and model-generated hypotheses used
    # for narration. Keep that untrusted/external lane out of the automatic policy
    # mutation prompt; reviewed follow-up can still consume the persisted research.
    trusted_facts = dict(facts)
    trusted_facts.pop("research", None)
    facts_json = json.dumps(trusted_facts, ensure_ascii=False, sort_keys=True)
    return (
        "あなたはPAPER暗号資産コーナーの戦略改善担当です。\n"
        "以下は今回コーナーの実データ(facts)です。事実だけを根拠にしてください。\n"
        f"{facts_json}\n\n"
        "外部ニュース・Wikipedia・それらを基に生成されたresearchは、"
        "この自動パラメータ更新の根拠にしないでください。\n"
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
    started_at = moment

    def publish(status: str, phase: str, progress: int, detail: str = "", changed=None) -> None:
        try:
            stamp = time.time() if now is None else float(now)
            _write_improve_status(
                target,
                status=status,
                phase=phase,
                progress=progress,
                started_at=started_at,
                updated_at=stamp,
                detail=detail,
                changed=changed,
            )
        except Exception:
            # Progress visibility must never make the trading improvement unsafe.
            pass

    publish("running", "facts", 10, "今回データを集計中")
    try:
        current = load_strategy_policy(target)
        facts = build_facts(target, now=moment, policy=current)
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

    publish("running", "generate", 35, "AIに改善候補を依頼中")
    try:
        raw_output = llm(prompt)
    except AiTextError:
        publish("failed", "generate", 35, "ai-error")
        return {"status": "failed", "reason": "ai-error"}
    except Exception as exc:
        reason = type(exc).__name__
        publish("failed", "generate", 35, reason)
        return {"status": "failed", "reason": reason}

    publish("running", "validate", 75, "改善候補を検証中")
    try:
        candidate = parse_policy_candidate(raw_output)
        new_policy = StrategyPolicy(**candidate)
    except (PaperImproveError, TradingValidationError) as exc:
        reason = _safe_reason(exc)
        publish("failed", "validate", 75, reason)
        return {"status": "failed", "reason": reason}
    except Exception as exc:
        reason = type(exc).__name__
        publish("failed", "validate", 75, reason)
        return {"status": "failed", "reason": reason}

    publish("running", "save", 90, "検証済み戦略を保存中")
    try:
        save_strategy_policy(target, new_policy)
    except Exception as exc:
        reason = f"save:{_safe_reason(exc)}"
        publish("failed", "save", 90, reason)
        return {"status": "failed", "reason": reason}

    changed = new_policy != current
    publish(
        "improved", "done", 100,
        "戦略パラメータを更新" if changed else "候補は現行戦略と同一",
        changed=changed,
    )
    return {
        "status": "improved",
        "policy": policy_to_payload(new_policy),
        "changed": changed,
    }
