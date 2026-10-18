/**
 * OthelloRoom Durable Object: one bounded viewer-vs-AI room per instance.
 *
 * Contract (mirrors `src/docich/othello_corner.py`):
 * - phases IDLE -> ROOM_OPEN -> PLAYING -> FINISHED (or FAILED/TIMEOUT);
 * - the viewer seat is black and moves first; the AI seat is white and plays
 *   the docich brain (`aiChooseMove`, deterministic);
 * - a viewer move and the AI reply commit inside ONE storage transaction, so
 *   a spectator never observes a half-applied turn;
 * - a running match is never force-quitted by this module: closing is the
 *   caller's explicit fail/timeout/question, same rule as AGENTS.md's
 *   "暗黙に試合を強制終了しない";
 * - only the spectator snapshot leaves this module (no secrets, no raw state).
 */
import {
  AI_PLAYER, OthelloRulesError, ROOM_PHASES, TRANSITIONS,
  VIEWER_PLAYER, aiChooseMove, applyMove, legalMoves, newBoard, opponent,
  score, validateBoard, winnerOf,
} from "./rules.js";

const OPEN_OR_PLAYING = new Set(["ROOM_OPEN", "PLAYING"]);

function nowIso() { return new Date().toISOString(); }

function initial() {
  return {
    schema: 1,
    phase: "IDLE",
    roomId: null,
    viewer: null,
    board: null,
    turn: null,
    movesPlayed: 0,
    moveWindowSeconds: null,
    moveDeadlineAt: null,
    openedAt: null,
    startedAt: null,
    endedAt: null,
    outcome: null,
    result: null,
    failureReason: null,
  };
}

export const ROOM_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
export const VIEWER_NAME_PATTERN = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/;
export const STATE_KEY = "othello:state";

export class OthelloRoomError extends Error {}

function failClosed(code) {
  return new OthelloRoomError(code);
}

/** The broadcast payload: a rebuilt projection, never the raw state. */
export function spectatorSnapshot(state) {
  if (!state || typeof state !== "object") throw failClosed("invalid_state");
  if (state.phase === "IDLE") {
    return {
      phase: "IDLE", roomId: null, board: null, turn: null,
      scores: null, movesPlayed: 0, outcome: null,
    };
  }
  const board = validateBoard(state.board ?? newBoard());
  const turn = state.turn === null ? null
    : (state.turn === VIEWER_PLAYER ? "black" : "white");
  const snapshot = {
    phase: state.phase,
    roomId: state.roomId,
    board,
    turn,
    scores: score(board),
    movesPlayed: state.movesPlayed ?? 0,
    outcome: state.outcome ?? null,
    result: state.result ?? null,
  };
  if (state.phase === "PLAYING") snapshot.yourTurn = state.turn === VIEWER_PLAYER;
  return snapshot;
}

/**
 * Room operations bound to one Durable Object storage. Every state-changing
 * operation runs inside a single storage transaction; the caller serializes
 * concurrent HTTP entries with the promise tail in `room.tail`.
 */
