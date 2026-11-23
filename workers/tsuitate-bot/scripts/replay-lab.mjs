#!/usr/bin/env node
/**
 * Offline replay lab for the Tsuitate brain.
 *
 * It rebuilds the true board from one finished public replay (the site's own
 * final SFEN must match, otherwise it fails closed), rebuilds the *own-view*
 * observation the bot had at each of its turns, and re-runs the brain on those
 * positions.  A candidate move is judged against the true board with the rules
 * the server enforces, so the report separates accepted moves, fouls (with the
 * mechanism) and captured material.
 *
 * This is a diagnostic, not a strength proof: the opponent's moves are kept as
 * actually played, positions are evaluated independently (no forward rollout),
 * and repetition / pawn-drop-mate are not judged.  It never talks to the
 * network and never reads credentials.
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export const STANDARD_START =
  "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1";
const RANKS = "abcdefghi";
const VALUE = { P: 1, L: 3, N: 3, S: 5, G: 6, B: 8, R: 10, K: 0 };
const HAND_ROLES = ["P", "L", "N", "S", "G", "B", "R"];
const PROMOTABLE = new Set(["P", "L", "N", "S", "B", "R"]);
const USI_MOVE = /^(?:[1-9][a-i][1-9][a-i]\+?|[PLNSGBR]\*[1-9][a-i])$/;
const SFEN = /^[A-Za-z0-9+/ \-]{1,200}$/;

function squareIndex(square) {
  return RANKS.indexOf(square[1]) * 9 + (9 - Number(square[0]));
}
function squareName(index) {
  return `${9 - (index % 9)}${RANKS[Math.floor(index / 9)]}`;
}
function sideOf(piece) {
  const letter = piece.slice(-1);
  return letter === letter.toUpperCase() ? "sente" : "gote";
}
function pieceRole(piece) {
  return piece.startsWith("+") ? `+${piece[1].toUpperCase()}` : piece.toUpperCase();
}
function baseRole(piece) {
  return piece.slice(-1).toUpperCase();
}

export function parseSfen(sfen) {
  if (typeof sfen !== "string" || !SFEN.test(sfen)) throw new Error("invalid_sfen");
  const [boardPart, turn, handPart] = sfen.split(" ");
  if (turn !== "b" && turn !== "w") throw new Error("invalid_sfen_turn");
  const squares = Array(81).fill(null);
  const rows = boardPart.split("/");
  if (rows.length !== 9) throw new Error("invalid_sfen_board");
  rows.forEach((row, rank) => {
    let slot = 0;
    for (let index = 0; index < row.length; index += 1) {
      const char = row[index];
      if (/[1-9]/.test(char)) {
        slot += Number(char);
        continue;
      }
      let piece = char;
      if (char === "+") {
        index += 1;
        if (index >= row.length || !/[A-Za-z]/.test(row[index])) throw new Error("invalid_sfen_piece");
        piece = `+${row[index]}`;
      } else if (!/[A-Za-z]/.test(char)) {
        throw new Error("invalid_sfen_piece");
      }
      if (slot > 8) throw new Error("invalid_sfen_row");
      squares[rank * 9 + slot] = piece;
      slot += 1;
    }
    if (slot !== 9) throw new Error("invalid_sfen_row");
  });
  const hands = { sente: {}, gote: {} };
  if (handPart !== "-") {
    let count = 0;
    for (const char of handPart) {
      if (/[0-9]/.test(char)) {
        count = count * 10 + Number(char);
        continue;
      }
      const role = char.toUpperCase();
      if (!HAND_ROLES.includes(role)) throw new Error("invalid_sfen_hand");
      const side = char === role ? "sente" : "gote";
      hands[side][role] = (hands[side][role] ?? 0) + (count || 1);
      count = 0;
    }
    if (count) throw new Error("invalid_sfen_hand");
  }
  return { squares, hands, turn };
}

export function toSfen(position, turn = "b") {
  const rows = [];
  for (let rank = 0; rank < 9; rank += 1) {
    let row = "";
    let empty = 0;
    for (let slot = 0; slot < 9; slot += 1) {
      const piece = position.squares[rank * 9 + slot];
      if (!piece) {
        empty += 1;
        continue;
      }
      if (empty) {
        row += String(empty);
        empty = 0;
      }
      row += piece;
    }
    if (empty) row += String(empty);
    rows.push(row);
  }
  const hand = (side) => HAND_ROLES
    .filter((role) => (position.hands[side][role] ?? 0) > 0)
    .sort((a, b) => "RBGSNLP".indexOf(a) - "RBGSNLP".indexOf(b))
    .map((role) => {
      const count = position.hands[side][role];
      const text = count > 1 ? String(count) : "";
      return text + (side === "sente" ? role : role.toLowerCase());
    }).join("");
  return `${rows.join("/")} ${turn} ${hand("sente") + hand("gote") || "-"}`;
}

function forward(side) {
  return side === "sente" ? -1 : 1;
}
function inZone(side, rank) {
  return side === "sente" ? rank <= 2 : rank >= 6;
}
function mustPromote(role, side, rank) {
  if (role === "P" || role === "L") return side === "sente" ? rank === 0 : rank === 8;
  if (role === "N") return side === "sente" ? rank <= 1 : rank >= 7;
  return false;
}
function deadEndDrop(role, side, rank) {
  return mustPromote(role, side, rank);
}

/** Does the piece on `from` attack `to` (blockers considered)? */
export function attacks(squares, from, to, ignoreBlockers = false) {
  const piece = squares[from];
  if (!piece || from === to) return false;
  const side = sideOf(piece);
  const role = pieceRole(piece);
  const dRank = Math.floor(to / 9) - Math.floor(from / 9);
  const dFile = (to % 9) - (from % 9);
  const r = dRank * forward(side); // forward-positive
  const steps = new Map();
  const add = (list) => list.forEach(([dr, df]) => steps.set(`${dr},${df}`, true));
  if (role === "P") add([[1, 0]]);
  else if (role === "N") add([[2, -1], [2, 0], [2, 1]]);
  else if (role === "S") add([[1, -1], [1, 0], [1, 1], [-1, -1], [-1, 1]]);
  else if (role === "G" || role === "+P" || role === "+L" || role === "+N" || role === "+S") {
    add([[1, -1], [1, 0], [1, 1], [0, -1], [0, 1], [-1, 0]]);
  } else if (role === "K") {
    add([[-1, -1], [-1, 0], [-1, 1], [0, -1], [0, 1], [1, -1], [1, 0], [1, 1]]);
  }
  if (role === "+B") add([[-1, 0], [1, 0], [0, -1], [0, 1]]);
  if (role === "+R") add([[-1, -1], [-1, 1], [1, -1], [1, 1]]);
  if (steps.has(`${r},${dFile}`)) return true;
  const rayDirs = [];
  if (role === "L") rayDirs.push([1, 0]);
  if (role === "B" || role === "+B") rayDirs.push([1, 1], [1, -1], [-1, 1], [-1, -1]);
  if (role === "R" || role === "+R") rayDirs.push([1, 0], [-1, 0], [0, 1], [0, -1]);
  for (const [normalizedDr, df] of rayDirs) {
    const dr = normalizedDr * forward(side);
    const fr = Math.floor(from / 9);
    const ff = from % 9;
    const tr = Math.floor(to / 9);
    const tf = to % 9;
    if (tr - fr !== 0 && Math.sign(tr - fr) !== Math.sign(dr) && dr !== 0) continue;
    if (tf - ff !== 0 && Math.sign(tf - ff) !== Math.sign(df) && df !== 0) continue;
    if (dr === 0 && tr !== fr) continue;
    if (df === 0 && tf !== ff) continue;
    if (dr !== 0 && (tr - fr) % dr !== 0) continue;
    if (df !== 0 && (tf - ff) % df !== 0) continue;
    const stepsNeeded = dr !== 0 ? Math.abs(tr - fr) : Math.abs(tf - ff);
    if (stepsNeeded < 1) continue;
    if (dr !== 0 && df !== 0 && Math.abs(tr - fr) !== Math.abs(tf - ff)) continue;
    let blocked = false;
    for (let step = 1; step < stepsNeeded; step += 1) {
      const row = fr + dr * step;
      const col = ff + df * step;
      if (!ignoreBlockers && squares[row * 9 + col]) {
        blocked = true;
        break;
      }
    }
    if (!blocked) return true;
  }
  return false;
}

