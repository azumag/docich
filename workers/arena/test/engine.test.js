import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BLACK, WHITE, EMPTY, opponent,
  initialBoard, boardToString, boardFromString, isValidBoardString,
  movesFor, applyMove, scoreOf, parseCoord, coordOf, hasAnyMove,
} from "../src/othello/engine.js";

test("initial board is the standard center and black opens", () => {
  const board = initialBoard();
  assert.equal(board[3 * 8 + 3], WHITE); // d4
  assert.equal(board[3 * 8 + 4], BLACK); // e4
  assert.equal(board[4 * 8 + 3], BLACK); // d5
  assert.equal(board[4 * 8 + 4], WHITE); // e5
  const rows = boardToString(board).match(/.{8}/g);
  assert.deepEqual(rows.slice(3, 5), ["...WB...", "...BW..."]);
  assert.deepEqual(scoreOf(board), { black: 2, white: 2 });
});

test("coordinates round-trip and reject everything else", () => {
  assert.equal(parseCoord("d3"), 19);
  assert.equal(coordOf(19), "d3");
  assert.equal(coordOf(0), "a1");
  assert.equal(coordOf(63), "h8");
  assert.equal(coordOf(1), "b1");
  for (const bad of [null, undefined, "", "a", "a0", "a9", "a10", "i1", "A1", "d3 ", " d3", "d3x"]) {
    assert.equal(parseCoord(bad), null, JSON.stringify(bad));
    assert.equal(coordOf(bad), null, JSON.stringify(bad));
  }
  assert.equal(parseCoord(1), null);
  for (const out of [-1, 64, 1.5, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.equal(coordOf(out), null, JSON.stringify(out));
  }
});

test("initial legal moves are the standard four for black", () => {
  const moves = movesFor(initialBoard(), BLACK).map((move) => coordOf(move.index));
  assert.deepEqual(moves.sort(), ["c4", "d3", "e6", "f5"]);
  // The initial position is symmetric: white (if it moved first) has the mirrored four.
  const whiteMoves = movesFor(initialBoard(), WHITE).map((move) => coordOf(move.index));
  assert.deepEqual(whiteMoves.sort(), ["c5", "d6", "e3", "f4"]);
  assert.equal(movesFor(initialBoard(), EMPTY).length, 0);
  assert.equal(movesFor(initialBoard(), 7).length, 0);
});

test("after d3 white can only answer c3 or c5", () => {
  const board = initialBoard();
  const d3 = movesFor(board, BLACK).find((move) => move.index === parseCoord("d3"));
  const next = applyMove(board, BLACK, d3);
  const nextText = boardToString(next);
  assert.equal(nextText[parseCoord("d3")], "B");
  assert.equal(nextText[parseCoord("d4")], "B");
  // original board is untouched
  assert.equal(boardToString(board)[parseCoord("d3")], ".");
  const answers = movesFor(next, WHITE).map((move) => coordOf(move.index));
  assert.deepEqual(answers.sort(), ["c3", "c5", "e3"]);
  const c3 = movesFor(next, WHITE).find((move) => move.index === parseCoord("c3"));
  assert.deepEqual(c3.flips, [parseCoord("d4")]);
});

test("lineFlips stops at the first own disc and never crosses it", () => {
  // Black anchor on e1 splits rank 1: a black a1 only captures b1..d1.
  const board = boardFromString(".WWWBWWWB.......................................................");
  assert.ok(board, "fixture board must parse");
  const move = movesFor(board, BLACK).find((entry) => entry.index === parseCoord("a1"));
  assert.ok(move, "a1 must be legal");
  assert.deepEqual(move.flips, [1, 2, 3]); // b1,c1,d1 (e1 is the own-color anchor)
  const applied = applyMove(board, BLACK, move);
  assert.equal(boardToString(applied).slice(0, 8), "BBBBBWWW");
});

test("board string helpers are strict", () => {
  assert.ok(isValidBoardString(boardToString(initialBoard())));
  for (const bad of [null, 42, "", "B", ".".repeat(63), `${".".repeat(63)}X`, `${".".repeat(64)}.`]) {
    assert.equal(isValidBoardString(bad), false, JSON.stringify(bad));
    assert.equal(boardFromString(bad), null, JSON.stringify(bad));
  }
  const board = boardFromString(boardToString(initialBoard()));
  assert.equal(board[27], WHITE);
});

// Deterministic fixtures found by seeded easy-level self-play (see design §3).
const BLACK_STUCK_BOARD =
  "WWBBBBB.WWBBBB..WBWWBBBB.BWWBBBBWBBWWBBBWBBBBWWBWWBWWWWBWWWWWBBB";

test("pass detection on a crafted immobile position", () => {
  const board = boardFromString(BLACK_STUCK_BOARD);
  assert.ok(board);
  assert.equal(hasAnyMove(board, BLACK), false, "black must be immobile");
  assert.equal(hasAnyMove(board, WHITE), true, "white must have a move");
});

const FULL_END_BOARD =
  "BBBBBBBBWWWBWWBWWBBWWWWWWBBBBBWWWWBWWWBWWWWWWBBWWWBBBBBWWWWWWWWW";

test("both sides immobile ends the game", () => {
  const board = boardFromString(FULL_END_BOARD);
  assert.ok(board);
  assert.equal(hasAnyMove(board, BLACK), false);
  assert.equal(hasAnyMove(board, WHITE), false);
  assert.deepEqual(scoreOf(board), { black: 26, white: 38 });
});

test("scoreOf counts full boards", () => {
  const board = new Uint8Array(64).fill(BLACK);
  board[0] = WHITE;
  assert.deepEqual(scoreOf(board), { black: 63, white: 1 });
});

test("opponent covers every color", () => {
  assert.equal(opponent(BLACK), WHITE);
  assert.equal(opponent(WHITE), BLACK);
  assert.equal(opponent(0), null);
  assert.equal(opponent(9), null);
});