export function makeRoomOperations({ storage, windowDefaultS = 45, windowMaxS = 300 } = {}) {
  const read = async () => (await storage.get(STATE_KEY)) ?? initial();

  const transition = (state, to) => {
    if (!ROOM_PHASES.includes(state.phase)) throw failClosed("invalid_state_phase");
    if (!TRANSITIONS[state.phase]?.has(to)) throw failClosed(`invalid_transition_${state.phase}_to_${to}`);
    state.phase = to;
    return state;
  };

  const finishInternal = (tx, state, endedAt) => {
    const board = validateBoard(state.board);
    const winner = winnerOf(board);
    const outcome = winner === null ? "draw"
      : (winner === VIEWER_PLAYER ? "viewer_win" : "ai_win");
    const counts = score(board);
    const next = {
      ...state,
      board,
      turn: null,
      outcome,
      result: { black: counts.black, white: counts.white, outcome, endedAt },
      endedAt,
    };
    transition(next, "FINISHED");
    tx.put(STATE_KEY, next);
    return spectatorSnapshot(next);
  };

  /** First seat with a legal reply, in [mover's opponent, mover] order. */
  const nextSeat = (board, mover) => {
    const waiting = opponent(mover);
    if (legalMoves(board, waiting).length > 0) return waiting;
    if (legalMoves(board, mover).length > 0) return mover;
    return null;
  };

  return {
    read,
    status: read,

    /** IDLE -> ROOM_OPEN. The viewer is the human seat (viewer vs AI). */
    async openRoom({ roomId, viewer, windowSeconds } = {}) {
      return storage.transaction(async (tx) => {
        const state = (await tx.get(STATE_KEY)) ?? initial();
        if (state.phase !== "IDLE") throw failClosed("room_not_idle");
        if (typeof roomId !== "string" || !ROOM_ID_PATTERN.test(roomId)) throw failClosed("invalid_room_id");
        if (typeof viewer !== "string" || !VIEWER_NAME_PATTERN.test(viewer)) throw failClosed("invalid_viewer_name");
        const ws = windowSeconds ?? windowDefaultS;
        if (!Number.isSafeInteger(ws) || ws < 1 || ws > windowMaxS) throw failClosed("invalid_window");
        const next = {
          ...state,
          roomId,
          viewer,
          moveWindowSeconds: ws,
          openedAt: nowIso(),
          board: newBoard(),
          turn: VIEWER_PLAYER,
          movesPlayed: 0,
          outcome: null,
          result: null,
          failureReason: null,
          endedAt: null,
        };
        transition(next, "ROOM_OPEN");
        await tx.put(STATE_KEY, next);
        return spectatorSnapshot(next);
      });
    },

    /** ROOM_OPEN -> PLAYING with the standard start position. */
    async startMatch() {
      return storage.transaction(async (tx) => {
        const state = await tx.get(STATE_KEY);
        if (!state || state.phase !== "ROOM_OPEN") throw failClosed("room_not_open");
        const next = {
          ...state,
          board: newBoard(),
          turn: VIEWER_PLAYER,
          movesPlayed: 0,
          startedAt: nowIso(),
          moveDeadlineAt: null,
        };
        transition(next, "PLAYING");
        await tx.put(STATE_KEY, next);
        return spectatorSnapshot(next);
      });
    },

    /** Viewer move; the AI reply commits inside the same transaction. */
    async viewerMove({ row, col } = {}) {
      if (!Number.isSafeInteger(row) || !Number.isSafeInteger(col)
          || row < 0 || row >= 8 || col < 0 || col >= 8) {
        throw failClosed("invalid_move");
      }
      return storage.transaction(async (tx) => {
        const state = (await tx.get(STATE_KEY)) ?? initial();
        if (state.phase !== "PLAYING") throw failClosed("not_playing");
        if (state.turn !== VIEWER_PLAYER) throw failClosed("not_your_turn");

        let board;
        try {
          board = applyMove(validateBoard(state.board), VIEWER_PLAYER, row, col);
        } catch (error) {
          throw error instanceof OthelloRulesError ? failClosed("illegal_move") : error;
        }
        let movesPlayed = (state.movesPlayed ?? 0) + 1;

        let seat = nextSeat(board, VIEWER_PLAYER);
        if (seat === null) return finishInternal(tx, { ...state, board, movesPlayed }, nowIso());

        // The AI seat answers within this transaction when it holds the turn.
        // Loop, because the viewer may have no reply after an AI move (pass):
        // the AI then keeps playing until the viewer can reply or the match ends.
        // Without the loop the turn would stick on white and freeze the room.
        while (seat === AI_PLAYER) {
          if (movesPlayed >= 80) {
            return finishInternal(tx, { ...state, board, movesPlayed }, nowIso());
          }
          const [ar, ac] = aiChooseMove(board, AI_PLAYER, legalMoves(board, AI_PLAYER));
          board = applyMove(board, AI_PLAYER, ar, ac);
          movesPlayed += 1;
          seat = nextSeat(board, AI_PLAYER);
          if (seat === null) {
            return finishInternal(tx, { ...state, board, movesPlayed }, nowIso());
          }
        }

        const next = {
          ...state,
          board,
          turn: seat,
          movesPlayed,
          moveDeadlineAt: null,
        };
        await tx.put(STATE_KEY, next);
        return spectatorSnapshot(next);
      });
    },

    /** Explicit close from ROOM_OPEN/PLAYING; never invents a result. */
    async fail({ reason } = {}) {
      if (typeof reason !== "string" || reason.length === 0 || reason.length > 256) {
        throw failClosed("invalid_reason");
      }
      return storage.transaction(async (tx) => {
        const state = await tx.get(STATE_KEY);
        if (!state || !OPEN_OR_PLAYING.has(state.phase)) throw failClosed("not_open_or_playing");
        const next = {
          ...state,
          failureReason: reason,
          outcome: "aborted",
          endedAt: nowIso(),
        };
        transition(next, "FAILED");
        await tx.put(STATE_KEY, next);
        return spectatorSnapshot(next);
      });
    },

    /** Explicit timeout from ROOM_OPEN/PLAYING: an unfinished match stays unfinished. */
    async timeout() {
      return storage.transaction(async (tx) => {
        const state = await tx.get(STATE_KEY);
        if (!state || !OPEN_OR_PLAYING.has(state.phase)) throw failClosed("not_open_or_playing");
        const next = { ...state, outcome: "timeout", endedAt: nowIso() };
        transition(next, "TIMEOUT");
        await tx.put(STATE_KEY, next);
        return spectatorSnapshot(next);
      });
    },
  };
}