export function kingSquare(squares, side) {
  const king = side === "sente" ? "K" : "k";
  const found = [];
  squares.forEach((piece, index) => { if (piece === king) found.push(index); });
  return found;
}

export function kingAttacked(squares, side) {
  const kings = kingSquare(squares, side);
  if (kings.length !== 1) return true; // malformed view must not authorize a move
  return squares.some((piece, index) => piece && sideOf(piece) !== side && attacks(squares, index, kings[0]));
}

export function applyMove(position, usi, side) {
  const squares = [...position.squares];
  const hands = { sente: { ...position.hands.sente }, gote: { ...position.hands.gote } };
  if (usi[1] === "*") {
    const role = usi[0];
    squares[squareIndex(usi.slice(2, 4))] = side === "sente" ? role : role.toLowerCase();
    hands[side][role] = (hands[side][role] ?? 0) - 1;
    if (hands[side][role] === 0) delete hands[side][role];
    return { squares, hands, captured: null, promote: false };
  }
  const from = squareIndex(usi.slice(0, 2));
  const to = squareIndex(usi.slice(2, 4));
  if (from === to) return { ok: false, reason: "invalid_usi" };
  const piece = squares[from];
  const captured = squares[to];
  if (captured) {
    const role = baseRole(captured);
    hands[side][role] = (hands[side][role] ?? 0) + 1;
  }
  const promote = usi.endsWith("+");
  const role = baseRole(piece);
  squares[to] = promote ? `+${side === "sente" ? role : role.toLowerCase()}` : piece;
  squares[from] = null;
  return { squares, hands, captured: captured ? baseRole(captured) : null, promote };
}

