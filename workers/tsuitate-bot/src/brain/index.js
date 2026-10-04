/** Site-independent, visible-information-only Tsuitate move selection. */
export const BRAIN_VERSION = "tsuitate-brain-v5";
const ROLES = new Set(["P", "L", "N", "S", "G", "B", "R", "K", "+P", "+L", "+N", "+S", "+B", "+R"]);
const HAND_ROLES = ["P", "L", "N", "S", "G", "B", "R"];
const HAND_LIMITS = { P: 18, L: 4, N: 4, S: 4, G: 4, B: 2, R: 2 };
const FEATURE_NAMES = ["advance", "centrality", "promotion", "drop", "kingMove", "distance", "repeat"];
const SQUARE = /^[1-9][a-i]$/;
const USI_MOVE = /^(?:[1-9][a-i][1-9][a-i]\+?|[PLNSGBR]\*[1-9][a-i])$/;
const GOLD = [[0, 1], [-1, 1], [1, 1], [-1, 0], [1, 0], [0, -1]];
const DIAGONALS = [[-1, -1], [-1, 1], [1, -1], [1, 1]];
const ORTHOGONALS = [[0, -1], [0, 1], [-1, 0], [1, 0]];

export const LEGACY_PROFILE = Object.freeze({
  schemaVersion: 1, id: "observed-sfen-heuristic-v1", policy: "legacy-v1",
});
export const LINEAR_PROFILE = Object.freeze({
  schemaVersion: 1,
  id: "linear-baseline-v1",
  policy: "linear-v1",
  weights: Object.freeze({
    advance: 1.2, centrality: 0.25, promotion: 1.4, drop: 0.25,
    kingMove: -0.35, distance: 0.1, repeat: -1.5,
  }),
  exploration: 0.04,
});

