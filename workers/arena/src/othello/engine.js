/** Pure Othello rules shared by the room authority, the AI and the tests.
 *
 * Board representation: Uint8Array(64), row-major. Index = row * 8 + column.
 * Row 0 is rank 1 (top), column 0 is file a (left). Standard Othello notation
 * is used on the wire: "a1".."h8" (file letter then rank digit).
 *
 * The engine never throws on game inputs: every function that parses or
 * queries returns null/false instead, so callers can fail closed with a fixed
 * error code instead of a stack trace.
 */
export const EMPTY = 0;
export const BLACK = 1;
export const WHITE = 2;

const DIRECTIONS = [
  [-1, -1], [-1, 0], [-1, 1],
  [0, -1], [0, 1],
  [1, -1], [1, 0], [1, 1],
];

export function opponent(color) {
  if (color === BLACK) return WHITE;
  if (color === WHITE) return BLACK;
  return null;
}

export function initialBoard() {
  const board = new Uint8Array(64);
  // d4 white, e4 black, d5 black, e5 white.
  board[3 * 8 + 3] = WHITE;
  board[3 * 8 + 4] = BLACK;
  board[4 * 8 + 3] = BLACK;
  board[4 * 8 + 4] = WHITE;
  return board;
}

/** "d3" -> 19; accepts a-h then 1-8 only. */
export function parseCoord(text) {
  if (typeof text !== "string" || !/^[a-h][1-8]$/.test(text)) return null;
  const column = text.charCodeAt(0) - 97;
  const row = text.charCodeAt(1) - 49;
  return row * 8 + column;
}

/** 19 -> "d3". */
export function coordOf(index) {
  if (!Number.isInteger(index) || index < 0 || index > 63) return null;
  const row = Math.floor(index / 8);
  const column = index % 8;
  return String.fromCharCode(97 + column) + String(row + 1);
}

/** Wire/storage form: 64 characters of ".", "B" or "W". */
export function boardToString(board) {
  let text = "";
  for (let index = 0; index < 64; index += 1) {
    text += board[index] === BLACK ? "B" : board[index] === WHITE ? "W" : ".";
  }
  return text;
}

export function isValidBoardString(text) {
  return typeof text === "string" && text.length === 64 && /^[.BW]{64}$/.test(text);
}

/** Strict parse; null on any deviation (never throws). */
export function boardFromString(text) {
  if (!isValidBoardString(text)) return null;
  const board = new Uint8Array(64);
  for (let index = 0; index < 64; index += 1) {
    const character = text[index];
    board[index] = character === "B" ? BLACK : character === "W" ? WHITE : EMPTY;
  }
  return board;
}

function lineFlips(board, color, row, column, deltaRow, deltaColumn) {
  const enemy = opponent(color);
  const flips = [];
  let currentRow = row + deltaRow;
  let currentColumn = column + deltaColumn;
  while (currentRow >= 0 && currentRow < 8 && currentColumn >= 0 && currentColumn < 8) {
    const cell = board[currentRow * 8 + currentColumn];
    if (cell === enemy) {
      flips.push(currentRow * 8 + currentColumn);
    } else if (cell === color) {
      return flips.length > 0 ? flips : null;
    } else {
      return null;
    }
    currentRow += deltaRow;
    currentColumn += deltaColumn;
  }
  return null;
}

/** All legal moves for `color`, in deterministic (index, direction) order. */
export function movesFor(board, color) {
  if (color !== BLACK && color !== WHITE) return [];
  const moves = [];
  for (let index = 0; index < 64; index += 1) {
    if (board[index] !== EMPTY) continue;
    const row = Math.floor(index / 8);
    const column = index % 8;
    const flips = [];
    for (const [deltaRow, deltaColumn] of DIRECTIONS) {
      const line = lineFlips(board, color, row, column, deltaRow, deltaColumn);
      if (line) flips.push(...line);
    }
    if (flips.length > 0) moves.push({ index, flips });
  }
  return moves;
}

export function hasAnyMove(board, color) {
  return movesFor(board, color).length > 0;
}

/** Apply a move obtained from movesFor(); returns a fresh board. */
export function applyMove(board, color, move) {
  const next = Uint8Array.from(board);
  next[move.index] = color;
  for (const index of move.flips) next[index] = color;
  return next;
}

export function scoreOf(board) {
  let black = 0;
  let white = 0;
  for (let index = 0; index < 64; index += 1) {
    if (board[index] === BLACK) black += 1;
    else if (board[index] === WHITE) white += 1;
  }
  return { black, white };
}
