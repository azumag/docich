"""Owner-approved initial LIVE policy: export, preview and guarded activation.

The executable experiment is taken from the existing PAPER evaluation history
(``entries[].experiment``) written by ``strategy_lab._record_evaluation_history``.
The history is read-only here; the only write in this module is the owner's explicit
initial activation of the ledger.  A WebUI request never supplies a filesystem path,
and the pasted import is limited to canonical JSON plus its independently supplied
full experiment digest.
"""
from __future__ import annotations

import hmac
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..strategy_lab import EVALUATION_HISTORY_FILENAME, StrategyExperiment
from ..strategy_lab import StrategyLabError
from ..models import TradingValidationError
from . import policy as live_policy
from .canonical import (CanonicalError, canonical_json, confirmation_token,
                        is_sha256_hex)
from .lease import LeaseHeld, acquire_writer_lease, release_writer_lease
from .ledger import LiveLedger, LiveLedgerError, NO_LEDGER
from .paths import LiveLayout, live_layout
from .policy import INITIAL_SYMBOLS, LivePolicySet

# The one experiment the owner approved as the initial LIVE policy.  This is an
# identity, not a hidden specification or digest: the executable rules must come
# from the history (or from an explicit operator import) and are revalidated.
INITIAL_EXPERIMENT_ID = "snapback_harvest_v25"
MAX_SNAPSHOT_BYTES = live_policy.MAX_SNAPSHOT_BYTES
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_TEXT = 200
_MAX_TOKEN = 10
_TOKEN_CHARS = frozenset("0123456789abcdef")

_ERROR_MESSAGES = {
    "history_missing": "PAPER の評価履歴が見つかりません。",
    "history_unreadable": "PAPER の評価履歴を安全に読めません。",
    "history_invalid": "PAPER の評価履歴の形式が不正です。",
    "candidate_missing": "初期方針の候補が評価履歴にありません。",
    "candidate_unsupported": "オーナー承認済みの初期方針以外の実験だけが履歴にあります。",
    "candidate_ambiguous": "同じ初期方針 ID で異なる内容が複数あります。一意に決められません。",
    "candidate_invalid": "初期方針の候補が厳密な検証に通りません。",
    "snapshot_invalid": "スナップショットが厳密な検証に通りません。",
    "snapshot_too_large": "スナップショットが大きすぎます。",
    "digest_invalid": "digest は 64 文字の小文字 hex で指定してください。",
    "digest_mismatch": "スナップショットと digest が一致しません。",
    "import_mismatch": "評価履歴にある初期方針とインポート内容が一致しません。",
    "policy_invalid": "初期方針のポリシーを作れません。",
    "operator_invalid": "operator は 1..200 文字で指定してください。",
    "note_invalid": "note は 1..200 文字で指定してください。",
    "source_invalid": "取得元の指定が不正です。",
    "stale_preview": "確認後に候補・台帳・有効方針が変わったため、もう一度確認してください。",
    "active_policy_conflict": "別の有効な方針があるため、初期セットアップでは上書きしません。",
    "lease_held": "LIVE worker が writer lease を保持しています。停止してから再実行してください。",
    "ledger_unavailable": "LIVE 台帳を安全に読めません。",
    "activation_refused": "台帳が初期方針の有効化を拒否しました。",
}


