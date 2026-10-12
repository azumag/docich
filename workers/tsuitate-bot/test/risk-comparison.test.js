import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { chooseMove, inspectMoveCandidates } from "../src/brain/index.js";
import { chooseRiskAwareMove } from "../src/brain/risk-aware.js";
import { compareRiskPolicies, MAX_COMPARISON_CASES } from "../src/brain/risk-comparison.js";

const state = (budget = 3) => ({ ruleset: "tsuitate-9x9", color: "b", turn: "b", moveNumber: 1,
  pieces: [{ square: "5i", role: "K" }, { square: "7g", role: "P" }], hand: { S: 1 },
  inCheck: false, attemptBudget: budget });
const dataset = (item = {}) => ({ schemaVersion: 1, cases: [{ observation: state(), ...item }] });
const both = (report) => [report.baseline, report.candidate];
function labelsFor(observation, label) {
  return Object.fromEntries(inspectMoveCandidates(observation).candidates.map((c) => [c.usi, label]));
}

test("invalid datasets are rejected without reflecting private content", () => {
  for (const input of [null, [], {}, { schemaVersion: 2, cases: [] }, { schemaVersion: 1, cases: [] },
    { schemaVersion: 1, cases: new Array(MAX_COMPARISON_CASES + 1) }]) {
    assert.throws(() => compareRiskPolicies(input), { message: "Invalid comparison dataset" });
  }
});

test("missing labels mean unknown, not wins or fouls", () => {
  const result = compareRiskPolicies(dataset());
  assert.equal(result.labelsVerified, false);
  assert.equal(result.winRateMeasured, false);
  for (const stats of both(result)) {
    assert.equal(stats.unknown, 1);
    assert.equal(stats.confirmedFouls, 0);
    assert.equal(stats.accepted, 0);
    assert.equal(stats.decisions, 1);
  }
});

test("supplied accepted labels count accepted moves, not game wins", () => {
  const result = compareRiskPolicies(dataset({ judgments: labelsFor(state(), { status: "accepted" }) }));
  for (const stats of both(result)) assert.equal(stats.accepted, 1);
  assert.equal(result.winRateMeasured, false);
});

test("confirmed foul decrements the same-turn budget and excludes retries", () => {
  const observation = state(2);
  const result = compareRiskPolicies(dataset({ observation, judgments: labelsFor(observation, { status: "foul", reason: "into_check" }) }));
  for (const stats of both(result)) {
    assert.equal(stats.confirmedFouls, 2);
    assert.equal(stats.reasons.into_check, 2);
    assert.equal(stats.budgetExhausted, 1);
    assert.equal(stats.decisions, 2);
  }
});

test("unknown ACK immediately stops evaluation without retry or promotion inference", () => {
  const observation = { ...state(2), pieces: [{ square: "5d", role: "P" }], hand: {} };
  const result = compareRiskPolicies(dataset({ observation,
    judgments: labelsFor(observation, { status: "unknown", reason: "into_check" }) }));
  for (const stats of both(result)) {
    assert.equal(stats.decisions, 1);
    assert.equal(stats.confirmedFouls, 0);
    assert.equal(stats.unknown, 1);
  }
});

test("unknown budget is not simulated as unlimited after a foul", () => {
  const observation = state(null);
  const result = compareRiskPolicies(dataset({ observation, judgments: labelsFor(observation, { status: "foul" }) }));
  for (const stats of both(result)) {
    assert.equal(stats.confirmedFouls, 1);
    assert.equal(stats.unknownBudget, 1);
    assert.equal(stats.budgetExhausted, 0);
  }
});

test("retry cap is reported separately from budget exhaustion", () => {
  const observation = { ...state(1001), pieces: [], hand: { S: 1, G: 1 } };
  const result = compareRiskPolicies(dataset({ observation, judgments: labelsFor(observation, { status: "foul" }) }));
  for (const stats of both(result)) {
    assert.equal(stats.confirmedFouls, 32);
    assert.equal(stats.retryCapReached, 1);
    assert.equal(stats.budgetExhausted, 0);
  }
});

