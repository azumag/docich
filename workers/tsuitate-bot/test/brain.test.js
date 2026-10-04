import assert from "node:assert/strict";
import { test } from "node:test";
import { chooseObservedMove } from "../src/bot.js";
import {
  BRAIN_VERSION, LEGACY_PROFILE, LINEAR_PROFILE, chooseMove, featuresForMove,
  normalizeObservation, validateProfile,
} from "../src/brain/index.js";
import { checksFromLastMove, chooseWebhookDecision, csaToUsi, observationFromWebhook, usiToCsa } from "../src/adapters/webhook.js";

function observation(pieces, options = {}) {
  return normalizeObservation({
    ruleset: "tsuitate-9x9", color: "b", turn: "b", moveNumber: 1,
    pieces: pieces.map(([square, role]) => ({ square, role })), hand: {}, ...options,
  });
}

function profile(weights = {}, overrides = {}) {
  return {
    ...LINEAR_PROFILE, exploration: 0,
    weights: Object.fromEntries(Object.keys(LINEAR_PROFILE.weights).map((key) => [key, weights[key] ?? 0])),
    ...overrides,
  };
}

test("normalization copies only the canonical own-view fields", () => {
  const raw = {
    ruleset: "tsuitate-9x9", color: "b", turn: "b", moveNumber: 3,
    pieces: [
      { square: "5i", role: "K", owner: "b", secret: "piece-marker" },
      { square: "7g", role: "P" },
      { square: "5a", role: "K", owner: "w" },
      { square: "2b", role: "R", color: "w" },
      { square: "3c", role: "P", owner: "unknown" },
    ],
    hand: { P: 1, opponent: { R: 1 } },
    inCheck: null, opponentInCheck: false,
    opponentBoard: "hidden-marker", legalMoves: ["hidden-legality-marker"], token: "secret-marker",
  };
  const normalized = normalizeObservation(raw);
  assert.deepEqual(normalized, {
    ruleset: "tsuitate-9x9", color: "b", turn: "b", moveNumber: 3,
    pieces: [{ square: "7g", role: "P" }, { square: "5i", role: "K" }],
    hand: { P: 1, L: 0, N: 0, S: 0, G: 0, B: 0, R: 0 },
    inCheck: null, opponentInCheck: false,
  });
  raw.pieces[1].role = "R";
  raw.hand.P = 18;
  assert.equal(normalized.pieces[0].role, "P");
  assert.equal(normalized.hand.P, 1);
  assert.equal(JSON.stringify(chooseMove(raw)).includes("marker"), false);
});

test("invalid observation and profile values fail closed", () => {
  const valid = observation([["5g", "P"]]);
  for (const patch of [
    { ruleset: "tsuitate-5x5" }, { color: "black" }, { moveNumber: 0 }, { moveNumber: Infinity },
    { pieces: [{ square: "5g", role: "P" }, { square: "5g", role: "R" }] },
    { pieces: [{ square: "5z", role: "P" }] }, { pieces: [{ square: "5g", role: "+K" }] },
    { hand: { P: 19 } }, { hand: { P: 0.5 } }, { inCheck: "unknown" },
  ]) assert.equal(normalizeObservation({ ...valid, ...patch }), null);
  assert.equal(chooseMove({ ...valid, turn: "w" }), null);
  for (const patch of [
    { schemaVersion: 2 }, { id: "unsafe\nvalue" }, { id: "" }, { policy: "remote-code" },
    { weights: { ...LINEAR_PROFILE.weights, advance: NaN } },
    { weights: { ...LINEAR_PROFILE.weights, advance: 10.01 } },
    { weights: {} }, { exploration: -0.1 }, { exploration: Infinity },
  ]) {
    const invalid = { ...LINEAR_PROFILE, ...patch };
    assert.equal(validateProfile(invalid), null);
    assert.equal(chooseMove(valid, { profile: invalid }), null);
  }
  assert.deepEqual(validateProfile({ ...LINEAR_PROFILE, token: "excluded", weights: { ...LINEAR_PROFILE.weights, hidden: 99 } }), LINEAR_PROFILE);
});

test("webhook adapter strips opposing pieces and opposing hand from mixed SFEN", () => {
  const sfen = "4k4/9/9/9/4+p4/9/4P4/9/4K4 b 2P3pRr 7";
  const black = observationFromWebhook({ sfen, color: "b" });
  assert.deepEqual(black.pieces, [{ square: "5g", role: "P" }, { square: "5i", role: "K" }]);
  assert.deepEqual(black.hand, { P: 2, L: 0, N: 0, S: 0, G: 0, B: 0, R: 1 });
  const white = observationFromWebhook({ sfen: sfen.replace(" b ", " w "), color: "w" });
  assert.deepEqual(white.pieces, [{ square: "5a", role: "K" }, { square: "5e", role: "+P" }]);
  assert.equal(white.hand.P, 3);
  assert.equal(black.inCheck, null);
});

