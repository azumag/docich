import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { BRAIN_VERSION, LINEAR_PROFILE, LEGACY_PROFILE, chooseMove,
  inspectMoveCandidates, featuresForMove } from "../src/brain/index.js";
import { RISK_BRAIN_VERSION, MAX_CHECK_HYPOTHESES, foulRiskMultiplier,
  checkingHypotheses, analyzeRiskCandidates, chooseRiskAwareMove } from "../src/brain/risk-aware.js";

function view(pieces = [["5i", "K"], ["7g", "P"]], patch = {}) {
  return { ruleset: "tsuitate-9x9", color: "b", turn: "b", moveNumber: 1,
    pieces: pieces.map(([square, role]) => ({ square, role })), hand: {},
    inCheck: false, attemptBudget: 5, ...patch };
}
function profile(weights) {
  return { ...LINEAR_PROFILE, exploration: 0,
    weights: Object.fromEntries(Object.keys(LINEAR_PROFILE.weights).map((key) => [key, weights[key] ?? 0])) };
}
const distance = (usi) => Math.max(Math.abs(Number(usi[0]) - Number(usi[2])), Math.abs(usi.charCodeAt(1) - usi.charCodeAt(3)));
const mirror = (s) => `${10 - Number(s[0])}${String.fromCharCode(202 - s.charCodeAt(1))}`;

for (const [budget, expected] of [[null, 4], [1, 8], [2, 4], [3, 2], [4, 1], [1001, 1]]) {
  test(`risk multiplier budget=${budget}`, () => assert.equal(foulRiskMultiplier(budget), expected));
}

test("invalid and exhausted budgets fail closed", () => {
  for (const budget of [0, -1, 0.5, 1002, NaN, Infinity, "2", undefined]) {
    assert.equal(foulRiskMultiplier(budget), null);
  }
  assert.equal(chooseRiskAwareMove(view(undefined, { attemptBudget: 0 })), null);
});

test("candidate policy remains opt-in; v11 and profile contract are unchanged", () => {
  assert.equal(BRAIN_VERSION, "tsuitate-brain-v11");
  assert.equal(chooseMove(view()).brainVersion, BRAIN_VERSION);
  assert.equal(chooseRiskAwareMove(view()).brainVersion, RISK_BRAIN_VERSION);
  assert.equal(chooseRiskAwareMove(view(), { profile: LEGACY_PROFILE }), null);
});

test("inspection uses shared movement/promotion/drop constraints for both colors", () => {
  for (const color of ["b", "w"]) {
    const state = view([["5e", "R"], ["5c", "P"], ["8f", "N"], ["5i", "K"]],
      { color, turn: color, hand: { P: 1, N: 1, S: 1 } });
    const result = inspectMoveCandidates(state);
    for (const item of result.candidates) {
      assert.deepEqual(item.features, featuresForMove(state, item.usi));
      assert.ok(Number.isFinite(item.score));
      assert.ok(Number.isInteger(item.unknownPathSquares));
      assert.ok(!item.usi.startsWith("P*5"));
    }
    assert.ok(!result.candidates.some((item) => item.usi === "5e5c"));
  }
});

test("unknown ACK excludes exact move, confirmed foul excludes promotion sibling", () => {
  const state = view([["5d", "P"]]);
  const moves = (options) => inspectMoveCandidates(state, options).candidates.map((c) => c.usi);
  assert.ok(moves({ forbiddenMoves: ["5d5c"] }).includes("5d5c+"));
  assert.deepEqual(moves({ foulMoves: ["5d5c"] }), []);
  const mandatory = view([["5b", "P"]]);
  assert.equal(chooseRiskAwareMove(mandatory, { foulMoves: ["5b5a"] }).usi, "5b5a+");
  assert.equal(chooseRiskAwareMove(mandatory, { forbiddenMoves: ["5b5a+"] }), null);
});

