/** Deterministic Othello AI for the arena AI seat.
 *
 * Pure function: same board + same seed -> same move. No clock, no randomness
 * beyond the caller-provided seed, no external services. The room derives the
 * seed from the room id and move number, so a recorded game can be replayed
 * move for move.
 *
 * Levels (design §3):
 * - "easy":   uniform random among legal moves (seeded).
 * - "normal": positional weights + mobility, 2-ply alpha-beta.
 * - "hard":   positional weights + mobility + frontier, 4-ply alpha-beta with
 *             a node budget so the worst case stays bounded inside a request.
 */
import { BLACK, WHITE, EMPTY, opponent, movesFor, applyMove, scoreOf } from "./engine.js";

export const AI_LEVELS = ["easy", "normal", "hard"];
export const DEFAULT_LEVEL = "normal";
const NODE_BUDGET = 20000;

// Positional weights, row-major from black's point of view (symmetric).
const WEIGHTS = [
  120, -20, 20, 5, 5, 20, -20, 120,
  -20, -40, -5, -5, -5, -5, -40, -20,
  20, -5, 15, 3, 3, 15, -5, 20,
  5, -5, 3, 3, 3, 3, -5, 5,
  5, -5, 3, 3, 3, 3, -5, 5,
  20, -5, 15, 3, 3, 15, -5, 20,
  -20, -40, -5, -5, -5, -5, -40, -20,
  120, -20, 20, 5, 5, 20, -20, 120,
];

/** mulberry32: tiny deterministic PRNG, sufficient for "easy". */
function mulberry32(seed) {
  let state = seed >>> 0;
  return function next() {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function evaluate(board, color) {
  const enemy = opponent(color);
  let positional = 0;
  let frontier = 0;
  for (let index = 0; index < 64; index += 1) {
    const cell = board[index];
    if (cell === EMPTY) continue;
    const sign = cell === color ? 1 : -1;
    positional += sign * WEIGHTS[index];
    if (hasEmptyNeighbor(board, index)) frontier += sign;
  }
  const mobility = movesFor(board, color).length - movesFor(board, enemy).length;
  return positional + mobility * 8 - frontier * 2;
}

function hasEmptyNeighbor(board, index) {
  const row = Math.floor(index / 8);
  const column = index % 8;
  for (let deltaRow = -1; deltaRow <= 1; deltaRow += 1) {
    for (let deltaColumn = -1; deltaColumn <= 1; deltaColumn += 1) {
      if (deltaRow === 0 && deltaColumn === 0) continue;
      const nextRow = row + deltaRow;
      const nextColumn = column + deltaColumn;
      if (nextRow < 0 || nextRow > 7 || nextColumn < 0 || nextColumn > 7) continue;
      if (board[nextRow * 8 + nextColumn] === 0) return true;
    }
  }
  return false;
}

function terminalScore(board, color) {
  const score = scoreOf(board);
  const mine = color === BLACK ? score.black : score.white;
  const theirs = color === BLACK ? score.white : score.black;
  if (mine > theirs) return 10000 + (mine - theirs);
  if (mine < theirs) return -10000 - (theirs - mine);
  return 0;
}

function negamax(board, color, depth, alpha, beta, budget) {
  budget.used += 1;
  const moves = movesFor(board, color);
  if (moves.length === 0) {
    if (!movesFor(board, opponent(color)).length) return terminalScore(board, color);
    // Pass: same depth, side switches.
    return -negamax(board, opponent(color), depth, -beta, -alpha, budget);
  }
  if (depth <= 0 || budget.used > budget.limit) return evaluate(board, color);
  // Deterministic ordering: best positional weight first, ties by index.
  moves.sort((left, right) => (WEIGHTS[right.index] - WEIGHTS[left.index]) || (left.index - right.index));
  let best = -Infinity;
  for (const move of moves) {
    const child = applyMove(board, color, move);
    const value = -negamax(child, opponent(color), depth - 1, -beta, -alpha, budget);
    if (value > best) best = value;
    if (best > alpha) alpha = best;
    if (alpha >= beta) break;
  }
  return best;
}

function searchBest(board, color, depth) {
  const budget = { used: 0, limit: NODE_BUDGET };
  const moves = movesFor(board, color);
  if (moves.length === 0) return null;
  moves.sort((left, right) => (WEIGHTS[right.index] - WEIGHTS[left.index]) || (left.index - right.index));
  let bestMove = moves[0];
  let bestValue = -Infinity;
  let alpha = -Infinity;
  for (const move of moves) {
    const child = applyMove(board, color, move);
    const value = -negamax(child, opponent(color), depth - 1, -Infinity, -alpha, budget);
    if (value > bestValue) {
      bestValue = value;
      bestMove = move;
    }
    if (bestValue > alpha) alpha = bestValue;
  }
  return bestMove.index;
}

/**
 * @param {Uint8Array} board
 * @param {number} color BLACK or WHITE
 * @param {{level?: string, seed?: number}} options
 * @returns {number|null} move index, or null when there is no legal move
 */
export function chooseMove(board, color, options = {}) {
  if (color !== BLACK && color !== WHITE) return null;
  const level = options.level ?? DEFAULT_LEVEL;
  const moves = movesFor(board, color);
  if (moves.length === 0) return null;
  if (level === "easy") {
    const random = mulberry32(Number.isInteger(options.seed) ? options.seed : 0);
    return moves[Math.floor(random() * moves.length) % moves.length].index;
  }
  if (level === "hard") return searchBest(board, color, 4);
  return searchBest(board, color, 2);
}
