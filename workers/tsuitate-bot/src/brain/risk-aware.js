/** Explicit opt-in candidate policy; never imported by the live adapters. */
import { inspectMoveCandidates, normalizeObservation } from "./index.js";

export const RISK_BRAIN_VERSION = "tsuitate-brain-v12-risk-candidate1";
export const MAX_CHECK_HYPOTHESES = 128;
const ROLES = ["P", "L", "N", "S", "G", "B", "R", "K", "+B", "+R"];
const RAYS = [[-1, -1], [-1, 0], [-1, 1], [0, -1], [0, 1], [1, -1], [1, 0], [1, 1]];
const f = (s) => Number(s[0]);
const r = (s) => s.charCodeAt(1) - 96;
const square = (x, y) => `${x}${String.fromCharCode(96 + y)}`;
const inside = (x, y) => x >= 1 && x <= 9 && y >= 1 && y <= 9;
const validBudget = (b) => b === null || (Number.isSafeInteger(b) && b >= 0 && b <= 1001);

/** Heuristic loss multiplier, NOT an estimated foul probability. */
export function foulRiskMultiplier(budget) {
  if (!validBudget(budget) || budget === 0) return null;
  if (budget === null) return 4;
  return budget === 1 ? 8 : budget === 2 ? 4 : budget === 3 ? 2 : 1;
}

function attacks(piece, target, occupied, enemyForward) {
  const dx = f(target) - f(piece.square);
  const dy = r(target) - r(piece.square);
  const ax = Math.abs(dx); const ay = Math.abs(dy);
  const forward = dy * enemyForward;
  if (ax === 0 && ay === 0) return false;
  switch (piece.role) {
    case "P": return dx === 0 && forward === 1;
    case "N": return ax === 1 && forward === 2;
    case "S": return (forward === 1 && ax <= 1) || (forward === -1 && ax === 1);
    case "G": return (forward === 1 && ax <= 1) || (dy === 0 && ax === 1) || (dx === 0 && forward === -1);
    case "K": return Math.max(ax, ay) === 1;
    case "+B": if (ax + ay === 1) return true; break;
    case "+R": if (ax === 1 && ay === 1) return true; break;
    default: break;
  }
  const ray = piece.role === "L" ? dx === 0 && forward > 0
    : piece.role.endsWith("B") ? ax === ay
      : piece.role.endsWith("R") && (dx === 0 || dy === 0);
  if (!ray) return false;
  for (let step = 1; step < Math.max(ax, ay); step += 1) {
    if (occupied.has(square(f(piece.square) + Math.sign(dx) * step,
      r(piece.square) + Math.sign(dy) * step))) return false;
  }
  return true;
}

function contextFor(observation) {
  const kings = observation.pieces.filter((piece) => piece.role === "K");
  const own = new Set(observation.pieces.map((piece) => piece.square));
  // Older evidence is not a statement of current occupancy in the hypothesis
  // model. The shared v11 generator retains its existing candidate exclusions.
  const freshEnemies = new Set(observation.knownEnemies.filter((item) => item.age === 0)
    .map((item) => item.square));
  const occupied = new Set([...own, ...freshEnemies]);
  return { king: kings.length === 1 ? kings[0].square : null, own, occupied,
    enemyForward: observation.color === "b" ? 1 : -1 };
}

function hypothesesFor(observation, context) {
  if (observation.inCheck !== true) return { status: "not-checked", hypotheses: [] };
  if (!context.king) return { status: "incomplete-king-view", hypotheses: [] };
  const hypotheses = [];
  for (let y = 1; y <= 9; y += 1) for (let x = 1; x <= 9; x += 1) {
    const origin = square(x, y);
    if (context.own.has(origin)) continue;
    for (const role of ROLES) {
      const piece = { square: origin, role };
      if (attacks(piece, context.king, context.occupied, context.enemyForward)) hypotheses.push(piece);
    }
  }
  // Fail soft, never silently bias the result by truncating one side of board.
  return hypotheses.length > MAX_CHECK_HYPOTHESES
    ? { status: "overflow", hypotheses: [] }
    : { status: hypotheses.length ? "single-checker-only" : "no-consistent-checker", hypotheses };
}

/** Own-view-derived attack hypotheses. No complete hidden board is constructed. */
export function checkingHypotheses(rawObservation) {
  const observation = normalizeObservation(rawObservation);
  if (!observation) return null;
  return hypothesesFor(observation, contextFor(observation));
}

function crosses(from, to, obstacle) {
  const dx = f(to) - f(from); const dy = r(to) - r(from);
  if (dx !== 0 && dy !== 0 && Math.abs(dx) !== Math.abs(dy)) return false;
  for (let step = 1; step < Math.max(Math.abs(dx), Math.abs(dy)); step += 1) {
    if (square(f(from) + Math.sign(dx) * step, r(from) + Math.sign(dy) * step) === obstacle) return true;
  }
  return false;
}

