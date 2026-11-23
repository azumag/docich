import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  STANDARD_START,
  archiveGame,
  applyMove,
  attacks,
  blockerBetween,
  buildTurns,
  evaluateReplay,
  judgeMove,
  normalizeReplay,
  parseSfen,
  replay,
  toSfen,
} from "../scripts/replay-lab.mjs";

const index = (square) => "abcdefghi".indexOf(square[1]) * 9 + (9 - Number(square[0]));

function positionFrom(moves) {
  let position = parseSfen(STANDARD_START);
  for (const [usi, by] of moves) {
    const verdict = judgeMove(position, by, usi);
    assert.equal(verdict.ok, true, `${usi}: ${verdict.reason}`);
    position = applyMove(position, usi, by);
  }
  return position;
}

// Final SFEN generated independently with docich's shogi_board applier.
const FINAL_A = "lnsgkgsnl/7r1/pppppp1pp/6p2/9/2P6/PP1PPPPPP/7R1/LNSGKGSNL b Bb 5";
const MOVES_A = [["7g7f", "sente"], ["3c3d", "gote"], ["8h2b+", "sente"], ["8b2b", "gote"]];
const FINAL_B =
  "lnsgk1snl/1r4gb1/pp+Pppp1pp/9/9/3P3P1/PP2PP+p1P/1B5R1/LNSGKGSNL w Pp 12";
const MOVES_B = [
  ["7g7f", "sente"], ["3c3d", "gote"], ["7f7e", "sente"], ["3d3e", "gote"],
  ["7e7d", "sente"], ["3e3f", "gote"], ["7d7c+", "sente"], ["3f3g+", "gote"],
  ["6g6f", "sente"], ["4a3b", "gote"], ["2g2f", "sente"],
];

test("SFEN round-trips the standard start", () => {
  const position = parseSfen(STANDARD_START);
  assert.match(toSfen(position), /^lnsgkgsnl\/1r5b1\/ppppppppp\/9\/9\/9\/PPPPPPPPP\/1B5R1\/LNSGKGSNL b -$/);
});

test("attack geometry follows the mover's forward direction and blockers", () => {
  const position = parseSfen(STANDARD_START);
  assert.equal(attacks(position.squares, index("5g"), index("5f")), true, "sente pawn forward");
  assert.equal(attacks(position.squares, index("5c"), index("5d")), true, "gote pawn forward");
  assert.equal(attacks(position.squares, index("5c"), index("5b")), false, "gote pawn does not move backward");
  assert.equal(attacks(position.squares, index("9i"), index("9h")), true, "lance step");
  assert.equal(attacks(position.squares, index("9i"), index("9e")), false, "own pawn blocks the lance");
  assert.equal(attacks(position.squares, index("2h"), index("2b")), false, "own pawn blocks the rook");
  assert.equal(blockerBetween(position.squares, index("2h"), index("2b")).square, "2g");
});

