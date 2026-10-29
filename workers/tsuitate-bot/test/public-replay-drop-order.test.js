import assert from "node:assert/strict";
import { test } from "node:test";
import { BRAIN_VERSION, LINEAR_PROFILE, chooseMove, inspectMoveCandidates } from "../src/brain/index.js";

function profile() {
  return { ...LINEAR_PROFILE, exploration: 0 };
}

function only(state, allowed) {
  const candidates = inspectMoveCandidates(state, { profile: profile() }).candidates.map((item) => item.usi);
  return candidates.filter((usi) => !allowed.has(usi));
}

function base(pieces, hand, patch = {}) {
  return {
    ruleset: "tsuitate-9x9",
    color: "b",
    turn: "b",
    moveNumber: 79,
    pieces: pieces.map(([square, role]) => ({ square, role })),
    hand,
    inCheck: false,
    opponentInCheck: false,
    attemptBudget: 8,
    knownEnemies: [],
    ...patch,
  };
}

test("v13 defers an occupancy-fragile drop behind a path-safe move to the same square", () => {
  const state = base([["5i", "K"], ["5e", "G"]], { G: 1 });
  const allowed = new Set(["G*5f", "5e5f"]);
  const forbiddenMoves = only(state, allowed);

  const first = chooseMove(state, { profile: profile(), forbiddenMoves, seed: "public-replay-29" });
  assert.equal(BRAIN_VERSION, "tsuitate-brain-v13");
  assert.equal(first.usi, "5e5f");

  // The drop is deferred, not forbidden. If the path-safe move itself is
  // rejected, the exact move is excluded and the drop returns to the pool.
  const retry = chooseMove(state, {
    profile: profile(),
    forbiddenMoves: [...forbiddenMoves, "5e5f"],
    foulMoves: ["5e5f"],
    seed: "public-replay-29",
  });
  assert.equal(retry.usi, "G*5f");
});

test("the same ordering applies to a check response without assuming the hidden checker", () => {
  // Public game hqw-_G83FKW4 showed G*4h foul first, then 5h4h accepted and
  // captured the checking gold. This fixture contains own-view geometry only;
  // no opponent piece or private playerView is supplied.
  const state = base([["5i", "K"], ["5h", "R"]], { G: 1 }, { inCheck: true });
  const allowed = new Set(["G*4h", "5h4h"]);
  const forbiddenMoves = only(state, allowed);

  const response = chooseMove(state, { profile: profile(), forbiddenMoves, seed: "public-replay-79" });
  assert.equal(response.usi, "5h4h");
});
