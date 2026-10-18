/** OthelloRoom: authoritative game state for one human-vs-AI Othello match.
 *
 * Design (docs/features/participatory-arena-othello-corner.md):
 * - one Durable Object per room, SQLite storage under the single key "room";
 * - the first human to say `hello` claims the black seat (design: human is
 *   always black, AI is white); everyone else is a spectator;
 * - every legal move is computed here from the persisted board; the client is
 *   never trusted;
 * - passes are automatic (standard Othello); the game ends when neither side
 *   can move, or on resignation;
 * - AI replies are computed inline (pure function, bounded search) right after
 *   the human move, so one client action yields one state broadcast;
 * - the AI is deterministic: seed = hash(roomId, gameNumber, moveNumber).
 *
 * Concurrency: DO events (fetch, webSocketMessage, alarm) may interleave, so
 * every handler body runs through a promise chain (runExclusive) and the
 * persisted object is the single source of truth after hibernation.
 */
import {
  BLACK, WHITE, opponent,
  initialBoard, boardToString, boardFromString, isValidBoardString,
  movesFor, applyMove, scoreOf, parseCoord, coordOf, hasAnyMove,
} from "../othello/engine.js";
import { chooseMove, AI_LEVELS, DEFAULT_LEVEL } from "../othello/ai.js";
import { parseClientMessage } from "../protocol.js";

export const ROOM_KEY = "room";
export const IDLE_MS = 30 * 60 * 1000;
export const HARD_IDLE_MS = 60 * 60 * 1000;
export const MAX_SPECTATORS = 100;
export const MAX_HISTORY = 256;
export const RATE_LIMIT_PER_SECOND = 20;
export const MAX_BAD_MESSAGES = 3;
export const HUMAN_COLOR = BLACK;
export const AI_COLOR = WHITE;
export const AI_NAME = "DoCi AI";
export const SEATS = { HUMAN: "black", AI: "white" };

const ROOM_ID_RE = /^[0-9a-f]{16}$/;

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

/** workerd requires `new Response(null, {status: 101, webSocket})`. Node's
 * undici (unit tests) caps statuses at 599, so re-expose the client socket on
 * a normal response there — but only for exactly that validation error, so a
 * genuine workerd failure still surfaces. */
function upgradeResponse(client) {
  try {
    return new Response(null, { status: 101, webSocket: client });
  } catch (error) {
    if (!/range of 200 to 599/.test(String(error?.message ?? error))) throw error;
    const response = new Response(null, { status: 200 });
    response.webSocket = client;
    return response;
  }
}

function colorName(color) {
  return color === BLACK ? "black" : "white";
}

/** FNV-1a over a short string -> uint32, for the deterministic AI seed. */
function hashSeed(text) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

function freshRoom(roomId, level, now) {
  return {
    schemaVersion: 1,
    roomId,
    level,
    status: "waiting",
    boardText: boardToString(initialBoard()),
    turn: colorName(HUMAN_COLOR),
    moveNumber: 0,
    lastMove: null,
    seats: { black: null, white: { kind: "ai", level, name: AI_NAME } },
    wins: { human: 0, ai: 0, draw: 0 },
    result: null,
    history: [],
    gameNumber: 1,
    createdAtMs: now,
    updatedAtMs: now,
  };
}

