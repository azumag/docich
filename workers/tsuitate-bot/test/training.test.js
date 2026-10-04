import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { LEGACY_PROFILE, LINEAR_PROFILE, validateProfile } from "../src/brain/index.js";
import { normalizeGameRecord, parseDataset, profileHash, report, splitDataset, trainCandidate } from "../src/training/index.js";

const BASE = { ...LINEAR_PROFILE, id: "training-fixture", weights: { ...LINEAR_PROFILE.weights } };

function observation({ file = 5, color = "b", moveNumber = 1 } = {}) {
  return {
    ruleset: "tsuitate-9x9", color, turn: color, moveNumber,
    pieces: [{ square: `${file}${color === "b" ? "g" : "c"}`, role: "P" }, { square: `5${color === "b" ? "i" : "a"}`, role: "K" }],
    hand: { P: 0, L: 0, N: 0, S: 0, G: 0, B: 0, R: 0 }, inCheck: false, opponentInCheck: null,
  };
}

function game(id, { file = 5, color = "b", feedback = "accepted", ...overrides } = {}) {
  return {
    schemaVersion: 1, kind: "tsuitate_game", site: "beta.tsuitate.info", ruleset: "tsuitate-9x9",
    rulesKey: "beta-300+3-f10", gameId: id, color,
    startedAt: "2026-10-03T00:00:00Z", endedAt: "2026-10-03T00:10:00Z", brainVersion: "brain-v1",
    profile: BASE,
    decisions: [{ moveNumber: 1, observation: observation({ file, color }), usi: `${file}${color === "b" ? "g" : "c"}${file}${color === "b" ? "f" : "d"}`, score: 0, features: { centrality: 9999 }, feedback }],
    historyComplete: true, completed: true, outcome: "win", reason: "checkmate", ...overrides,
  };
}

function partitionGames(partition, count, { prefix = "fixture", ...options } = {}) {
  const games = [];
  for (let index = 0; games.length < count && index < 10000; index += 1) {
    const record = game(`${prefix}-${index}`, options);
    if (splitDataset([record])[partition].length) games.push(record);
  }
  assert.equal(games.length, count);
  return games;
}

function outcomeTraining(count = 12) {
  return partitionGames("training", count).map((record, index) =>
    game(record.gameId, index % 2 ? { file: 1, outcome: "loss" } : { file: 5, outcome: "win" }));
}

test("game records reconstruct safe observations and recompute features and profile identity", () => {
  const raw = game("sanitized");
  raw.token = "SENSITIVE_FIXTURE";
  raw.kifu = { enemyBoard: "SENSITIVE_FIXTURE" };
  raw.profileHash = "SENSITIVE_FIXTURE";
  raw.decisions[0].features = { token: "SENSITIVE_FIXTURE", centrality: -1 };
  raw.decisions[0].observation.rawEvent = { auth: "SENSITIVE_FIXTURE" };
  raw.decisions[0].rawResponse = "SENSITIVE_FIXTURE";
  const normalized = normalizeGameRecord(raw);
  assert.ok(normalized);
  assert.equal(normalized.decisions[0].features.centrality, 1);
  assert.equal(normalized.profileHash, profileHash(BASE));
  assert.equal(JSON.stringify(normalized).includes("SENSITIVE_FIXTURE"), false);
  assert.deepEqual(normalizeGameRecord(normalized), normalized);
});

test("malformed records fail closed without leaking raw values", () => {
  for (const mutation of [
    { outcome: "victory" }, { color: "enemy" }, { reason: "raw SENSITIVE_FIXTURE error" },
    { startedAt: "2026-02-31T00:00:00Z" }, { endedAt: "2026-10-02T00:00:00Z" },
    { completed: false, outcome: "loss" }, { site: "https://beta.tsuitate.info" },
    { historyComplete: "true" }, { profile: { ...BASE, weights: { ...BASE.weights, advance: Infinity } } },
  ]) assert.equal(normalizeGameRecord(game("invalid", mutation)), null);
  const record = game("bad-move");
  record.decisions[0].usi = "SENSITIVE_FIXTURE";
  assert.equal(normalizeGameRecord(record), null);
  assert.throws(() => parseDataset("SENSITIVE_FIXTURE"), { message: "invalid_dataset_json" });
});

test("actual beta game IDs may start with a hyphen or underscore", () => {
  for (const id of ["-YgIx2UjAvkD", "_game-fixture"]) {
    const normalized = normalizeGameRecord(game(id));
    assert.ok(normalized);
    assert.equal(normalized.gameId, id);
    assert.equal(parseDataset(JSON.stringify(normalized)).records[0].gameId, id);
  }
});

test("JSONL drops duplicate games and rejects contradictory outcomes or profiles", () => {
  const record = game("duplicate");
  const parsed = parseDataset(`${JSON.stringify(record)}\n\n${JSON.stringify(record)}\n`);
  assert.equal(parsed.records.length, 1);
  assert.equal(parsed.duplicatesDropped, 1);
  for (const conflicting of [
    { ...record, outcome: "loss" },
    { ...record, profile: { ...BASE, weights: { ...BASE.weights, centrality: BASE.weights.centrality + 0.01 } } },
    { ...record, historyComplete: false },
  ]) assert.throws(() => parseDataset([record, conflicting].map(JSON.stringify).join("\n")), { message: "conflicting_game_record" });
});