test("judgeMove separates blocked rays, own pieces, drops and king safety", () => {
  const start = parseSfen(STANDARD_START);
  assert.equal(judgeMove(start, "sente", "7g7f").ok, true);
  assert.equal(judgeMove(start, "sente", "5g5e").ok, false, "two-square pawn push");
  assert.equal(judgeMove(start, "sente", "5g5f").ok, true);

  // A rook ray blocked by an own pawn is a blocked path, not bad geometry.
  const blocked = judgeMove(start, "sente", "2h2b");
  assert.equal(blocked.ok, false);
  assert.equal(blocked.reason, "blocked");
  assert.equal(blocked.blocker, "2g");


  assert.equal(judgeMove(start, "sente", "9i9h").ok, true);
  assert.equal(judgeMove(start, "sente", "5i5h").ok, true, "king step");
  assert.equal(judgeMove(start, "sente", "5i5j").reason, "invalid_usi");
  assert.equal(judgeMove(start, "gote", "5g5f").reason, "no_own_piece");
  assert.equal(judgeMove(start, "sente", "5g5g").reason, "invalid_usi");
  const opened = positionFrom([["7g7f", "sente"], ["3c3d", "gote"]]);
  assert.equal(attacks(opened.squares, index("8h"), index("2b")), true, "bishop ray once the pawn moves");

  // Drops: hand availability, occupancy, nifu and dead ends.
  const hand = parseSfen("4k4/9/9/9/9/9/4P4/9/4K4 b P 1");
  assert.equal(judgeMove(hand, "sente", "P*4e").ok, true);
  assert.equal(judgeMove(hand, "sente", "P*5e").reason, "nifu");
  assert.equal(judgeMove(hand, "sente", "P*5g").reason, "occupied_drop");
  assert.equal(judgeMove(hand, "sente", "P*4a").reason, "dead_end_drop");
  assert.equal(judgeMove(hand, "sente", "G*5e").reason, "no_hand_piece");
  assert.equal(judgeMove(hand, "sente", "P*5i").reason, "occupied_drop");

  // Leaving the king on an attacked file is an into_check foul; so is a drop
  // that does not answer the check.
  const pinned = parseSfen("k3r4/9/9/9/9/9/9/9/4K4 b G 1");
  assert.equal(judgeMove(pinned, "sente", "5i5h").reason, "into_check");
  assert.equal(judgeMove(pinned, "sente", "5i4i").ok, true, "leaving the rook's file escapes");
  assert.equal(judgeMove(pinned, "sente", "G*5h").ok, true, "blocking drop answers the check");
});

test("replay rebuilds every position and refuses a wrong final SFEN", () => {
  const positions = replay(MOVES_A.map(([usi, by]) => ({ usi, by })), FINAL_A);
  assert.equal(positions.length, MOVES_A.length + 1);
  assert.equal(toSfen(positions.at(-1)).split(" ").slice(0, 3).join(" "),
    FINAL_A.split(" ").slice(0, 3).join(" "));
  assert.throws(() => replay(MOVES_A.map(([usi, by]) => ({ usi, by })), FINAL_B), /final_sfen_mismatch/);
});

test("buildTurns discovers a captured own piece as known-enemy evidence", () => {
  const moves = MOVES_B.map(([usi, by]) => ({ usi, by }));
  const positions = replay(moves, FINAL_B);
  const turns = buildTurns(positions, moves, [], "sente");
  assert.deepEqual(turns.map((turn) => turn.moveNumber), [1, 3, 5, 7, 9, 11]);
  // 3g is still ours at move 5 and is captured by 3f3g+ on move 8.
  assert.deepEqual(turns.find((turn) => turn.moveNumber === 5).knownEnemies, []);
  const ninth = turns.find((turn) => turn.moveNumber === 9);
  assert.deepEqual(ninth.knownEnemies, [{ square: "3g", age: 0 }]);
  assert.equal(ninth.priorFouls, 0);
  // Evidence ages one step per two plies (one of our turns).
  assert.deepEqual(turns.find((turn) => turn.moveNumber === 11).knownEnemies, [{ square: "3g", age: 1 }]);
});

test("buildTurns counts prior fouls and carries a rejected drop square", () => {
  const moves = MOVES_B.map(([usi, by]) => ({ usi, by }));
  const positions = replay(moves, FINAL_B);
  const foulAttempts = [
    { moveNumber: 1, byColor: "sente", usi: "P*5e" },
    { moveNumber: 3, byColor: "gote", usi: "P*9e" },
  ];
  const turns = buildTurns(positions, moves, foulAttempts, "sente");
  assert.equal(turns.find((turn) => turn.moveNumber === 3).priorFouls, 1);
  assert.deepEqual(turns.find((turn) => turn.moveNumber === 7).knownEnemies
    .find((item) => item.square === "5e"), { square: "5e", age: 3 });
});

