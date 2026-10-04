import assert from "node:assert/strict";
import { test } from "node:test";
import { chooseObservedMove } from "../src/bot.js";
import {
  BRAIN_VERSION, LEGACY_PROFILE, LINEAR_PROFILE, chooseMove, featuresForMove,
  normalizeObservation, validateProfile,
} from "../src/brain/index.js";
import { attemptBudgetFromWebhook, checksFromLastMove, chooseWebhookDecision, csaToUsi, observationFromWebhook, usiToCsa } from "../src/adapters/webhook.js";

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
    inCheck: null, opponentInCheck: false, attemptBudget: null,
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

test("checked fallback spends its last attempt on a possible capture instead of unrelated advance", () => {
  for (const [color, king, gold, pawn, target, forwardTargets] of [
    ["b", "5e", "6e", "8h", "6f", ["5d", "6d"]],
    ["w", "5e", "4e", "2b", "4d", ["5f", "4f"]],
  ]) {
    const state = observation([[king, "K"], [gold, "G"], [pawn, "P"]],
      { color, turn: color, inCheck: true, attemptBudget: 1 });
    const escapes = ["4d", "4e", "4f", "5d", "5f", "6d", "6e", "6f"]
      .map((to) => king + to).filter((usi) => featuresForMove(state, usi));
    const forbiddenMoves = [...escapes, ...forwardTargets.map((to) => gold + to)];
    for (const selectedProfile of [LEGACY_PROFILE, LINEAR_PROFILE]) {
      for (const seed of ["response-a", "response-b", "response-c"]) {
        const choice = chooseMove(state, { profile: selectedProfile, seed, forbiddenMoves });
        assert.equal(choice.usi, gold + target);
        assert.equal(choice.candidateCount, 1);
        assert.deepEqual(chooseMove({ ...state, opponentBoard: "hidden-marker", legalMoves: [] },
          { profile: selectedProfile, seed, forbiddenMoves }), choice);
      }
    }
  }
});

test("check responses retain knight captures past an own piece for both colors", () => {
  for (const [color, knight, blocker, pawn, target] of [
    ["b", "3e", "4d", "9h", "4c"], ["w", "7e", "6f", "1b", "6g"],
  ]) {
    const state = observation([["5e", "K"], [knight, "N"], [blocker, "P"], [pawn, "P"]],
      { color, turn: color, inCheck: true, attemptBudget: 1 });
    const forbiddenMoves = ["4d", "4e", "4f", "5d", "5f", "6d", "6e", "6f"]
      .map((to) => "5e" + to).concat([blocker + target, blocker + target + "+"]);
    assert.equal(chooseMove(state, { profile: { ...LINEAR_PROFILE, exploration: 0 }, forbiddenMoves }).usi,
      knight + target + "+");
  }
});

test("last-attempt knight response captures instead of dropping beside its checking origin", () => {
  for (const [color, pieces, target, seed] of [
    ["b", [["5e", "K"], ["4d", "L"], ["4e", "S"], ["4f", "P"], ["5d", "P"],
      ["5f", "G"], ["6d", "G"], ["6e", "S"], ["6f", "L"]], "4c", "1"],
    ["w", [["5e", "K"], ["6f", "L"], ["6e", "S"], ["6d", "P"], ["5f", "P"],
      ["5d", "G"], ["4f", "G"], ["4e", "S"], ["4d", "L"]], "6g", "0"],
  ]) {
    const state = observation(pieces, { color, turn: color, hand: { G: 1 }, inCheck: true, attemptBudget: 1 });
    // No king escape is visible. A valid profile favouring drops must still
    // distinguish capture of a checking knight from an ineffective drop.
    const choice = chooseMove(state, { profile: profile({ drop: 1 }), seed });
    assert.notEqual(choice.usi[1], "*");
    assert.equal(choice.usi.slice(2, 4), target);
  }
});

test("check-response drops include blocks but exclude knight origins and rays behind own blockers", () => {
  for (const [color, blocker, blocked, block, knightOrigin, unrelated] of [
    ["b", "5d", "5c", "5f", "4c", "6g"], ["w", "5f", "5g", "5d", "6g", "4c"],
  ]) {
    const state = observation([["5e", "K"], [blocker, "P"]],
      { color, turn: color, hand: { P: 1, L: 1, N: 1, S: 1, G: 1, B: 1, R: 1 }, inCheck: true, attemptBudget: 1 });
    for (const selectedProfile of [LEGACY_PROFILE, LINEAR_PROFILE]) {
      const forbiddenMoves = ["4d", "4e", "4f", "5d", "5f", "6d", "6e", "6f"].map((to) => "5e" + to);
      const count = chooseMove(state, { profile: selectedProfile, forbiddenMoves }).candidateCount;
      const responses = [];
      for (let i = 0; i < count; i += 1) {
        const choice = chooseMove(state, { profile: selectedProfile, forbiddenMoves });
        responses.push(choice.usi); forbiddenMoves.push(choice.usi);
      }
      assert.ok(responses.includes("G*" + block));
      for (const role of "PLNSGBR") assert.ok(!responses.includes(role + "*" + knightOrigin));
      assert.ok(!responses.includes("G*" + blocked));
      assert.ok(!responses.includes("G*" + unrelated));
    }
  }
});