/** The first square strictly between a ray's source and destination, if occupied. */
export function blockerBetween(squares, from, to) {
  const fr = Math.floor(from / 9);
  const ff = from % 9;
  const tr = Math.floor(to / 9);
  const tf = to % 9;
  const dRank = tr - fr;
  const dFile = tf - ff;
  if (dRank !== 0 && dFile !== 0 && Math.abs(dRank) !== Math.abs(dFile)) return null;
  const stepRank = Math.sign(dRank);
  const stepFile = Math.sign(dFile);
  const distance = Math.max(Math.abs(dRank), Math.abs(dFile));
  for (let step = 1; step < distance; step += 1) {
    const index = (fr + stepRank * step) * 9 + (ff + stepFile * step);
    if (squares[index]) return { square: squareName(index), piece: squares[index] };
  }
  return null;
}

/** Judge one candidate against the true board with the server's own rules. */
export function judgeMove(position, side, usi) {
  if (typeof usi !== "string" || !USI_MOVE.test(usi)) return { ok: false, reason: "invalid_usi" };
  const { squares, hands } = position;
  if (usi[1] === "*") {
    const role = usi[0];
    const to = squareIndex(usi.slice(2, 4));
    const rank = Math.floor(to / 9);
    if ((hands[side][role] ?? 0) < 1) return { ok: false, reason: "no_hand_piece" };
    if (squares[to]) return { ok: false, reason: "occupied_drop" };
    if (deadEndDrop(role, side, rank)) return { ok: false, reason: "dead_end_drop" };
    if (role === "P") {
      const file = to % 9;
      const ownPawn = side === "sente" ? "P" : "p";
      for (let r = 0; r < 9; r += 1) if (squares[r * 9 + file] === ownPawn) return { ok: false, reason: "nifu" };
    }
    const after = applyMove(position, usi, side);
    if (kingAttacked(after.squares, side)) return { ok: false, reason: "into_check" };
    return { ok: true, captured: null };
  }
  const from = squareIndex(usi.slice(0, 2));
  const to = squareIndex(usi.slice(2, 4));
  if (from === to) return { ok: false, reason: "invalid_usi" };
  const piece = squares[from];
  if (!piece || sideOf(piece) !== side) return { ok: false, reason: "no_own_piece" };
  const target = squares[to];
  if (target && sideOf(target) === side) return { ok: false, reason: "own_piece" };
  if (target && baseRole(target) === "K") return { ok: false, reason: "king_capture" };
  if (!attacks(squares, from, to)) {
    // Distinguish "wrong shape" from "right direction but blocked between":
    // only the latter says anything about a hidden piece on the path.
    if (attacks(squares, from, to, true)) {
      const blocker = blockerBetween(squares, from, to);
      if (blocker) return { ok: false, reason: "blocked", blocker: blocker.square };
    }
    return { ok: false, reason: "geometry" };
  }
  const role = baseRole(piece);
  const promote = usi.endsWith("+");
  if (promote && (!PROMOTABLE.has(role) || piece.startsWith("+")
      || !(inZone(side, Math.floor(from / 9)) || inZone(side, Math.floor(to / 9))))) {
    return { ok: false, reason: "promotion" };
  }
  if (!promote && !piece.startsWith("+") && mustPromote(role, side, Math.floor(to / 9))) {
    return { ok: false, reason: "promotion" };
  }
  const after = applyMove(position, usi, side);
  if (kingAttacked(after.squares, side)) return { ok: false, reason: "into_check" };
  return { ok: true, captured: target ? baseRole(target) : null };
}