test("invalid observations/options and oversized exclusions fail closed", () => {
  for (const patch of [{ ruleset: "other" }, { turn: "w" }, { hand: { P: 99 } },
    { pieces: [{ square: "0z", role: "K" }] }, { inCheck: "yes" }, { attemptBudget: -1 }]) {
    assert.equal(chooseRiskAwareMove(view(undefined, patch)), null);
  }
  for (const options of [null, [], 5, { seed: "a".repeat(513) }, { forbiddenMoves: new Array(4097) },
    { foulMoves: {} }, { profile: { ...LINEAR_PROFILE, weights: {} } }]) {
    assert.equal(chooseRiskAwareMove(view(), options), null);
  }
});

test("opponent/secret fields cannot alter candidate decisions or leak into diagnostics", () => {
  const state = view();
  const polluted = structuredClone(state);
  polluted.pieces.push({ square: "5a", role: "R", owner: "w", token: "PRIVATE_MARKER" });
  polluted.opponentBoard = "PRIVATE_MARKER";
  polluted.legalMoves = ["PRIVATE_MARKER"];
  polluted.token = "PRIVATE_MARKER";
  polluted.hand.opponent = { R: 2 };
  assert.deepEqual(chooseRiskAwareMove(polluted), chooseRiskAwareMove(state));
  assert.equal(JSON.stringify(chooseRiskAwareMove(polluted)).includes("PRIVATE_MARKER"), false);
  assert.deepEqual(checkingHypotheses(polluted), checkingHypotheses(state));
});

test("low budget penalizes blind drops without prohibiting all drops", () => {
  const state = view(undefined, { hand: { S: 1 } });
  const options = { profile: profile({ drop: 2 }) };
  assert.equal(chooseRiskAwareMove({ ...state, attemptBudget: 5 }, options).features.drop, 1);
  assert.equal(chooseRiskAwareMove({ ...state, attemptBudget: 1 }, options).features.drop, 0);
  const forced = view([], { hand: { G: 1 }, attemptBudget: 1 });
  assert.equal(chooseRiskAwareMove(forced).features.drop, 1);
});

test("low budget shortens unknown rays instead of treating long paths as free", () => {
  const state = view([["5e", "R"]]);
  const options = { profile: profile({ distance: 8 }) };
  assert.ok(distance(chooseRiskAwareMove({ ...state, attemptBudget: 5 }, options).usi) > 1);
  assert.equal(distance(chooseRiskAwareMove({ ...state, attemptBudget: 1 }, options).usi), 1);
});

test("unknown budget is conservative and never presented as infinite", () => {
  const state = view(undefined, { attemptBudget: null });
  const decision = chooseRiskAwareMove(state);
  assert.equal(decision.diagnostics.multiplier, 4);
  assert.equal(decision.diagnostics.attemptBudget, null);
});

test("fresh capture evidence retains its tier and never creates a drop capture", () => {
  const state = view([["5i", "K"], ["4h", "G"]], { inCheck: true, hand: { G: 1 },
    knownEnemies: [{ square: "5h", age: 0 }] });
  const decision = chooseRiskAwareMove(state);
  assert.equal(decision.usi, "4h5h");
  assert.equal(decision.diagnostics.reason, "fresh-evidence");
  assert.ok(!analyzeRiskCandidates(state).ranked.some((c) => c.usi === "G*5h"));
});

test("hypotheses are absent unless a unique own king is checked", () => {
  for (const inCheck of [false, null]) assert.equal(checkingHypotheses(view(undefined, { inCheck })).hypotheses.length, 0);
  for (const pieces of [[], [["5e", "K"], ["5i", "K"]]]) {
    assert.equal(checkingHypotheses(view(pieces, { inCheck: true })).status, "incomplete-king-view");
  }
  assert.equal(checkingHypotheses(null), null);
});

