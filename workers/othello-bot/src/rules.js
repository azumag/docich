/**
 * Othello rules + docich brain for the participatory-games MVP (Issue #699).
 *
 * Faithful JS mirror of `src/docich/othello_corner.py` so the Python offline
 * contract and the Cloudflare Worker side share one set of rules. Keep the two
 * files in lockstep: same constants, same legality, same deterministic move
 * choice. Board orientations match the Python core.
 */

export const SCHEMA = 1;
export const BOARD_SIZE = 8;
export const CELL_EMPTY = 0;
export const CELL_BLACK = 1;
export const CELL_WHITE = 2;
export const PLAYERS = [CELL_BLACK, CELL_WHITE];
export const PLAYER_LABELS = Object.freeze({ 1: "black", 2: "white" });
export const VIEWER_PLAYER = CELL_BLACK;
export const AI_PLAYER = CELL_WHITE;

export const ROOM_PHASES = Object.freeze(["IDLE", "ROOM_OPEN", "PLAYING", "FINISHED", "FAILED", "TIMEOUT"]);
export const TRANSITIONS = Object.freeze({
  IDLE: new Set(["ROOM_OPEN"]),
  ROOM_OPEN: new Set(["PLAYING", "FAILED", "TIMEOUT"]),
  PLAYING: new Set(["FINISHED", "FAILED", "TIMEOUT"]),
  FINISHED: new Set(["IDLE"]),
  FAILED: new Set(["IDLE"]),
  TIMEOUT: new Set(["IDLE"]),
});

export const MAX_MOVES = 80;
export const MOVE_WINDOW_DEFAULT_S = 45;
export const MOVE_WINDOW_MAX_S = 300;
export const OUTCOMES = new Set(["viewer_win", "ai_win", "draw", "aborted", "timeout"]);

export const POSITIONAL_WEIGHTS = Object.freeze([
  [120, -20, 20, 5, 5, 20, -20, 120],
  [-20, -40, -5, -5, -5, -5, -40, -20],
  [20, -5, 15, 3, 3, 15, -5, 20],
  [5, -5, 3, 3, 3, 3, -5, 5],
  [5, -5, 3, 3, 3, 3, -5, 5],
  [20, -5, 15, 3, 3, 15, -5, 20],
  [-20, -40, -5, -5, -5, -5, -40, -20],
  [120, -20, 20, 5, 5, 20, -20, 120],
]);

const DIRECTIONS = [
  [-1, -1], [-1, 0], [-1, 1],
  [0, -1], [0, 1],
  [1, -1], [1, 0], [1, 1],
];

export class OthelloRulesError extends Error {}

export function opponent(player) {
  if (player === CELL_BLACK) return CELL_WHITE;
  if (player === CELL_WHITE) return CELL_BLACK;
  throw new OthelloRulesError("invalid player");
}

export function newBoard() {
  const mid = BOARD_SIZE / 2;
  const board = Array.from({ length: BOARD_SIZE }, () => new Array(BOARD_SIZE).fill(CELL_EMPTY));
  board[mid - 1][mid - 1] = CELL_WHITE;
  board[mid][mid] = CELL_WHITE;
  board[mid - 1][mid] = CELL_BLACK;
  board[mid][mid - 1] = CELL_BLACK;
  return board;
}

export function validateShape(board) {
  if (!Array.isArray(board) || board.length !== BOARD_SIZE) throw new OthelloRulesError("invalid board");
  for (const row of board) {
    if (!Array.isArray(row) || row.length !== BOARD_SIZE) throw new OthelloRulesError("invalid board row");
    for (const cell of row) {
      if (cell !== CELL_EMPTY && cell !== CELL_BLACK && cell !== CELL_WHITE) {
        throw new OthelloRulesError("invalid cell");
      }
    }
  }
  return board;
}