class InitialSetupError(ValueError):
    """A fixed public error code; the UI maps it to a safe Japanese message."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

    @property
    def message(self) -> str:
        return _ERROR_MESSAGES.get(self.code, "初期方針の操作を拒否しました。")


@dataclass(frozen=True)
class InitialCandidate:
    spec: StrategyExperiment
    experiment_digest: str
    policy_set: LivePolicySet
    snapshot_json: str
    source: str


@dataclass(frozen=True)
class _LedgerState:
    present: bool
    ledger_id: str
    active_digest: str | None


class _DuplicateKey(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey("duplicate JSON key")
        result[key] = value
    return result


def _no_constant(value):
    raise ValueError("non-finite JSON number")


def _json(data: bytes) -> Any:
    try:
        text = data.decode("utf-8")
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid JSON") from exc


def _read_regular(path: Path, limit: int, *, missing: str, unreadable: str,
                  too_large: str, invalid: str) -> bytes:
    """Read a bounded regular file without following a symlink or blocking on a FIFO."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError as exc:
        raise InitialSetupError(missing) from exc
    except OSError as exc:
        raise InitialSetupError(unreadable) from exc
    try:
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise InitialSetupError(unreadable)
            if before.st_size > limit:
                raise InitialSetupError(too_large)
            chunks = []
            remaining = limit + 1
            while remaining > 0:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(fd)
        except InitialSetupError:
            raise
        except OSError as exc:
            raise InitialSetupError(invalid) from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    if len(data) > limit or len(data) != after.st_size or (
            before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise InitialSetupError(invalid)
    return data


def _text(value: object, code: str) -> str:
    if not isinstance(value, str):
        raise InitialSetupError(code)
    text = value.strip()
    if not text or len(text) > MAX_TEXT:
        raise InitialSetupError(code)
    return text


def _candidate(spec: StrategyExperiment, source: str) -> InitialCandidate:
    if spec.experiment_id != INITIAL_EXPERIMENT_ID:
        raise InitialSetupError("candidate_unsupported")
    try:
        policy_set = live_policy.build_policy_set(spec, INITIAL_SYMBOLS)
    except (live_policy.LivePolicyError, CanonicalError, TradingValidationError,
            StrategyLabError, TypeError, ValueError) as exc:
        raise InitialSetupError("policy_invalid") from exc
    if len(policy_set.policies) != len(INITIAL_SYMBOLS) or tuple(policy_set.policies) != INITIAL_SYMBOLS:
        raise InitialSetupError("policy_invalid")
    try:
        snapshot_bytes = canonical_json(live_policy.experiment_payload(spec))
        if len(snapshot_bytes) > MAX_SNAPSHOT_BYTES:
            raise InitialSetupError("candidate_invalid")
        snapshot_json = snapshot_bytes.decode("utf-8")
        digest = live_policy.experiment_digest(spec)
    except InitialSetupError:
        raise
    except (CanonicalError, TypeError, ValueError) as exc:
        raise InitialSetupError("candidate_invalid") from exc
    return InitialCandidate(spec, digest, policy_set, snapshot_json, source)


def _history_candidate(trading_dir: Path) -> InitialCandidate:
    path = Path(trading_dir) / EVALUATION_HISTORY_FILENAME
    raw = _read_regular(path, MAX_HISTORY_BYTES, missing="history_missing",
                        unreadable="history_unreadable", too_large="history_invalid",
                        invalid="history_invalid")
    try:
        data = _json(raw)
    except ValueError as exc:
        raise InitialSetupError("history_invalid") from exc
    if (not isinstance(data, Mapping) or type(data.get("schema_version")) is not int
            or data.get("schema_version") != 1):
        raise InitialSetupError("history_invalid")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise InitialSetupError("history_invalid")
    if not entries:
        raise InitialSetupError("candidate_missing")
    candidates: dict[str, InitialCandidate] = {}
    saw_other = False
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        experiment = entry.get("experiment")
        top_id = entry.get("experiment_id")
        inner_id = experiment.get("experiment_id") if isinstance(experiment, Mapping) else None
        if top_id not in (None, INITIAL_EXPERIMENT_ID) and inner_id != INITIAL_EXPERIMENT_ID:
            saw_other = True
            continue
        if not isinstance(experiment, Mapping):
            if top_id == INITIAL_EXPERIMENT_ID or inner_id == INITIAL_EXPERIMENT_ID:
                raise InitialSetupError("candidate_invalid")
            saw_other = True
            continue
        try:
            spec = live_policy.strict_experiment(experiment)
        except (live_policy.LivePolicyError, StrategyLabError, TradingValidationError,
                TypeError, ValueError) as exc:
            if top_id == INITIAL_EXPERIMENT_ID or inner_id == INITIAL_EXPERIMENT_ID:
                raise InitialSetupError("candidate_invalid") from exc
            saw_other = True
            continue
        if spec.experiment_id != INITIAL_EXPERIMENT_ID:
            if top_id == INITIAL_EXPERIMENT_ID or inner_id == INITIAL_EXPERIMENT_ID:
                raise InitialSetupError("candidate_invalid")
            saw_other = True
            continue
        if top_id is not None and top_id != spec.experiment_id:
            raise InitialSetupError("candidate_invalid")
        candidate = _candidate(spec, "history")
        candidates[candidate.experiment_digest] = candidate
    if not candidates:
        raise InitialSetupError("candidate_unsupported" if saw_other else "candidate_missing")
    if len(candidates) != 1:
        raise InitialSetupError("candidate_ambiguous")
    return next(iter(candidates.values()))


def candidate_from_import(snapshot_text: object, expected_digest: object) -> InitialCandidate:
    """Strictly parse an operator-pasted canonical snapshot and its full digest."""
    if not is_sha256_hex(expected_digest):
        raise InitialSetupError("digest_invalid")
    if not isinstance(snapshot_text, str):
        raise InitialSetupError("snapshot_invalid")
    raw = snapshot_text.encode("utf-8")
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise InitialSetupError("snapshot_too_large")
    if not snapshot_text.strip():
        raise InitialSetupError("snapshot_invalid")
    try:
        data = _json(raw)
    except ValueError as exc:
        raise InitialSetupError("snapshot_invalid") from exc
    if not isinstance(data, Mapping):
        raise InitialSetupError("snapshot_invalid")
    try:
        spec = live_policy.strict_experiment(data)
    except (live_policy.LivePolicyError, StrategyLabError, TradingValidationError,
            TypeError, ValueError) as exc:
        raise InitialSetupError("snapshot_invalid") from exc
    if live_policy.experiment_digest(spec) != expected_digest:
        raise InitialSetupError("digest_mismatch")
    return _candidate(spec, "import")


def _candidate_view(candidate: InitialCandidate) -> dict:
    return {
        "source": candidate.source,
        "experiment_id": candidate.spec.experiment_id,
        "experiment_digest": candidate.experiment_digest,
        "policy_set_digest": candidate.policy_set.digest,
        "symbols": list(INITIAL_SYMBOLS),
        "snapshot": candidate.snapshot_json,
    }


def _paper_dir(state_dir) -> Path:
    """The PAPER directory under the configured state dir; never a symlink or a write."""
    trading = Path(state_dir) / "trading"
    if trading.is_symlink():
        raise InitialSetupError("history_unreadable")
    return trading


def export_history(state_dir) -> dict:
    """Read-only export from the authoritative PAPER history (no ledger, no writes)."""
    candidate = _history_candidate(_paper_dir(state_dir))
    return {"schema_version": 1, "kind": "docich-live-initial-policy-export", **_candidate_view(candidate)}


def _ledger_state(layout: LiveLayout) -> _LedgerState:
    if layout.root.is_symlink() or layout.ledger_path.is_symlink():
        raise InitialSetupError("ledger_unavailable")
    if not layout.ledger_path.is_file():
        return _LedgerState(False, NO_LEDGER, None)
    try:
        ledger = LiveLedger.open_readonly(layout)
    except Exception as exc:
        raise InitialSetupError("ledger_unavailable") from exc
    try:
        with ledger.read_cursor() as cur:
            row = cur.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()
        active = ledger.active_policy_set_digest()
    except Exception as exc:
        raise InitialSetupError("ledger_unavailable") from exc
    finally:
        try:
            ledger.close()
        except Exception:
            pass
    if row is None:
        raise InitialSetupError("ledger_unavailable")
    return _LedgerState(True, row[0], active)


def _content(candidate: InitialCandidate, source: str, active_digest: str | None,
             operator: str, note: str) -> dict[str, str]:
    return {
        "source": source,
        "experiment_digest": candidate.experiment_digest,
        "policy_set_digest": candidate.policy_set.digest,
        "active_policy_set_digest": active_digest or "none",
        "operator": operator,
        "note": note,
    }


def _check_import_history(trading_dir: Path, candidate: InitialCandidate) -> None:
    """Allow an import only when PAPER has no candidate, or the full digest matches it."""
    try:
        approved = _history_candidate(trading_dir)
    except InitialSetupError as exc:
        if exc.code in ("history_missing", "candidate_missing", "candidate_unsupported"):
            return
        raise
    if approved.experiment_digest != candidate.experiment_digest:
        raise InitialSetupError("import_mismatch")


def _load_candidate(state_dir, source, snapshot_text, expected_digest) -> InitialCandidate:
    if source == "history":
        return _history_candidate(_paper_dir(state_dir))
    if source == "import":
        candidate = candidate_from_import(snapshot_text, expected_digest)
        _check_import_history(_paper_dir(state_dir), candidate)
        return candidate
    raise InitialSetupError("source_invalid")


def preview_policy(state_dir, *, source: object, snapshot: object = None,
                   experiment_digest: object = None, operator: object = None,
                   note: object = None) -> dict:
    """Validate, re-read and issue a token; no ledger or control write happens here."""
    op = _text(operator, "operator_invalid")
    nt = _text(note, "note_invalid")
    candidate = _load_candidate(state_dir, source, snapshot, experiment_digest)
    layout = live_layout(state_dir)
    state = _ledger_state(layout)
    if state.active_digest is not None and state.active_digest != candidate.policy_set.digest:
        raise InitialSetupError("active_policy_conflict")
    content = _content(candidate, candidate.source, state.active_digest, op, nt)
    token = confirmation_token(content, state.ledger_id)
    return {
        "schema_version": 1,
        "kind": "docich-live-initial-policy-preview",
        **_candidate_view(candidate),
        "active_policy_set_digest": state.active_digest,
        "already_active": state.active_digest == candidate.policy_set.digest,
        "ledger_present": state.present,
        "token": token,
    }


def _already_active(candidate: InitialCandidate) -> dict:
    return {
        "schema_version": 1,
        "kind": "docich-live-initial-policy-result",
        "already_active": True,
        "activated": False,
        "experiment_id": candidate.spec.experiment_id,
        "experiment_digest": candidate.experiment_digest,
        "policy_set_digest": candidate.policy_set.digest,
        "active_policy_set_digest": candidate.policy_set.digest,
        "symbols": list(INITIAL_SYMBOLS),
        "reason": "already_active",
    }


def _activated(candidate: InitialCandidate) -> dict:
    return {
        "schema_version": 1,
        "kind": "docich-live-initial-policy-result",
        "already_active": False,
        "activated": True,
        "experiment_id": candidate.spec.experiment_id,
        "experiment_digest": candidate.experiment_digest,
        "policy_set_digest": candidate.policy_set.digest,
        "active_policy_set_digest": candidate.policy_set.digest,
        "symbols": list(INITIAL_SYMBOLS),
        "reason": "owner_initial",
    }


def _confirm_token(value: object) -> str:
    """Accept only the 10 lowercase hex token format before any timing-safe compare."""
    if not isinstance(value, str):
        raise InitialSetupError("stale_preview")
    token = value.strip()
    if len(token) != _MAX_TOKEN or any(ch not in _TOKEN_CHARS for ch in token):
        raise InitialSetupError("stale_preview")
    return token


def _token_matches(token: str, candidate: InitialCandidate, active_digest: str | None,
                   ledger_id: str, operator: str, note: str) -> bool:
    expected = confirmation_token(
        _content(candidate, candidate.source, active_digest, operator, note), ledger_id)
    return hmac.compare_digest(token, expected)


def _confirmed_any_state(token: str, candidate: InitialCandidate, before: _LedgerState,
                         operator: str, note: str) -> bool:
    """Match the preview state or the exact pre-activation form of a successful retry."""
    states = [(before.active_digest, before.ledger_id)]
    if before.active_digest == candidate.policy_set.digest:
        # The first success may have changed ``active`` from none and/or created the
        # ledger that preview saw as ``no-ledger``.  Accept only those exact forms so
        # a lost response or a second click is an idempotent no-op.
        states.append((None, before.ledger_id))
        if before.ledger_id != NO_LEDGER:
            states.append((None, NO_LEDGER))
    return any(_token_matches(token, candidate, active, ledger_id, operator, note)
               for active, ledger_id in states)


def activate_policy(state_dir, *, source: object, snapshot: object = None,
                    experiment_digest: object = None, operator: object = None,
                    note: object = None, confirm: object = None,
                    now: float) -> dict:
    """Activate the initial policy exactly once after a previewed token.

    The candidate, token and ledger's active policy are validated even when the set is
    already active.  A different active policy is never overwritten; the same policy is
    a safe no-op.  A retry whose preview saw an absent ledger can still match after the
    first success changed both the ledger id and the active digest.  If an empty ledger
    was created after that preview (or by a failed attempt), the token is stale and the
    operator must preview again; this path never writes.
    """
    op = _text(operator, "operator_invalid")
    nt = _text(note, "note_invalid")
    token = _confirm_token(confirm)
    candidate = _load_candidate(state_dir, source, snapshot, experiment_digest)
    layout = live_layout(state_dir)
    before = _ledger_state(layout)
    if not _confirmed_any_state(token, candidate, before, op, nt):
        raise InitialSetupError("stale_preview")
    if before.active_digest is not None:
        if before.active_digest == candidate.policy_set.digest:
            return _already_active(candidate)
        raise InitialSetupError("active_policy_conflict")

    # Do not create or replace a ledger after a preview that saw another state.
    if layout.root.is_symlink() or layout.ledger_path.is_symlink():
        raise InitialSetupError("ledger_unavailable")
    present_now = layout.ledger_path.is_file()
    if present_now != before.present:
        raise InitialSetupError("stale_preview")

    ledger = None
    writer = None
    try:
        try:
            ledger = LiveLedger.open(layout, paper_dir=_paper_dir(state_dir), now=now)
        except InitialSetupError:
            raise
        except Exception as exc:
            raise InitialSetupError("ledger_unavailable") from exc
        try:
            writer = acquire_writer_lease(ledger, layout, now=now)
        except LeaseHeld as exc:
            # A first request may have completed between our read and this attempt.
            try:
                after = _ledger_state(layout)
            except InitialSetupError:
                raise InitialSetupError("lease_held") from exc
            if after.active_digest == candidate.policy_set.digest:
                return _already_active(candidate)
            raise InitialSetupError("lease_held") from exc
        except Exception as exc:
            raise InitialSetupError("ledger_unavailable") from exc
        fresh = _load_candidate(state_dir, source, snapshot, experiment_digest)
        if (fresh.experiment_digest != candidate.experiment_digest
                or fresh.policy_set.digest != candidate.policy_set.digest):
            raise InitialSetupError("stale_preview")
        try:
            active_now = ledger.active_policy_set_digest()
        except Exception as exc:
            raise InitialSetupError("ledger_unavailable") from exc
        if active_now is not None:
            if active_now == fresh.policy_set.digest:
                return _already_active(fresh)
            raise InitialSetupError("active_policy_conflict")
        try:
            with ledger.read_cursor() as cur:
                row = cur.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()
        except Exception as exc:
            raise InitialSetupError("ledger_unavailable") from exc
        current_id = row[0] if row else NO_LEDGER
        if current_id == NO_LEDGER:
            raise InitialSetupError("ledger_unavailable")
        if before.ledger_id != NO_LEDGER and current_id != before.ledger_id:
            raise InitialSetupError("stale_preview")
        try:
            ledger.record_initial_activation(writer, fresh.policy_set, operator=op, note=nt, now=now)
        except (LiveLedgerError, ValueError) as exc:
            # Fail closed, but report an already-completed first request as success.
            try:
                active_after = ledger.active_policy_set_digest()
            except Exception:
                active_after = None
            if active_after == fresh.policy_set.digest:
                return _already_active(fresh)
            if active_after is not None:
                raise InitialSetupError("active_policy_conflict") from exc
            raise InitialSetupError("activation_refused") from exc
        except Exception as exc:
            raise InitialSetupError("activation_refused") from exc
        return _activated(fresh)
    finally:
        if writer is not None:
            try:
                release_writer_lease(ledger, writer, now=now)
            except Exception:
                pass
        if ledger is not None:
            ledger.close()


__all__ = [
    "INITIAL_EXPERIMENT_ID", "MAX_SNAPSHOT_BYTES", "MAX_HISTORY_BYTES",
    "InitialSetupError", "InitialCandidate", "candidate_from_import",
    "export_history", "preview_policy", "activate_policy",
]