test("knight-check origins use the opponent's direction in both colors", () => {
  for (const [color, yes, no] of [["b", "4c", "4g"], ["w", "4g", "4c"]]) {
    const hypotheses = checkingHypotheses(view([["5e", "K"]], { color, turn: color, inCheck: true })).hypotheses;
    assert.ok(hypotheses.some((h) => h.role === "N" && h.square === yes));
    assert.ok(!hypotheses.some((h) => h.role === "N" && h.square === no));
  }
});

test("fresh enemy occupancy blocks rays; stale evidence does not become certainty", () => {
  const state = view([["5i", "K"]], { inCheck: true, knownEnemies: [{ square: "5f", age: 0 }] });
  const hasRook = (s) => checkingHypotheses(s).hypotheses.some((h) => h.square === "5b" && h.role === "R");
  assert.equal(hasRook(state), false);
  assert.equal(hasRook({ ...state, knownEnemies: [{ square: "5f", age: 1 }] }), true);
});

test("hypothesis bound holds at all 81 king squares and is color/rotation symmetric", () => {
  for (let x = 1; x <= 9; x += 1) for (let y = 1; y <= 9; y += 1) {
    const king = `${x}${String.fromCharCode(96 + y)}`;
    const a = checkingHypotheses(view([[king, "K"]], { inCheck: true })).hypotheses;
    const b = checkingHypotheses(view([[mirror(king), "K"]], { inCheck: true, color: "w", turn: "w" })).hypotheses;
    assert.ok(a.length > 0 && a.length <= MAX_CHECK_HYPOTHESES);
    assert.deepEqual(a.map((h) => `${mirror(h.square)}:${h.role}`).sort(), b.map((h) => `${h.square}:${h.role}`).sort());
  }
});

// Independent step/ray reference. This checks the incomplete single-checker
// model, NOT full shogi legality, multiple checks, hidden defenders or drop mate.
function referenceCovered(state, move, checker) {
  const xy = (s) => [Number(s[0]), s.charCodeAt(1) - 96];
  const occupied = new Set(state.pieces.map((p) => p.square));
  for (const h of state.knownEnemies ?? []) if (h.age === 0) occupied.add(h.square);
  const from = move.usi.slice(0, 2); const to = move.usi.slice(2, 4);
  if (move.usi[1] === "*") { if (to === checker.square) return false; }
  else {
    const [fx, fy] = xy(from); const [tx, ty] = xy(to);
    const dx = tx - fx; const dy = ty - fy;
    if (dx === 0 || dy === 0 || Math.abs(dx) === Math.abs(dy)) {
      for (let i = 1; i < Math.max(Math.abs(dx), Math.abs(dy)); i += 1) {
        const intermediate = `${fx + Math.sign(dx) * i}${String.fromCharCode(96 + fy + Math.sign(dy) * i)}`;
        if (intermediate === checker.square) return false;
      }
    }
    if (to === checker.square) return true;
    occupied.delete(from);
  }
  occupied.add(to);
  const king = move.role === "K" ? to : state.pieces.find((p) => p.role === "K").square;
  const [cx, cy] = xy(checker.square); const [kx, ky] = xy(king);
  const forward = state.color === "b" ? 1 : -1;
  const steps = {
    P: [[0, 1]], N: [[-1, 2], [1, 2]],
    S: [[0, 1], [-1, 1], [1, 1], [-1, -1], [1, -1]],
    G: [[0, 1], [-1, 1], [1, 1], [-1, 0], [1, 0], [0, -1]],
    K: [[-1, -1], [-1, 0], [-1, 1], [0, -1], [0, 1], [1, -1], [1, 0], [1, 1]],
    "+B": [[-1, 0], [1, 0], [0, -1], [0, 1]], "+R": [[-1, -1], [-1, 1], [1, -1], [1, 1]],
  };
  if ((steps[checker.role] ?? []).some(([dx, dy]) => cx + dx === kx && cy + dy * forward === ky)) return false;
  const diagonal = [[-1, -1], [-1, 1], [1, -1], [1, 1]];
  const orthogonal = [[-1, 0], [1, 0], [0, -1], [0, 1]];
  const rays = checker.role === "L" ? [[0, forward]] : ["B", "+B"].includes(checker.role)
    ? diagonal : ["R", "+R"].includes(checker.role) ? orthogonal : [];
  for (const [dx, dy] of rays) for (let i = 1; i <= 8; i += 1) {
    const x = cx + i * dx; const y = cy + i * dy;
    if (x < 1 || x > 9 || y < 1 || y > 9) break;
    if (x === kx && y === ky) return false;
    if (occupied.has(`${x}${String.fromCharCode(96 + y)}`)) break;
  }
  return true;
}

