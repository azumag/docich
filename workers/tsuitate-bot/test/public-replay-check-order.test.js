import assert from "node:assert/strict";
import { test } from "node:test";
import { BRAIN_VERSION, LINEAR_PROFILE, chooseMove } from "../src/brain/index.js";

const profile = { ...LINEAR_PROFILE, exploration: 0 };

/**
 * Own-view only: a gote king on 6a, two pawns in hand and a check. The screen
 * hides the checker, so every response is a guess. This position was reached in
 * a finished public replay at ply 52; v13 answered with the blind drop P*5b
 * first, while v14 probes the visible king escape before it.
 */
const state = {
  ruleset: "tsuitate-9x9",
  color: "w",
  turn: "w",
  moveNumber: 52,
  pieces: [{ square: "6a", role: "K" }],
  hand: { P: 2 },
  inCheck: true,
  opponentInCheck: false,
  attemptBudget: 8,
  knownEnemies: [],
};

test("v14 probes the visible king escape before a blind block", () => {
  assert.equal(BRAIN_VERSION, "tsuitate-brain-v14");
  const choice = chooseMove(state, { profile, seed: "fixture:52" });
  assert.equal(choice.usi, "6a7a");
  assert.equal(choice.features.kingMove, 1);
  const retry = chooseMove(state, { profile, seed: "fixture:52", forbiddenMoves: [choice.usi] });
  assert.equal(retry.usi, "6a5a");
  assert.equal(retry.features.kingMove, 1);
  // Only after the visible escapes are excluded does the response pool (a drop
  // that may block the unknown checker) return.
  const fallback = chooseMove(state, {
    profile,
    seed: "fixture:52",
    forbiddenMoves: ["6a7a", "6a5a", "6a7b"],
    foulMoves: ["6a7a", "6a5a", "6a7b"],
  });
  assert.equal(fallback.features.kingMove, 0);
  assert.equal(fallback.usi, "P*5b");
});

test("the final attempt still uses the response pool instead of a king probe", () => {
  const last = chooseMove({ ...state, attemptBudget: 1 }, { profile, seed: "fixture:52" });
  assert.equal(last.features.kingMove, 0);
  assert.equal(last.usi, "P*5b");
});