test("evaluateReplay excludes a foul and retries within the same turn", () => {
  const payload = {
    gameId: "fixture",
    color: "sente",
    moves: MOVES_A.map(([usi, by]) => ({ usi, by })),
    foulAttempts: [],
    finalSfen: FINAL_A,
  };
  const replayData = normalizeReplay(payload);
  const positions = replay(replayData.moves, replayData.finalSfen);
  const brain = {
    BRAIN_VERSION: "stub",
    chooseMove(_observation, options) {
      // The rook ray is blocked by our own pawn at 2g: a certain foul. Once it
      // is excluded, fall back to a legal pawn push.
      return { usi: options.forbiddenMoves.includes("2h2b") ? "5g5f" : "2h2b" };
    },
  };
  const report = evaluateReplay({ ...replayData, positions, color: "sente", brain,
    profile: {}, gameId: "fixture" });
  assert.equal(report.turns, 2);
  assert.ok(report.fouls >= 1, JSON.stringify(report.byReason));
  assert.equal(report.details[0].attempts, 2, "foul then accepted");
});

function publicPayload(moves, finalSfen, gameId = "fixture-game", senteName = "DoCiAI", goteName = "opponent") {
  // Minimal devalue table: every object/value is a table index.
  const table = [{}];
  const push = (value) => { table.push(value); return table.length - 1; };
  const moveRefs = moves.map(([usi, by]) => push({ usi: push(usi), byColor: push(by), ms: push(250), foulsBefore: push(0) }));
  const game = push({ id: push(gameId), result: push("sente_win"), reason: push("checkmate"),
    moves: push(moveRefs), foulAttempts: push([]), finalSfen: push(finalSfen) });
  table[0].game = game;
  table[0].senteName = push(senteName);
  table[0].goteName = push(goteName);
  return { type: "data", nodes: [{ type: "data", data: table }] };
}

test("archiveGame backfills a historical run from the public replay alone", async () => {
  const directory = await mkdtemp(join(tmpdir(), "replay-lab-backfill-"));
  const first = await archiveGame({
    publicPayload: publicPayload(MOVES_A, FINAL_A, "backfill-game"),
    outDir: directory,
    brainVersion: "tsuitate-brain-v12",
  });
  const saved = JSON.parse(await readFile(first.path, "utf8"));
  assert.equal(saved.color, "sente", "the bot side is inferred from the public names");
  assert.equal(saved.brainVersion, "tsuitate-brain-v12");
  assert.equal(saved.result, null, "a historical run has no local terminal state");
  assert.equal(saved.moves.length, MOVES_A.length);
  // An unknown bot name must not be guessed.
  await assert.rejects(() => archiveGame({
    publicPayload: publicPayload(MOVES_A, FINAL_A, "unknown-game", "someone", "another"),
    outDir: directory,
  }), /color_unknown/);
});

test("archiveGame writes one verified record and refuses a conflicting game", async () => {
  const directory = await mkdtemp(join(tmpdir(), "replay-lab-"));
  const state = { beta_game_id: "fixture-game", beta_brainVersion: "tsuitate-brain-v13",
    beta_color: "sente", beta_result: { outcome: "win", reason: "checkmate" }, started_at: 1 };
  const review = { moves: MOVES_A.map(([usi, by], index) => ({ n: index + 1, usi, side: by })) };
  const payload = publicPayload(MOVES_A, FINAL_A);
  const first = await archiveGame({ state, review, publicPayload: payload, outDir: directory });
  assert.equal(first.written, true);
  const saved = JSON.parse(await readFile(first.path, "utf8"));
  assert.equal(saved.foulAttempts.length, 0);
  assert.equal(saved.moves.length, MOVES_A.length);
  // Same game id is idempotent, not an overwrite.
  const second = await archiveGame({ state, review, publicPayload: payload, outDir: directory });
  assert.equal(second.written, false);
  // A drifted move list must fail before anything is written.
  const drifted = { moves: review.moves.slice(1) };
  await assert.rejects(() => archiveGame({ state, review: drifted, publicPayload: payload, outDir: directory }),
    /review_moves_mismatch/);
  await assert.rejects(() => archiveGame({ state: { ...state, beta_game_id: "other" }, review,
    publicPayload: payload, outDir: directory }), /game_id_mismatch/);
});
