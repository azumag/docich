import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BLACK, WHITE, initialBoard, boardFromString, boardToString,
  movesFor, applyMove, coordOf, parseCoord, scoreOf,
} from "../src/othello/engine.js";
import { chooseMove, AI_LEVELS, DEFAULT_LEVEL } from "../src/othello/ai.js";

const WHITE_ANSWER_BOARD = (() => {
  // Position after black d3 from the engine tests.
  const board = initialBoard();
  const d3 = movesFor(board, BLACK).find((move) => move.index === parseCoord("d3"));
  return applyMove(board, BLACK, d3);
})();

test("easy picks a legal move and is seed-deterministic", () => {
  const first = chooseMove(initialBoard(), BLACK, { level: "easy", seed: 42 });
  assert.ok(movesFor(initialBoard(), BLACK).some((move) => move.index === first));
  assert.equal(chooseMove(initialBoard(), BLACK, { level: "easy", seed: 42 }), first);
  // Different seeds may differ but must stay legal.
  for (let seed = 0; seed < 20; seed += 1) {
    const move = chooseMove(initialBoard(), BLACK, { level: "easy", seed });
    assert.ok(movesFor(initialBoard(), BLACK).some((entry) => entry.index === move), `seed ${seed}`);
  }
});

test("chooseMove returns null only when there is no legal move", () => {
  const stuck = boardFromString("WWBBBBB.WWBBBB..WBWWBBBB.BWWBBBBWBBWWBBBWBBBBWWBWWBWWWWBWWWWWBBB");
  assert.equal(chooseMove(stuck, BLACK, { level: "normal" }), null);
  assert.equal(chooseMove(stuck, BLACK, { level: "hard" }), null);
  assert.equal(chooseMove(initialBoard(), 9, { level: "normal" }), null);
});

test("normal and hard always return legal moves mid-game", () => {
  for (const level of ["normal", "hard"]) {
    const move = chooseMove(WHITE_ANSWER_BOARD, WHITE, { level, seed: 7 });
    assert.ok(
      movesFor(WHITE_ANSWER_BOARD, WHITE).some((entry) => entry.index === move),
      `${level} must pick a legal move`,
    );
  }
});

test("normal and hard take the free corner", () => {
  // Black a1 captures b1..d1 (e1 anchor) and a2 anchors down; the corner is
  // dominant for both searching levels.
  const board = boardFromString(".WWWBWWWB.......................................................");
  for (const level of ["normal", "hard"]) {
    assert.equal(chooseMove(board, BLACK, { level, seed: 1 }), parseCoord("a1"), level);
  }
});

test("self-play with every level terminates inside the move bound", () => {
  for (const level of AI_LEVELS) {
    let board = initialBoard();
    let color = BLACK;
    let plies = 0;
    let lastBoard = null;
    while (plies < 200) {
      const move = chooseMove(board, color, { level, seed: 1000 + plies });
      const legal = movesFor(board, color);
      if (legal.length === 0) break;
      assert.ok(legal.some((entry) => entry.index === move), `${level} ply ${plies} must be legal`);
      board = applyMove(board, color, legal.find((entry) => entry.index === move));
      lastBoard = board;
      color = color === BLACK ? WHITE : BLACK;
      plies += 1;
    }
    assert.ok(plies < 200, `${level} game must end before the bound`);
    assert.ok(lastBoard !== null);
    const score = scoreOf(lastBoard);
    assert.ok(score.black + score.white <= 64);
  }
});

test("default level is normal and unknown options stay deterministic", () => {
  assert.equal(DEFAULT_LEVEL, "normal");
  const seeded = chooseMove(WHITE_ANSWER_BOARD, WHITE, { seed: 3 });
  const explicit = chooseMove(WHITE_ANSWER_BOARD, WHITE, { level: "normal", seed: 3 });
  assert.equal(seeded, explicit);
  // Missing seed falls back deterministically.
  assert.equal(chooseMove(WHITE_ANSWER_BOARD, WHITE, {}), chooseMove(WHITE_ANSWER_BOARD, WHITE, {}));
});