let DurableObject;
try {
  ({ DurableObject } = await import("cloudflare:workers"));
} catch {
  DurableObject = class {
    constructor(state, env) { this.state = state; this.env = env; }
  };
}

const DISPATCH_ROUTES = new Set(["snapshot", "openRoom", "startMatch", "viewerMove", "fail", "timeout"]);

/**
 * The Cloudflare Durable Object class: routes one dispatch per request and
 * serializes concurrent operations on this instance with a promise tail.
 */
export class OthelloRoom extends DurableObject {
  constructor(state, env) {
    super(state, env);
    this.state = state;
    this.env = env;
    this.ops = makeRoomOperations({ storage: state.storage });
    this.tail = Promise.resolve();
  }

  /** Called by the Worker entrypoint via the DO stub. */
  dispatch({ route, payload } = {}) {
    if (!DISPATCH_ROUTES.has(route ?? "")) {
      const bad = Promise.reject(failClosed("dispatch_unknown"));
      this.tail = bad.catch(() => {});
      return bad;
    }
    const operation = route === "snapshot"
      ? async () => spectatorSnapshot(await this.ops.status())
      : () => this.ops[route](payload ?? {});
    const result = this.tail.then(operation);
    this.tail = result.catch(() => {});
    return result;
  }

  /** Cloudflare also routes plain HTTP fetches straight into the class. */
  async fetch(request) {
    const url = new URL(request.url);
    const route = request.method === "GET"
      ? (url.pathname === "/snapshot" ? "snapshot" : null)
      : ({ "/open-room": "openRoom", "/start-match": "startMatch",
           "/viewer-move": "viewerMove", "/fail": "fail", "/timeout": "timeout" }[url.pathname] ?? null);
    let payload = null;
    if (route !== "snapshot" && route !== null) {
      try {
        payload = await request.json();
      } catch { return Response.json({ error: "invalid_json" }, { status: 400 }); }
    }
    try {
      const snapshot = await this.dispatch({ route, payload });
      return Response.json(snapshot ?? {}, { headers: { "cache-control": "no-store" } });
    } catch (error) {
      return Response.json({ error: String(error?.message ?? "room_unavailable") }, { status: 409 });
    }
  }
}
