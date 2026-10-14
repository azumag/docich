"""Experimental strategy/executor contract; deliberately NOT wired to the bot.

Pure transitions only: no input sender, model, clock, filesystem or reset. A
measured driver proposes bounded pad inputs through ``prepare_input``. Its
caller MUST durably save the returned session before sending the returned
intent. A pending intent is never automatically replayed after an ambiguous
send. This module does not itself provide durable storage or exactly-once I/O.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import hashlib
import json
import re

EXECUTOR_VERSION = 'hanjuku-sortie-contract-v1'
MAX_ORDERS = 16
MAX_EXECUTIONS = 128
MAX_PLAN_BYTES = 16384
BUTTONS = frozenset({'a', 'b', 'x', 'y', 'l', 'r', 'start', 'select',
                     'up', 'down', 'left', 'right'})


class ContractError(ValueError):
    """Fixed code only; never echo an untrusted plan or an exception body."""


def _require(condition, code):
    if not condition:
        raise ContractError(code)


def _text(value, *, identifier=False):
    _require(type(value) is str and 0 < len(value) <= 64
             and not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF
                         for c in value), 'INVALID_TEXT')
    if identifier:
        _require(re.fullmatch(r'[A-Za-z0-9_.:-]+', value) is not None, 'INVALID_ID')


def _integer(value, low=0, high=2**53 - 1):
    _require(type(value) is int and low <= value <= high, 'INVALID_INTEGER')


def _names(values, *, unique=True):
    _require(type(values) is tuple and len(values) <= 256, 'INVALID_NAMES')
    for name in values:
        _text(name)
    _require(not unique or len(set(values)) == len(values), 'DUPLICATE_NAME')


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class RunIdentity:
    runtime_id: str
    generation: int
    lease_id: str

    def __post_init__(self):
        _text(self.runtime_id, identifier=True)
        _integer(self.generation, 1)
        _text(self.lease_id, identifier=True)


@dataclass(frozen=True)
class SortieOrder:
    order_id: str
    general: str
    source: str
    target: str
    cards: tuple[str, ...] = ()

    def __post_init__(self):
        _text(self.order_id, identifier=True)
        for name in (self.general, self.source, self.target):
            _text(name)
        _require(self.source != self.target, 'SAME_CASTLE')
        _names(self.cards, unique=False)
        _require(len(self.cards) <= 3, 'TOO_MANY_CARDS')


@dataclass(frozen=True)
class StrategyPlan:
    """Strategy data, not observations or the execution journal.

    An issued node may remain in the old plan; only an unissued suffix may
    replace it through replan. The journal is authoritative about issuance.
    """
    run: RunIdentity
    orders: tuple[SortieOrder, ...]

    def __post_init__(self):
        _require(type(self.run) is RunIdentity, 'INVALID_RUN')
        _require(type(self.orders) is tuple and len(self.orders) <= MAX_ORDERS
                 and all(type(o) is SortieOrder for o in self.orders), 'INVALID_ORDERS')
        _require(len({o.order_id for o in self.orders}) == len(self.orders), 'DUPLICATE_ORDER')

    @property
    def revision(self):
        return _digest(asdict(self))


def parse_plan(text: str, run: RunIdentity) -> StrategyPlan:
    """LLM boundary: data-only orders. Run identity is supplied by the caller.

    Empty orders mean no new work, not permission to drop an issued command.
    Unknown fields (including actions/code/run/revision) are rejected.
    """
    _require(type(text) is str, 'INVALID_PLAN')
    try:
        _require(len(text.encode('utf-8')) <= MAX_PLAN_BYTES, 'PLAN_TOO_LARGE')
        def pairs(items):
            out = {}
            for key, value in items:
                _require(key not in out, 'DUPLICATE_KEY')
                out[key] = value
            return out
        def constant(_):
            raise ContractError('NONFINITE_JSON')
        doc = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ContractError('INVALID_JSON') from None
    _require(type(doc) is dict and set(doc) == {'schema', 'orders'}, 'INVALID_PLAN')
    _require(type(doc['schema']) is int and doc['schema'] == 1, 'INVALID_SCHEMA')
    _require(type(doc['orders']) is list and len(doc['orders']) <= MAX_ORDERS, 'INVALID_ORDERS')
    orders = []
    for raw in doc['orders']:
        _require(type(raw) is dict and set(raw) ==
                 {'order_id', 'general', 'source', 'target', 'cards'}, 'INVALID_ORDER')
        _require(type(raw['cards']) is list, 'INVALID_CARDS')
        orders.append(SortieOrder(**{**raw, 'cards': tuple(raw['cards'])}))
    return StrategyPlan(run, tuple(orders))


GENERAL_STATUSES = frozenset({'unknown', 'at_castle', 'marching', 'unavailable'})


@dataclass(frozen=True)
class GeneralState:
    """Explicit observation state: absence from WorldState is still unknown."""
    name: str
    status: str
    castle: str | None = None

    def __post_init__(self):
        _text(self.name)
        _require(type(self.status) is str and self.status in GENERAL_STATUSES,
                 'INVALID_GENERAL_STATUS')
        if self.castle is not None:
            _text(self.castle)
        if self.status == 'at_castle':
            _require(self.castle is not None, 'GENERAL_CASTLE_REQUIRED')
        else:
            _require(self.castle is None, 'GENERAL_CASTLE_NOT_ALLOWED')


@dataclass(frozen=True)
class WorldState:
    """Trusted observation adapter input; None/absence means unknown, not zero.

    The adapter must omit stale facts. ``sequence`` alone does NOT prove that
    every carried-forward fact is fresh, nor that a visual reading is correct.
    ``castles`` is the measured navigation capability for this chapter.
    """
    run: RunIdentity
    sequence: int
    castles: tuple[str, ...]
    own_castles: tuple[str, ...] | None
    generals: tuple[GeneralState, ...]
    card_stock: tuple[tuple[str, int], ...]
    frame_sha256: str

    def __post_init__(self):
        _require(type(self.run) is RunIdentity, 'INVALID_RUN')
        _integer(self.sequence)
        _require(type(self.frame_sha256) is str
                 and re.fullmatch(r'[0-9a-f]{64}', self.frame_sha256) is not None,
                 'INVALID_FRAME_HASH')
        _names(self.castles)
        if self.own_castles is not None:
            _names(self.own_castles)
            _require(set(self.own_castles) <= set(self.castles), 'UNKNOWN_OWN_CASTLE')
        _require(type(self.generals) is tuple and len(self.generals) <= 256
                 and all(type(g) is GeneralState for g in self.generals), 'INVALID_GENERALS')
        _require(len({g.name for g in self.generals}) == len(self.generals), 'DUPLICATE_GENERAL')
        _require(type(self.card_stock) is tuple and len(self.card_stock) <= 256, 'INVALID_STOCK')
        names = []
        for item in self.card_stock:
            _require(type(item) is tuple and len(item) == 2, 'INVALID_STOCK')
            _text(item[0])
            _integer(item[1], 0, 999)
            names.append(item[0])
        _require(len(set(names)) == len(names), 'DUPLICATE_STOCK')


@dataclass(frozen=True)
class SortieCommand:
    run: RunIdentity
    plan_revision: str
    order: SortieOrder
    based_on_sequence: int

    def __post_init__(self):
        _require(type(self.run) is RunIdentity, 'INVALID_RUN')
        _require(type(self.order) is SortieOrder, 'INVALID_ORDER')
        _require(type(self.plan_revision) is str
                 and re.fullmatch(r'[0-9a-f]{64}', self.plan_revision) is not None,
                 'INVALID_REVISION')
        _integer(self.based_on_sequence)

    @property
    def command_id(self):
        return _digest(asdict(self))


@dataclass(frozen=True)
class SortieEvidence:
    """Observer-only provenance binding, not proof of visual correctness."""
    run: RunIdentity
    sequence: int
    order: SortieOrder
    kind: str
    frame_sha256: str

    def __post_init__(self):
        _require(type(self.run) is RunIdentity, 'INVALID_RUN')
        _integer(self.sequence)
        _require(type(self.order) is SortieOrder, 'INVALID_ORDER')
        _require(type(self.kind) is str and self.kind in {'selection', 'departure'},
                 'INVALID_EVIDENCE_KIND')
        _require(type(self.frame_sha256) is str
                 and re.fullmatch(r'[0-9a-f]{64}', self.frame_sha256) is not None,
                 'INVALID_FRAME_HASH')


def _evidence_matches(evidence, world, order, kind):
    return (type(evidence) is SortieEvidence and evidence.run == world.run
            and evidence.sequence == world.sequence and evidence.order == order
            and evidence.kind == kind and evidence.frame_sha256 == world.frame_sha256)


class Phase(str, Enum):
    READY = 'ready'
    PENDING_INPUT = 'pending_input'
    RUNNING = 'running'
    AWAITING_DEPARTURE = 'awaiting_departure'
    UNCERTAIN = 'uncertain'
    DEPARTED = 'departure_observed'
    CANCELLED = 'cancelled'


@dataclass(frozen=True)
class PadAction:
    """Trusted driver output only; never accepted by parse_plan."""
    button: str
    hold_ms: int = 100

    def __post_init__(self):
        _require(type(self.button) is str and self.button in BUTTONS, 'INVALID_BUTTON')
        _integer(self.hold_ms, 1, 500)


@dataclass(frozen=True)
class InputIntent:
    command_id: str
    index: int
    observation_sequence: int
    actions: tuple[PadAction, ...]
    final_confirmation: bool

    @property
    def input_id(self):
        return _digest(asdict(self))


@dataclass(frozen=True)
class CommandResult:
    command_id: str | None
    status: str
    reason: str
    # A sortie command never asserts arrival, victory or capture.
    goal_completed: bool = field(default=False, init=False)


@dataclass(frozen=True)
class Execution:
    command: SortieCommand
    phase: Phase
    last_sequence: int
    input_count: int = 0
    pending: InputIntent | None = None
    final_input_id: str | None = None
    selection_evidence: SortieEvidence | None = None
    departure_evidence: SortieEvidence | None = None


@dataclass(frozen=True)
class Session:
    plan: StrategyPlan
    executions: tuple[Execution, ...] = ()
    executor_version: str = EXECUTOR_VERSION

    def __post_init__(self):
        _require(type(self.plan) is StrategyPlan, 'INVALID_PLAN')
        _require(type(self.executions) is tuple and len(self.executions) <= MAX_EXECUTIONS
                 and all(type(e) is Execution for e in self.executions), 'INVALID_JOURNAL')
        _require(self.executor_version == EXECUTOR_VERSION, 'EXECUTOR_VERSION_MISMATCH')
        _require(all(e.command.run == self.plan.run for e in self.executions), 'RUN_MISMATCH')
        _require(len({e.command.order.order_id for e in self.executions}) == len(self.executions),
                 'DUPLICATE_EXECUTION')


def replan(session: Session, candidate: StrategyPlan, *, expected_revision: str) -> Session:
    """Compare-and-swap ONLY the pending suffix; keep the journal byte-for-byte.

    Issued node IDs are never reused. A retry must have a new node ID and still
    pass the executor's live preconditions and single-writer gate.
    """
    _require(expected_revision == session.plan.revision, 'STALE_PLAN')
    _require(candidate.run == session.plan.run, 'RUN_MISMATCH')
    issued = {e.command.order.order_id for e in session.executions}
    _require(not issued.intersection(o.order_id for o in candidate.orders), 'ISSUED_ORDER_REUSED')
    return replace(session, plan=candidate)


def _precondition(order: SortieOrder, world: WorldState) -> str | None:
    if order.source not in world.castles or order.target not in world.castles:
        return 'UNSUPPORTED_CASTLE'
    if world.own_castles is None:
        return 'OWNERSHIP_UNKNOWN'
    if order.source not in world.own_castles:
        return 'SOURCE_NOT_OWNED'
    general = next((g for g in world.generals if g.name == order.general), None)
    if general is None or general.status == 'unknown':
        return 'GENERAL_STATE_UNKNOWN'
    if general.status == 'unavailable':
        return 'GENERAL_UNAVAILABLE'
    if general.status == 'marching':
        return 'GENERAL_MARCHING'
    if general.castle != order.source:
        return 'GENERAL_ELSEWHERE'
    stock = dict(world.card_stock)
    for name, needed in Counter(order.cards).items():
        if name not in stock:
            return 'STOCK_UNKNOWN'
        if stock[name] < needed:
            return 'STOCK_INSUFFICIENT'
    return None


def _contradiction(order: SortieOrder, world: WorldState) -> str | None:
    """A fresh fact that explicitly invalidates an already-issued command.

    Unknown facts are not contradictions: once accepted, a command may continue
    through observations that cannot re-read ownership, garrison or stock. But a
    current observation must not drive further input after it positively says
    the source is lost, the general is elsewhere/marching/unavailable, or a
    required card is known to be missing.
    """
    if order.source not in world.castles or order.target not in world.castles:
        return 'UNSUPPORTED_CASTLE'
    if world.own_castles is not None and order.source not in world.own_castles:
        return 'SOURCE_NOT_OWNED'
    general = next((g for g in world.generals if g.name == order.general), None)
    if general is not None:
        if general.status == 'unavailable':
            return 'GENERAL_UNAVAILABLE'
        if general.status == 'marching':
            return 'GENERAL_MARCHING'
        if general.status == 'at_castle' and general.castle != order.source:
            return 'GENERAL_ELSEWHERE'
    stock = dict(world.card_stock)
    for name, needed in Counter(order.cards).items():
        if name in stock and stock[name] < needed:
            return 'STOCK_INSUFFICIENT'
    return None


def begin(session: Session, order_id: str, world: WorldState) -> tuple[Session, CommandResult]:
    _require(world.run == session.plan.run, 'RUN_MISMATCH')
    previous = next((e for e in session.executions if e.command.order.order_id == order_id), None)
    if previous is not None:
        return session, CommandResult(previous.command.command_id, previous.phase.value, 'ALREADY_ISSUED')
    order = next((o for o in session.plan.orders if o.order_id == order_id), None)
    _require(order is not None, 'ORDER_NOT_PENDING')
    _require(len(session.executions) < MAX_EXECUTIONS, 'JOURNAL_FULL')
    busy = any(e.phase not in {Phase.DEPARTED, Phase.CANCELLED} for e in session.executions)
    if busy:
        return session, CommandResult(None, 'blocked', 'EXECUTOR_BUSY')
    if session.executions:
        _require(world.sequence > max(e.last_sequence for e in session.executions), 'STALE_OBSERVATION')
    # Even a fresh-looking stale garrison must not reissue a departed general.
    # Arrival/return reconciliation is a later slice; keep this reservation.
    if any(e.phase == Phase.DEPARTED and e.command.order.general == order.general
           for e in session.executions):
        return session, CommandResult(None, 'blocked', 'GENERAL_RESERVED')
    reason = _precondition(order, world)
    if reason:
        status = 'needs_observation' if reason.endswith('_UNKNOWN') else 'precondition_failed'
        return session, CommandResult(None, status, reason)
    command = SortieCommand(world.run, session.plan.revision, order, world.sequence)
    execution = Execution(command, Phase.READY, world.sequence)
    return replace(session, executions=(*session.executions, execution)), CommandResult(
        command.command_id, Phase.READY.value, 'ACCEPTED_NOT_SENT')


def _get(session, command_id):
    entry = next((e for e in session.executions if e.command.command_id == command_id), None)
    _require(entry is not None, 'UNKNOWN_COMMAND')
    return entry


def _put(session, execution):
    return replace(session, executions=tuple(
        execution if e.command.command_id == execution.command.command_id else e
        for e in session.executions))


def prepare_input(session: Session, command_id: str, world: WorldState,
                  actions: tuple[PadAction, ...], *, final_confirmation=False,
                  verified_selection: SortieEvidence | None = None) -> tuple[Session, InputIntent]:
    """Driver port. Persist the returned session BEFORE sending this intent.

    A final confirmation needs an exact current selection from the trusted
    observer. Mismatch/unknown does not substitute a general or drop a card.
    This port cannot establish whether that observer is visually correct.
    """
    entry = _get(session, command_id)
    _require(world.run == entry.command.run, 'RUN_MISMATCH')
    _require(entry.phase in {Phase.READY, Phase.RUNNING}, 'INPUT_NOT_ALLOWED')
    _require(world.sequence > entry.last_sequence, 'STALE_OBSERVATION')
    contradiction = _contradiction(entry.command.order, world)
    _require(contradiction is None, contradiction or 'PRECONDITION_LOST')
    _require(type(actions) is tuple and 1 <= len(actions) <= 4
             and all(type(a) is PadAction for a in actions), 'INVALID_ACTIONS')
    _require(type(final_confirmation) is bool, 'INVALID_FINAL_FLAG')
    if final_confirmation:
        _require(_evidence_matches(verified_selection, world, entry.command.order, 'selection'),
                 'SELECTION_NOT_VERIFIED')
        _require(len(actions) == 1 and actions[0].button == 'a', 'INVALID_FINAL_INPUT')
    intent = InputIntent(command_id, entry.input_count + 1, world.sequence,
                         actions, final_confirmation)
    changed = replace(entry, phase=Phase.PENDING_INPUT, last_sequence=world.sequence,
                      input_count=intent.index, pending=intent,
                      final_input_id=intent.input_id if final_confirmation else None,
                      selection_evidence=verified_selection if final_confirmation else None)
    return _put(session, changed), intent


def acknowledge(session: Session, command_id: str, input_id: str, disposition: str) -> Session:
    """A transport receipt is NOT departure evidence.

    Only ``not_sent`` proves safe non-dispatch; a timeout/error is ``unknown``.
    Repeated, contradictory or late receipts fail rather than reapply an input.
    """
    entry = _get(session, command_id)
    _require(entry.phase == Phase.PENDING_INPUT and entry.pending is not None,
             'NO_PENDING_INPUT')
    _require(input_id == entry.pending.input_id, 'INPUT_MISMATCH')
    _require(type(disposition) is str and disposition in {'sent', 'not_sent', 'unknown'},
             'INVALID_DISPOSITION')
    if disposition == 'unknown':
        return _put(session, replace(entry, phase=Phase.UNCERTAIN))
    if disposition == 'not_sent':
        return _put(session, replace(entry, phase=Phase.RUNNING, pending=None,
                                     final_input_id=None, selection_evidence=None))
    phase = Phase.AWAITING_DEPARTURE if entry.pending.final_confirmation else Phase.RUNNING
    return _put(session, replace(entry, phase=phase, pending=None))


def observe_departure(session: Session, command_id: str, world: WorldState,
                      *, evidence: SortieEvidence, input_id: str) -> tuple[Session, CommandResult]:
    """Trusted observer's linked departure event; NOT an arbitrary LLM claim.

    Only an explicit matching departure after the final intent can resolve an
    ambiguous send. Seeing a map, absence from a partial list, or a victory
    elsewhere must not be converted into this event by the adapter.
    """
    entry = _get(session, command_id)
    _require(world.run == entry.command.run, 'RUN_MISMATCH')
    _require(entry.phase in {Phase.AWAITING_DEPARTURE, Phase.UNCERTAIN}, 'DEPARTURE_NOT_EXPECTED')
    _require(entry.final_input_id is not None and input_id == entry.final_input_id,
             'INPUT_MISMATCH')
    _require(world.sequence > entry.last_sequence, 'STALE_OBSERVATION')
    _require(_evidence_matches(evidence, world, entry.command.order, 'departure'),
             'DEPARTURE_MISMATCH')
    changed = replace(entry, phase=Phase.DEPARTED, last_sequence=world.sequence, pending=None,
                      departure_evidence=evidence)
    return _put(session, changed), CommandResult(command_id, Phase.DEPARTED.value,
                                                'DEPARTURE_NOT_CAPTURE')


def mark_uncertain(session: Session, command_id: str) -> Session:
    """Stop issuing input on a stall, retaining the command and reservation."""
    entry = _get(session, command_id)
    _require(entry.phase not in {Phase.DEPARTED, Phase.CANCELLED}, 'COMMAND_TERMINAL')
    return _put(session, replace(entry, phase=Phase.UNCERTAIN))


def cancel_before_confirmation(session: Session, command_id: str) -> Session:
    """No generic rollback: only the driver's clean, unsent safe point."""
    entry = _get(session, command_id)
    _require(entry.phase == Phase.READY and entry.input_count == 0, 'CANCEL_NOT_SAFE')
    return _put(session, replace(entry, phase=Phase.CANCELLED))
