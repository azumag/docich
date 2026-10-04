import { createHash } from "node:crypto";
import { featuresForMove, normalizeObservation, validateProfile } from "../brain/index.js";

const FEATURE_KEYS = ["advance", "centrality", "promotion", "drop", "kingMove", "distance", "repeat"];
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,127}$/;
const GAME_ID = /^[A-Za-z0-9_.:+/-]{1,128}$/;
const SITE = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$/;
const OUTCOMES = new Set(["win", "loss", "draw", "unknown"]);
export const GAME_REASONS = Object.freeze([
  "normal", "checkmate", "stalemate", "resign", "timeout", "foul_limit", "repetition", "draw",
  "aborted", "disconnect", "transport_error", "protocol_error", "storage_error", "interrupted", "no_move", "unknown",
]);
const ABORT_REASONS = new Set(["aborted", "disconnect", "transport_error", "protocol_error", "storage_error", "interrupted", "no_move"]);
const FEEDBACK = new Set(["accepted", "foul", "unknown"]);
const MAX_DECISIONS = 10000;
const MAX_LINE_BYTES = 8 * 1024 * 1024;
export const DEFAULT_SPLIT = Object.freeze({ seed: "tsuitate-v1", holdoutFraction: 0.2 });

function failure(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hash(value) {
  return createHash("sha256").update(value).digest("hex");
}

function timestamp(value) {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z$/.test(value)) return null;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return null;
  const canonical = new Date(parsed).toISOString();
  return canonical.slice(0, 19) === value.slice(0, 19) ? canonical : null;
}

/** Behavior identity deliberately excludes the human-readable profile id. */
export function profileHash(rawProfile) {
  const profile = validateProfile(rawProfile);
  if (!profile) throw failure("invalid_profile");
  if (profile.policy === "legacy-v1") return hash(JSON.stringify({ schemaVersion: profile.schemaVersion, policy: profile.policy }));
  const weights = Object.fromEntries(FEATURE_KEYS.map((key) => [key, profile.weights[key]]));
  return hash(JSON.stringify({ schemaVersion: profile.schemaVersion, policy: profile.policy, weights, exploration: profile.exploration }));
}

/** Rebuild an allowlisted record. Never persist raw events, errors, tokens or post-game kifu. */
export function normalizeGameRecord(raw) {
  if (!object(raw) || raw.schemaVersion !== 1 || raw.kind !== "tsuitate_game") return null;
  if (typeof raw.site !== "string" || raw.site.length > 253 || !SITE.test(raw.site)) return null;
  if (raw.ruleset !== "tsuitate-9x9" || !["b", "w"].includes(raw.color)) return null;
  if (typeof raw.gameId !== "string" || !GAME_ID.test(raw.gameId)) return null;
  if (![raw.rulesKey, raw.brainVersion].every((value) => typeof value === "string" && IDENTIFIER.test(value))) return null;
  const startedAt = timestamp(raw.startedAt);
  const endedAt = timestamp(raw.endedAt);
  if (!startedAt || !endedAt || endedAt < startedAt) return null;
  const profile = validateProfile(raw.profile);
  if (!profile || typeof raw.completed !== "boolean" || !OUTCOMES.has(raw.outcome) || !GAME_REASONS.includes(raw.reason)) return null;
  if (raw.historyComplete !== undefined && typeof raw.historyComplete !== "boolean") return null;
  if (!raw.completed && raw.outcome !== "unknown") return null;
  if (!Array.isArray(raw.decisions) || raw.decisions.length > MAX_DECISIONS) return null;
  const decisions = [];
  const recentAccepted = [];
  let previousMoveNumber = 0;
  let previousFeedback;
  for (const decision of raw.decisions) {
    if (!object(decision) || !FEEDBACK.has(decision.feedback)) return null;
    const observation = normalizeObservation(decision.observation);
    if (!observation || observation.color !== raw.color || observation.turn !== raw.color) return null;
    if (!Number.isSafeInteger(decision.moveNumber) || decision.moveNumber !== observation.moveNumber) return null;
    if (decision.moveNumber < previousMoveNumber || (decision.moveNumber === previousMoveNumber && previousFeedback === "accepted")) return null;
    const features = featuresForMove(observation, decision.usi, recentAccepted);
    if (!features || !FEATURE_KEYS.every((key) => Number.isFinite(features[key]) && Math.abs(features[key]) <= 1)) return null;
    if (!Number.isFinite(decision.score) || Math.abs(decision.score) > 1000000) return null;
    decisions.push({
      moveNumber: observation.moveNumber,
      observation,
      usi: decision.usi,
      features: Object.fromEntries(FEATURE_KEYS.map((key) => [key, features[key]])),
      score: decision.score,
      feedback: decision.feedback,
    });
    if (decision.feedback === "accepted") {
      recentAccepted.push(decision.usi);
      if (recentAccepted.length > 64) recentAccepted.shift();
    }
    previousMoveNumber = decision.moveNumber;
    previousFeedback = decision.feedback;
  }
  return {
    schemaVersion: 1, kind: "tsuitate_game", site: raw.site, ruleset: raw.ruleset,
    rulesKey: raw.rulesKey, gameId: raw.gameId, color: raw.color, startedAt, endedAt,
    brainVersion: raw.brainVersion, profile, profileHash: profileHash(profile), decisions,
    historyComplete: raw.historyComplete === true, completed: raw.completed, outcome: raw.outcome, reason: raw.reason,
  };
}

