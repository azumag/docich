import assert from "node:assert/strict";
import { test } from "node:test";
import { BRAIN_VERSION, LINEAR_PROFILE, chooseMove } from "../src/brain/index.js";

const profile = { ...LINEAR_PROFILE, exploration: 0 };

/**
 * Own-view only: a gote king on 5a, a bishop on 3c and a silver on 5c. 3c7g+
 * was just rejected as a blocked long promotion. v15 retried 3c8h+ along the
 * same unseen diagonal (the real game repeated this for 10 fouls); v16 pays an
 * extra penalty per unseen square beyond the first and picks the short 5c5d.
 */
const state = {
  ruleset: "tsuitate-9x9",
  color: "w",
  turn: "w",
  moveNumber: 30,
  pieces: [{ square: "5a", role: "K" }, { square: "3c", role: "B" }, { square: "5c", role: "S" }],
  hand: {},
  inCheck: false,
  opponentInCheck: false,
  attemptBudget: 9,
  knownEnemies: [],
};

test("v16 stops retrying a blocked long ray after a foul", () => {
  assert.equal(BRAIN_VERSION, "tsuitate-brain-v16");
  const retry = chooseMove(state, {
    profile,
    seed: "fixture:30",
    forbiddenMoves: ["3c7g+"],
    foulMoves: ["3c7g+"],
  });
  assert.equal(retry.usi, "5c5d");
  assert.notEqual(retry.usi, "3c8h+");
  // The long ray remains a candidate; it is ordered below the safe move.
  const first = chooseMove(state, { profile, seed: "fixture:30" });
  assert.equal(first.usi, "3c7g+");
});