test("one confirmed foul can be followed by acceptance using an independent label map", () => {
  const observation = { ...state(3), pieces: [{ square: "5e", "role": "R" }], hand: {} };
  const labels = labelsFor(observation, { status: "accepted" });
  labels[chooseMove(observation).usi] = { status: "foul", reason: "blocked" };
  labels[chooseRiskAwareMove(observation).usi] = { status: "foul", reason: "blocked" };
  const result = compareRiskPolicies(dataset({ observation, judgments: labels }));
  for (const stats of both(result)) {
    assert.equal(stats.accepted, 1);
    assert.ok(stats.confirmedFouls >= 1);
    assert.equal(stats.budgetExhausted, 0);
  }
});

test("invalid cases/options are counted; private identifiers and raw reasons are not reflected", () => {
  const input = dataset({ id: "PRIVATE_GAME", token: "SECRET_MARKER",
    judgments: labelsFor(state(), { status: "foul", reason: "SECRET_REASON" }) });
  input.cases.push(null, { observation: { private: "SECRET_MARKER" } },
    { observation: state(), options: { seed: 1 } }, { observation: state(), options: null });
  const result = compareRiskPolicies(input);
  assert.equal(result.invalidCases, 4);
  assert.ok(result.baseline.reasons.other > 0);
  const serialized = JSON.stringify(result);
  for (const value of ["PRIVATE_GAME", "SECRET", "5i", "7g", "S*"]) assert.ok(!serialized.includes(value));
});

test("comparison input and label maps remain unchanged", () => {
  const input = dataset({ judgments: labelsFor(state(), { status: "foul", reason: "blocked" }) });
  const before = structuredClone(input);
  compareRiskPolicies(input);
  assert.deepEqual(input, before);
});

test("performance statistics describe actual local wall time, not an advertised deadline", () => {
  const result = compareRiskPolicies(dataset());
  for (const stats of both(result)) {
    assert.ok(stats.wallMilliseconds.p50 >= 0);
    assert.ok(stats.wallMilliseconds.p95 >= stats.wallMilliseconds.p50);
    assert.ok(stats.wallMilliseconds.max >= stats.wallMilliseconds.p95);
  }
});

test("CLI emits aggregate JSON and input digest, never case data", async () => {
  const directory = await mkdtemp(join(tmpdir(), "tsuitate-risk-"));
  try {
    const path = join(directory, "PRIVATE_FILE.json");
    await writeFile(path, JSON.stringify(dataset({ id: "PRIVATE_GAME" })));
    const script = fileURLToPath(new URL("../scripts/compare-risk-policy.mjs", import.meta.url));
    const run = spawnSync(process.execPath, [script, path], { encoding: "utf8", timeout: 10000 });
    assert.equal(run.status, 0, run.stderr);
    assert.equal(run.stderr, "");
    const parsed = JSON.parse(run.stdout);
    assert.match(parsed.inputSha256, /^[0-9a-f]{64}$/);
    assert.equal(parsed.totalCases, 1);
    assert.ok(!run.stdout.includes("PRIVATE"));
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("CLI rejects malformed, oversized and missing files without echoing paths or content", async () => {
  const directory = await mkdtemp(join(tmpdir(), "tsuitate-risk-"));
  try {
    const script = fileURLToPath(new URL("../scripts/compare-risk-policy.mjs", import.meta.url));
    const malformed = join(directory, "PRIVATE_bad.json");
    const oversized = join(directory, "PRIVATE_large.json");
    await writeFile(malformed, '{"PRIVATE_SECRET": INVALID');
    await writeFile(oversized, "x".repeat(5 * 1024 * 1024 + 1));
    for (const args of [[], [malformed], [oversized], [join(directory, "PRIVATE_missing")], [directory]]) {
      const run = spawnSync(process.execPath, [script, ...args], { encoding: "utf8", timeout: 10000 });
      assert.equal(run.status, 1);
      assert.equal(run.stdout, "");
      assert.ok(!run.stderr.includes("PRIVATE"));
      assert.match(run.stderr, /^Comparison failed/);
    }
  } finally { await rm(directory, { recursive: true, force: true }); }
});
