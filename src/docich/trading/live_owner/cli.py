"""The owner's LIVE command line (L4a): status, cashflow list, cashflow add and activate.

    python -m docich.trading.live_owner --state-dir <dir> status
    python -m docich.trading.live_owner --state-dir <dir> cashflow list
    python -m docich.trading.live_owner --state-dir <dir> cashflow add --kind deposit ...
    python -m docich.trading.live_owner --state-dir <dir> activate --snapshot ... --experiment-digest ...

The two writers (``cashflow add`` and ``activate``) print a token first and act only when the owner
repeats it.  Nothing here talks to an exchange: the state dir is the only input.

Exit codes: 0 ok, 1 refused, 2 usage, 5 the writer lease is held by the live worker.
Money is a Decimal from text; floats never appear (``exchange.dec``/``dec_text``).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, TextIO

from . import revision as revision_module
from ..live import enable as live_enable, lease as live_lease, policy as live_policy, safety
from ..live.canonical import canonical_json, sha256_hex
from ..live.exchange import dec, dec_text
from ..live.ledger import Cashflow, LiveLedger, LiveLedgerError
from ..live.paths import LiveLayout, live_layout
from ..live.policy import INITIAL_SYMBOLS

EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_LEASE_HELD = 0, 1, 2, 5
MAX_TEXT = 200                  # operator and note
MAX_AMOUNT_PLACES = 8
FUTURE_SKEW_S = 60.0
_PLAIN_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")


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
    return parser


def main(argv: list[str] | None = None, *, out: TextIO | None = None, clock: Callable[[], float] = time.time,
         is_tty: bool | None = None,
         revision: Callable[[], str] = revision_module.current_revision) -> int:
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
    if args.cashflow_command == "list":
        return _cashflow_list(args, out, is_tty)
    return _cashflow_add(args, out, clock, is_tty)