function record(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Return a fresh allowlisted profile, or null. No arbitrary model/code loading. */
export function validateProfile(raw) {
  if (!record(raw) || raw.schemaVersion !== 1
      || typeof raw.id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(raw.id)) return null;
  if (raw.policy === "legacy-v1") {
    return { schemaVersion: 1, id: raw.id, policy: raw.policy };
  }
  if (raw.policy !== "linear-v1" || !record(raw.weights)
      || typeof raw.exploration !== "number" || !Number.isFinite(raw.exploration)
      || raw.exploration < 0 || raw.exploration > 1) return null;
  const weights = {};
  for (const name of FEATURE_NAMES) {
    if (!Object.hasOwn(raw.weights, name) || typeof raw.weights[name] !== "number"
        || !Number.isFinite(raw.weights[name]) || Math.abs(raw.weights[name]) > 10) return null;
    weights[name] = raw.weights[name];
  }
  return { schemaVersion: 1, id: raw.id, policy: raw.policy, weights, exploration: raw.exploration };
}

/**
 * This boundary carries own pieces and own hand only; unknown squares are not
 * empty-square evidence. Adapters must filter site-specific ownership first.
 * Explicit opponent/unknown ownership is also excluded as defence in depth.
 */
export function normalizeObservation(raw) {
  if (!record(raw) || raw.ruleset !== "tsuitate-9x9"
      || !["b", "w"].includes(raw.color) || !["b", "w"].includes(raw.turn)
      || !Number.isSafeInteger(raw.moveNumber) || raw.moveNumber < 1
      || !Array.isArray(raw.pieces) || raw.pieces.length > 81 || !record(raw.hand)) return null;
  const pieces = [];
  const occupied = new Set();
  for (const piece of raw.pieces) {
    if (!record(piece)) return null;
    if ((Object.hasOwn(piece, "owner") && piece.owner !== raw.color)
        || (Object.hasOwn(piece, "color") && piece.color !== raw.color)) continue;
    if (typeof piece.square !== "string" || !SQUARE.test(piece.square) || !ROLES.has(piece.role)
        || occupied.has(piece.square)) return null;
    occupied.add(piece.square);
    pieces.push({ square: piece.square, role: piece.role });
  }
  pieces.sort((a, b) => rank(a.square) - rank(b.square) || file(b.square) - file(a.square));
  const hand = {};
  for (const role of HAND_ROLES) {
    const count = Object.hasOwn(raw.hand, role) ? raw.hand[role] : 0;
    if (!Number.isSafeInteger(count) || count < 0 || count > HAND_LIMITS[role]) return null;
    hand[role] = count;
  }
  const checks = {};
  for (const name of ["inCheck", "opponentInCheck"]) {
    const value = raw[name] ?? null;
    if (value !== null && typeof value !== "boolean") return null;
    checks[name] = value;
  }
  const attemptBudget = raw.attemptBudget ?? null;
  if (attemptBudget !== null && (!Number.isSafeInteger(attemptBudget)
      || attemptBudget < 0 || attemptBudget > 1001)) return null;
  return {
    ruleset: "tsuitate-9x9", color: raw.color, turn: raw.turn,
    moveNumber: raw.moveNumber, pieces, hand, ...checks, attemptBudget,
  };
}

function file(square) { return Number(square[0]); }
function rank(square) { return square.charCodeAt(1) - 96; }
function square(fileNumber, rankNumber) { return `${fileNumber}${String.fromCharCode(96 + rankNumber)}`; }
function inBounds(fileNumber, rankNumber) { return fileNumber >= 1 && fileNumber <= 9 && rankNumber >= 1 && rankNumber <= 9; }
function mustPromote(color, role, rankNumber) {
  const finalRank = color === "b" ? rankNumber === 1 : rankNumber === 9;
  return (["P", "L"].includes(role) && finalRank)
    || (role === "N" && (color === "b" ? rankNumber <= 2 : rankNumber >= 8));
}
function promotionZone(color, rankNumber) { return color === "b" ? rankNumber <= 3 : rankNumber >= 7; }

function movement(role, forward, legacy) {
  if (["G", "+P", "+L", "+N", "+S"].includes(role)) {
    return { steps: GOLD.map(([x, y]) => [x, y * forward]), rays: [] };
  }
  if (role === "P") return { steps: [[0, forward]], rays: [] };
  if (role === "N") return { steps: [[-1, 2 * forward], [1, 2 * forward]], rays: [] };
  if (role === "S") {
    return { steps: [[0, forward], [-1, forward], [1, forward], [-1, -forward], [1, -forward]], rays: [] };
  }
  if (role === "K") return { steps: legacy ? [] : [...ORTHOGONALS, ...DIAGONALS], rays: [] };
  const rays = role === "L" ? [[0, forward]] : role.endsWith("B") ? DIAGONALS : ORTHOGONALS;
  const steps = role === "+B" ? ORTHOGONALS : role === "+R" ? DIAGONALS : [];
  return legacy ? { steps: [...rays, ...steps], rays: [] } : { steps, rays };
}

function candidatesFor(observation, legacy) {
  const occupied = new Set(observation.pieces.map((piece) => piece.square));
  const forward = observation.color === "b" ? -1 : 1;
  const pieces = [...observation.pieces].sort((a, b) => Math.abs(file(a.square) - 5) - Math.abs(file(b.square) - 5)
    || rank(a.square) - rank(b.square) || file(b.square) - file(a.square));
  const candidates = [];
  for (const piece of pieces) {
    const sourceFile = file(piece.square); const sourceRank = rank(piece.square);
    const { steps, rays } = movement(piece.role, forward, legacy);
    const append = (destinationFile, destinationRank) => {
      const destination = square(destinationFile, destinationRank);
      const usi = `${piece.square}${destination}`;
      const mandatory = mustPromote(observation.color, piece.role, destinationRank);
      if (!mandatory) candidates.push({ usi, role: piece.role });
      if (mandatory || (!legacy && !piece.role.startsWith("+") && "PLNSBR".includes(piece.role)
          && (promotionZone(observation.color, sourceRank) || promotionZone(observation.color, destinationRank)))) {
        candidates.push({ usi: `${usi}+`, role: piece.role });
      }
    };
    for (const [dx, dy] of steps) {
      const x = sourceFile + dx; const y = sourceRank + dy;
      if (inBounds(x, y) && !occupied.has(square(x, y))) append(x, y);
    }
    for (const [dx, dy] of rays) {
      for (let step = 1; step <= 8; step += 1) {
        const x = sourceFile + dx * step; const y = sourceRank + dy * step;
        if (!inBounds(x, y) || occupied.has(square(x, y))) break;
        append(x, y);
      }
    }
  }
  if (!legacy) {
    const pawnFiles = new Set(observation.pieces.filter((piece) => piece.role === "P").map((piece) => file(piece.square)));
    for (const role of HAND_ROLES) {
      if (observation.hand[role] === 0) continue;
      for (let y = 1; y <= 9; y += 1) {
        for (let x = 9; x >= 1; x -= 1) {
          const destination = square(x, y);
          if (occupied.has(destination) || mustPromote(observation.color, role, y)
              || (role === "P" && pawnFiles.has(x))) continue;
          candidates.push({ usi: `${role}*${destination}`, role });
        }
      }
    }
  }
  return candidates;
}

/** Necessary check-response geometry, not evidence of an enemy or a legal move. */
function checkResponses(observation, candidates) {
  const kings = observation.pieces.filter((piece) => piece.role === "K");
  if (kings.length !== 1) return candidates;
  const king = kings[0].square;
  const occupied = new Set(observation.pieces.map((piece) => piece.square));
  const forward = observation.color === "b" ? -1 : 1;
  return candidates.filter((candidate) => {
    if (candidate.role === "K") return true;
    const destination = candidate.usi.slice(2, 4);
    const dx = file(destination) - file(king); const dy = rank(destination) - rank(king);
    // A checking knight can only be captured by a move, not blocked by a drop.
    // Its origin is two ranks forward; own pieces between do not obstruct it.
    if (Math.abs(dx) === 1 && dy === 2 * forward) return candidate.usi[1] !== "*";
    if (dx !== 0 && dy !== 0 && Math.abs(dx) !== Math.abs(dy)) return false;
    const distance = Math.max(Math.abs(dx), Math.abs(dy));
    for (let step = 1; step < distance; step += 1) {
      if (occupied.has(square(file(king) + Math.sign(dx) * step,
        rank(king) + Math.sign(dy) * step))) return false;
    }
    return true;
  });
}

/** Shorten a rejected ray using only current own geometry and foul feedback. */
function shortRayRetries(observation, candidates, forbidden) {
  if (observation.inCheck !== false) return [];
  const kings = observation.pieces.filter((piece) => piece.role === "K");
  if (kings.length !== 1) return [];
  const king = kings[0].square;
  const forward = observation.color === "b" ? -1 : 1;
  const rejectedPaths = new Set(candidates.filter((candidate) => forbidden.has(candidate.usi))
    .map((candidate) => candidate.usi.replace(/\+$/, "")));
  const adjacent = new Set();
  for (const candidate of candidates) {
    if (!forbidden.has(candidate.usi) || candidate.usi[1] === "*") continue;
    const from = candidate.usi.slice(0, 2); const to = candidate.usi.slice(2, 4);
    const dx = file(to) - file(from); const dy = rank(to) - rank(from);
    const distance = Math.max(Math.abs(dx), Math.abs(dy));
    if (distance < 2 || !movement(candidate.role, forward, false).rays
      .some(([x, y]) => dx === x * distance && dy === y * distance)) continue;
    // A source on a king ray may be shielding a hidden attacker. Do not
    // prescribe another move of that piece based on a possible blocker alone.
    const kingDx = file(from) - file(king); const kingDy = rank(from) - rank(king);
    if (kingDx === 0 || kingDy === 0 || Math.abs(kingDx) === Math.abs(kingDy)) continue;
    const shorter = from + square(file(from) + Math.sign(dx), rank(from) + Math.sign(dy));
    if (!rejectedPaths.has(shorter)) adjacent.add(shorter);
  }
  return candidates.filter((candidate) => !forbidden.has(candidate.usi)
    && adjacent.has(candidate.usi.replace(/\+$/, "")));
}

function validRecentMoves(raw) {
  return Array.isArray(raw) ? raw.slice(-64).filter((move) => typeof move === "string" && USI_MOVE.test(move)) : [];
}

function features(observation, candidate, recentMoves) {
  const drop = candidate.usi[1] === "*";
  const from = candidate.usi.slice(0, 2); const to = candidate.usi.slice(2, 4);
  return {
    advance: drop ? 0 : (rank(to) - rank(from)) * (observation.color === "b" ? -1 : 1) / 8,
    centrality: (4 - Math.abs(file(to) - 5)) / 4,
    promotion: candidate.usi.endsWith("+") ? 1 : 0,
    drop: drop ? 1 : 0,
    kingMove: candidate.role === "K" ? 1 : 0,
    distance: drop ? 0 : Math.max(Math.abs(file(to) - file(from)), Math.abs(rank(to) - rank(from))) / 8,
    repeat: recentMoves.includes(candidate.usi) ? 1 : 0,
  };
}

/** Recompute features from observation, never from untrusted game-log scores. */
export function featuresForMove(rawObservation, usi, recentMoves = []) {
  const observation = normalizeObservation(rawObservation);
  if (!observation || observation.turn !== observation.color || typeof usi !== "string") return null;
  const candidate = candidatesFor(observation, false).find((item) => item.usi === usi);
  return candidate ? features(observation, candidate, validRecentMoves(recentMoves)) : null;
}

function hash(text) {
  return [...text].reduce((value, char) => (value * 33 + char.charCodeAt(0)) >>> 0, 5381);
}

/**
 * These candidates satisfy visible own-piece constraints only. Hidden blockers,
 * enemy attacks and pawn-drop mate remain the site's legality responsibility.
 */
export function chooseMove(rawObservation, { profile = LINEAR_PROFILE, seed = "", recentMoves = [], forbiddenMoves = [] } = {}) {
  const observation = normalizeObservation(rawObservation);
  const selectedProfile = validateProfile(profile);
  if (!observation || observation.turn !== observation.color || observation.attemptBudget === 0 || !selectedProfile
      || typeof seed !== "string" || seed.length > 512 || !Array.isArray(forbiddenMoves)
      || forbiddenMoves.length > 4096) return null;
  const recent = validRecentMoves(recentMoves);
  const rejected = new Set(forbiddenMoves.filter((move) => typeof move === "string" && USI_MOVE.test(move)));
  const forbidden = new Set(rejected);
  const legacy = selectedProfile.policy === "legacy-v1";
  if (legacy && recent.length) forbidden.add(recent.at(-1));
  // Probe escapes only while another attempt can follow a foul. Otherwise rank
  // possible blocks/captures and king moves, not unrelated attacking advances.
  const prioritizeEscapes = observation.inCheck === true
    && (observation.attemptBudget === null || observation.attemptBudget > 1);
  const generated = candidatesFor(observation, legacy && observation.inCheck !== true);
  // Both visibly valid promotion variants have the same path, destination
  // occupancy and own-king safety. A foul on either rules out that path here.
  // Invalid variants (outside-zone or missing mandatory promotion) establish
  // nothing about the valid move and must not exclude it.
  const rejectedPaths = new Set(generated.filter((candidate) => rejected.has(candidate.usi))
    .map((candidate) => candidate.usi.replace(/\+$/, "")));
  const available = generated.filter((candidate) => !forbidden.has(candidate.usi)
    && !rejectedPaths.has(candidate.usi.replace(/\+$/, "")));
  const escapes = prioritizeEscapes
    ? available.filter((candidate) => candidate.role === "K") : [];
  const responses = observation.inCheck === true ? checkResponses(observation, available) : available;
  const retries = shortRayRetries(observation, generated, forbidden);
  // Preserve the existing fallback for incomplete own-king observations or
  // exhausted response candidates; geometry cannot prove mate or legality.
  const candidates = escapes.length ? escapes : retries.length ? retries : responses.length ? responses : available;
  if (!candidates.length) return null;
  const scored = candidates.map((candidate) => {
    const values = features(observation, candidate, recent);
    const score = legacy ? 0 : FEATURE_NAMES.reduce((sum, name) => sum + values[name] * selectedProfile.weights[name], 0);
    return { usi: candidate.usi, features: values, score };
  });
  let selected;
  if (legacy || hash(`${seed}:explore`) / 2 ** 32 < selectedProfile.exploration) {
    selected = scored[hash(seed) % scored.length];
  } else {
    const maximum = Math.max(...scored.map((candidate) => candidate.score));
    const best = scored.filter((candidate) => candidate.score === maximum);
    selected = best[hash(seed) % best.length];
  }
  return {
    ...selected, brainVersion: BRAIN_VERSION, profileId: selectedProfile.id,
    candidateCount: candidates.length,
  };
}
