const CSA_TYPES = {
  P: "FU",
  L: "KY",
  N: "KE",
  S: "GI",
  G: "KI",
  B: "KA",
  R: "HI",
  K: "OU",
};

const PROMOTED_CSA = {
  P: "TO",
  L: "NY",
  N: "NK",
  S: "NG",
  B: "UM",
  R: "RY",
};

const GOLD_STEPS = [
  [0, 1], [-1, 1], [1, 1], [-1, 0], [1, 0], [0, -1],
];

/** Parse the visible portion of SFEN. Empty squares are deliberately treated as unknown. */
export function parseVisibleSfen(sfen) {
  if (typeof sfen !== "string" || sfen.length > 160) return null;
  const fields = sfen.trim().split(/\s+/);
  if (fields.length !== 4 || !/^[bw]$/.test(fields[1])) return null;
  if (!/^(?:-|(?:(?:[1-9]\d*)?[PLNSGBRplnsgbr])*)$/.test(fields[2])) return null;
  if (!/^[1-9]\d*$/.test(fields[3])) return null;

  const rows = fields[0].split("/");
  if (rows.length !== 9) return null;
  const board = new Map();
  for (let rankIndex = 0; rankIndex < rows.length; rankIndex += 1) {
    const row = rows[rankIndex];
    let file = 9;
    for (let index = 0; index < row.length; index += 1) {
      const char = row[index];
      if (/^[1-9]$/.test(char)) {
        file -= Number(char);
        if (file < 0) return null;
        continue;
      }
      let promoted = false;
      let letter = char;
      if (char === "+") {
        promoted = true;
        index += 1;
        letter = row[index];
      }
      if (typeof letter !== "string" || !/^[PLNSGBRKplnsgbrk]$/.test(letter) || file < 1) return null;
      const type = letter.toUpperCase();
      if (promoted && !PROMOTED_CSA[type]) return null;
      board.set(`${file}${rankIndex + 1}`, {
        owner: letter === type ? "b" : "w",
        type,
        promoted,
      });
      file -= 1;
    }
    if (file !== 0) return null;
  }
  return { board, turn: fields[1], moveNumber: Number(fields[3]) };
}

function mandatoryPromotion(color, type, rank) {
  if (color === "b") {
    return (type === "P" || type === "L") ? rank === 1 : type === "N" && rank <= 2;
  }
  return (type === "P" || type === "L") ? rank === 9 : type === "N" && rank >= 8;
}

function isGoldMover(piece) {
  return piece.promoted && ["P", "L", "N", "S"].includes(piece.type);
}

function stepsFor(piece, forward) {
  if (isGoldMover(piece) || piece.type === "G") return GOLD_STEPS.map(([x, y]) => [x, y * forward]);
  if (piece.promoted && piece.type === "B") {
    return [[-1, -1], [-1, 1], [1, -1], [1, 1], [0, -1], [0, 1], [-1, 0], [1, 0]];
  }
  if (piece.promoted && piece.type === "R") {
    return [[0, -1], [0, 1], [-1, 0], [1, 0], [-1, -1], [-1, 1], [1, -1], [1, 1]];
  }
  switch (piece.type) {
    case "P":
    case "L": return [[0, forward]];
    case "N": return [[-1, 2 * forward], [1, 2 * forward]];
    case "S": return [[0, forward], [-1, forward], [1, forward], [-1, -forward], [1, -forward]];
    case "B": return [[-1, -1], [-1, 1], [1, -1], [1, 1]];
    case "R": return [[0, -1], [0, 1], [-1, 0], [1, 0]];
    // King moves are intentionally omitted: the opponent's attack map is hidden.
    default: return [];
  }
}

function toMove(color, fromFile, fromRank, toFile, toRank, piece) {
  const type = piece.promoted
    ? PROMOTED_CSA[piece.type] ?? CSA_TYPES[piece.type]
    : mandatoryPromotion(color, piece.type, toRank)
      ? PROMOTED_CSA[piece.type]
      : CSA_TYPES[piece.type];
  return `${color === "b" ? "+" : "-"}${fromFile}${fromRank}${toFile}${toRank}${type}`;
}

function lastOwnMove(recentOwnMoves) {
  for (let index = recentOwnMoves.length - 1; index >= 0; index -= 1) {
    if (typeof recentOwnMoves[index] === "string") return recentOwnMoves[index];
  }
  return null;
}

/**
 * A bounded, deterministic, low-cost move picker. It uses only pieces exposed
 * in the player's SFEN. It does not assert full legality in a hidden position.
 */
export function chooseObservedMove({ sfen, color, gameId, ply, recentOwnMoves = [], forbiddenMoves = [] }) {
  const position = parseVisibleSfen(sfen);
  if (!position || position.turn !== color) return null;

  const forward = color === "b" ? -1 : 1;
  const moves = [];
  const ownPieces = [...position.board.entries()]
    .filter(([, piece]) => piece.owner === color && piece.type !== "K")
    .sort(([a], [b]) => {
      const af = Number(a[0]); const bf = Number(b[0]);
      return Math.abs(af - 5) - Math.abs(bf - 5) || Number(a[1]) - Number(b[1]) || bf - af;
    });

  for (const [square, piece] of ownPieces) {
    const fromFile = Number(square[0]);
    const fromRank = Number(square[1]);
    for (const [fileDelta, rankDelta] of stepsFor(piece, forward)) {
      const toFile = fromFile + fileDelta;
      const toRank = fromRank + rankDelta;
      if (toFile < 1 || toFile > 9 || toRank < 1 || toRank > 9) continue;
      const occupied = position.board.get(`${toFile}${toRank}`);
      if (occupied?.owner === color) continue;
      moves.push(toMove(color, fromFile, fromRank, toFile, toRank, piece));
    }
  }

  if (moves.length === 0) return null;
  const previous = lastOwnMove(recentOwnMoves);
  const forbidden = new Set([...forbiddenMoves, previous].filter(Boolean));
  const fresh = moves.filter((move) => !forbidden.has(move));
  const pool = fresh.length > 0 ? fresh : moves;
  const seed = [...`${gameId}:${ply}`].reduce((value, char) => (value * 33 + char.charCodeAt(0)) >>> 0, 5381);
  return pool[seed % pool.length];
}
