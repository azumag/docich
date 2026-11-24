import { strict as assert } from "node:assert";
import test from "node:test";

import {
  AI_PLAYER, CELL_BLACK, CELL_EMPTY, CELL_WHITE, VIEWER_PLAYER,
  OthelloRulesError, aiChooseMove, applyMove, flipsFor, isTerminal, legalMoves,
  newBoard, opponent, parseMove, score, validateBoard, winnerOf,
} from "../src/rules.js";

function eightCounts(board) {
  return score(board);
}

test("newBoard is the standard start", () => {
  const board = newBoard();
  assert.equal(board[3][4], CELL_BLACK);
  assert.equal(board[4][3], CELL_BLACK);
  assert.equal(board[3][3], CELL_WHITE);
  assert.equal(board[4][4], CELL_WHITE);
  assert.deepEqual(eightCounts(board), { black: 2, white: 2 });
});

test("the four opening moves are the central squares", () => {
  const moves = legalMoves(newBoard(), CELL_BLACK).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  assert.deepEqual(moves, [[2, 3], [3, 2], [4, 5], [5, 4]]);
});

test("a move flips and re-counts discs", () => {
  const board = applyMove(newBoard(), CELL_BLACK, 2, 3);
  assert.deepEqual(eightCounts(board), { black: 4, white: 1 });
  assert.equal(board[3][3], CELL_BLACK);
});

test("occupied, out-of-bounds and non-flanking moves are rejected", () => {
  assert.throws(() => applyMove(newBoard(), CELL_BLACK, 3, 3), OthelloRulesError);
  assert.throws(() => applyMove(newBoard(), CELL_BLACK, -1, 3), OthelloRulesError);
  assert.throws(() => applyMove(newBoard(), CELL_BLACK, 8, 0), OthelloRulesError);
  const sparse = [[CELL_EMPTY, CELL_EMPTY, CELL_EMPTY, CELL_EMPTY, CELL_EMPTY, CELL_EMPTY, CELL_EMPTY, CELL_EMPTY],
    ...Array.from({ length: 7 }, () => [CELL_EMPTY, CELL_EMPTY, CELL_EMPTY, CELL_EMPTY, CELL_EMPTY, CELL_EMPTY, CELL_EMPTY, CELL_EMPTY])];
  assert.equal(flipsFor(sparse, CELL_BLACK, 0, 0).length, 0);
});

test("draw has no winner; full boards are terminal", () => {
  const draw = Array.from({ length: 8 }, (_, r) =>
    Array.from({ length: 8 }, (_, c) => (c % 2 === 0 ? CELL_BLACK : CELL_WHITE)));
  assert.equal(winnerOf(draw), null);
  assert.equal(isTerminal(draw), true);
  const blackFull = Array.from({ length: 8 }, () => new Array(8).fill(CELL_BLACK));
  assert.equal(winnerOf(blackFull), CELL_BLACK);
});

test("the brain is deterministic and refuses a moveless seat", () => {
  const board = newBoard();
  assert.deepEqual(aiChooseMove(board, CELL_BLACK), aiChooseMove(board, CELL_BLACK));
  const full = Array.from({ length: 8 }, () => new Array(8).fill(CELL_BLACK));
  assert.throws(() => aiChooseMove(full, AI_PLAYER), OthelloRulesError);
});

test("parseMove mirrors the Python grammar", () => {
  assert.deepEqual(parseMove("!move d3"), [2, 3]);
  assert.deepEqual(parseMove("move e6"), [5, 4]);
  assert.deepEqual(parseMove("d3"), [2, 3]);
  assert.equal(parseMove("d9"), null);
  assert.equal(parseMove("i3"), null);
  assert.equal(parseMove("read this"), null);
  assert.equal(parseMove(null), null);
});

test("validateBoard refuses corrupt boards", () => {
  assert.throws(() => validateBoard([[CELL_EMPTY]]), OthelloRulesError);
  assert.throws(() => validateBoard(Array.from({ length: 8 }, () => new Array(8).fill(99))), OthelloRulesError);
  const tooFew = Array.from({ length: 8 }, () => new Array(8).fill(CELL_EMPTY));
  tooFew[0][0] = CELL_BLACK;
  tooFew[0][1] = CELL_WHITE;
  assert.throws(() => validateBoard(tooFew), OthelloRulesError);
});

test("opponent mapping", () => {
  assert.equal(opponent(CELL_BLACK), CELL_WHITE);
  assert.equal(opponent(CELL_WHITE), CELL_BLACK);
  assert.throws(() => opponent(0), OthelloRulesError);
});
