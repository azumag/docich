import { normalizeObservation } from "../brain/index.js";

// Socket.IO transport belongs to the runner. This module accepts only the
// player-visible contract published at https://beta.tsuitate.info/bot-api.
const MAX_INTEGER = Number.MAX_SAFE_INTEGER;
const COLORS = new Set(["sente", "gote"]);
const ROLE_TO_USI = Object.freeze({
  pawn: "P", lance: "L", knight: "N", silver: "S", gold: "G",
  bishop: "B", rook: "R", king: "K", tokin: "+P",
  promotedlance: "+L", promotedknight: "+N", promotedsilver: "+S",
  horse: "+B", dragon: "+R",
});
const HAND_ROLES = Object.freeze(["pawn", "lance", "knight", "silver", "gold", "bishop", "rook"]);
const SQUARE = /^[1-9][a-i]$/;
const MOVE = /^(?:[1-9][a-i][1-9][a-i]\+?|[PLNSGBR]\*[1-9][a-i])$/;

export class ProtocolError extends Error {
  constructor(code) {
    super(code);
    this.name = "ProtocolError";
    this.code = code;
  }
}

function record(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

// Never invoke accessors or copy arbitrary input keys into an observation.
function field(object, name) {
  const descriptor = Object.getOwnPropertyDescriptor(object, name);
  return descriptor && Object.hasOwn(descriptor, "value") ? descriptor.value : undefined;
}

function integer(value, code, min = 0, max = MAX_INTEGER) {
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new ProtocolError(code);
  return value;
}

function color(value) {
  if (!COLORS.has(value)) throw new ProtocolError("invalid_color");
  return value;
}

function gameId(value) {
  if (typeof value !== "string" || value.length < 1 || value.length > 128
      || value !== value.trim() || /[\p{C}\p{Zl}\p{Zp}]/u.test(value)) {
    throw new ProtocolError("invalid_game_id");
  }
  return value;
}

function clockNumber(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > MAX_INTEGER) {
    throw new ProtocolError("invalid_clocks");
  }
  return value;
}

function validMove(value) {
  return typeof value === "string" && value.length <= 5 && MOVE.test(value)
    && (value.includes("*") || value.slice(0, 2) !== value.slice(2, 4));
}