/** Rebuild every position; fail closed unless the site's final SFEN matches. */
export function replay(moves, finalSfen) {
  if (!Array.isArray(moves) || moves.length < 2 || moves.length > 512) throw new Error("invalid_moves");
  const positions = [parseSfen(STANDARD_START)];
  for (const move of moves) {
    if (!move || typeof move.usi !== "string" || (move.by !== "sente" && move.by !== "gote")) {
      throw new Error("invalid_move");
    }
    const current = positions.at(-1);
    const verdict = judgeMove(current, move.by, move.usi);
    if (!verdict.ok) throw new Error(`replay_move_rejected:${verdict.reason}`);
    const next = applyMove(current, move.usi, move.by);
    positions.push({ squares: next.squares, hands: next.hands, turn: move.by === "sente" ? "w" : "b" });
  }
  const expected = parseSfen(finalSfen);
  const actual = positions.at(-1);
  if (toSfen(actual).split(" ").slice(0, 3).join(" ") !== toSfen(expected).split(" ").slice(0, 3).join(" ")) {
    throw new Error("final_sfen_mismatch");
  }
  return positions;
}

function ownPieces(position, side) {
  const pieces = [];
  position.squares.forEach((piece, index) => {
    if (piece && sideOf(piece) === side) pieces.push({ square: squareName(index), role: pieceRole(piece) });
  });
  return pieces.sort((a, b) => a.square.localeCompare(b.square));
}

/**
 * Own-view observations for each of `side`'s turns.  Capture evidence is
 * rebuilt the same way the session does: a piece that was ours right after our
 * previous move but is gone at our next view proves an opponent piece there.
 */
export function buildTurns(positions, moves, foulAttempts, side) {
  const turns = [];
  const discovered = new Map();
  const carriedDrops = new Map();
  for (const attempt of foulAttempts) {
    if (attempt.byColor !== side || attempt.usi[1] !== "*") continue;
    carriedDrops.set(attempt.usi.slice(2, 4), Math.min(
      carriedDrops.get(attempt.usi.slice(2, 4)) ?? Infinity, attempt.moveNumber));
  }
  let acceptedMoves = [];
  let lastAfterOwn = null;
  for (let index = 0; index < moves.length; index += 1) {
    if (moves[index].by !== side) continue;
    const before = positions[index];
    const moveNumber = index + 1;
    const ownNow = new Set(ownPieces(before, side).map((piece) => piece.square));
    // Discover losses caused by the opponent's reply to our previous move: a
    // square that was ours right after that move and is gone at this view.
    if (lastAfterOwn) {
      for (const square of lastAfterOwn) {
        if (!ownNow.has(square)) discovered.set(square, moveNumber);
      }
    }
    const evidence = new Map();
    for (const [square, lostAt] of discovered) {
      if (ownNow.has(square)) continue;
      evidence.set(square, Math.max(0, Math.floor((moveNumber - lostAt) / 2)));
    }
    for (const [square, droppedAt] of carriedDrops) {
      if (ownNow.has(square) || droppedAt >= moveNumber) continue;
      const age = Math.max(0, Math.floor((moveNumber - droppedAt) / 2));
      evidence.set(square, Math.min(evidence.get(square) ?? Infinity, age));
    }
    const priorFouls = foulAttempts.filter((attempt) => attempt.byColor === side
      && attempt.moveNumber < moveNumber).length;
    turns.push({
      moveNumber,
      position: before,
      ownPieces: ownPieces(before, side),
      hand: { ...before.hands[side] },
      inCheck: kingAttacked(before.squares, side),
      knownEnemies: [...evidence.entries()].map(([square, age]) => ({ square, age }))
        .sort((a, b) => a.age - b.age).slice(0, 40),
      priorFouls,
      actualUsi: moves[index].usi,
      recentMoves: [...acceptedMoves].slice(-64),
    });
    acceptedMoves.push(moves[index].usi);
    lastAfterOwn = new Set(ownPieces(positions[index + 1], side).map((piece) => piece.square));
  }
  return turns;
}

