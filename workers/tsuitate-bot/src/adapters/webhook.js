import { chooseMove, LEGACY_PROFILE, normalizeObservation } from "../brain/index.js";

const CSA = { P: "FU", L: "KY", N: "KE", S: "GI", G: "KI", B: "KA", R: "HI", K: "OU",
  "+P": "TO", "+L": "NY", "+N": "NK", "+S": "NG", "+B": "UM", "+R": "RY" };
const FROM_CSA = Object.fromEntries(Object.entries(CSA).map(([usi, csa]) => [csa, usi]));
const rankToUsi = (value) => String.fromCharCode(96 + Number(value));
const rankToCsa = (value) => String(value.charCodeAt(0) - 96);

/** Compatibility parser: board keys retain CSA numeric ranks. Empty means unknown. */
export function parseVisibleSfen(sfen) {
  if (typeof sfen !== "string" || sfen.length > 160) return null;
  const fields = sfen.trim().split(/\s+/);
  if (fields.length !== 4 || !/^[bw]$/.test(fields[1])
      || !/^(?:-|(?:(?:[1-9]\d*)?[PLNSGBRplnsgbr])+)$/.test(fields[2])
      || !/^[1-9]\d*$/.test(fields[3]) || !Number.isSafeInteger(Number(fields[3]))) return null;
  const rows = fields[0].split("/");
  if (rows.length !== 9) return null;
  const board = new Map();
  for (let rank = 0; rank < rows.length; rank += 1) {
    let file = 9;
    for (let index = 0; index < rows[rank].length; index += 1) {
      const char = rows[rank][index];
      if (/^[1-9]$/.test(char)) {
        file -= Number(char);
        if (file < 0) return null;
        continue;
      }
      let promoted = false; let letter = char;
      if (char === "+") { promoted = true; index += 1; letter = rows[rank][index]; }
      if (typeof letter !== "string" || !/^[PLNSGBRKplnsgbrk]$/.test(letter) || file < 1) return null;
      const type = letter.toUpperCase();
      if (promoted && !CSA[`+${type}`]) return null;
      board.set(`${file}${rank + 1}`, { owner: letter === type ? "b" : "w", type, promoted });
      file -= 1;
    }
    if (file !== 0) return null;
  }
  // The public compatibility return shape is unchanged. Hand parsing is adapter-private.
  return { board, turn: fields[1], moveNumber: Number(fields[3]) };
}

/** Only own pieces and own hand cross the brain boundary, even if SFEN includes both sides. */
export function observationFromWebhook({ sfen, color, inCheck = null, opponentInCheck = null, attemptBudget = null }) {
  const parsed = parseVisibleSfen(sfen);
  if (!parsed || !["b", "w"].includes(color)) return null;
  const pieces = [...parsed.board].filter(([, piece]) => piece.owner === color)
    .map(([square, piece]) => ({
      square: `${square[0]}${rankToUsi(square[1])}`, role: `${piece.promoted ? "+" : ""}${piece.type}`,
    }));
  const hand = {};
  for (const match of sfen.trim().split(/\s+/)[2].matchAll(/([1-9]\d*)?([PLNSGBRplnsgbr])/g)) {
    const role = match[2].toUpperCase();
    if ((match[2] === role ? "b" : "w") === color) hand[role] = (hand[role] ?? 0) + Number(match[1] ?? 1);
  }
  return normalizeObservation({
    ruleset: "tsuitate-9x9", color, turn: parsed.turn, moveNumber: parsed.moveNumber,
    pieces, hand, inCheck, opponentInCheck, attemptBudget,
  });
}