export class OthelloRoom {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.room = null;
    this.loaded = false;
    this.tail = Promise.resolve();
    this.rate = new Map();
    this.strikes = new Map();
  }

  runExclusive(operation) {
    const result = this.tail.then(operation, operation);
    this.tail = result.catch(() => {});
    return result;
  }

  async ensureLoaded() {
    if (this.loaded) return this.room;
    const stored = await this.state.storage.get(ROOM_KEY);
    this.room = this.revive(stored);
    this.loaded = true;
    return this.room;
  }

  /** Defense against hand-edited or older storage shapes: fail closed. */
  revive(stored) {
    if (stored === null || stored === undefined) return null;
    if (typeof stored !== "object") return null;
    if (stored.schemaVersion !== 1) return null;
    if (!isValidBoardString(stored.boardText)) return null;
    if (stored.status !== "waiting" && stored.status !== "playing" && stored.status !== "ended") return null;
    if (stored.turn !== "black" && stored.turn !== "white" && stored.turn !== "none") return null;
    if (!Array.isArray(stored.history)) return null;
    if (!stored.seats || typeof stored.seats !== "object") return null;
    return stored;
  }

  board() {
    return boardFromString(this.room.boardText);
  }

  touch() {
    this.room.updatedAtMs = Date.now();
  }

  async persist() {
    await this.state.storage.put(ROOM_KEY, this.room);
    await this.state.storage.setAlarm(this.room.updatedAtMs + IDLE_MS);
  }

  /** Public JSON contract (design §4). Served to players and spectators alike. */
  snapshot() {
    const room = this.room;
    const score = scoreOf(this.board());
    return {
      schemaVersion: 1,
      kind: "arena_othello",
      roomId: room.roomId,
      status: room.status,
      level: room.level,
      board: room.boardText,
      turn: room.turn,
      moveNumber: room.moveNumber,
      lastMove: room.lastMove,
      seats: room.seats,
      score,
      wins: room.wins,
      result: room.result,
      gameNumber: room.gameNumber,
      updatedAt: new Date(room.updatedAtMs).toISOString(),
    };
  }

  broadcast(type, payload) {
    const data = JSON.stringify({ type, ...payload });
    for (const socket of this.state.getWebSockets()) {
      try {
        socket.send(data);
      } catch {
        // A dead socket must never break the game loop; cleanup is the alarm's job.
      }
    }
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/init") {
      return this.runExclusive(() => this.handleInit(request));
    }
    if (request.method === "GET" && url.pathname === "/snapshot") {
      const room = await this.ensureLoaded();
      if (!room) return jsonResponse(404, { ok: false, code: "unknown_room" });
      return jsonResponse(200, { ok: true, snapshot: this.snapshot() });
    }
    if (request.method === "GET" && url.pathname === "/ws") {
      const room = await this.ensureLoaded();
      if (!room) return jsonResponse(404, { ok: false, code: "unknown_room" });
      if (request.headers.get("upgrade")?.toLowerCase() !== "websocket") {
        return jsonResponse(426, { ok: false, code: "upgrade_required" });
      }
      return this.runExclusive(() => this.handleUpgrade());
    }
    return jsonResponse(404, { ok: false, code: "not_found" });
  }

  async handleInit(request) {
    let body;
    try {
      body = await request.json();
    } catch {
      return jsonResponse(400, { ok: false, code: "invalid_request" });
    }
    const roomId = typeof body?.roomId === "string" ? body.roomId : "";
    if (!ROOM_ID_RE.test(roomId)) return jsonResponse(400, { ok: false, code: "invalid_room_id" });
    const level = body?.level ?? DEFAULT_LEVEL;
    if (!AI_LEVELS.includes(level)) return jsonResponse(400, { ok: false, code: "invalid_level" });
    const existing = await this.ensureLoaded();
    if (existing) {
      if (existing.roomId !== roomId) return jsonResponse(409, { ok: false, code: "room_id_mismatch" });
      return jsonResponse(200, { ok: true, snapshot: this.snapshot() });
    }
    this.room = freshRoom(roomId, level, Date.now());
    this.loaded = true;
    await this.persist();
    return jsonResponse(200, { ok: true, snapshot: this.snapshot() });
  }

  async handleUpgrade() {
    // One player socket + MAX_SPECTATORS spectator sockets, at most.
    if (this.state.getWebSockets().length >= MAX_SPECTATORS + 1) {
      return jsonResponse(429, { ok: false, code: "room_full" });
    }
    // workerd returns the pair as {0: client, 1: server}; destructuring via
    // Object.values matches the current WebSocketPair contract.
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.state.acceptWebSocket(server);
    server.serializeAttachment({ role: "spectator" });
    this.touch();
    await this.persist();
    const welcome = { type: "welcome", role: "spectator", state: this.snapshot() };
    server.send(JSON.stringify(welcome));
    return upgradeResponse(client);
  }

  async webSocketMessage(socket, raw) {
    await this.runExclusive(() => this.handleSocketMessage(socket, raw));
  }

  async webSocketClose(socket) {
    // Drop per-socket bookkeeping so reconnect cycles cannot grow the maps.
    if (socket) {
      this.rate.delete(socket);
      this.strikes.delete(socket);
    }
  }

  async webSocketError(socket) {
    // Same cleanup as close; the alarm is the idle backstop.
    if (socket) {
      this.rate.delete(socket);
      this.strikes.delete(socket);
    }
  }

  checkRate(socket) {
    const now = Date.now();
    const bucket = this.rate.get(socket) ?? { count: 0, resetAt: now + 1000 };
    if (now > bucket.resetAt) {
      bucket.count = 0;
      bucket.resetAt = now + 1000;
    }
    bucket.count += 1;
    this.rate.set(socket, bucket);
    if (bucket.count > RATE_LIMIT_PER_SECOND) {
      socket.close(1008, "rate_limited");
      return false;
    }
    return true;
  }

  failSocket(socket, code) {
    const strikes = (this.strikes.get(socket) ?? 0) + 1;
    this.strikes.set(socket, strikes);
    socket.send(JSON.stringify({ type: "error", code }));
    if (strikes >= MAX_BAD_MESSAGES) socket.close(1008, "bad_messages");
  }

  isPlayer(socket) {
    const attachment = socket.deserializeAttachment?.() ?? null;
    return attachment?.role === "player";
  }

  async handleSocketMessage(socket, raw) {
    const room = await this.ensureLoaded();
    if (!room) return;
    if (!this.checkRate(socket)) return;
    const parsed = parseClientMessage(raw);
    if (!parsed.ok) {
      this.failSocket(socket, parsed.code);
      return;
    }
    const message = parsed.message;
    const player = this.isPlayer(socket);
    let changed = false;

    switch (message.type) {
      case "hello":
        changed = this.handleHello(socket, message.name, player);
        break;
      case "move":
        if (!player) {
          this.failSocket(socket, "not_seated");
          break;
        }
        changed = this.handleMove(socket, message.coord);
        break;
      case "resign":
        if (!player) {
          this.failSocket(socket, "not_seated");
          break;
        }
        changed = this.handleResign();
        break;
      case "rematch":
        if (!player) {
          this.failSocket(socket, "not_seated");
          break;
        }
        changed = this.handleRematch();
        break;
      case "ping":
        socket.send(JSON.stringify({ type: "pong" }));
        break;
      default:
        this.failSocket(socket, "unknown_type");
        break;
    }

    if (changed) {
      this.touch();
      await this.persist();
      this.broadcast("state", { state: this.snapshot() });
      if (this.room.status === "ended") this.broadcast("game_end", { state: this.snapshot() });
    }
  }

  /** First hello claims the black seat; later hellos reclaim an abandoned seat. */
  handleHello(socket, name, player) {
    if (player) {
      if (this.room.seats.black) this.room.seats.black.name = name;
      socket.send(JSON.stringify({ type: "joined", seat: SEATS.HUMAN, state: this.snapshot() }));
      return true;
    }
    if (this.room.seats.black && this.hasLivePlayerSocket(socket)) {
      socket.send(JSON.stringify({ type: "error", code: "seat_taken" }));
      return false;
    }
    socket.serializeAttachment({ role: "player" });
    this.room.seats.black = { kind: "human", name };
    if (this.room.status === "waiting") {
      this.room.status = "playing";
      this.room.turn = colorName(HUMAN_COLOR);
    }
    socket.send(JSON.stringify({ type: "joined", seat: SEATS.HUMAN, state: this.snapshot() }));
    return true;
  }

  hasLivePlayerSocket(except = null) {
    for (const socket of this.state.getWebSockets()) {
      if (socket === except) continue;
      const attachment = socket.deserializeAttachment?.() ?? null;
      if (attachment?.role === "player") return true;
    }
    return false;
  }

  /** @returns {boolean} true when the room state changed. */
  handleMove(socket, coordText) {
    if (this.room.status !== "playing") {
      this.failSocket(socket, "game_over");
      return false;
    }
    if (this.room.turn !== colorName(HUMAN_COLOR)) {
      this.failSocket(socket, "not_your_turn");
      return false;
    }
    const index = parseCoord(coordText);
    const board = this.board();
    const move = index === null ? null : movesFor(board, HUMAN_COLOR).find((entry) => entry.index === index);
    if (!move) {
      this.failSocket(socket, "invalid_move");
      return false;
    }
    this.room.boardText = boardToString(applyMove(board, HUMAN_COLOR, move));
    this.room.moveNumber += 1;
    this.room.lastMove = { coord: coordOf(move.index), color: colorName(HUMAN_COLOR), pass: false };
    this.room.history.push({ n: this.room.moveNumber, color: colorName(HUMAN_COLOR), coord: coordOf(move.index), pass: false });
    if (this.room.history.length >= MAX_HISTORY) {
      // Unreachable by construction (64 discs + bounded passes); enforced as a
      // fail-closed bound on the persisted history.
      this.endGame("score");
      return true;
    }
    this.advanceTurn(HUMAN_COLOR);
    this.runAiTurns();
    return true;
  }

  /** Standard Othello turn advance with automatic passes and end detection. */
  advanceTurn(justMoved) {
    const next = opponent(justMoved);
    const board = this.board();
    if (hasAnyMove(board, next)) {
      this.room.turn = colorName(next);
      return;
    }
    if (hasAnyMove(board, justMoved)) {
      this.room.history.push({ n: this.room.moveNumber, color: colorName(next), coord: null, pass: true });
      this.room.lastMove = { coord: null, color: colorName(next), pass: true };
      this.room.turn = colorName(justMoved);
      return;
    }
    this.endGame("score");
  }

  runAiTurns() {
    let guard = 0;
    while (this.room.status === "playing" && this.room.turn === colorName(AI_COLOR) && guard < 64) {
      guard += 1;
      const board = this.board();
      const legal = movesFor(board, AI_COLOR);
      if (legal.length === 0) break; // defensive; advanceTurn never leaves AI stuck
      const seed = hashSeed(`${this.room.roomId}:${this.room.gameNumber}:${this.room.moveNumber}`);
      const wanted = chooseMove(board, AI_COLOR, { level: this.room.level, seed });
      const move = legal.find((entry) => entry.index === wanted) ?? legal[0];
      this.room.boardText = boardToString(applyMove(board, AI_COLOR, move));
      this.room.moveNumber += 1;
      this.room.lastMove = { coord: coordOf(move.index), color: colorName(AI_COLOR), pass: false };
      this.room.history.push({ n: this.room.moveNumber, color: colorName(AI_COLOR), coord: coordOf(move.index), pass: false });
      if (this.room.history.length >= MAX_HISTORY) {
        this.endGame("score");
        return;
      }
      this.advanceTurn(AI_COLOR);
    }
  }

  handleResign() {
    if (this.room.status !== "playing") return false;
    this.endGame("resign", colorName(AI_COLOR));
    return true;
  }

  handleRematch() {
    if (this.room.status !== "ended") return false;
    this.room.status = "playing";
    this.room.boardText = boardToString(initialBoard());
    this.room.turn = colorName(HUMAN_COLOR);
    this.room.moveNumber = 0;
    this.room.lastMove = null;
    this.room.result = null;
    this.room.history = [];
    this.room.gameNumber += 1;
    return true;
  }

  endGame(reason, forcedWinner = null) {
    const score = scoreOf(this.board());
    let winner;
    if (forcedWinner) winner = forcedWinner;
    else if (score.black > score.white) winner = "black";
    else if (score.white > score.black) winner = "white";
    else winner = "draw";
    this.room.status = "ended";
    this.room.turn = "none";
    this.room.result = { winner, reason, black: score.black, white: score.white };
    if (winner === colorName(HUMAN_COLOR)) this.room.wins.human += 1;
    else if (winner === colorName(AI_COLOR)) this.room.wins.ai += 1;
    else this.room.wins.draw += 1;
  }

  async alarm() {
    await this.runExclusive(async () => {
      const room = await this.ensureLoaded();
      if (!room) return;
      const now = Date.now();
      const idleMs = now - room.updatedAtMs;
      const keepWhilePlaying = room.status === "playing" && this.hasLivePlayerSocket() && idleMs < HARD_IDLE_MS;
      if (keepWhilePlaying) {
        await this.state.storage.setAlarm(now + IDLE_MS);
        return;
      }
      await this.state.storage.deleteAll();
      this.room = null;
      this.loaded = false;
    });
  }
}