test("coverage matches independent reference for vacated sources, captures, drops, knights and blocked moves", () => {
  for (const color of ["b", "w"]) {
    for (const pieces of [[["5e", "K"], ["7g", "R"], ["6f", "S"]],
      [["5i", "K"], ["5h", "G"], ["4h", "G"], ["8g", "N"]]]) {
      const state = view(pieces, { color, turn: color, inCheck: true, hand: { G: 1, P: 1, N: 1 } });
      const hypotheses = checkingHypotheses(state).hypotheses;
      for (const candidate of analyzeRiskCandidates(state).ranked) {
        assert.equal(candidate.risk.coveredHypotheses,
          hypotheses.filter((h) => referenceCovered(state, candidate, h)).length, candidate.usi);
        assert.ok(candidate.risk.coveredHypotheses <= candidate.risk.checkHypotheses);
      }
    }
  }
});

test("decisions are deterministic, finite, non-mutating and contain no fictitious legal probability", () => {
  const state = view([["5e", "K"], ["7g", "R"]], { inCheck: true, hand: { S: 1 } });
  const options = { seed: "repeat", forbiddenMoves: ["5e4e"] };
  const before = structuredClone({ state, options });
  const decision = chooseRiskAwareMove(state, options);
  assert.deepEqual(chooseRiskAwareMove(state, options), decision);
  assert.deepEqual({ state, options }, before);
  assert.ok(Number.isFinite(decision.score));
  assert.equal(decision.diagnostics.hypothesisStatus, "single-checker-only");
  assert.ok(!JSON.stringify(decision).includes("probability"));
});

test("baseline golden digest: 2400 decisions from unmodified v11 blob", () => {
  let randomState = 123456789;
  const rand = (n) => { randomState = (Math.imul(randomState, 1664525) + 1013904223) >>> 0; return randomState % n; };
  const roles = ["P", "L", "N", "S", "G", "B", "R", "+P", "+B", "+R"];
  const output = [];
  for (let i = 0; i < 200; i += 1) {
    const color = i % 2 ? "w" : "b";
    const squares = new Set(); const pieces = [];
    for (let j = 0; j < 1 + i % 12; j += 1) {
      let key;
      do { key = `${1 + rand(9)}${String.fromCharCode(97 + rand(9))}`; } while (squares.has(key));
      squares.add(key); pieces.push([key, j === 0 ? "K" : roles[rand(roles.length)]]);
    }
    const state = view(pieces, { color, turn: color, inCheck: [true, false, null][i % 3],
      attemptBudget: [null, 1, 2, 3, 5][i % 5], hand: { P: i % 2, S: i % 3 ? 1 : 0 }, moveNumber: i + 1 });
    for (const selectedProfile of [LEGACY_PROFILE, LINEAR_PROFILE]) for (const seed of ["a", "b", "c"]) {
      const first = chooseMove(state, { profile: selectedProfile, seed });
      output.push(first);
      output.push(chooseMove(state, { profile: selectedProfile, seed,
        forbiddenMoves: first ? [first.usi] : [], foulMoves: first && i % 2 ? [first.usi] : [] }));
    }
  }
  const digest = createHash("sha256").update(JSON.stringify(output)).digest("hex");
  assert.equal(output.length, 2400);
  assert.equal(digest, "ede3c962e613239a2d70a5d2ff6805143b374bfb5687499a65bb3e91b9bd7d1c");
});
