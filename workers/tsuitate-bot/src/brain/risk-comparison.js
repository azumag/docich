/** Offline-only comparison. Supplied labels are not verified game outcomes. */
import { performance } from "node:perf_hooks";
import { BRAIN_VERSION, chooseMove, normalizeObservation, validateProfile, LINEAR_PROFILE } from "./index.js";
import { RISK_BRAIN_VERSION, chooseRiskAwareMove } from "./risk-aware.js";

export const MAX_COMPARISON_CASES = 1000;
const MAX_RETRIES = 32;
const REASONS = new Set(["into_check", "blocked", "drop_occupied", "nifu", "pawn_drop_mate", "other"]);
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const emptyStats = () => ({ accepted: 0, confirmedFouls: 0, unknown: 0, noMove: 0,
  budgetExhausted: 0, unknownBudget: 0, retryCapReached: 0, proposedDrops: 0,
  reasons: Object.fromEntries([...REASONS].map((reason) => [reason, 0])), durations: [] });

function optionsFor(raw) {
  if (raw === undefined) raw = {};
  if (!record(raw)) return null;
  const profile = validateProfile(raw.profile ?? LINEAR_PROFILE);
  const seed = raw.seed ?? "";
  const recentMoves = raw.recentMoves ?? [];
  const forbiddenMoves = raw.forbiddenMoves ?? [];
  const foulMoves = raw.foulMoves ?? [];
  if (profile?.policy !== "linear-v1" || typeof seed !== "string" || seed.length > 512
      || !Array.isArray(recentMoves) || recentMoves.length > 4096
      || !Array.isArray(forbiddenMoves) || forbiddenMoves.length > 4064
      || !Array.isArray(foulMoves) || foulMoves.length > 4064) return null;
  return { profile, seed, recentMoves: [...recentMoves], forbiddenMoves: [...forbiddenMoves], foulMoves: [...foulMoves] };
}

function evaluate(chooser, observation, originalOptions, labels, stats) {
  const options = structuredClone(originalOptions);
  const state = structuredClone(observation);
  let firstMove = null;
  for (let attempt = 0; attempt < MAX_RETRIES; attempt += 1) {
    const started = performance.now();
    const decision = chooser(state, options);
    stats.durations.push(performance.now() - started);
    if (!decision) { stats.noMove += 1; return firstMove; }
    firstMove ??= decision.usi;
    if (decision.usi[1] === "*") stats.proposedDrops += 1;
    // Only the evaluator sees labels. Neither chooser receives this object.
    const label = record(labels) && Object.hasOwn(labels, decision.usi) ? labels[decision.usi] : null;
    if (!record(label) || !["accepted", "foul"].includes(label.status)) {
      stats.unknown += 1;
      return firstMove; // Unknown ACK: never retry or infer a foul.
    }
    if (label.status === "accepted") { stats.accepted += 1; return firstMove; }
    stats.confirmedFouls += 1;
    const reason = REASONS.has(label.reason) ? label.reason : "other";
    stats.reasons[reason] += 1;
    if (state.attemptBudget === null) { stats.unknownBudget += 1; return firstMove; }
    state.attemptBudget -= 1;
    if (state.attemptBudget === 0) { stats.budgetExhausted += 1; return firstMove; }
    options.forbiddenMoves.push(decision.usi);
    options.foulMoves.push(decision.usi);
  }
  stats.retryCapReached += 1;
  return firstMove;
}

function summarize(stats) {
  const { durations, ...counts } = stats;
  const sorted = [...durations].sort((a, b) => a - b);
  const percentile = (p) => sorted.length ? sorted[Math.ceil(sorted.length * p) - 1] : null;
  return { ...counts, decisions: sorted.length,
    wallMilliseconds: { p50: percentile(0.5), p95: percentile(0.95), max: sorted.at(-1) ?? null } };
}

/** No case IDs, own boards, USI moves, filenames or label text in the output. */
export function compareRiskPolicies(input) {
  if (!record(input) || input.schemaVersion !== 1 || !Array.isArray(input.cases)
      || input.cases.length === 0 || input.cases.length > MAX_COMPARISON_CASES) {
    throw new TypeError("Invalid comparison dataset");
  }
  const baseline = emptyStats(); const candidate = emptyStats();
  let invalidCases = 0; let changedFirstMove = 0;
  for (const item of input.cases) {
    const observation = record(item) ? normalizeObservation(item.observation) : null;
    const options = record(item) ? optionsFor(item.options) : null;
    if (!observation || !options || observation.color !== observation.turn) { invalidCases += 1; continue; }
    const a = evaluate(chooseMove, observation, options, item.judgments, baseline);
    const b = evaluate(chooseRiskAwareMove, observation, options, item.judgments, candidate);
    if (a !== b) changedFirstMove += 1;
  }
  return {
    schemaVersion: 1, comparisonKind: "fixed-own-view-supplied-labels",
    labelsVerified: false, winRateMeasured: false,
    baselineVersion: BRAIN_VERSION, candidateVersion: RISK_BRAIN_VERSION,
    totalCases: input.cases.length, invalidCases, changedFirstMove,
    baseline: summarize(baseline), candidate: summarize(candidate),
  };
}