function consolidate(rawRecords) {
  if (!Array.isArray(rawRecords)) throw failure("invalid_dataset");
  const byGame = new Map();
  let duplicatesDropped = 0;
  for (const raw of rawRecords) {
    const record = normalizeGameRecord(raw);
    if (!record) throw failure("invalid_game_record");
    const key = JSON.stringify([record.site, record.gameId]);
    const canonical = JSON.stringify(record);
    const previous = byGame.get(key);
    if (previous && previous.canonical !== canonical) throw failure("conflicting_game_record");
    if (previous) duplicatesDropped += 1;
    else byGame.set(key, { record, canonical });
  }
  return { records: [...byGame.values()].map(({ record }) => record), duplicatesDropped };
}

export function parseDataset(jsonl) {
  if (typeof jsonl !== "string") throw failure("invalid_dataset");
  const records = [];
  for (const line of jsonl.split(/\r?\n/)) {
    if (!line.trim()) continue;
    if (Buffer.byteLength(line) > MAX_LINE_BYTES) throw failure("record_too_large");
    try {
      records.push(JSON.parse(line));
    } catch {
      throw failure("invalid_dataset_json");
    }
  }
  return consolidate(records);
}

function splitOptions(options = {}) {
  const seed = options.seed ?? DEFAULT_SPLIT.seed;
  const holdoutFraction = options.holdoutFraction ?? DEFAULT_SPLIT.holdoutFraction;
  if (typeof seed !== "string" || !IDENTIFIER.test(seed) || !Number.isFinite(holdoutFraction) || holdoutFraction < 0.1 || holdoutFraction > 0.5) {
    throw failure("invalid_split_options");
  }
  return { seed, holdoutFraction };
}

/** Stable by game, so reordering/duplicates/profile labels cannot cross the split. */
export function splitDataset(rawRecords, options = {}) {
  const { records, duplicatesDropped } = consolidate(rawRecords);
  const split = splitOptions(options);
  const training = [];
  const holdout = [];
  for (const record of records) {
    const value = parseInt(hash(JSON.stringify([split.seed, record.site, record.gameId])).slice(0, 8), 16) / 0x100000000;
    (value < split.holdoutFraction ? holdout : training).push(record);
  }
  return { training, holdout, duplicatesDropped, split: { algorithm: "sha256-site-game-v1", ...split } };
}

function classification(record) {
  if (!record.completed || ABORT_REASONS.has(record.reason)) return "aborted";
  if (record.outcome === "unknown" || record.reason === "unknown") return "unknown";
  return record.historyComplete ? "completed" : "incompleteHistory";
}

function contextKey(record) {
  return JSON.stringify([record.site, record.ruleset, record.rulesKey, record.color, record.brainVersion]);
}

function emptyCounts() {
  return { recorded: 0, completed: 0, wins: 0, losses: 0, draws: 0, aborted: 0, unknown: 0, incompleteHistory: 0, decisions: 0, acceptedMoves: 0, fouls: 0, unknownFeedback: 0, completedFouls: 0, reasonCounts: {} };
}

function addCounts(counts, record) {
  counts.recorded += 1;
  counts.reasonCounts[record.reason] = (counts.reasonCounts[record.reason] ?? 0) + 1;
  const state = classification(record);
  counts[state] += 1;
  if (state === "completed") counts[{ win: "wins", loss: "losses", draw: "draws" }[record.outcome]] += 1;
  for (const decision of record.decisions) {
    counts.decisions += 1;
    counts[{ accepted: "acceptedMoves", foul: "fouls", unknown: "unknownFeedback" }[decision.feedback]] += 1;
    if (state === "completed" && decision.feedback === "foul") counts.completedFouls += 1;
  }
}

function winInterval(wins, total) {
  if (!total) return null;
  const z = 1.959963984540054;
  const rate = wins / total;
  const denominator = 1 + z * z / total;
  const middle = (rate + z * z / (2 * total)) / denominator;
  const radius = z * Math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator;
  return { lower: Math.max(0, middle - radius), upper: Math.min(1, middle + radius) };
}