function freeze(value) {
  if (value !== null && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

/** A detached, immutable allowlist projection; extras never reach the brain. */
export function parsePlayerView(raw) {
  if (!record(raw)) throw new ProtocolError("invalid_view");
  const id = gameId(field(raw, "gameId"));
  const yourColor = color(field(raw, "yourColor"));
  const turn = color(field(raw, "turn"));
  const moveNumber = integer(field(raw, "moveNumber"), "invalid_move_number", 1);
  const status = field(raw, "status");
  if (status !== "playing" && status !== "ended") throw new ProtocolError("invalid_status");

  const pieces = field(raw, "yourPieces");
  if (!Array.isArray(pieces) || pieces.length > 40) throw new ProtocolError("invalid_pieces");
  const seen = new Set();
  const yourPieces = [];
  for (const piece of pieces) {
    if (!record(piece)) throw new ProtocolError("invalid_piece");
    const square = field(piece, "square");
    const role = field(piece, "role");
    if (typeof square !== "string" || square.length !== 2 || !SQUARE.test(square)
        || typeof role !== "string" || !Object.hasOwn(ROLE_TO_USI, role) || seen.has(square)) {
      throw new ProtocolError("invalid_piece");
    }
    // Explicit contrary ownership must not be relabelled as a friendly piece.
    for (const key of ["owner", "color"]) {
      if (Object.hasOwn(piece, key) && field(piece, key) !== yourColor) throw new ProtocolError("invalid_piece");
    }
    seen.add(square);
    yourPieces.push({ square, role });
  }
  yourPieces.sort((a, b) => a.square.localeCompare(b.square));

  const hand = field(raw, "yourHand");
  if (!record(hand) || Object.keys(hand).some((key) => !HAND_ROLES.includes(key))) {
    throw new ProtocolError("invalid_hand");
  }
  const yourHand = {};
  let total = yourPieces.length;
  for (const role of HAND_ROLES) {
    if (!Object.hasOwn(hand, role)) continue;
    const count = integer(field(hand, role), "invalid_hand", 0, 40);
    // Missing and explicit zero counts describe the same board.
    if (count > 0) yourHand[role] = count;
    total += count;
  }
  if (total > 40) throw new ProtocolError("invalid_piece_total");

  const clock = field(raw, "clocks");
  if (!record(clock)) throw new ProtocolError("invalid_clocks");
  const running = field(clock, "running");
  if ((status === "playing" && running !== turn) || (status === "ended" && running !== null)) {
    throw new ProtocolError("invalid_running_clock");
  }
  const clocks = {
    senteMs: clockNumber(field(clock, "senteMs")),
    goteMs: clockNumber(field(clock, "goteMs")),
    running,
    serverTime: clockNumber(field(clock, "serverTime")),
  };
  const foul = field(raw, "fouls");
  if (!record(foul)) throw new ProtocolError("invalid_fouls");
  const fouls = {
    you: integer(field(foul, "you"), "invalid_fouls", 0, 10),
    opponent: integer(field(foul, "opponent"), "invalid_fouls", 0, 10),
  };
  const youInCheck = field(raw, "youInCheck");
  const opponentInCheck = field(raw, "opponentInCheck");
  if (typeof youInCheck !== "boolean" || typeof opponentInCheck !== "boolean") {
    throw new ProtocolError("invalid_checks");
  }
  return freeze({
    gameId: id, yourColor, yourPieces, yourHand, turn, moveNumber,
    clocks, fouls, youInCheck, opponentInCheck, status,
  });
}

/** Use only the current player's observation, never a full terminal board. */
export function toBrainObservation(raw) {
  try {
    const view = parsePlayerView(raw);
    return normalizeObservation({
      ruleset: "tsuitate-9x9",
      color: view.yourColor === "sente" ? "b" : "w",
      turn: view.turn === "sente" ? "b" : "w",
      moveNumber: view.moveNumber,
      pieces: view.yourPieces.map(({ square, role }) => ({ square, role: ROLE_TO_USI[role] })),
      hand: Object.fromEntries(Object.entries(view.yourHand).map(([role, count]) => [ROLE_TO_USI[role], count])),
      inCheck: view.youInCheck,
      opponentInCheck: view.opponentInCheck,
    });
  } catch (error) {
    if (error instanceof ProtocolError) return null;
    throw error;
  }
}

function makeIntent(sequence, generation, view, usi) {
  return freeze({
    sequence, generation, gameId: view.gameId, moveNumber: view.moveNumber, usi,
    payload: { gameId: view.gameId, usi },
  });
}

/**
 * One unresolved move at a time. ACK success is not a new board. A timed-out
 * or disconnected send remains unresolved across sync and process restarts
 * until an advanced position, an increased foul count, or ended view proves
 * its outcome. There is deliberately no resend operation.
 */
export class MoveGate {
  constructor() {
    this.generation = 0;
    this.connected = false;
    this.needsSync = true;
    this.quiescing = false;
    this.view = null;
    this.pending = null;
    this._sequence = 0;
    this._ackedSequence = 0;
    this._attempted = new Set();
    this._foulFloor = 0;
  }

  newConnection() {
    this.generation = integer(this.generation + 1, "invalid_generation", 1);
    this.connected = true;
    this.needsSync = true;
    return this.generation;
  }

  disconnect(generation) {
    if (generation !== this.generation) return;
    this.connected = false;
    this.needsSync = true;
  }

  acceptView(raw, generation, { synchronized = false } = {}) {
    // A late callback must not even parse or poison the new connection.
    if (generation !== this.generation || !this.connected) return false;
    try {
      return this._acceptCurrentView(raw, synchronized);
    } catch (error) {
      this.needsSync = true;
      throw error;
    }
  }

  _acceptCurrentView(raw, synchronized) {
    if (raw === null) {
      if (!synchronized || this.view?.status === "playing" || this.pending !== null) {
        this.needsSync = true;
        return false;
      }
      this.needsSync = false;
      return true;
    }
    const view = parsePlayerView(raw);
    const old = this.view;
    const sameGame = old !== null && old.gameId === view.gameId;
    if (old !== null && !sameGame && old.status === "playing") throw new ProtocolError("unexpected_game");
    if (sameGame) {
      if (view.yourColor !== old.yourColor) throw new ProtocolError("changed_color");
      if (view.moveNumber < old.moveNumber || view.fouls.you < old.fouls.you
          || view.fouls.opponent < old.fouls.opponent) return false;
      if (old.status === "ended" && view.status !== "ended") return false;
      if (view.status === "playing" && view.moveNumber === old.moveNumber
          && (view.turn !== old.turn || JSON.stringify(view.yourPieces) !== JSON.stringify(old.yourPieces)
              || JSON.stringify(view.yourHand) !== JSON.stringify(old.yourHand))) {
        throw new ProtocolError("conflicting_position");
      }
    }
    const advanced = old === null || !sameGame || view.moveNumber > old.moveNumber;
    const foulProved = sameGame && view.fouls.you > old.fouls.you;
    if (this.needsSync && !synchronized && !(sameGame && advanced)) return false;
    if (sameGame && view.fouls.you < this._foulFloor) return false;
    if (advanced) {
      this.pending = null;
      this._attempted.clear();
      this._foulFloor = view.fouls.you;
    } else if (foulProved || view.status === "ended") {
      this.pending = null;
    }
    this.view = view;
    this.needsSync = false;
    return true;
  }

  get canMove() {
    return this.connected && !this.needsSync && this.view !== null && this.view.status === "playing"
      && this.view.turn === this.view.yourColor && this.pending === null && this.view.fouls.you < 10;
  }

  get canJoinQueue() {
    return this.connected && !this.needsSync && !this.quiescing && this.pending === null
      && (this.view === null || this.view.status === "ended");
  }

  get attemptedMoves() {
    return new Set(this._attempted);
  }

  prepareMove(usi) {
    if (!this.canMove) throw new ProtocolError("move_not_ready");
    if (!validMove(usi)) throw new ProtocolError("invalid_usi");
    if (this._attempted.has(usi)) throw new ProtocolError("already_attempted");
    this._sequence = integer(this._sequence + 1, "invalid_sequence", 1);
    const intent = makeIntent(this._sequence, this.generation, this.view, usi);
    this._attempted.add(usi);
    this.pending = intent;
    return intent;
  }

  acknowledge(intent, raw) {
    if (!this.connected || intent !== this.pending || intent?.generation !== this.generation
        || intent.sequence === this._ackedSequence) return false;
    try {
      if (!record(raw) || typeof field(raw, "ok") !== "boolean") throw new ProtocolError("invalid_ack");
      if (!field(raw, "ok")) {
        if (field(raw, "reason") === "foul") {
          const count = integer(field(raw, "foulCount"), "invalid_ack", 1, 10);
          if (this.view === null || count <= this.view.fouls.you) throw new ProtocolError("invalid_ack");
          this._foulFloor = count;
        } else if (field(raw, "reason") !== "error" || typeof field(raw, "error") !== "string") {
          throw new ProtocolError("invalid_ack");
        }
        this.pending = null;
      }
      this._ackedSequence = intent.sequence;
      this.needsSync = true;
      return true;
    } catch (error) {
      // An invalid ACK is ambiguous. Keep the intent, block further input.
      this.needsSync = true;
      throw error;
    }
  }

  timeout(intent) {
    if (intent === this.pending) this.needsSync = true;
  }

  requestQuiesce() {
    // Finish the current game; only future matchmaking is stopped.
    this.quiescing = true;
  }

  /** Persist this before emitting the intent; it contains no transport data. */
  checkpoint() {
    return freeze({
      version: 1,
      generation: this.generation,
      sequence: this._sequence,
      ackedSequence: this._ackedSequence,
      view: this.view,
      pending: this.pending,
      attemptedMoves: [...this._attempted].sort(),
      foulFloor: this._foulFloor,
      quiescing: this.quiescing,
    });
  }

  /** Validate everything before replacing any live gate state. */
  restore(raw) {
    let restored;
    try {
      if (!record(raw) || field(raw, "version") !== 1) throw new ProtocolError("invalid_checkpoint");
      const generation = integer(field(raw, "generation"), "invalid_checkpoint");
      const sequence = integer(field(raw, "sequence"), "invalid_checkpoint");
      const ackedSequence = integer(field(raw, "ackedSequence"), "invalid_checkpoint", 0, sequence);
      const foulFloor = integer(field(raw, "foulFloor"), "invalid_checkpoint", 0, 10);
      const quiescing = field(raw, "quiescing");
      if (typeof quiescing !== "boolean") throw new ProtocolError("invalid_checkpoint");
      const savedView = field(raw, "view");
      const view = savedView === null ? null : parsePlayerView(savedView);
      const attempts = field(raw, "attemptedMoves");
      // The USI syntax itself has fewer than 14,000 distinct moves.
      if (!Array.isArray(attempts) || attempts.length > 14000 || attempts.some((usi) => !validMove(usi))
          || new Set(attempts).size !== attempts.length || attempts.length > sequence) {
        throw new ProtocolError("invalid_checkpoint");
      }
      const savedPending = field(raw, "pending");
      let pending = null;
      if (savedPending !== null) {
        if (!record(savedPending) || view === null || view.status !== "playing" || view.turn !== view.yourColor) {
          throw new ProtocolError("invalid_checkpoint");
        }
        const pendingSequence = integer(field(savedPending, "sequence"), "invalid_checkpoint", 1, sequence);
        const pendingGeneration = integer(field(savedPending, "generation"), "invalid_checkpoint", 1, generation);
        const usi = field(savedPending, "usi");
        const payload = field(savedPending, "payload");
        if (pendingSequence !== sequence || field(savedPending, "gameId") !== view.gameId
            || field(savedPending, "moveNumber") !== view.moveNumber || !validMove(usi) || !attempts.includes(usi)
            || !record(payload) || field(payload, "gameId") !== view.gameId || field(payload, "usi") !== usi) {
          throw new ProtocolError("invalid_checkpoint");
        }
        pending = makeIntent(pendingSequence, pendingGeneration, view, usi);
      }
      if (view === null && (pending !== null || attempts.length !== 0 || foulFloor !== 0)) {
        throw new ProtocolError("invalid_checkpoint");
      }
      restored = {
        generation: integer(Math.max(this.generation, generation) + 1, "invalid_checkpoint", 1),
        sequence, ackedSequence, view, pending, attempts, foulFloor, quiescing,
      };
    } catch {
      // Neither malformed snapshots nor arbitrary nested errors are logged.
      throw new ProtocolError("invalid_checkpoint");
    }
    this.generation = restored.generation;
    this.connected = false;
    this.needsSync = true;
    this._sequence = restored.sequence;
    this._ackedSequence = restored.ackedSequence;
    this.view = restored.view;
    this.pending = restored.pending;
    this._attempted = new Set(restored.attempts);
    this._foulFloor = restored.foulFloor;
    this.quiescing = restored.quiescing;
    return true;
  }
}