function brainObservation(turn, side, budget) {
  return {
    ruleset: "tsuitate-9x9",
    color: side === "sente" ? "b" : "w",
    turn: side === "sente" ? "b" : "w",
    moveNumber: turn.moveNumber,
    pieces: turn.ownPieces,
    hand: turn.hand,
    inCheck: turn.inCheck,
    opponentInCheck: false,
    attemptBudget: budget,
    knownEnemies: turn.knownEnemies,
  };
}

function mergeEvidence(evidence, square, age) {
  const others = evidence.filter((item) => item.square !== square);
  return [...others, { square, age }].sort((a, b) => a.age - b.age).slice(0, 40);
}

/** Re-run one brain over every own turn of a replay and score the outcome. */
export function evaluateReplay({ positions, moves, foulAttempts, color, brain, profile, gameId }) {
  const turns = buildTurns(positions, moves, foulAttempts, color);
  const report = { color, turns: turns.length, accepted: 0, fouls: 0, exhausted: 0,
    noCandidate: 0, capturedValue: 0, byReason: {}, details: [] };
  for (const turn of turns) {
    let budget = 10 - turn.priorFouls;
    let evidence = [...turn.knownEnemies];
    const forbidden = [];
    const foulsThisTurn = [];
    let outcome = null;
    let chosen = null;
    let attempts = 0;
    const turnFouls = [];
    while (budget > 0) {
      const choice = brain.chooseMove(brainObservation({ ...turn, knownEnemies: evidence }, color, budget), {
        profile,
        seed: `${gameId}:${turn.moveNumber}`,
        recentMoves: turn.recentMoves,
        forbiddenMoves: [...forbidden],
        foulMoves: [...foulsThisTurn],
      });
      if (!choice) { outcome = "no_candidate"; break; }
      attempts += 1;
      chosen = choice.usi;
      const verdict = judgeMove(turn.position, color, choice.usi);
      if (verdict.ok) {
        outcome = "accepted";
        report.accepted += 1;
        report.capturedValue += verdict.captured ? VALUE[verdict.captured] : 0;
        break;
      }
      outcome = "foul";
      report.fouls += 1;
      report.byReason[verdict.reason] = (report.byReason[verdict.reason] ?? 0) + 1;
      turnFouls.push({ usi: choice.usi, reason: verdict.reason, blocker: verdict.blocker ?? null });
      if (verdict.blocker) {
        const known = evidence.some((item) => item.square === verdict.blocker);
        const key = known ? "blocked_known" : "blocked_unknown";
        report.byReason[key] = (report.byReason[key] ?? 0) + 1;
      }
      budget -= 1;
      forbidden.push(choice.usi);
      foulsThisTurn.push(choice.usi);
      if (choice.usi[1] === "*") evidence = mergeEvidence(evidence, choice.usi.slice(2, 4), 0);
    }
    if (outcome === "foul" && budget === 0) { report.exhausted += 1; outcome = "exhausted"; }
    if (outcome === "no_candidate") report.noCandidate += 1;
    report.details.push({ n: turn.moveNumber, outcome, usi: chosen, fouls: turnFouls, attempts,
      inCheck: turn.inCheck, actualUsi: turn.actualUsi,
      knownEnemies: turn.knownEnemies.length });
  }
  report.brainVersion = brain.BRAIN_VERSION;
  return report;
}

