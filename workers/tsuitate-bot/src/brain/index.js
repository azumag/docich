/** Site-independent, visible-information-only Tsuitate move selection. */
export const BRAIN_VERSION = "tsuitate-brain-v11";
const ROLES = new Set(["P", "L", "N", "S", "G", "B", "R", "K", "+P", "+L", "+N", "+S", "+B", "+R"]);
const HAND_ROLES = ["P", "L", "N", "S", "G", "B", "R"];
const HAND_LIMITS = { P: 18, L: 4, N: 4, S: 4, G: 4, B: 2, R: 2 };
// Own-view capture evidence: a square our own piece left without our move now
// holds an opponent piece. `age` counts our turns since that observation.
const KNOWN_ENEMY_LIMIT = 40;
const RECAPTURE_AGE_LIMIT = 2;
// A long move crosses squares we cannot see, so it can be blocked and foul.
// This only lowers the ordering score; it never claims a move is legal or not.
const PATH_RISK_WEIGHT = 0.25;
// While checked, a king escape is worth probing only while some way out still
// looks sheltered. When every escape scores at least this exposure, probing
// them all spends the foul budget one certain foul at a time; blocks and
// captures that address the check geometry are tried first instead. Ordering
// only: neither threshold claims any move is legal.
const ESCAPE_EXPOSURE_LIMIT = 18;
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
  const knownEnemies = [];
  if (Object.hasOwn(raw, "knownEnemies")) {
    if (!Array.isArray(raw.knownEnemies) || raw.knownEnemies.length > KNOWN_ENEMY_LIMIT) return null;
    const freshest = new Map();
    for (const item of raw.knownEnemies) {
      if (!record(item) || typeof item.square !== "string" || !SQUARE.test(item.square)
          || !Number.isSafeInteger(item.age) || item.age < 0 || item.age > 999) return null;
      if (!freshest.has(item.square) || item.age < freshest.get(item.square)) freshest.set(item.square, item.age);
    }
    for (const [key, age] of freshest) knownEnemies.push({ square: key, age });
  }
  return {
    ruleset: "tsuitate-9x9", color: raw.color, turn: raw.turn,
    moveNumber: raw.moveNumber, pieces, hand, ...checks, attemptBudget, knownEnemies,
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
  // Capture evidence marks occupied squares: a drop there is a certain foul, so
  // it never enters the candidate set. Moves keep every own-piece candidate.
  const knownEnemy = new Set(observation.knownEnemies.map((item) => item.square));
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
        // 証拠のある相手駒のマスは、その先へは通れない。そのマス自体への
        // 着手は捕獲として成立するので到達してから射線を止める。
        if (knownEnemy.has(square(x, y))) break;
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
          if (occupied.has(destination) || knownEnemy.has(destination) || mustPromote(observation.color, role, y)
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

/**
 * Visible-only exposure of a possible king escape: how many unknown squares
 * have an unobstructed line to the destination. Own pieces block and never
 * count as attackers, and no enemy piece is assumed. Unknown squares on our
 * own king's rays count double: the check must come from one of them, so they
 * are the most plausible hidden attackers. This orders candidates only; it
 * never claims a destination is legal.
 */
function escapeExposure(observation, king, destination) {
  const own = new Set(observation.pieces.map((piece) => piece.square));
  const tf = file(destination);
  const tr = rank(destination);
  const kf = file(king);
  const kr = rank(king);
  const weight = (key) => {
    const dx = file(key) - kf;
    const dy = rank(key) - kr;
    if (dx !== 0 && dy !== 0 && Math.abs(dx) !== Math.abs(dy)) return 1;
    const distance = Math.max(Math.abs(dx), Math.abs(dy));
    for (let step = 1; step < distance; step += 1) {
      if (own.has(square(kf + Math.sign(dx) * step, kr + Math.sign(dy) * step))) return 1;
    }
    return 2;
  };
  let count = 0;
  for (const [dx, dy] of [...ORTHOGONALS, ...DIAGONALS]) {
    let x = tf;
    let y = tr;
    for (let step = 1; step <= 8; step += 1) {
      x += dx;
      y += dy;
      if (!inBounds(x, y)) break;
      const key = square(x, y);
      if (own.has(key)) break;
      count += weight(key);
    }
  }
  for (const [dx, dy] of [...ORTHOGONALS, ...DIAGONALS, [-1, -2], [1, -2], [-1, 2], [1, 2]]) {
    const x = tf + dx;
    const y = tr + dy;
    if (inBounds(x, y) && !own.has(square(x, y))) count += weight(square(x, y));
  }
  return count;
}

/** Shorten a rejected ray using only current own geometry and foul feedback. */
function shortRayRetries(observation, candidates, rejected, forbidden) {
  if (observation.inCheck !== false) return [];
  const kings = observation.pieces.filter((piece) => piece.role === "K");
  if (kings.length !== 1) return [];
  const king = kings[0].square;
  const forward = observation.color === "b" ? -1 : 1;
  const rejectedPaths = new Set(candidates.filter((candidate) => rejected.has(candidate.usi))
    .map((candidate) => candidate.usi.replace(/\+$/, "")));
  const adjacent = new Set();
  for (const candidate of candidates) {
    if (!rejected.has(candidate.usi) || candidate.usi[1] === "*") continue;
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

/**
 * How many squares we cannot account for lie strictly between source and
 * destination. Own pieces and capture evidence are known; every other square on
 * the ray may hide an opponent piece, which would make the move a blocked foul.
 * This is a score penalty for ordering only, not a legality decision: the trial
 * still goes to the server, which owns the board.
 */
function pathRisk(observation, candidate) {
  if (candidate.usi[1] === "*") return 0;
  const from = candidate.usi.slice(0, 2);
  const to = candidate.usi.slice(2, 4);
  const dx = file(to) - file(from);
  const dy = rank(to) - rank(from);
  if ((dx === 0 && dy === 0) || (dx !== 0 && dy !== 0 && Math.abs(dx) !== Math.abs(dy))) return 0;
  const distance = Math.max(Math.abs(dx), Math.abs(dy));
  if (distance < 2) return 0;
  const occupied = new Set(observation.pieces.map((piece) => piece.square));
  let unknown = 0;
  for (let step = 1; step < distance; step += 1) {
    const crossing = square(file(from) + Math.sign(dx) * step, rank(from) + Math.sign(dy) * step);
    if (!occupied.has(crossing)) unknown += 1;
  }
  return unknown * PATH_RISK_WEIGHT;
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
export function chooseMove(rawObservation, { profile = LINEAR_PROFILE, seed = "", recentMoves = [], forbiddenMoves = [], foulMoves = [] } = {}) {
  const observation = normalizeObservation(rawObservation);
  const selectedProfile = validateProfile(profile);
  if (!observation || observation.turn !== observation.color || observation.attemptBudget === 0 || !selectedProfile
      || typeof seed !== "string" || seed.length > 512 || !Array.isArray(forbiddenMoves)
      || forbiddenMoves.length > 4096 || !Array.isArray(foulMoves) || foulMoves.length > 4096) return null;
  const recent = validRecentMoves(recentMoves);
  // Attempts with an unknown/error ACK stay excluded without supplying legality
  // evidence. Only confirmed fouls can rule out sibling moves or shorten rays.
  const rejected = new Set(foulMoves.filter((move) => typeof move === "string" && USI_MOVE.test(move)));
  const forbidden = new Set([...rejected,
    ...forbiddenMoves.filter((move) => typeof move === "string" && USI_MOVE.test(move))]);
  const legacy = selectedProfile.policy === "legacy-v1";
  if (legacy && recent.length) forbidden.add(recent.at(-1));
  const generated = candidatesFor(observation, legacy && observation.inCheck !== true);
  // Both visibly valid promotion variants have the same path, destination
  // occupancy and own-king safety. A foul on either rules out that path here.
  // Invalid variants (outside-zone or missing mandatory promotion) establish
  // nothing about the valid move and must not exclude it.
  const rejectedPaths = new Set(generated.filter((candidate) => rejected.has(candidate.usi))
    .map((candidate) => candidate.usi.replace(/\+$/, "")));
  const available = generated.filter((candidate) => !forbidden.has(candidate.usi)
    && !rejectedPaths.has(candidate.usi.replace(/\+$/, "")));
  // Probe escapes only while another attempt can follow a foul, and only while
  // some way out still looks sheltered. Otherwise rank possible blocks and
  // captures first, not unrelated attacking advances.
  const canProbeEscapes = observation.inCheck === true
    && (observation.attemptBudget === null || observation.attemptBudget > 1);
  const escapeCandidates = canProbeEscapes ? available.filter((candidate) => candidate.role === "K") : [];
  const checkedKing = canProbeEscapes ? observation.pieces.find((piece) => piece.role === "K") : null;
  const escapeMinExposure = (escapeCandidates.length && checkedKing)
    ? Math.min(...escapeCandidates.map((candidate) =>
      escapeExposure(observation, checkedKing.square, candidate.usi.slice(2, 4))))
    : Infinity;
  const prioritizeEscapes = canProbeEscapes && escapeMinExposure < ESCAPE_EXPOSURE_LIMIT;
  const escapes = prioritizeEscapes ? escapeCandidates : [];
  const responses = observation.inCheck === true ? checkResponses(observation, available) : available;
  const retries = shortRayRetries(observation, generated, rejected, forbidden);
  // Capture evidence outranks the ray-shortening heuristic: an own piece that
  // vanished without our move proves an opponent piece on that square, so a
  // move there recaptures it (or reaches an empty square without a foul) while
  // a drop there would foul. Never act on it during check: a recapture that
  // fails to resolve the check is a certain foul, and the king escape is not.
  const recaptureAge = new Map(observation.knownEnemies.map((item) => [item.square, item.age]));
  const recaptures = observation.inCheck === true ? [] : available.filter((candidate) => {
    if (candidate.usi[1] === "*") return false;
    const age = recaptureAge.get(candidate.usi.slice(2, 4));
    return age !== undefined && age <= RECAPTURE_AGE_LIMIT;
  });
  // While checked, fresh capture evidence on a response line is a plausible
  // checker: a move there can capture it, while a drop can only block. These
  // moves are ordered ahead of the escapes; the evidence never claims legality.
  const evidenceResponses = observation.inCheck === true ? responses.filter((candidate) => {
    if (candidate.role === "K" || candidate.usi[1] === "*") return false;
    const age = recaptureAge.get(candidate.usi.slice(2, 4));
    return age !== undefined && age <= RECAPTURE_AGE_LIMIT;
  }) : [];
  // Preserve the existing fallback for incomplete own-king observations or
  // exhausted response candidates; geometry cannot prove mate or legality.
  const candidates = recaptures.length ? recaptures
    : escapes.length ? [...evidenceResponses, ...escapes]
      : retries.length ? retries : responses.length ? responses : available;
  if (!candidates.length) return null;
  const scored = candidates.map((candidate) => {
    const values = features(observation, candidate, recent);
    const score = legacy ? 0
      : FEATURE_NAMES.reduce((sum, name) => sum + values[name] * selectedProfile.weights[name], 0)
        - pathRisk(observation, candidate);
    return { usi: candidate.usi, role: candidate.role, features: values, score, priority: 0 };
  });
  // 王手時は、新鮮な証拠（自分の駒が消えたマス）への幾何応手を先頭にする。
  // そこが実際の王手駒なら捕獲で王手が解ける。打駒は捕獲できないので含めない。
  if (observation.inCheck === true && evidenceResponses.length) {
    const evidenceAge = new Map(observation.knownEnemies.map((item) => [item.square, item.age]));
    const evidenceSet = new Set(evidenceResponses.map((candidate) => candidate.usi));
    scored.filter((candidate) => evidenceSet.has(candidate.usi))
      .sort((a, b) => (evidenceAge.get(a.usi.slice(2, 4)) - evidenceAge.get(b.usi.slice(2, 4)))
        || b.score - a.score)
      .forEach((candidate, index) => { candidate.priority = 100 - index; });
  }
  // 脱出候補がどれも露出過多（閾値以上）のときは、ブロック・捕獲になり得る
  // 移動を先に試し、玉の移動は後ろに回す。残り予算が少ないときは後回しに
  // しない（合法な玉脱出が応手の後ろに隠れたまま試行を使い切るのを避ける）。
  if (observation.inCheck === true && !prioritizeEscapes
      && (observation.attemptBudget === null || observation.attemptBudget >= 3)
      && scored.some((candidate) => candidate.role !== "K")) {
    for (const candidate of scored) if (candidate.role === "K") candidate.priority = -1;
  }
  // 王手中は玉の脱出候補を露出度の低い順に試す。特徴量スコアは前進を
  // 好むため、そのままでは隠れた駒の多い方向へ玉を運び反則になる。
  if (prioritizeEscapes && checkedKing) {
    scored.filter((candidate) => candidate.role === "K")
      .map((candidate) => ({ candidate, exposure: escapeExposure(observation, checkedKing.square, candidate.usi.slice(2, 4)) }))
      .sort((a, b) => a.exposure - b.exposure || b.candidate.score - a.candidate.score)
      .forEach((entry, index) => { entry.candidate.priority = -index; });
  }
  const topPriority = Math.max(...scored.map((candidate) => candidate.priority));
  const pool = scored.filter((candidate) => candidate.priority === topPriority);
  let selected;
  if (legacy || hash(`${seed}:explore`) / 2 ** 32 < selectedProfile.exploration) {
    selected = pool[hash(seed) % pool.length];
  } else {
    const maximum = Math.max(...pool.map((candidate) => candidate.score));
    const best = pool.filter((candidate) => candidate.score === maximum);
    selected = best[hash(seed) % best.length];
  }
  return {
    usi: selected.usi, features: selected.features, score: selected.score,
    brainVersion: BRAIN_VERSION, profileId: selectedProfile.id, candidateCount: candidates.length,
  };
}