function rates(counts) {
  return {
    winRate: counts.completed ? counts.wins / counts.completed : null,
    scoreRate: counts.completed ? (counts.wins + 0.5 * counts.draws) / counts.completed : null,
    winRate95: winInterval(counts.wins, counts.completed),
    foulsPerCompletedGame: counts.completed ? counts.completedFouls / counts.completed : null,
  };
}

function grouped(records) {
  const groups = new Map();
  for (const record of records) {
    const key = JSON.stringify([record.profileHash, contextKey(record)]);
    if (!groups.has(key)) groups.set(key, {
      site: record.site, ruleset: record.ruleset, rulesKey: record.rulesKey,
      color: record.color, brainVersion: record.brainVersion, profileHash: record.profileHash,
      profileIds: [], ...emptyCounts(),
    });
    const group = groups.get(key);
    if (!group.profileIds.includes(record.profile.id)) group.profileIds.push(record.profile.id);
    addCounts(group, record);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, group]) => ({ ...group, profileIds: group.profileIds.sort(), ...rates(group) }));
}

/** Only observed game outcomes can compare profiles; replay scores are never wins. */
export function report(rawRecords, options = {}) {
  const { records, duplicatesDropped } = consolidate(rawRecords);
  const totals = emptyCounts();
  records.forEach((record) => addCounts(totals, record));
  const result = {
    schemaVersion: 1, kind: "tsuitate_brain_report", duplicatesDropped,
    totals, groups: grouped(records), automaticPromotion: false,
  };
  if (options.baselineProfile === undefined && options.candidateProfile === undefined) return result;
  const baselineHash = profileHash(options.baselineProfile);
  const candidateHash = profileHash(options.candidateProfile);
  if (baselineHash === candidateHash) throw failure("identical_comparison_profiles");
  const provenance = options.trainingProvenance;
  if (!object(provenance) || provenance.schemaVersion !== 1 || provenance.method !== "stratified-outcome-covariance-foul-v1" ||
    provenance.baseProfileHash !== baselineHash || provenance.candidateProfileHash !== candidateHash ||
    !object(provenance.split) || provenance.split.algorithm !== "sha256-site-game-v1" ||
    typeof provenance.trainingFingerprint !== "string" || !/^[0-9a-f]{64}$/.test(provenance.trainingFingerprint)) throw failure("comparison_requires_matching_provenance");
  const recordedSplit = splitOptions(provenance.split);
  if ((options.seed !== undefined && options.seed !== recordedSplit.seed) ||
    (options.holdoutFraction !== undefined && options.holdoutFraction !== recordedSplit.holdoutFraction)) throw failure("evaluation_split_mismatch");
  const { holdout, split } = splitDataset(records, recordedSplit);
  const evaluationGroups = grouped(holdout);
  const comparisons = [];
  const contexts = new Map();
  for (const group of evaluationGroups) {
    if (![baselineHash, candidateHash].includes(group.profileHash)) continue;
    const key = contextKey(group);
    if (!contexts.has(key)) contexts.set(key, {});
    contexts.get(key)[group.profileHash === baselineHash ? "baseline" : "candidate"] = group;
  }
  for (const pair of contexts.values()) {
    const context = pair.baseline ?? pair.candidate;
    const ready = Boolean(pair.baseline?.completed && pair.candidate?.completed);
    comparisons.push({
      site: context.site, ruleset: context.ruleset, rulesKey: context.rulesKey,
      color: context.color, brainVersion: context.brainVersion,
      baseline: pair.baseline ?? null, candidate: pair.candidate ?? null,
      status: ready ? "observed_results" : "insufficient_data",
      winRateDifference: ready ? pair.candidate.winRate - pair.baseline.winRate : null,
    });
  }
  return {
    ...result,
    evaluation: { split, subset: "holdout", trainingFingerprint: provenance.trainingFingerprint, baselineProfileHash: baselineHash, candidateProfileHash: candidateHash, comparisons,
      limitation: "Descriptive comparison only; opponents and timing are not controlled. No automatic promotion." },
  };
}

function vector() {
  return Object.fromEntries(FEATURE_KEYS.map((key) => [key, 0]));
}

function summarizeActions(record) {
  const accepted = record.decisions.filter((decision) => decision.feedback === "accepted");
  const observed = record.decisions.filter((decision) => decision.feedback !== "unknown");
  const successfulFeatures = vector();
  const foulFeatures = vector();
  for (const key of FEATURE_KEYS) {
    if (accepted.length) successfulFeatures[key] = accepted.reduce((sum, decision) => sum + decision.features[key], 0) / accepted.length;
    if (observed.length) foulFeatures[key] = observed.filter((decision) => decision.feedback === "foul").reduce((sum, decision) => sum + decision.features[key], 0) / observed.length;
  }
  return { reward: { win: 1, loss: -1, draw: 0 }[record.outcome], hasAccepted: accepted.length > 0, successfulFeatures, foulFeatures };
}