function resolveTable(payload) {
  if (!payload || payload.type !== "data" || !Array.isArray(payload.nodes)) return null;
  for (const node of payload.nodes) {
    const table = node?.data;
    if (!Array.isArray(table) || !table[0] || typeof table[0] !== "object" || !("game" in table[0])) continue;
    const game = table[table[0].game];
    if (!game || !("finalSfen" in game)) continue;
    const read = (value) => (typeof value === "number" ? table[value] : value);
    const moves = read(game.moves).map((ref) => {
      const move = read(ref);
      return { usi: read(move.usi), by: read(move.byColor), ms: read(move.ms), foulsBefore: read(move.foulsBefore) };
    });
    const foulAttempts = read(game.foulAttempts).map((ref) => {
      const attempt = read(ref);
      return { moveNumber: read(attempt.moveNumber), byColor: read(attempt.byColor), usi: read(attempt.usi) };
    });
    const root = table[0];
    const name = (key) => (root && Number.isSafeInteger(root[key]) && typeof table[root[key]] === "string"
      ? table[root[key]] : null);
    return { gameId: read(game.id), moves, foulAttempts, finalSfen: read(game.finalSfen),
      result: read(game.result), reason: read(game.reason),
      senteName: name("senteName"), goteName: name("goteName") };
  }
  return null;
}

/** Accept an archived record (moves[].side) or the site's __data.json payload. */
export function normalizeReplay(payload, fallback = {}) {
  const table = resolveTable(payload);
  if (table) return table;
  if (!payload || !Array.isArray(payload.moves) || typeof payload.finalSfen !== "string") {
    throw new Error("unrecognized_replay");
  }
  const moves = payload.moves.map((move) => ({
    usi: move.usi, by: move.by ?? move.side,
    ms: move.ms ?? null, foulsBefore: move.foulsBefore ?? 0,
  }));
  return {
    gameId: payload.gameId ?? fallback.gameId,
    moves,
    foulAttempts: Array.isArray(payload.foulAttempts) ? payload.foulAttempts : [],
    finalSfen: payload.finalSfen,
    result: payload.result?.result ?? payload.result,
    reason: payload.result?.reason ?? payload.reason,
    senteName: payload.senteName ?? null,
    goteName: payload.goteName ?? null,
  };
}

/** The bot's own public site name; only used to infer its side on backfill. */
export const BOT_NAME = "DoCiAI";

/**
 * Write one compact, replay-verified record. Accepts either the live corner
 * state (preferred: brain version and side are authoritative) or only the
 * public replay for a historical run listed from Cloudflare storage. Never
 * overwrites a record for a different game.
 */