function exposure(king, occupied) {
  if (!king) return 0;
  let count = 0;
  for (const [dx, dy] of RAYS) for (let step = 1; step <= 8; step += 1) {
    const x = f(king) + dx * step; const y = r(king) + dy * step;
    if (!inside(x, y) || occupied.has(square(x, y))) break;
    count += 1;
  }
  return count;
}

function evaluateCandidate(candidate, context, hypotheses) {
  const drop = candidate.usi[1] === "*";
  const from = candidate.usi.slice(0, 2); const to = candidate.usi.slice(2, 4);
  const king = candidate.role === "K" ? to : context.king;
  const after = new Set(context.occupied);
  // Vacating the source matters: it can expose a checking ray behind the king.
  if (!drop) after.delete(from);
  after.add(to);
  let covered = 0;
  for (const checker of hypotheses) {
    if (drop && checker.square === to) continue; // A drop cannot capture.
    if (!drop && crosses(from, to, checker.square)) continue; // Hidden blocker.
    if (!drop && checker.square === to) { covered += 1; continue; }
    if (!attacks(checker, king, after, context.enemyForward)) covered += 1;
  }
  const afterExposure = exposure(king, after);
  const opening = candidate.role === "K" ? 0 : Math.max(0, afterExposure - exposure(context.king, context.occupied));
  return {
    unknownPathSquares: candidate.unknownPathSquares,
    unknownDrop: drop ? 1 : 0,
    kingExposure: candidate.role === "K" ? afterExposure : 0,
    openedRaySquares: opening,
    checkHypotheses: hypotheses.length,
    coveredHypotheses: covered,
    // This ratio describes this deliberately incomplete hypothesis set only.
    uncoveredFraction: hypotheses.length ? 1 - covered / hypotheses.length : null,
  };
}

function hash(text) {
  let value = 5381;
  for (const char of text) value = (value * 33 + char.charCodeAt(0)) >>> 0;
  return value;
}

/** Private detailed analysis; callers must not broadcast this own-view data. */
export function analyzeRiskCandidates(rawObservation, options = {}) {
  const inspected = inspectMoveCandidates(rawObservation, options);
  if (!inspected) return null;
  const { observation, profile, seed, candidates } = inspected;
  const multiplier = foulRiskMultiplier(observation.attemptBudget);
  const context = contextFor(observation);
  const { status, hypotheses } = hypothesesFor(observation, context);
  const ranked = candidates.map((candidate) => {
    const risk = evaluateCandidate(candidate, context, hypotheses);
    const penalty = 0.25 * risk.unknownPathSquares + 0.75 * risk.unknownDrop
      + 0.025 * (risk.kingExposure + risk.openedRaySquares)
      + 2 * (risk.uncoveredFraction ?? 0);
    return { ...candidate, risk, riskPenalty: penalty,
      adjustedScore: candidate.score - multiplier * penalty,
      evidenceTier: candidate.evidenceCapture ? 1 : 0,
      tieBreak: hash(`${seed}:${candidate.usi}`) };
  });
  // Preserve v11's fresh-evidence tier; risk changes ordering within tiers.
  // No exploration noise: an uncalibrated information-gain model must not spend
  // the last foul as though probing were free. The seed only breaks exact ties.
  ranked.sort((a, b) => b.evidenceTier - a.evidenceTier || b.adjustedScore - a.adjustedScore
    || a.tieBreak - b.tieBreak || (a.usi < b.usi ? -1 : a.usi > b.usi ? 1 : 0));
  return { brainVersion: RISK_BRAIN_VERSION, profileId: profile.id,
    attemptBudget: observation.attemptBudget, multiplier, hypothesisStatus: status, ranked };
}

/** Explicit candidate entry point, separate from production chooseMove. */
export function chooseRiskAwareMove(rawObservation, options = {}) {
  const analysis = analyzeRiskCandidates(rawObservation, options);
  if (!analysis?.ranked.length) return null;
  const selected = analysis.ranked[0];
  return {
    usi: selected.usi, features: selected.features, score: selected.adjustedScore,
    brainVersion: RISK_BRAIN_VERSION, profileId: analysis.profileId,
    candidateCount: analysis.ranked.length,
    diagnostics: {
      schemaVersion: 1, policyVersion: RISK_BRAIN_VERSION,
      attemptBudget: analysis.attemptBudget, multiplier: analysis.multiplier,
      hypothesisStatus: analysis.hypothesisStatus,
      reason: selected.evidenceTier ? "fresh-evidence" : selected.risk.checkHypotheses ? "check-risk" : "budget-risk",
      baseScore: selected.score, riskPenalty: selected.riskPenalty,
      ...selected.risk,
    },
  };
}