test("CSA and USI round-trip both colors, drops and promotion without leaking site notation into brain", () => {
  for (const color of ["b", "w"]) {
    const state = observation([["5b", "P"], ["6d", "+P"]], { color, turn: color, hand: { S: 1 } });
    const sign = color === "b" ? "+" : "-";
    for (const [usi, csa] of [["5b5a+", `${sign}5251TO`], ["6d6c", `${sign}6463TO`], ["S*7f", `${sign}0076GI`]]) {
      assert.equal(usiToCsa(usi, state), csa);
      assert.equal(csaToUsi(csa, state), usi);
    }
    assert.equal(csaToUsi(`${sign}5251HI`, state), null);
    assert.equal(usiToCsa("6d6c+", state), null);
    assert.equal(usiToCsa("9a9b", state), null);
  }
});

test("legacy fixtures preserve the original order and seed modulo behavior", () => {
  const vectors = [
    ["9/9/9/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1", "b", ["+8786FU", "+2726FU", "+2818HI", "+2838HI", "+9796FU"]],
    ["lnsgkgsnl/1r5b1/ppppppppp/9/9/9/9/9/9 w - 2", "w", ["-8272HI", "-8292HI", "-8384FU", "-2324FU", "-9192KY"]],
    ["9/4P4/9/2+B3+R2/4+S4/9/9/9/4K4 b P 17", "b", ["+3433RY", "+3435RY", "+3424RY", "+3444RY", "+3423RY"]],
    ["4k4/9/9/9/4+s4/2+b3+r2/9/4p4/9 w p 18", "w", ["-3635RY", "-3637RY", "-3626RY", "-3646RY", "-3625RY"]],
  ];
  for (const [sfen, color, moves] of vectors) {
    moves.forEach((move, ply) => assert.equal(chooseObservedMove({ sfen, color, gameId: "compat", ply }), move));
  }
  const onlyPawn = { sfen: "9/9/9/9/9/9/4P4/9/4K4 b - 1", color: "b", gameId: "compat", ply: 1 };
  assert.equal(chooseObservedMove({ ...onlyPawn, recentOwnMoves: ["+5756FU"] }), null);
  assert.equal(chooseObservedMove({ ...onlyPawn, recentOwnMoves: ["+5756FU", "unmatched"] }), "+5756FU");
  assert.equal(chooseObservedMove({ ...onlyPawn, forbiddenMoves: ["+5756FU"] }), null);
});

test("pawn direction, optional promotion and mandatory promotion work for both colors", () => {
  for (const [color, source, target, finalSource, finalTarget] of [
    ["b", "5d", "5c", "5b", "5a"], ["w", "5f", "5g", "5h", "5i"],
  ]) {
    const state = observation([[source, "P"]], { color, turn: color });
    assert.equal(featuresForMove(state, source + target)?.promotion, 0);
    assert.equal(featuresForMove(state, source + target + "+")?.promotion, 1);
    assert.equal(featuresForMove(state, target + source), null);
    const mandatory = observation([[finalSource, "P"]], { color, turn: color });
    assert.equal(featuresForMove(mandatory, finalSource + finalTarget), null);
    assert.equal(chooseMove(mandatory).usi, finalSource + finalTarget + "+");
  }
  const leavingZone = observation([["5c", "S"]]);
  assert.ok(featuresForMove(leavingZone, "5c4d+"));
  assert.equal(featuresForMove(observation([["5c", "G"]]), "5c5b+"), null);
  assert.equal(featuresForMove(observation([["5c", "+P"]]), "5c5b+"), null);
  assert.equal(featuresForMove(observation([["5c", "N"]]), "5c4a"), null);
  assert.ok(featuresForMove(observation([["5c", "N"]]), "5c4a+"));
});

test("linear includes king and long-range moves while blocking own-piece paths", () => {
  const king = observation([["5e", "K"]]);
  assert.equal(chooseMove(king, { profile: LEGACY_PROFILE }), null);
  assert.equal(chooseMove(king).candidateCount, 8);
  assert.equal(featuresForMove(king, "5e5d").kingMove, 1);
  const rook = observation([["5e", "R"], ["5c", "P"]]);
  assert.ok(featuresForMove(rook, "5e5d"));
  assert.equal(featuresForMove(rook, "5e5c"), null);
  assert.equal(featuresForMove(rook, "5e5b+"), null);
  assert.ok(featuresForMove(rook, "5e9e"));
  assert.ok(featuresForMove(observation([["5e", "+B"]]), "5e1a"));
  assert.ok(featuresForMove(observation([["5e", "+B"]]), "5e5d"));
  assert.equal(featuresForMove(observation([["5e", "+B"]]), "5e5c"), null);
  assert.ok(featuresForMove(observation([["5e", "+R"]]), "5e4d"));
  assert.equal(featuresForMove(observation([["5e", "+R"]]), "5e3c"), null);
  assert.ok(featuresForMove(observation([["5e", "N"], ["5d", "P"]]), "5e4c"));
});