export async function archiveGame({ state = null, review = null, publicPayload, outDir,
  brainVersion = null, color = null }) {
  const publicReplay = normalizeReplay(publicPayload);
  const gameId = state?.beta_game_id ?? publicReplay.gameId;
  if (typeof gameId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(gameId)) {
    throw new Error("invalid_state_game_id");
  }
  if (publicReplay.gameId && publicReplay.gameId !== gameId) throw new Error("game_id_mismatch");
  const resolvedColor = color ?? state?.beta_color
    ?? (publicReplay.senteName === BOT_NAME ? "sente"
      : publicReplay.goteName === BOT_NAME ? "gote" : null);
  if (resolvedColor !== "sente" && resolvedColor !== "gote") throw new Error("color_unknown");
  if (review) {
    const reviewMoves = (review.moves ?? []).map((move) => ({ usi: move.usi, by: move.side ?? move.by }));
    if (reviewMoves.length !== publicReplay.moves.length
        || reviewMoves.some((move, index) => move.usi !== publicReplay.moves[index].usi
          || move.by !== publicReplay.moves[index].by)) {
      throw new Error("review_moves_mismatch");
    }
  }
  // Refuse to archive a replay whose own position rebuild does not match the
  // site's final SFEN: a drifted move list must never become training data.
  replay(publicReplay.moves, publicReplay.finalSfen);
  const entry = {
    schema: 1,
    gameId,
    brainVersion: brainVersion ?? state?.beta_brainVersion ?? null,
    color: resolvedColor,
    startedAt: state?.started_at ?? null,
    result: state?.beta_result ?? null,
    moves: publicReplay.moves,
    foulAttempts: publicReplay.foulAttempts,
    finalSfen: publicReplay.finalSfen,
    siteResult: publicReplay.result,
    siteReason: publicReplay.reason,
  };
  await mkdir(outDir, { recursive: true });
  const path = join(outDir, `${gameId}.json`);
  try {
    const existing = JSON.parse(await readFile(path, "utf8"));
    if (existing.gameId === entry.gameId) return { path, written: false };
    throw new Error("archive_conflict");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await writeFile(path, `${JSON.stringify(entry)}\n`, { mode: 0o600 });
  return { path, written: true };
}

async function loadBrain(path) {
  if (!path) throw new Error("brain_path_required");
  return import(pathToFileURL(path).href);
}

async function archiveMain(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--state") { args.state = argv[index + 1]; index += 1; }
    else if (flag === "--review") { args.review = argv[index + 1]; index += 1; }
    else if (flag === "--public") { args.public = argv[index + 1]; index += 1; }
    else if (flag === "--out") { args.out = argv[index + 1]; index += 1; }
    else if (flag === "--brain-version") { args.brainVersion = argv[index + 1]; index += 1; }
    else if (flag === "--color") { args.color = argv[index + 1]; index += 1; }
  }
  if (!args.public || !args.out) {
    console.error("usage: replay-lab.mjs archive --public <__data.json> --out <dir> [--state <corner.json>] [--review <review.json>] [--brain-version <v>] [--color sente|gote]");
    return 2;
  }
  const result = await archiveGame({
    state: args.state ? JSON.parse(await readFile(args.state, "utf8")) : null,
    review: args.review ? JSON.parse(await readFile(args.review, "utf8")) : null,
    publicPayload: JSON.parse(await readFile(args.public, "utf8")),
    outDir: args.out,
    brainVersion: args.brainVersion ?? null,
    color: args.color ?? null,
  });
  console.log(JSON.stringify(result));
  return 0;
}

async function main(argv) {
  if (argv[0] === "archive") return archiveMain(argv.slice(1));
  const args = { replays: [], brain: null, compare: null, color: null, profile: null };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (flag === "--replay") { args.replays.push(value); index += 1; }
    else if (flag === "--brain") { args.brain = value; index += 1; }
    else if (flag === "--compare") { args.compare = value; index += 1; }
    else if (flag === "--color") { args.color = value; index += 1; }
  }
  if (!args.replays.length || !args.brain) {
    console.error("usage: replay-lab.mjs --replay <file> --brain <brain/index.js> [--compare <brain/index.js>] [--color sente|gote]");
    return 2;
  }
  const brain = await loadBrain(args.brain);
  const other = args.compare ? await loadBrain(args.compare) : null;
  if (!brain.chooseMove || !brain.LINEAR_PROFILE) throw new Error("brain_module_missing_exports");
  const output = [];
  for (const file of args.replays) {
    const payload = JSON.parse(await readFile(file, "utf8"));
    const replayData = normalizeReplay(payload);
    const positions = replay(replayData.moves, replayData.finalSfen);
    const color = args.color ?? payload.color ?? "sente";
    const profile = payload.profile ?? brain.LINEAR_PROFILE;
    const base = evaluateReplay({ ...replayData, positions, color, brain, profile, gameId: replayData.gameId });
    const row = { file, gameId: replayData.gameId, result: replayData.result, base };
    if (other) {
      row.compare = evaluateReplay({ ...replayData, positions, color, brain: other, profile, gameId: replayData.gameId });
      row.delta = { fouls: row.compare.fouls - base.fouls,
        capturedValue: row.compare.capturedValue - base.capturedValue,
        accepted: row.compare.accepted - base.accepted };
    }
    output.push(row);
  }
  console.log(JSON.stringify(output, null, 2));
  return 0;
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; })
    .catch((error) => { console.error(JSON.stringify({ error: String(error.message ?? error) })); process.exitCode = 1; });
}