export function usiToCsa(usi, rawObservation) {
  const observation = normalizeObservation(rawObservation);
  if (!observation || typeof usi !== "string") return null;
  const sign = observation.color === "b" ? "+" : "-";
  const drop = /^([PLNSGBR])\*([1-9])([a-i])$/.exec(usi);
  if (drop) return `${sign}00${drop[2]}${rankToCsa(drop[3])}${CSA[drop[1]]}`;
  const move = /^([1-9])([a-i])([1-9])([a-i])(\+?)$/.exec(usi);
  if (!move) return null;
  const piece = observation.pieces.find((item) => item.square === `${move[1]}${move[2]}`);
  if (!piece) return null;
  const role = move[5] ? `+${piece.role}` : piece.role;
  return CSA[role] ? `${sign}${move[1]}${rankToCsa(move[2])}${move[3]}${rankToCsa(move[4])}${CSA[role]}` : null;
}

/** Current own source role disambiguates an already-promoted move from promotion. */
export function csaToUsi(csa, rawObservation) {
  const observation = normalizeObservation(rawObservation);
  if (!observation || typeof csa !== "string" || csa[0] !== (observation.color === "b" ? "+" : "-")) return null;
  const drop = /^[+-]00([1-9])([1-9])(FU|KY|KE|GI|KI|KA|HI)$/.exec(csa);
  if (drop) return `${FROM_CSA[drop[3]]}*${drop[1]}${rankToUsi(drop[2])}`;
  const move = /^[+-]([1-9])([1-9])([1-9])([1-9])(FU|KY|KE|GI|KI|KA|HI|OU|TO|NY|NK|NG|UM|RY)$/.exec(csa);
  if (!move) return null;
  const source = `${move[1]}${rankToUsi(move[2])}`;
  const piece = observation.pieces.find((item) => item.square === source);
  const resultRole = FROM_CSA[move[5]];
  if (!piece || (resultRole !== piece.role && resultRole !== `+${piece.role}`)) return null;
  return `${source}${move[3]}${rankToUsi(move[4])}${resultRole === piece.role ? "" : "+"}`;
}

/** Decode only the viewer's public last-move information, never enemy SFEN. */
export function checksFromLastMove(position, color) {
  const ownSign = color === "b" ? "+" : "-";
  const sign = position?.lastMove?.[0];
  const info = position?.lastInfo;
  if (!["+", "-"].includes(sign) || ![0, 1, 2, 3].includes(info)) {
    return { inCheck: null, opponentInCheck: null };
  }
  if (info === 0) return { inCheck: false, opponentInCheck: false };
  const checked = info === 2 || info === 3;
  const own = sign === ownSign;
  return info === 3
    ? { inCheck: own ? false : true, opponentInCheck: own ? true : false }
    : { inCheck: own ? checked : null, opponentInCheck: own ? null : checked };
}

/** Viewer fouls are remaining allowances: zero still permits one final attempt. */
export function attemptBudgetFromWebhook(position, color) {
  const remaining = position?.fouls?.[color];
  if (remaining === undefined) return null;
  return Number.isInteger(remaining) && remaining >= 0 && remaining <= 1000 ? remaining + 1 : -1;
}

export function chooseWebhookDecision({ sfen, color, gameId, ply, inCheck = null, opponentInCheck = null, attemptBudget = null, recentOwnMoves = [], forbiddenMoves = [], profile = LEGACY_PROFILE }) {
  const observation = observationFromWebhook({ sfen, color, inCheck, opponentInCheck, attemptBudget });
  if (!observation) return null;
  // Legacy considered only the last string, even if it cannot match a current candidate.
  const history = profile?.policy === "legacy-v1"
    ? recentOwnMoves.filter((move) => typeof move === "string").slice(-1) : recentOwnMoves;
  // The viewer caller supplies confirmed same-position foul attempts here.
  const rejected = forbiddenMoves.map((move) => csaToUsi(move, observation)).filter(Boolean);
  const decision = chooseMove(observation, {
    profile, seed: `${gameId}:${ply}`,
    recentMoves: history.map((move) => csaToUsi(move, observation)).filter(Boolean),
    forbiddenMoves: rejected, foulMoves: rejected,
  });
  if (!decision) return null;
  const move = usiToCsa(decision.usi, observation);
  return move ? { move, decision } : null;
}
