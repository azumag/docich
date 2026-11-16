"""The owner's LIVE command line (L4a): status, cashflow list, cashflow add and activate.

    python -m docich.trading.live_owner --state-dir <dir> status
    python -m docich.trading.live_owner --state-dir <dir> cashflow list
    python -m docich.trading.live_owner --state-dir <dir> cashflow add --kind deposit ...
    python -m docich.trading.live_owner --state-dir <dir> activate --snapshot ... --experiment-digest ...
    python -m docich.trading.live_owner --state-dir <dir> unknown list|show|resolve ...

The two writers (``cashflow add`` and ``activate``) print a token first and act only when the owner
repeats it.  ``unknown resolve`` does the same and records the owner's evidence.  Nothing here talks
to an exchange except the checks in ``unknown show|resolve`` (through a read-only reader).

Exit codes: 0 ok, 1 refused, 2 usage, 5 the writer lease is held by the live worker.
Money is a Decimal from text; floats never appear (``exchange.dec``/``dec_text``).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, TextIO

from . import revision as revision_module
from ..live import enable as live_enable, executor as live_executor, ingest as live_ingest, lease as live_lease
from ..live import policy as live_policy, reconcile as live_reconcile, safety, views as live_views
from ..live.canonical import canonical_json, sha256_hex
from ..live.exchange import ExchangeError, OrderNotFound, dec, dec_text
from ..live.ledger import Cashflow, LiveLedger, LiveLedgerError
from ..live.paths import LiveLayout, live_layout
from ..live.policy import INITIAL_SYMBOLS

EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_LEASE_HELD = 0, 1, 2, 5
MAX_TEXT = 200                  # operator and note
MIN_EVIDENCE, MAX_EVIDENCE = 20, 400
MAX_AMOUNT_PLACES = 8
FUTURE_SKEW_S = 60.0
_PLAIN_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
# An owner resolution must not pass an active stop of these kinds (other stops do not block it).
_BLOCKING_STOPS = frozenset({safety.SafetyReason.UNKNOWN_ORDER.value, safety.SafetyReason.DATA_ANOMALY.value,
                             safety.SafetyReason.FEE_MISSING.value, safety.SafetyReason.BALANCE_MISMATCH.value})


class _Parser(argparse.ArgumentParser):
    """An ArgumentParser that writes usage and help to the caller's stream, not to sys.stderr."""

    def __init__(self, *args, stream: TextIO, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stream = stream

    def _print_message(self, message: str, file=None) -> None:
        if message:
            self._stream.write(message)


# ---- normalisation (every failure is one ``refused:`` line) ---------------------------------
def _allowed_currencies() -> frozenset[str]:
    return frozenset({"JPY"} | {symbol.split("/")[0] for symbol in INITIAL_SYMBOLS})


def _parse_currency(text: str) -> str:
    currency = text.strip().upper()
    if currency not in _allowed_currencies():
        raise ValueError("currency must be JPY or the base currency of a LIVE symbol")
    return currency


def _parse_amount(kind: str, text: str) -> Decimal:
    raw = text.strip()
    if _PLAIN_DECIMAL.fullmatch(raw) is None:
        raise ValueError("amount must be a plain decimal number (no exponent)")
    amount = dec(raw)
    if amount == 0:
        raise ValueError("amount must not be zero")
    if kind != "adjustment" and amount < 0:
        raise ValueError(f"a {kind} must be positive")
    if -amount.as_tuple().exponent > MAX_AMOUNT_PLACES:
        raise ValueError(f"amount must have at most {MAX_AMOUNT_PLACES} decimal places")
    return amount


def _parse_when(text: str, now: float) -> datetime:
    try:
        when = datetime.fromisoformat(text.strip())
    except ValueError as exc:
        raise ValueError("occurred-at must be an ISO 8601 timestamp") from exc
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("occurred-at must include a timezone offset")
    when = when.astimezone(timezone.utc)
    if when.timestamp() > now + FUTURE_SKEW_S:
        raise ValueError("occurred-at must not be in the future")
    return when


def _parse_text(value: str, name: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    if len(text) > MAX_TEXT:
        raise ValueError(f"{name} must be at most {MAX_TEXT} characters")
    return text


def _content(kind: str, currency: str, amount: Decimal, when: datetime, operator: str,
             note: str) -> dict[str, str]:
    """The previewed content: the token and the cashflow id are hashes of exactly this."""
    return {"kind": kind, "currency": currency, "amount": dec_text(amount),
            "occurred_at": when.isoformat(), "operator": operator, "note": note}


def _token(content: dict[str, str], ledger_id: str) -> str:
    return sha256_hex(canonical_json({**content, "ledger_id": ledger_id}))[:10]


def _cashflow_id(content: dict[str, str]) -> str:
    return "cf-" + sha256_hex(canonical_json(content))[:24]


def _ledger_id(layout: LiveLayout) -> str:
    """The ledger's own id, or ``no-ledger`` when there is none (nothing is created here)."""
    if not layout.ledger_path.is_file():
        return "no-ledger"
    ledger = LiveLedger.open_readonly(layout)
    try:
        with ledger.read_cursor() as cur:
            row = cur.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()
    finally:
        ledger.close()
    return row[0] if row else "no-ledger"


# ---- subcommands (read-only) -----------------------------------------------------------------
def _safety_line(state) -> str:
    if not state.ledger_readable:
        return "unreadable"
    return ", ".join(trip.reason for trip in state.trips) if state.trips else "none"


def _status(args, out: TextIO) -> int:
    layout = live_layout(args.state_dir)
    if not layout.ledger_path.is_file():
        print("ledger: not created", file=out)
        return EXIT_OK
    ledger = LiveLedger.open_readonly(layout)
    try:
        digest = ledger.active_policy_set_digest()
        with ledger.read_cursor() as cur:
            intents = cur.execute("SELECT state, COUNT(*) FROM intents GROUP BY state ORDER BY state").fetchall()
            positions = dict(cur.execute("SELECT status, COUNT(*) FROM positions GROUP BY status").fetchall())
            cashflows = cur.execute("SELECT COUNT(*) FROM cashflows").fetchone()[0]
            events = cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        state = safety.read_safety_state(ledger)
    finally:
        ledger.close()
    print("ledger: ok", file=out)
    print(f"policy: {digest[:12] if digest else 'none'}", file=out)
    print("intents: " + (" ".join(f"{name}={count}" for name, count in intents) if intents else "none"), file=out)
    print(f"positions.open: {positions.get('open', 0)}", file=out)
    print(f"positions.residual_inventory: {positions.get('residual_inventory', 0)}", file=out)
    print(f"safety: {_safety_line(state)}", file=out)
    print(f"unknown_submissions: {state.unresolved_unknown_submissions}", file=out)
    print(f"cashflows: {cashflows}", file=out)
    print(f"events: {events}", file=out)
    return EXIT_OK


def _cashflow_list(args, out: TextIO, is_tty: bool) -> int:
    layout = live_layout(args.state_dir)
    if not layout.ledger_path.is_file():
        print("ledger: not created", file=out)
        return EXIT_OK
    ledger = LiveLedger.open_readonly(layout)
    try:
        with ledger.read_cursor() as cur:
            rows = cur.execute("SELECT cashflow_id, kind, currency, amount, source, occurred_at FROM cashflows "
                               "ORDER BY occurred_at, cashflow_id").fetchall()
    finally:
        ledger.close()
    if not rows:
        print("cashflows: none", file=out)
        return EXIT_OK
    for cashflow_id, kind, currency, amount, source, occurred_at in rows:
        when = datetime.fromtimestamp(occurred_at, tz=timezone.utc).isoformat()
        print(f"{cashflow_id} {kind} {currency} {when} {source} amount: {amount if is_tty else 'hidden'}",
              file=out)
    return EXIT_OK


# ---- cashflow add (the only writer) ----------------------------------------------------------
def _preview(content: dict[str, str], token: str, out: TextIO, is_tty: bool, ledger_exists: bool) -> None:
    print(f"ledger: {'exists' if ledger_exists else 'will be created'}", file=out)
    print(f"kind: {content['kind']}", file=out)
    print(f"currency: {content['currency']}", file=out)
    print(f"amount: {content['amount'] if is_tty else 'hidden'}", file=out)
    print(f"occurred_at: {content['occurred_at']}", file=out)
    print(f"operator: {content['operator']}", file=out)
    print(f"token: {token}", file=out)


def _already_recorded(ledger, cashflow_id: str) -> bool:
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT 1 FROM cashflows WHERE cashflow_id=?", (cashflow_id,)).fetchone() is not None


def _identical_exists(ledger, kind: str, currency: str, amount: Decimal) -> bool:
    with ledger.read_cursor() as cur:
        rows = cur.execute("SELECT amount FROM cashflows WHERE kind=? AND currency=?",
                           (kind, currency)).fetchall()
    return any(Decimal(row[0]) == amount for row in rows)


def _release_writer(ledger, writer, clock: Callable[[], float]) -> None:
    """Give the writer lease back and close the ledger; both may be None (nothing to undo)."""
    if writer is not None:
        try:
            live_lease.release_writer_lease(ledger, writer, now=clock())
        except Exception:           # never hide the outcome of the write behind a release failure
            pass
    if ledger is not None:
        ledger.close()


def _record(args, content: dict[str, str], occurred_at: float, cashflow_id: str, out: TextIO,
            clock: Callable[[], float]) -> int:
    layout = live_layout(args.state_dir)
    ledger, writer = None, None
    try:
        ledger = LiveLedger.open(layout, paper_dir=Path(args.state_dir) / "trading", now=clock())
        try:
            writer = live_lease.acquire_writer_lease(ledger, layout, now=clock())
        except live_lease.LeaseHeld:
            print("refused: the live worker holds the writer lease; stop it first", file=out)
            return EXIT_LEASE_HELD
        if _already_recorded(ledger, cashflow_id):
            print("refused: this cashflow is already recorded", file=out)
            return EXIT_REFUSED
        amount = dec(content["amount"])
        if not args.allow_repeat and _identical_exists(ledger, content["kind"], content["currency"], amount):
            print("refused: an identical cashflow exists (use --allow-repeat to record another)", file=out)
            return EXIT_REFUSED
        try:
            ledger.record_cashflow(writer, Cashflow(cashflow_id=cashflow_id, kind=content["kind"],
                                                   currency=content["currency"], amount=content["amount"],
                                                   source="owner_manual",
                                                   occurred_at=occurred_at,
                                                   note=f"{content['operator']}: {content['note']}"), now=clock())
        except LiveLedgerError:
            print("refused: the ledger refused the cashflow", file=out)
            return EXIT_REFUSED
        print(f"recorded: {cashflow_id}", file=out)
        return EXIT_OK
    finally:
        _release_writer(ledger, writer, clock)


def _cashflow_add(args, out: TextIO, clock: Callable[[], float], is_tty: bool) -> int:
    try:
        currency = _parse_currency(args.currency)
        amount = _parse_amount(args.kind, args.amount)
        when = _parse_when(args.occurred_at, clock())
        operator = _parse_text(args.operator, "operator")
        note = _parse_text(args.note, "note")
    except ValueError as exc:
        print(f"refused: {exc}", file=out)
        return EXIT_REFUSED
    content = _content(args.kind, currency, amount, when, operator, note)
    layout = live_layout(args.state_dir)
    token = _token(content, _ledger_id(layout))
    if args.confirm is None:
        _preview(content, token, out, is_tty, layout.ledger_path.is_file())
        return EXIT_OK
    if args.confirm.strip() != token:
        print("refused: confirmation token does not match", file=out)
        return EXIT_REFUSED
    return _record(args, content, when.timestamp(), _cashflow_id(content), out, clock)


# ---- activate (the owner's policy set activation) --------------------------------------------
def _short(exc: BaseException) -> str:
    """One short line of an exception's message (never a file's content)."""
    text = " ".join(str(exc).split())
    return text[:200] if text else "invalid input"


def _activation_content(ps, enable_result, rev: str, operator: str, note: str) -> dict[str, str]:
    return {"policy_set_digest": ps.digest, "enable_file_sha256": enable_result.file_sha256 or "",
            "code_revision": rev, "operator": operator, "note": note}


def _preview_activation(ps, spec, enable_result, rev: str, operator: str, token: str, out: TextIO,
                        ledger_exists: bool) -> None:
    print(f"ledger: {'exists' if ledger_exists else 'will be created'}", file=out)
    print(f"policy_set: {ps.digest[:12]}", file=out)
    print(f"experiment: {live_policy.experiment_digest(spec)[:12]}", file=out)
    print(f"code_revision: {rev[:12]}", file=out)
    print(f"mode: {enable_result.mode}", file=out)
    print(f"operator: {operator}", file=out)
    print(f"token: {token}", file=out)


def _record_activation(args, ps, enable_result, rev: str, operator: str, note: str, out: TextIO,
                       clock: Callable[[], float]) -> int:
    layout = live_layout(args.state_dir)
    ledger, writer = None, None
    try:
        ledger = LiveLedger.open(layout, paper_dir=Path(args.state_dir) / "trading", now=clock())
        try:
            writer = live_lease.acquire_writer_lease(ledger, layout, now=clock())
        except live_lease.LeaseHeld:
            print("refused: the live worker holds the writer lease; stop it first", file=out)
            return EXIT_LEASE_HELD
        again = live_enable.live_enabled(args.state_dir, policy_set_digest=ps.digest, code_revision=rev,
                                         now=clock())
        if (again.enabled, again.file_sha256, again.mode) != (enable_result.enabled, enable_result.file_sha256,
                                                             enable_result.mode):
            print("refused: the enable file changed; run the preview again", file=out)
            return EXIT_REFUSED
        try:
            ledger.record_owner_activation(writer, ps, enable=again, operator=operator, note=note, now=clock())
        except LiveLedgerError:
            print("refused: the ledger refused the activation", file=out)
            return EXIT_REFUSED
        print(f"activated: {ps.digest[:12]}", file=out)
        return EXIT_OK
    finally:
        _release_writer(ledger, writer, clock)


def _activate(args, out: TextIO, clock: Callable[[], float], revision: Callable[[], str]) -> int:
    try:
        operator = _parse_text(args.operator, "operator")
        note = _parse_text(args.note, "note")
    except ValueError as exc:
        print(f"refused: {exc}", file=out)
        return EXIT_REFUSED
    try:
        spec = live_policy.load_experiment_snapshot(args.snapshot, expected_digest=args.experiment_digest)
        ps = live_policy.build_policy_set(spec, INITIAL_SYMBOLS)
    except Exception as exc:        # the snapshot is the owner's input: refuse it, never traceback
        print(f"refused: {type(exc).__name__}: {_short(exc)}", file=out)
        return EXIT_REFUSED
    try:
        rev = revision()
    except revision_module.RevisionError as exc:
        print(f"refused: {exc}", file=out)
        return EXIT_REFUSED
    enable_result = live_enable.live_enabled(args.state_dir, policy_set_digest=ps.digest, code_revision=rev,
                                             now=clock())
    if not enable_result.enabled:
        print("refused: enable file is not valid: "
              + ", ".join(reason.value for reason in enable_result.reasons), file=out)
        return EXIT_REFUSED
    content = _activation_content(ps, enable_result, rev, operator, note)
    layout = live_layout(args.state_dir)
    token = _token(content, _ledger_id(layout))
    if args.confirm is None:
        _preview_activation(ps, spec, enable_result, rev, operator, token, out, layout.ledger_path.is_file())
        return EXIT_OK
    if args.confirm.strip() != token:
        print("refused: confirmation token does not match", file=out)
        return EXIT_REFUSED
    return _record_activation(args, ps, enable_result, rev, operator, note, out, clock)


# ---- unknown: a human resolves an UNKNOWN_SUBMISSION intent --------------------------------
def _exchange_reader(reader_factory, state_dir):
    """The reader for one command: injected by tests, else the real factory (imported here, lazily)."""
    if reader_factory is not None:
        return reader_factory(Path(state_dir))
    from . import exchange_access          # the exchange layer is never imported with this module
    return exchange_access.open_reader(state_dir)


def _is_exchange_failure(exc: BaseException) -> bool:
    if isinstance(exc, ExchangeError):
        return True
    from . import exchange_access
    return isinstance(exc, exchange_access.ReaderUnavailableError)


def _unavailable(exc: BaseException, out: TextIO) -> int:
    print(f"refused: exchange unavailable: {type(exc).__name__}", file=out)
    return EXIT_REFUSED


def _closed_gate():
    """The read-only executor's gate: always closed, so even a bug can never send."""
    return live_enable.EnableResult(False, (live_enable.EnableReason.FILE_MISSING,))


def _open_read_only_executor(state_dir, ledger, writer, reader, clock):
    """An executor that can poll but never send (sender None): every owner check runs through it."""
    return live_executor.OrderExecutor(ledger, writer, reader=reader, sender=None, enable_check=_closed_gate,
                                       clock=clock, symbols=INITIAL_SYMBOLS,
                                       config=live_ingest.ExecutorConfig())


def _window(ledger, intent, now: float, config) -> tuple[int, int]:
    submitted_at = live_views.submitting_time(ledger, intent.intent_id) or intent.updated_at
    margin_ms = int(config.skew_margin_s * 1000)
    return int(submitted_at * 1000) - margin_ms, int(now * 1000) + margin_ms


def _settle_elapsed(ledger, intent, now: float, config) -> bool:
    submitted_at = live_views.submitting_time(ledger, intent.intent_id) or intent.updated_at
    return now - submitted_at >= config.settle_s


@dataclass
class _Candidates:
    """What the exchange shows that the ledger does not link to an intent."""
    open_complete: bool
    fills_complete: bool
    candidates: list           # OrderSnapshot, open orders plus orders only visible in fills
    matching: list             # candidates the intent's own window and attributes accept


def _candidate_orders(ledger, reader, intent, lo_ms: int, hi_ms: int) -> _Candidates:
    """Every unknown order of this symbol: the open page AND the orders only the fill history shows.

    LIVE prices limit orders to fill at once, so a lost order is usually absent from the open list and
    visible only as a fill.  An id the ledger recorded without an intent is still a candidate.  Any
    exchange failure while fetching a fills-only order propagates: it must never read as "no such order".
    """
    page = reader.fetch_open_orders(intent.symbol)
    fills = reader.fetch_fills(intent.symbol, since_ms=max(0, lo_ms))
    known = {eid for eid, intent_id in live_views.known_order_ids(ledger).items() if intent_id is not None}
    by_id = {snap.exchange_order_id: snap for snap in page.orders if snap.exchange_order_id not in known}
    for fill in fills.fills:
        if fill.symbol != intent.symbol or fill.exchange_order_id in known or fill.exchange_order_id in by_id:
            continue
        by_id[fill.exchange_order_id] = reader.fetch_order(intent.symbol, fill.exchange_order_id)
    candidates = list(by_id.values())
    return _Candidates(page.complete, fills.complete, candidates,
                       [snap for snap in candidates if live_reconcile.matches_intent(intent, snap, lo_ms, hi_ms)])


def _lookup_order(reader, intent, exchange_order_id: str) -> tuple[object | None, str | None]:
    """The owner's own order id: (snapshot, None) or (None, the refusal line)."""
    try:
        return reader.fetch_order(intent.symbol, exchange_order_id), None
    except OrderNotFound:
        return None, "refused: the exchange does not know that order id"
    except ExchangeError as exc:
        return None, f"refused: exchange unavailable: {type(exc).__name__}"


def _found_order_problem(ledger, intent, exchange_order_id: str, snap, lo_ms: int, hi_ms: int) -> str | None:
    """Why this order cannot be this intent's order (None: it can and nothing would trip)."""
    linked = live_views.order_for_intent(ledger, intent.intent_id)
    if linked is not None and linked.exchange_order_id != exchange_order_id:
        return "refused: the ledger already has a different order for this intent"
    if not live_reconcile.matches_intent(intent, snap, lo_ms, hi_ms) or live_ingest.intent_mismatch(intent, snap):
        return "refused: the exchange order does not match this intent"
    row = live_views.order_row(ledger, exchange_order_id)
    if live_ingest.order_problems(row, snap):
        return "refused: the exchange order is not consistent"
    if row is not None and row.intent_id not in (None, intent.intent_id):
        return "refused: that exchange order id belongs to another intent"
    return None


def _not_created_problem(ledger, reader, intent, lo_ms: int, hi_ms: int, settle: bool) -> tuple[str | None, object]:
    """(refusal line, None) or (None, the candidates) for a --not-created resolution."""
    if live_views.order_for_intent(ledger, intent.intent_id) is not None:
        return "refused: the ledger already has an order for this intent; use --found-order", None
    found = _candidate_orders(ledger, reader, intent, lo_ms, hi_ms)
    if found.matching:
        return "refused: a matching order exists on the exchange; use --found-order", None
    if not settle:
        return "refused: settle time has not passed", None
    return None, found


def _active_stop_reasons(ledger, blocking_only: bool = False) -> str:
    """The active stops, comma separated ("" if none); blocking_only keeps the four refusal reasons."""
    reasons = (trip.reason for trip in safety.read_safety_state(ledger).trips
               if not blocking_only or trip.reason in _BLOCKING_STOPS)
    return ", ".join(reasons)


def _evidence(mode: str, operator: str, evidence: str, checked_at: float, result: str, open_list_complete: str,
              fills_complete: str, candidates: int, override: bool, found_order_id: str | None = None) -> str:
    """The proof the owner puts on the intent row: strings only, no amounts.

    The exchange order id appears only for a found order (the not_created proof names no order).
    """
    payload: dict[str, object] = {
        "method": "owner_manual", "mode": mode, "operator": operator, "evidence": evidence,
        "checked_at": str(checked_at),
        "machine": {"result": result, "open_list_complete": open_list_complete,
                    "fills_complete": fills_complete, "candidates": str(candidates),
                    "override_incomplete_proof": "true" if override else "false"}}
    if found_order_id is not None:
        payload["found_order_id"] = found_order_id
    return canonical_json(payload).decode("utf-8")


def _unknown_list(args, out: TextIO, clock: Callable[[], float], is_tty: bool) -> int:
    layout = live_layout(args.state_dir)
    if not layout.ledger_path.is_file():
        print("ledger: not created", file=out)
        return EXIT_OK
    ledger = LiveLedger.open_readonly(layout)
    try:
        intents = ledger.list_intents(states=("UNKNOWN_SUBMISSION",))
    finally:
        ledger.close()
    if not intents:
        print("unknown_submissions: none", file=out)
        return EXIT_OK
    now = clock()
    for intent in intents:
        age = max(0, int(now - intent.created_at))
        amount = intent.amount if is_tty else "hidden"
        price = (intent.price if intent.price is not None else "none") if is_tty else "hidden"
        print(f"{intent.intent_id} {intent.symbol} {intent.side} {intent.order_type} "
              f"age: {age}s amount: {amount} price: {price}", file=out)
    return EXIT_OK


def _unknown_show(args, out: TextIO, clock: Callable[[], float], is_tty: bool, reader_factory) -> int:
    layout = live_layout(args.state_dir)
    if not layout.ledger_path.is_file():
        print("ledger: not created", file=out)
        return EXIT_OK
    ledger = LiveLedger.open_readonly(layout)
    try:
        intent = ledger.get_intent(args.intent)
        if intent is None or intent.state != "UNKNOWN_SUBMISSION":
            print("refused: not an unknown submission", file=out)
            return EXIT_REFUSED
        config, now = live_ingest.ExecutorConfig(), clock()
        lo_ms, hi_ms = _window(ledger, intent, now, config)
        settle = _settle_elapsed(ledger, intent, now, config)
        ledger_order = live_views.order_for_intent(ledger, intent.intent_id) is not None
        try:
            reader = _exchange_reader(reader_factory, args.state_dir)
            found = _candidate_orders(ledger, reader, intent, lo_ms, hi_ms)
        except Exception as exc:
            if not _is_exchange_failure(exc):
                raise
            return _unavailable(exc, out)
    finally:
        ledger.close()
    print(f"intent: {intent.intent_id}", file=out)
    print(f"window_ms: {lo_ms}..{hi_ms}", file=out)
    print(f"open_list_complete: {'true' if found.open_complete else 'false'}", file=out)
    print(f"fills_complete: {'true' if found.fills_complete else 'false'}", file=out)
    print(f"candidates: {len(found.candidates)}", file=out)
    print(f"matching: {len(found.matching)}", file=out)
    if found.matching:
        if is_tty:
            for snap in found.matching:
                print(f"order: {snap.exchange_order_id}", file=out)
        else:
            print("order: id: hidden", file=out)
    print(f"ledger_order: {'yes' if ledger_order else 'no'}", file=out)
    print(f"settle_elapsed: {'true' if settle else 'false'}", file=out)
    return EXIT_OK


def _unknown_resolve(args, out: TextIO, clock: Callable[[], float], is_tty: bool, reader_factory) -> int:
    if (args.found_order is not None) == bool(args.not_created):
        print("refused: give exactly one of --found-order and --not-created", file=out)
        return EXIT_REFUSED
    try:
        operator = _parse_text(args.operator, "operator")
        evidence = args.evidence.strip()
        if not MIN_EVIDENCE <= len(evidence) <= MAX_EVIDENCE:
            raise ValueError(f"evidence must be {MIN_EVIDENCE}..{MAX_EVIDENCE} characters")
    except ValueError as exc:
        print(f"refused: {exc}", file=out)
        return EXIT_REFUSED
    mode = f"found:{args.found_order}" if args.found_order is not None else "not_created"
    layout = live_layout(args.state_dir)
    if not layout.ledger_path.is_file():
        print("refused: unknown intent", file=out)
        return EXIT_REFUSED
    content = {"intent_id": args.intent, "mode": mode, "evidence": evidence, "operator": operator,
               "override_incomplete_proof": "true" if args.override_incomplete_proof else "false"}
    token = _token(content, _ledger_id(layout))
    ledger = LiveLedger.open_readonly(layout)
    try:
        intent = ledger.get_intent(args.intent)
        if intent is None:
            print("refused: unknown intent", file=out)
            return EXIT_REFUSED
        if intent.state != "UNKNOWN_SUBMISSION":
            print("refused: not an unknown submission", file=out)
            return EXIT_REFUSED
        if args.confirm is None:
            return _preview_resolution(args, out, clock, is_tty, reader_factory, ledger, intent, mode, operator,
                                       evidence, token)
    finally:
        ledger.close()
    if args.confirm.strip() != token:
        print("refused: confirmation token does not match", file=out)
        return EXIT_REFUSED
    return _run_resolution(args, out, clock, is_tty, reader_factory, operator, evidence)


def _preview_resolution(args, out, clock, is_tty, reader_factory, ledger, intent, mode, operator, evidence,
                        token) -> int:
    config, now = live_ingest.ExecutorConfig(), clock()
    lo_ms, hi_ms = _window(ledger, intent, now, config)
    settle = _settle_elapsed(ledger, intent, now, config)
    found = None
    stops = ""
    try:
        reader = _exchange_reader(reader_factory, args.state_dir)
        if args.found_order is not None:
            snap, refusal = _lookup_order(reader, intent, args.found_order)
            if refusal is not None:
                print(refusal, file=out)
                return EXIT_REFUSED
            refusal = _found_order_problem(ledger, intent, args.found_order, snap, lo_ms, hi_ms)
            if refusal is not None:
                print(refusal, file=out)
                return EXIT_REFUSED
        else:
            refusal, found = _not_created_problem(ledger, reader, intent, lo_ms, hi_ms, settle)
            if refusal is not None:
                print(refusal, file=out)
                return EXIT_REFUSED
            stops = _active_stop_reasons(ledger, blocking_only=True)
            if stops and args.override_incomplete_proof:
                print(f"refused: an active safety stop must be cleared first ({stops})", file=out)
                return EXIT_REFUSED
    except Exception as exc:
        if not _is_exchange_failure(exc):
            raise
        return _unavailable(exc, out)
    print(f"intent: {intent.intent_id}", file=out)
    print(f"mode: {mode}", file=out)
    print(f"window_ms: {lo_ms}..{hi_ms}", file=out)
    if args.found_order is not None:
        print("order_matches: true", file=out)
        print("order_unclaimed: true", file=out)
    else:
        print(f"open_list_complete: {'true' if found.open_complete else 'false'}", file=out)
        print(f"fills_complete: {'true' if found.fills_complete else 'false'}", file=out)
        print(f"candidates: {len(found.candidates)}", file=out)
        print(f"matching: {len(found.matching)}", file=out)
        print(f"settle_elapsed: {'true' if settle else 'false'}", file=out)
        if stops:
            print(f"safety_stops_active: {stops}", file=out)
        print(f"override_incomplete_proof: {'true' if args.override_incomplete_proof else 'false'}", file=out)
    print(f"operator: {operator}", file=out)
    print(f"evidence: {evidence}", file=out)
    print(f"token: {token}", file=out)
    return EXIT_OK


def _run_resolution(args, out, clock, is_tty, reader_factory, operator, evidence) -> int:
    layout = live_layout(args.state_dir)
    ledger, writer = None, None
    try:
        ledger = LiveLedger.open(layout, paper_dir=Path(args.state_dir) / "trading", now=clock())
        try:
            writer = live_lease.acquire_writer_lease(ledger, layout, now=clock())
        except live_lease.LeaseHeld:
            print("refused: the live worker holds the writer lease; stop it first", file=out)
            return EXIT_LEASE_HELD
        intent = ledger.get_intent(args.intent)                 # the world may have moved since the preview
        if intent is None or intent.state != "UNKNOWN_SUBMISSION":
            print("refused: not an unknown submission", file=out)
            return EXIT_REFUSED
        try:
            reader = _exchange_reader(reader_factory, args.state_dir)
            ex = _open_read_only_executor(args.state_dir, ledger, writer, reader, clock)
            config, now = ex.config, clock()
            lo_ms, hi_ms = _window(ledger, intent, now, config)
            if args.found_order is not None:
                return _resolve_found(args, out, clock, ex, ledger, intent, lo_ms, hi_ms, operator, evidence)
            return _resolve_not_created(args, out, clock, ex, ledger, intent, lo_ms, hi_ms, operator, evidence)
        except Exception as exc:
            if not _is_exchange_failure(exc):
                raise
            return _unavailable(exc, out)
    finally:
        _release_writer(ledger, writer, clock)


def _resolve_found(args, out, clock, ex, ledger, intent, lo_ms, hi_ms, operator, evidence) -> int:
    snap, refusal = _lookup_order(ex.reader, intent, args.found_order)
    if refusal is not None:
        print(refusal, file=out)
        return EXIT_REFUSED
    refusal = _found_order_problem(ledger, intent, args.found_order, snap, lo_ms, hi_ms)
    if refusal is not None:
        print(refusal, file=out)
        return EXIT_REFUSED
    proof = _evidence("found_order", operator, evidence, clock(), "not_run", "unknown", "unknown", 1,
                      bool(args.override_incomplete_proof), found_order_id=args.found_order)
    try:
        linked = live_reconcile.link_found_order(ex, intent, snap, "owner", evidence=proof)
    except ExchangeError as exc:                    # the state moved, the refresh could not read the exchange
        after = ledger.get_intent(intent.intent_id)
        if after is not None and after.state != "UNKNOWN_SUBMISSION":
            print("resolved: found (the exchange could not be refreshed; the next worker cycle will)", file=out)
            return EXIT_OK
        print(f"refused: exchange unavailable: {type(exc).__name__}", file=out)
        return EXIT_REFUSED
    if linked == "anomaly":
        print("refused: the ledger found an anomaly", file=out)
        return EXIT_REFUSED
    print("resolved: found", file=out)
    return EXIT_OK


def _resolve_not_created(args, out, clock, ex, ledger, intent, lo_ms, hi_ms, operator, evidence) -> int:
    refusal, found = _not_created_problem(ledger, ex.reader, intent, lo_ms, hi_ms,
                                          _settle_elapsed(ledger, intent, clock(), ex.config))
    if refusal is not None:
        print(refusal, file=out)
        return EXIT_REFUSED
    counter = {"anomalies": 0}
    original = ex.ingest.anomaly

    def counted(detail):
        counter["anomalies"] += 1
        return original(detail)

    ex.ingest.anomaly = counted                          # only this executor instance, restored below
    before = live_views.trip_count(ledger)               # a trip inserted outside the wrapper is invisible to it
    try:
        result = live_reconcile.try_resolve(ex, intent)  # the machine's own proof is tried first
    finally:
        ex.ingest.anomaly = original
    if result in ("acked", "not_created"):
        print(f"resolved: by the machine ({result})", file=out)
        return EXIT_OK
    if result == "anomaly" or counter["anomalies"]:
        print("refused: the machine found an anomaly; the override does not apply", file=out)
        return EXIT_REFUSED
    if live_views.trip_count(ledger) > before:           # a ledger method may have tripped the latch itself
        print(f"refused: the machine recorded a safety stop during this run "
              f"({_active_stop_reasons(ledger)}); the override does not apply", file=out)
        return EXIT_REFUSED
    stops = _active_stop_reasons(ledger, blocking_only=True)
    if stops:
        print(f"refused: an active safety stop must be cleared first ({stops})", file=out)
        return EXIT_REFUSED
    if not args.override_incomplete_proof:
        print("refused: the machine cannot prove this; add --override-incomplete-proof if you checked "
              "the exchange yourself", file=out)
        return EXIT_REFUSED
    proof = _evidence("not_created", operator, evidence, clock(), result,
                      "true" if found.open_complete else "false", "true" if found.fills_complete else "false",
                      len(found.candidates), True)
    try:
        done = ledger.transition_intent(ex.lease, intent.intent_id, expected_state="UNKNOWN_SUBMISSION",
                                        to_state="NOT_CREATED", now=clock(), reason_code="owner_not_created",
                                        evidence=proof)
    except LiveLedgerError:
        print("refused: the ledger refused the resolution", file=out)
        return EXIT_REFUSED
    ex.ingest._close_if_flat(done)                           # exactly what try_resolve does at its end
    print("resolved: not created (owner evidence)", file=out)
    return EXIT_OK


# ---- argument parsing and dispatch -----------------------------------------------------------
def _build_parser(out: TextIO) -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m docich.trading.live_owner", stream=out,
                     description="owner-only LIVE commands (read-only unless a cashflow is added)")
    parser.add_argument("--state-dir", required=True, help="LIVE state dir (an absolute path)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", stream=out, help="read-only summary of the LIVE ledger")
    cashflow = commands.add_parser("cashflow", stream=out, help="the owner's cashflows")
    flows = cashflow.add_subparsers(dest="cashflow_command", required=True)
    flows.add_parser("list", stream=out, help="read-only list (amounts only on a TTY)")
    add = flows.add_parser("add", stream=out, help="record a cashflow after a previewed token")
    add.add_argument("--kind", required=True, choices=("deposit", "withdrawal", "adjustment"))
    add.add_argument("--currency", required=True, help="JPY or a LIVE base currency (e.g. OAS)")
    add.add_argument("--amount", required=True, help="a decimal string (at most 8 places)")
    add.add_argument("--occurred-at", required=True, help="ISO 8601 with a timezone offset")
    add.add_argument("--operator", required=True)
    add.add_argument("--note", required=True)
    add.add_argument("--confirm", help="the token printed by the preview")
    add.add_argument("--allow-repeat", action="store_true",
                     help="record another cashflow with the same kind, currency and amount")
    activate = commands.add_parser("activate", stream=out,
                                   help="record an owner activation of a policy set (needs a valid enable file)")
    activate.add_argument("--snapshot", required=True, help="the experiment snapshot (JSON)")
    activate.add_argument("--experiment-digest", required=True, help="the snapshot's 64-hex digest")
    activate.add_argument("--operator", required=True)
    activate.add_argument("--note", required=True)
    activate.add_argument("--confirm", help="the token printed by the preview")
    unknown = commands.add_parser("unknown", stream=out, help="UNKNOWN_SUBMISSION intents")
    unknowns = unknown.add_subparsers(dest="unknown_command", required=True)
    unknowns.add_parser("list", stream=out, help="read-only list (no exchange)")
    show = unknowns.add_parser("show", stream=out, help="read-only checks against the exchange")
    show.add_argument("--intent", required=True)
    resolve = unknowns.add_parser("resolve", stream=out, help="record the owner's resolution with evidence")
    resolve.add_argument("--intent", required=True)
    resolve.add_argument("--found-order", help="the exchange order id that is this intent's order")
    resolve.add_argument("--not-created", action="store_true", help="the order was never created")
    resolve.add_argument("--evidence", required=True, help="what the owner checked (20..400 characters)")
    resolve.add_argument("--operator", required=True)
    resolve.add_argument("--override-incomplete-proof", action="store_true",
                         help="accept the owner's conclusion when the machine cannot prove it")
    resolve.add_argument("--confirm", help="the token printed by the preview")
    return parser


def main(argv: list[str] | None = None, *, out: TextIO | None = None, clock: Callable[[], float] = time.time,
         is_tty: bool | None = None,
         revision: Callable[[], str] = revision_module.current_revision,
         reader_factory: Callable[[Path], object] | None = None) -> int:
    out = sys.stdout if out is None else out
    if is_tty is None:
        is_tty = bool(getattr(out, "isatty", lambda: False)())
    parser = _build_parser(out)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:        # argparse: usage errors (2) and --help (0)
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if not Path(args.state_dir).is_absolute():
        print("refused: --state-dir must be an absolute path", file=out)
        return EXIT_USAGE
    if not Path(args.state_dir).is_dir():
        print("refused: --state-dir must be an existing directory", file=out)
        return EXIT_USAGE
    if args.command == "status":
        return _status(args, out)
    if args.command == "activate":
        return _activate(args, out, clock, revision)
    if args.command == "unknown":
        if args.unknown_command == "list":
            return _unknown_list(args, out, clock, is_tty)
        if args.unknown_command == "show":
            return _unknown_show(args, out, clock, is_tty, reader_factory)
        return _unknown_resolve(args, out, clock, is_tty, reader_factory)
    if args.cashflow_command == "list":
        return _cashflow_list(args, out, is_tty)
    return _cashflow_add(args, out, clock, is_tty)
