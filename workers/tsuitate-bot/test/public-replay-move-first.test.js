import assert from "node:assert/strict";
import { test } from "node:test";
import { BRAIN_VERSION, LINEAR_PROFILE, chooseMove } from "../src/brain/index.js";

const profile = { ...LINEAR_PROFILE, exploration: 0 };

/**
 * Own-view only: a gote king on 6a, a pawn on 3c and a gold in hand while in
 * check. 3c3d travels the king's diagonal and can block; G*5a can only block.
 * v14 chose the gold drop first; v15 prefers the non-king move.
 */
const state = {
  ruleset: "tsuitate-9x9",
  color: "w",
  turn: "w",
  moveNumber: 42,
  pieces: [{ square: "6a", role: "K" }, { square: "3c", role: "P" }],
  hand: { G: 1 },
  inCheck: true,
  opponentInCheck: false,
  attemptBudget: 1,
  knownEnemies: [],
};

test("v16 prefers a non-king move over a drop in the check response pool", () => {
  assert.equal(BRAIN_VERSION, "tsuitate-brain-v16");
  const choice = chooseMove(state, { profile, seed: "fixture:42" });
  assert.equal(choice.usi, "3c3d");
  assert.equal(choice.features.kingMove, 0);
  // The drop is deferred, not forbidden: once the move is rejected it returns.
  const retry = chooseMove(state, {
    profile,
    seed: "fixture:42",
    forbiddenMoves: [choice.usi],
    foulMoves: [choice.usi],
  });
  assert.equal(retry.usi, "G*5a");
  assert.equal(retry.features.kingMove, 0);
});