test("profile hashes distinguish changed behavior behind the same id and combine renames", () => {
  const altered = { ...BASE, weights: { ...BASE.weights, centrality: BASE.weights.centrality + 0.01 } };
  assert.notEqual(profileHash(BASE), profileHash(altered));
  assert.equal(profileHash(BASE), profileHash({ ...BASE, id: "renamed-only" }));
  const summary = report([game("a"), game("b", { profile: altered }), game("c", { profile: { ...BASE, id: "renamed-only" } })]);
  assert.equal(summary.groups.length, 2);
  assert.equal(summary.groups.find((group) => group.profileHash === profileHash(BASE)).completed, 2);
  const legacy = normalizeGameRecord(game("legacy", { profile: LEGACY_PROFILE }));
  assert.ok(legacy);
  assert.notEqual(legacy.profileHash, profileHash(BASE));
  assert.equal(report([legacy]).groups[0].completed, 1);
  assert.throws(() => trainCandidate([legacy], LEGACY_PROFILE), { message: "invalid_training_profile" });
});

test("report separates providers, rules, colors and brain versions", () => {
  const summary = report([
    game("a"), game("b", { site: "another.tsuitate.info" }),
    game("c", { rulesKey: "beta-600+5-f10" }), game("d", { color: "w" }),
    game("e", { brainVersion: "brain-v2" }),
  ]);
  assert.equal(summary.groups.length, 5);
  assert.equal(summary.totals.completed, 5);
  assert.equal(summary.automaticPromotion, false);
});

test("aborts, unknown outcomes and partial histories cannot change the training candidate", () => {
  const records = outcomeTraining();
  const result = trainCandidate(records, BASE);
  const excluded = [
    game("abort", { completed: false, outcome: "unknown", reason: "transport_error", feedback: "foul" }),
    game("unknown", { outcome: "unknown", reason: "unknown", feedback: "foul" }),
    game("disconnected-win", { outcome: "win", reason: "disconnect", feedback: "foul" }),
    game("partial", { historyComplete: false, outcome: "loss", feedback: "foul" }),
    game("missing-history", { historyComplete: undefined, outcome: "loss", feedback: "foul" }),
  ];
  const augmented = trainCandidate([...records, ...excluded], BASE);
  assert.deepEqual(augmented.profile, result.profile);
  assert.equal(augmented.provenance.excludedRecords, 5);
  const summary = report(excluded);
  assert.equal(summary.totals.aborted, 2);
  assert.equal(summary.totals.unknown, 1);
  assert.equal(summary.totals.incompleteHistory, 2);
  assert.equal(summary.totals.completed, 0);
  assert.ok(summary.groups.every((group) => group.winRate === null));
});

test("held-out outcomes, order and duplication do not affect candidate weights", () => {
  const training = outcomeTraining();
  const heldOut = partitionGames("holdout", 8, { prefix: "held-out" });
  const first = trainCandidate([...training, ...heldOut], BASE);
  const alteredHoldout = heldOut.map((record) => game(record.gameId, { file: 1, outcome: "loss", feedback: "foul" }));
  const second = trainCandidate([...alteredHoldout, ...training, training[0]].reverse(), BASE);
  assert.deepEqual(first.profile, second.profile);
  assert.equal(first.provenance.trainingFingerprint, second.provenance.trainingFingerprint);
  assert.equal(first.provenance.holdoutGames, 8);
  assert.equal(second.provenance.duplicatesDropped, 1);
  const split = splitDataset([...training, ...heldOut]);
  assert.equal(split.training.length, training.length);
  assert.equal(split.holdout.length, heldOut.length);
});

test("fouls affect only a bounded candidate and never mutate the base profile", () => {
  const records = partitionGames("training", 12, { feedback: "foul" });
  const before = JSON.stringify(BASE);
  const candidate = trainCandidate(records, BASE);
  assert.ok(candidate.update.centrality < 0);
  assert.ok(candidate.update.advance < 0);
  assert.notEqual(candidate.profile.id, BASE.id);
  assert.ok(validateProfile(candidate.profile));
  assert.equal(JSON.stringify(BASE), before);
  assert.ok(Object.values(candidate.update).every((delta) => Math.abs(delta) <= 0.05));
  assert.equal(candidate.automaticPromotion, false);
  assert.throws(() => trainCandidate(records.slice(0, 2), BASE), { message: "insufficient_training_games" });
  const tiny = trainCandidate(records, BASE, { maxStep: 0.000000017 });
  assert.ok(Object.values(tiny.update).every((delta) => Math.abs(delta) <= 0.000000017));
  assert.throws(() => trainCandidate(records, BASE, { maxStep: 0.0000000001 }), { message: "insufficient_training_signal" });
});