test("final public attempt budget ranks possible responses instead of probing a king", () => {
  const options = { sfen: "9/9/9/9/9/9/9/3P1G3/3LKL3 b - 1", color: "b",
    gameId: "final-attempt", ply: 0, inCheck: true, profile: { ...LINEAR_PROFILE, exploration: 0 } };
  assert.equal(chooseWebhookDecision({ ...options, attemptBudget: 1 }).move, "+4857KI");
  assert.equal(chooseWebhookDecision({ ...options, attemptBudget: 2 }).move, "+5958OU");
  assert.equal(chooseWebhookDecision(options).move, "+5958OU"); // Unknown budget is explicit null.
  assert.equal(chooseWebhookDecision({ ...options, attemptBudget: 0 }), null);
  assert.ok(chooseWebhookDecision({ ...options, profile: LEGACY_PROFILE, attemptBudget: 1 }));
  for (const invalid of [-1, 1.5, 1002, "1", false]) {
    assert.equal(chooseWebhookDecision({ ...options, attemptBudget: invalid }), null);
  }
  assert.equal(attemptBudgetFromWebhook({ fouls: { b: 0, w: 9 } }, "b"), 1);
  assert.equal(attemptBudgetFromWebhook({ fouls: { b: 0, w: 9 } }, "w"), 10);
  assert.equal(attemptBudgetFromWebhook({}, "b"), null);
  assert.equal(chooseWebhookDecision({ ...options,
    attemptBudget: attemptBudgetFromWebhook({ fouls: { b: null } }, "b") }), null);
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

test("a visibly valid rejected move excludes both optional promotion variants", () => {
  const mirror = (square) => `${10 - Number(square[0])}${String.fromCharCode(202 - square.charCodeAt(1))}`;
  for (const color of ["b", "w"]) {
    for (const [role, source, target] of [
      ["P", "5c", "5b"], ["L", "5c", "5b"], ["N", "5e", "4c"],
      ["S", "5c", "4b"], ["B", "5c", "3a"], ["R", "5c", "5b"],
    ]) {
      const from = color === "b" ? source : mirror(source);
      const to = color === "b" ? target : mirror(target);
      const path = from + to;
      for (const inCheck of [false, true, null]) {
        const state = observation([[from, role]], { color, turn: color, inCheck, attemptBudget: 1 });
        assert.ok(featuresForMove(state, path));
        assert.ok(featuresForMove(state, path + "+"));
        for (const selectedProfile of [profile({}, { exploration: 0 }), profile({}, { exploration: 1 }),
          ...(inCheck === true ? [LEGACY_PROFILE] : [])]) {
          const original = chooseMove(state, { profile: selectedProfile });
          for (const rejected of [path, path + "+"]) {
            const choice = chooseMove(state, { profile: selectedProfile, seed: "promotion-feedback",
              forbiddenMoves: [rejected] });
            assert.notEqual(choice?.usi.replace(/\+$/, ""), path);
            assert.equal(choice?.candidateCount ?? 0, original.candidateCount - 2);
          }
        }
      }
    }
  }
});

test("invalid promotion variants cannot exclude a visibly valid move", () => {
  for (const [pieces, valid, invalid] of [
    [[["5g", "P"]], "5g5f", "5g5f+"], // Promotion outside the zone.
    [[["5b", "P"]], "5b5a+", "5b5a"], // Mandatory promotion.
    [[["5c", "+P"]], "5c5b", "5c5b+"], // Already promoted.
    [[["5c", "G"]], "5c5b", "5c5b+"], // Unpromotable role.
  ]) {
    const state = observation(pieces);
    assert.ok(featuresForMove(state, valid));
    assert.equal(featuresForMove(state, invalid), null);
    for (const exploration of [0, 1]) {
      const options = { profile: profile({}, { exploration }), seed: "invalid-promotion" };
      assert.deepEqual(chooseMove(state, { ...options, forbiddenMoves: [invalid] }), chooseMove(state, options));
    }
  }
});

test("legacy repetition avoidance does not treat an accepted move as foul feedback", () => {
  const state = observation([["5c", "P"]], { inCheck: true, attemptBudget: 1 });
  const choice = chooseMove(state, { profile: LEGACY_PROFILE, recentMoves: ["5c5b"] });
  assert.equal(choice.usi, "5c5b+");
  assert.equal(choice.candidateCount, 1);
});

test("an ordinary rejected sliding move retries the adjacent square before another long path", () => {
  for (const [color, king, role, source, rejected, adjacent] of [
    ["b", "3i", "B", "8h", "8h1a+", "8h7g"], ["w", "7a", "B", "2b", "2b9i+", "2b3c"],
    ["b", "7i", "R", "3f", "3f3a+", "3f3e"], ["w", "3a", "R", "7d", "7d7i+", "7d7e"],
    ["b", "7i", "L", "2f", "2f2a+", "2f2e"], ["w", "3a", "L", "8d", "8d8i+", "8d8e"],
    ["b", "3i", "+B", "8h", "8h1a", "8h7g"], ["w", "7a", "+B", "2b", "2b9i", "2b3c"],
    ["b", "7i", "+R", "3f", "3f3a", "3f3e"], ["w", "3a", "+R", "7d", "7d7i", "7d7e"],
  ]) {
    const state = observation([[king, "K"], [source, role]],
      { color, turn: color, inCheck: false, attemptBudget: 1 });
    assert.ok(featuresForMove(state, rejected));
    for (const exploration of [0, 1]) {
      const choice = chooseMove(state, { profile: { ...LINEAR_PROFILE, exploration },
        seed: "short-ray", forbiddenMoves: [rejected] });
      assert.equal(choice.usi, adjacent);
      assert.equal(choice.candidateCount, 1);
      assert.deepEqual(chooseMove({ ...state, opponentBoard: "hidden-marker", legalMoves: [] },
        { profile: { ...LINEAR_PROFILE, exploration }, seed: "short-ray", forbiddenMoves: [rejected] }), choice);
    }
  }
});

test("short-ray retry requires known no-check, one own king and a source away from king rays", () => {
  const state = observation([["3i", "K"], ["8h", "B"]], { inCheck: false, attemptBudget: 2 });
  const selectedProfile = { ...LINEAR_PROFILE, exploration: 0 };
  for (const inCheck of [null, true]) {
    const choice = chooseMove({ ...state, inCheck }, { profile: selectedProfile, forbiddenMoves: ["8h1a+"] });
    assert.notEqual(choice.usi, "8h7g");
    assert.ok(choice.candidateCount > 1);
    if (inCheck === true) assert.equal(choice.features.kingMove, 1);
  }
  const possiblePin = observation([["3i", "K"], ["3f", "R"]], { inCheck: false });
  const pinnedChoice = chooseMove(possiblePin, { profile: selectedProfile, forbiddenMoves: ["3f9f"] });
  assert.notEqual(pinnedChoice.usi, "3f4f");
  assert.ok(pinnedChoice.candidateCount > 1);
  const missingKing = observation([["8h", "B"]], { inCheck: false });
  assert.ok(chooseMove(missingKing, { profile: selectedProfile, forbiddenMoves: ["8h1a+"] }).candidateCount > 1);
});

test("invalid promotion feedback and exhausted short retries do not invent a new probe", () => {
  const state = observation([["3i", "K"], ["8h", "B"]], { inCheck: false });
  const selectedProfile = { ...LINEAR_PROFILE, exploration: 0 };
  assert.equal(featuresForMove(state, "8h6f+"), null); // Outside the promotion zone.
  assert.deepEqual(chooseMove(state, { profile: selectedProfile, forbiddenMoves: ["8h6f+"] }),
    chooseMove(state, { profile: selectedProfile }));
  const exhausted = chooseMove(state, { profile: selectedProfile, forbiddenMoves: ["8h1a+", "8h7g"] });
  assert.ok(!["8h1a+", "8h7g"].includes(exhausted.usi));
  assert.ok(exhausted.candidateCount > 1);
  for (const [color, king, source, rejected, shorter] of [
    ["b", "3i", "8c", "8c3h+", "8c7d"], ["w", "7a", "2g", "2g7b+", "2g3f"],
  ]) {
    const promotedZone = observation([[king, "K"], [source, "B"]], { color, turn: color, inCheck: false });
    assert.ok(featuresForMove(promotedZone, shorter + "+"));
    const choice = chooseMove(promotedZone, { profile: selectedProfile, forbiddenMoves: [rejected, shorter] });
    assert.notEqual(choice.usi, shorter + "+");
    assert.ok(choice.candidateCount > 1);
  }
  assert.deepEqual(chooseMove(state, { profile: LEGACY_PROFILE, forbiddenMoves: ["8h1a+"] }),
    chooseMove(state, { profile: LEGACY_PROFILE }));
});