/** Shape + the 4-disc invariant a *played* board must keep. */
export function validateBoard(board) {
  validateShape(board);
  let discs = 0;
  for (const row of board) for (const cell of row) {
    if (cell !== CELL_EMPTY) discs += 1;
  }
  if (discs < 4) throw new OthelloRulesError("board lost its starting discs");
  return board;
}

export function flipsFor(board, player, row, col) {
  if (!Number.isSafeInteger(row) || !Number.isSafeInteger(col)
      || row < 0 || row >= BOARD_SIZE || col < 0 || col >= BOARD_SIZE) {
    throw new OthelloRulesError("invalid move");
  }
  if (!PLAYERS.includes(player)) throw new OthelloRulesError("invalid player");
  validateShape(board);
  if (board[row][col] !== CELL_EMPTY) return [];
  const opp = opponent(player);
  const flipped = [];
  for (const [dr, dc] of DIRECTIONS) {
    const line = [];
    let r = row + dr, c = col + dc;
    while (r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE && board[r][c] === opp) {
      line.push([r, c]);
      r += dr; c += dc;
    }
    if (line.length && r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE && board[r][c] === player) {
      flipped.push(...line);
    }
  }
  return flipped;
}

export function legalMoves(board, player) {
  validateShape(board);
  if (!PLAYERS.includes(player)) throw new OthelloRulesError("invalid player");
  const moves = [];
  for (let r = 0; r < BOARD_SIZE; r += 1) {
    for (let c = 0; c < BOARD_SIZE; c += 1) {
      if (board[r][c] === CELL_EMPTY && flipsFor(board, player, r, c).length > 0) {
        moves.push([r, c]);
      }
    }
  }
  return moves;
}

export function applyMove(board, player, row, col) {
  const flipped = flipsFor(board, player, row, col);
  if (flipped.length === 0) throw new OthelloRulesError("illegal move");
  const next = board.map((r) => r.slice());
  next[row][col] = player;
  for (const [r, c] of flipped) next[r][c] = player;
  // The played board must keep at least its four starting discs.
  return validateBoard(next);
}

export function score(board) {
  validateShape(board);
  let black = 0, white = 0;
  for (const row of board) for (const cell of row) {
    if (cell === CELL_BLACK) black += 1;
    else if (cell === CELL_WHITE) white += 1;
  }
  return { black, white };
}

export function isTerminal(board) {
  return legalMoves(board, CELL_BLACK).length === 0 && legalMoves(board, CELL_WHITE).length === 0;
}

export function winnerOf(board) {
  const counts = score(board);
  if (counts.black > counts.white) return CELL_BLACK;
  if (counts.white > counts.black) return CELL_WHITE;
  return null;
}

/** Deterministic positional-weighted greedy: the same rule the Python core uses. */
export function aiChooseMove(board, player, moves = null) {
  validateBoard(board);
  if (!PLAYERS.includes(player)) throw new OthelloRulesError("invalid player");
  const candidates = (moves ?? legalMoves(board, player)).slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (candidates.length === 0) throw new OthelloRulesError("no legal move");
  let best = null, bestScore = null;
  for (const [r, c] of candidates) {
    const value = POSITIONAL_WEIGHTS[r][c];
    if (bestScore === null || value > bestScore) { best = [r, c]; bestScore = value; }
  }
  return best;
}

/** `d3` -> [2, 3]. Same grammar as the Python parse_move. */
export function parseMove(text) {
  if (typeof text !== "string") return null;
  let t = text.trim().toLowerCase();
  if (t.startsWith("!")) t = t.slice(1).trim();
  const parts = t.split(/\s+/).filter(Boolean);
  if (parts.length === 2 && (parts[0] === "move" || parts[0] === "m")) t = parts[1];
  else if (parts.length > 1) return null;
  if (t.length !== 2) return null;
  const colCh = t[0], rowCh = t[1];
  if (!"abcdefgh".includes(colCh) || !"12345678".includes(rowCh)) return null;
  return [rowCh.charCodeAt(0) - 49, colCh.charCodeAt(0) - 97];
}