test("winning feature association is centered within each site and color", () => {
  const records = outcomeTraining();
  const candidate = trainCandidate(records, BASE);
  assert.ok(candidate.update.centrality > 0);
  const noWithinSiteVariation = records.map((record, index) => game(record.gameId, index % 2
    ? { file: 1, outcome: "loss", site: "losing.tsuitate.info" }
    : { file: 5, outcome: "win", site: "winning.tsuitate.info" }));
  assert.throws(() => trainCandidate(noWithinSiteVariation, BASE, { minGames: 2 }), { message: "insufficient_training_signal" });
});

test("A/B reporting uses actual held-out games and leaves missing comparisons unproven", () => {
  const trained = trainCandidate(outcomeTraining(), BASE);
  const candidate = trained.profile;
  const baselineGames = partitionGames("holdout", 4, { prefix: "eval-base", outcome: "loss" });
  const candidateGames = partitionGames("holdout", 4, { prefix: "eval-candidate", profile: candidate, outcome: "win" });
  const options = { baselineProfile: BASE, candidateProfile: candidate, trainingProvenance: trained.provenance };
  const beforeEvaluation = report(outcomeTraining(), options);
  assert.deepEqual(beforeEvaluation.evaluation.comparisons, []);
  const measured = report([...outcomeTraining(), ...baselineGames, ...candidateGames], options);
  assert.equal(measured.evaluation.subset, "holdout");
  assert.equal(measured.evaluation.comparisons.length, 1);
  const pair = measured.evaluation.comparisons[0];
  assert.equal(pair.baseline.completed, 4);
  assert.equal(pair.candidate.completed, 4);
  assert.equal(pair.winRateDifference, 1);
  assert.equal(pair.status, "observed_results");
  assert.equal(measured.automaticPromotion, false);
  assert.throws(() => report([], { baselineProfile: BASE, candidateProfile: { ...BASE, id: "renamed" } }), { message: "identical_comparison_profiles" });
  assert.throws(() => report([], { baselineProfile: BASE, candidateProfile: candidate }), { message: "comparison_requires_matching_provenance" });
  assert.throws(() => report([], { ...options, trainingProvenance: { ...trained.provenance, candidateProfileHash: "0".repeat(64) } }), { message: "comparison_requires_matching_provenance" });
  assert.throws(() => report([], { ...options, seed: "different-split" }), { message: "evaluation_split_mismatch" });
});

test("A/B uses the candidate's recorded split rather than a fresh default split", () => {
  const records = Array.from({ length: 50 }, (_, index) => game(`custom-${index}`, index % 2 ? { file: 1, outcome: "loss" } : { file: 5 }));
  const trained = trainCandidate(records, BASE, { seed: "custom-training-split", holdoutFraction: 0.3 });
  const summary = report(records, { baselineProfile: BASE, candidateProfile: trained.profile, trainingProvenance: trained.provenance });
  assert.equal(summary.evaluation.split.seed, "custom-training-split");
  assert.equal(summary.evaluation.split.holdoutFraction, 0.3);
  assert.equal(summary.evaluation.comparisons[0].baseline.completed, splitDataset(records, { seed: "custom-training-split", holdoutFraction: 0.3 }).holdout.length);
});

test("CLI emits a loadable profile plus provenance and never overwrites an existing output", async () => {
  const directory = await mkdtemp(join(tmpdir(), "tsuitate-training-test-"));
  const script = new URL("../scripts/train-brain.mjs", import.meta.url).pathname;
  try {
    const input = join(directory, "games.jsonl");
    const base = join(directory, "base.json");
    const output = join(directory, "candidate.json");
    const trainingReport = join(directory, "candidate-report.json");
    await writeFile(input, outcomeTraining().map(JSON.stringify).join("\n"));
    await writeFile(base, JSON.stringify(BASE));
    const args = [script, "candidate", "--input", input, "--profile", base, "--output", output, "--report", trainingReport];
    const success = execFileSync(process.execPath, args, { encoding: "utf8" });
    assert.equal(JSON.parse(success).automaticPromotion, false);
    const saved = await readFile(output, "utf8");
    assert.ok(validateProfile(JSON.parse(saved)));
    const provenance = JSON.parse(await readFile(trainingReport, "utf8"));
    assert.equal(provenance.provenance.trainingGames, 12);
    assert.equal(Object.hasOwn(provenance, "profile"), false);
    const repeated = spawnSync(process.execPath, args, { encoding: "utf8" });
    assert.equal(repeated.status, 1);
    assert.equal(JSON.parse(repeated.stderr).error, "EEXIST");
    assert.equal(await readFile(output, "utf8"), saved);
    const existingReport = join(directory, "existing-report.json");
    const newOutput = join(directory, "should-not-remain.json");
    await writeFile(existingReport, "keep");
    const partial = spawnSync(process.execPath, [script, "candidate", "--input", input, "--profile", base, "--output", newOutput, "--report", existingReport], { encoding: "utf8" });
    assert.equal(partial.status, 1);
    assert.equal(await readFile(existingReport, "utf8"), "keep");
    await assert.rejects(readFile(newOutput), { code: "ENOENT" });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