test("public check probes king escapes before other moves in both policies and colors", () => {
  for (const color of ["b", "w"]) for (const selectedProfile of [LEGACY_PROFILE, LINEAR_PROFILE]) {
    const state = observation([["5e", "K"], ["8h", "P"]], { color, turn: color, inCheck: true });
    const forbiddenMoves = [];
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const choice = chooseMove(state, { profile: selectedProfile, seed: String(attempt), forbiddenMoves });
      assert.equal(choice.features.kingMove, 1);
      assert.ok(!forbiddenMoves.includes(choice.usi));
      forbiddenMoves.push(choice.usi);
    }
    // After every escape is rejected, blocks/captures remain available.
    const fallback = chooseMove(state, { profile: selectedProfile, forbiddenMoves });
    assert.equal(fallback.features.kingMove, 0);
    assert.ok(!forbiddenMoves.includes(fallback.usi));
    assert.deepEqual(chooseMove({ ...state, opponentBoard: "hidden-marker", legalMoves: [] },
      { profile: selectedProfile, forbiddenMoves }), fallback);
  }
});

test("viewer public lastInfo identifies check without disclosing the attacking square", () => {
  for (const [color, own, opponent] of [["b", "+", "-"], ["w", "-", "+"]]) {
    assert.deepEqual(checksFromLastMove({ lastMove: `${opponent}0000ZZ`, lastInfo: 3 }, color),
      { inCheck: true, opponentInCheck: false });
    assert.deepEqual(checksFromLastMove({ lastMove: `${own}0000ZZ`, lastInfo: 2 }, color),
      { inCheck: true, opponentInCheck: null });
    assert.deepEqual(checksFromLastMove({ lastMove: `${own}0000ZZ`, lastInfo: 1 }, color),
      { inCheck: false, opponentInCheck: null });
    assert.deepEqual(checksFromLastMove({ lastMove: `${own}0000ZZ`, lastInfo: 3 }, color),
      { inCheck: false, opponentInCheck: true });
  }
  assert.deepEqual(checksFromLastMove({}, "b"), { inCheck: null, opponentInCheck: null });
  const options = { sfen: "9/9/9/9/4K4/9/4P4/9/9 b - 1", color: "b", gameId: "check-fixture", ply: 1,
    ...checksFromLastMove({ lastMove: "-0000ZZ", lastInfo: 3 }, "b") };
  assert.equal(chooseWebhookDecision(options).decision.features.kingMove, 1);
});

test("drops exclude nifu, occupied squares and dead-end ranks for both colors", () => {
  for (const [color, finalRank, nextRank] of [["b", "a", "b"], ["w", "i", "h"]]) {
    const state = observation([["5e", "P"], ["4e", "+P"]], { color, turn: color, hand: { P: 1, L: 1, N: 1, G: 1 } });
    assert.equal(featuresForMove(state, "P*5f"), null);
    assert.ok(featuresForMove(state, "P*4f"));
    assert.equal(featuresForMove(state, "G*5e"), null);
    assert.equal(featuresForMove(state, `P*3${finalRank}`), null);
    assert.equal(featuresForMove(state, `L*3${finalRank}`), null);
    assert.equal(featuresForMove(state, `N*3${finalRank}`), null);
    assert.equal(featuresForMove(state, `N*3${nextRank}`), null);
    assert.ok(featuresForMove(state, `G*3${finalRank}`));
    assert.equal(featuresForMove(state, "R*3e"), null);
  }
  const handOnly = observation([], { hand: { R: 1 } });
  assert.match(chooseMove(handOnly).usi, /^R\*/);
  assert.equal(chooseMove(handOnly, { profile: LEGACY_PROFILE }), null);
});

test("profiles change the same observation's choice without an interface change", () => {
  const state = observation([["5g", "P"], ["9g", "P"]]);
  const center = chooseMove(state, { profile: profile({ centrality: 1 }, { id: "center-v1" }) });
  const edge = chooseMove(state, { profile: profile({ centrality: -1 }, { id: "edge-v1" }) });
  assert.equal(center.usi, "5g5f");
  assert.equal(edge.usi, "9g9f");
  assert.equal(center.profileId, "center-v1");
  assert.equal(center.brainVersion, BRAIN_VERSION);
  assert.deepEqual(center.features, featuresForMove(state, center.usi));
  assert.equal(chooseMove(state, { profile: profile({ centrality: 1, repeat: -2 }), recentMoves: ["5g5f"] }).usi, "9g9f");
  assert.equal(featuresForMove(state, "5g5f", ["5g5f"]).repeat, 1);
});

test("known rejected moves stay excluded even under full exploration", () => {
  const state = observation([["5g", "P"], ["9g", "P"]]);
  const options = { profile: profile({}, { exploration: 1 }), seed: "repeatable", forbiddenMoves: ["5g5f"] };
  const chosen = chooseMove(state, options);
  assert.equal(chosen.usi, "9g9f");
  assert.equal(chosen.candidateCount, 1);
  assert.deepEqual(chooseMove(state, options), chosen);
  assert.equal(chooseMove(state, { ...options, forbiddenMoves: ["5g5f", "9g9f"] }), null);
});