/** Conservative association-based proposal, not a proven strength improvement. */
export function trainCandidate(rawRecords, rawBaseProfile, options = {}) {
  const baseProfile = validateProfile(rawBaseProfile);
  if (!baseProfile || baseProfile.policy !== "linear-v1") throw failure("invalid_training_profile");
  const baseHash = profileHash(baseProfile);
  const { training, holdout, duplicatesDropped, split } = splitDataset(rawRecords, options);
  const minGames = options.minGames ?? 10;
  const learningRate = options.learningRate ?? 0.1;
  const maxStep = options.maxStep ?? 0.05;
  const foulPenalty = options.foulPenalty ?? 0.5;
  if (!Number.isSafeInteger(minGames) || minGames < 2 || minGames > 10000 ||
    !Number.isFinite(learningRate) || learningRate <= 0 || learningRate > 0.25 ||
    !Number.isFinite(maxStep) || maxStep <= 0 || maxStep > 0.1 ||
    !Number.isFinite(foulPenalty) || foulPenalty < 0 || foulPenalty > 2) throw failure("invalid_training_options");
  const eligible = (record) => classification(record) === "completed" && record.profileHash === baseHash && record.decisions.some((decision) => decision.feedback !== "unknown");
  const trainingGames = training.filter(eligible).sort((a, b) => {
    const left = JSON.stringify([a.site, a.gameId]); const right = JSON.stringify([b.site, b.gameId]);
    return left < right ? -1 : left > right ? 1 : 0;
  });
  if (trainingGames.length < minGames) throw failure("insufficient_training_games");
  const groups = new Map();
  for (const record of trainingGames) {
    const key = contextKey(record);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(record);
  }
  const signal = vector();
  for (const games of groups.values()) {
    const actions = games.map(summarizeActions);
    const accepted = actions.filter((game) => game.hasAccepted);
    const meanReward = accepted.length ? accepted.reduce((sum, game) => sum + game.reward, 0) / accepted.length : 0;
    for (const key of FEATURE_KEYS) {
      const meanFeature = accepted.length ? accepted.reduce((sum, game) => sum + game.successfulFeatures[key], 0) / accepted.length : 0;
      const covariance = accepted.length > 1 ? accepted.reduce((sum, game) => sum + (game.reward - meanReward) * (game.successfulFeatures[key] - meanFeature), 0) / accepted.length : 0;
      const foulSignal = actions.reduce((sum, game) => sum + game.foulFeatures[key], 0) / actions.length;
      signal[key] += (games.length / trainingGames.length) * (covariance - foulPenalty * foulSignal);
    }
  }
  const weights = {};
  const update = {};
  for (const key of FEATURE_KEYS) {
    const step = Math.max(-maxStep, Math.min(maxStep, learningRate * signal[key]));
    // Quantize the delta toward zero, preserving the original profile precision
    // and keeping an explicitly tiny maxStep from growing through rounding.
    const quantizedStep = Math.trunc(step * 100000000) / 100000000;
    weights[key] = Math.max(-10, Math.min(10, baseProfile.weights[key] + quantizedStep));
    update[key] = Number((weights[key] - baseProfile.weights[key]).toFixed(8));
  }
  if (!FEATURE_KEYS.some((key) => update[key] !== 0)) throw failure("insufficient_training_signal");
  const proposal = { ...baseProfile, weights };
  const candidateHash = profileHash(proposal);
  proposal.id = `${baseProfile.id.slice(0, 40)}-candidate-${candidateHash.slice(0, 12)}`;
  const profile = validateProfile(proposal);
  if (!profile) throw failure("invalid_candidate_profile");
  const trainingFingerprint = hash(trainingGames.map((record) => JSON.stringify(record)).sort().join("\n"));
  return {
    profile,
    provenance: {
      schemaVersion: 1, method: "stratified-outcome-covariance-foul-v1",
      baseProfileId: baseProfile.id, baseProfileHash: baseHash, candidateProfileHash: candidateHash,
      sourceRecords: training.length + holdout.length, duplicatesDropped,
      trainingGames: trainingGames.length, holdoutGames: holdout.filter(eligible).length,
      excludedRecords: [...training, ...holdout].filter((record) => !eligible(record)).length,
      split, trainingFingerprint, settings: { minGames, learningRate, maxStep, foulPenalty },
      trainingGroups: grouped(trainingGames),
    },
    update, automaticPromotion: false,
    warning: "A small heuristic candidate based on associations and foul feedback. Strength must be measured using new games and the held-out comparison; weights are not automatically promoted.",
  };
}
