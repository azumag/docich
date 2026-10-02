// The current picker has only been scoped against the ordinary Tsuitate rules.
// Other modes stay safely rejected until their rule-specific behavior is tested.
export const SUPPORTED_GAME_TYPES = new Set(["ついたて"]);
export const MAX_BODY_BYTES = 256 * 1024;

const CSA_PIECE = /^(?:FU|KY|KE|GI|KI|KA|HI)$/;
const CSA_MOVE = /^[+-](?:[1-9]{4}(?:FU|KY|KE|GI|KI|KA|HI|OU|TO|NY|NK|NG|UM|RY)|00[1-9]{2}(?:FU|KY|KE|GI|KI|KA|HI)|00(?:00|[1-9]{2})ZZ|0000TORYO)$/;

export class ProtocolFault extends Error {
  constructor(status, code) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

function record(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function visibleId(value, max) {
  return typeof value === "string" && value.length > 0 && value.length <= max && /^[\x21-\x7e]+$/.test(value);
}

function int(value, min, max) {
  return Number.isSafeInteger(value) && value >= min && value <= max;
}

function parseSfen(sfen) {
  if (typeof sfen !== "string" || sfen.length > 160) throw new ProtocolFault(400, "invalid_sfen");
  const fields = sfen.trim().split(/\s+/);
  if (fields.length !== 4 || !/^[bw]$/.test(fields[1]) || !/^[1-9]\d*$/.test(fields[3])) {
    throw new ProtocolFault(400, "invalid_sfen");
  }
  if (!(fields[2] === "-" || (/^(?:(?:[1-9]\d*)?[PLNSGBRplnsgbr])+$/.test(fields[2])))) {
    throw new ProtocolFault(400, "invalid_sfen");
  }
  const rows = fields[0].split("/");
  if (rows.length !== 9) throw new ProtocolFault(400, "invalid_sfen");
  for (const row of rows) {
    let cells = 0;
    for (let i = 0; i < row.length; i += 1) {
      const char = row[i];
      if (/^[1-9]$/.test(char)) cells += Number(char);
      else {
        let piece = char;
        if (char === "+") {
          i += 1;
          piece = row[i];
          if (!/^[PLNSBRplnsbr]$/.test(piece)) throw new ProtocolFault(400, "invalid_sfen");
        } else if (!/^[PLNSGBRKplnsgbrk]$/.test(piece)) {
          throw new ProtocolFault(400, "invalid_sfen");
        }
        cells += 1;
      }
      if (cells > 9) throw new ProtocolFault(400, "invalid_sfen");
    }
    if (cells !== 9) throw new ProtocolFault(400, "invalid_sfen");
  }
  return { turn: fields[1] };
}

function parsePosition(value) {
  if (!record(value)) throw new ProtocolFault(400, "invalid_position");
  const normalized = {};
  if (typeof value.sfen !== "string") throw new ProtocolFault(400, "invalid_position");
  parseSfen(value.sfen);
  normalized.sfen = value.sfen;

  if (value.lastMove !== undefined) {
    if (typeof value.lastMove !== "string" || !CSA_MOVE.test(value.lastMove)) throw new ProtocolFault(400, "invalid_position");
    normalized.lastMove = value.lastMove;
  }
  if (value.lastInfo !== undefined) {
    if (!int(value.lastInfo, 0, 4)) throw new ProtocolFault(400, "invalid_position");
    normalized.lastInfo = value.lastInfo;
  }
  if (value.lastCapture !== undefined) {
    if (typeof value.lastCapture !== "string" || !CSA_PIECE.test(value.lastCapture)) throw new ProtocolFault(400, "invalid_position");
    normalized.lastCapture = value.lastCapture;
  }
  if (value.wasPromotion !== undefined) {
    if (typeof value.wasPromotion !== "boolean") throw new ProtocolFault(400, "invalid_position");
    normalized.wasPromotion = value.wasPromotion;
  }

  for (const field of ["fouls", "times", "byoyomiActive"]) {
    if (value[field] === undefined) continue;
    if (!record(value[field]) || !("b" in value[field]) || !("w" in value[field])) throw new ProtocolFault(400, "invalid_position");
    const pair = {};
    for (const color of ["b", "w"]) {
      const item = value[field][color];
      if (field === "fouls" && !int(item, 0, 1000)) throw new ProtocolFault(400, "invalid_position");
      if (field === "times" && (typeof item !== "number" || !Number.isFinite(item) || item < 0 || item > 86400)) {
        throw new ProtocolFault(400, "invalid_position");
      }
      if (field === "byoyomiActive" && typeof item !== "boolean") throw new ProtocolFault(400, "invalid_position");
      pair[color] = item;
    }
    normalized[field] = pair;
  }
  return normalized;
}

function parsePlayers(value) {
  if (!record(value)) throw new ProtocolFault(400, "invalid_players");
  const players = {};
  for (const color of ["b", "w"]) {
    if (!int(value[color], 1, Number.MAX_SAFE_INTEGER)) throw new ProtocolFault(400, "invalid_players");
    players[color] = value[color];
  }
  return players;
}

export function validateWebhookPayload(value) {
  if (!record(value)) throw new ProtocolFault(400, "invalid_request");
  const { requestId, gameId, color, number, ply, positions } = value;
  if (!visibleId(requestId, 200) || !visibleId(gameId, 128)) throw new ProtocolFault(400, "invalid_identity");
  if (color !== "b" && color !== "w") throw new ProtocolFault(400, "invalid_color");
  if (!int(number, 0, Number.MAX_SAFE_INTEGER) || !int(ply, 0, 10000)) throw new ProtocolFault(400, "invalid_ply");
  if (!record(positions)) throw new ProtocolFault(400, "invalid_positions");

  const hasGame = Object.hasOwn(value, "game");
  const hasBase = Object.hasOwn(value, "basePly");
  let game;
  let basePly;
  if (hasGame) {
    if (hasBase || !record(value.game) || typeof value.game.type !== "string") throw new ProtocolFault(400, "invalid_game");
    if (!SUPPORTED_GAME_TYPES.has(value.game.type)) throw new ProtocolFault(422, "unsupported_game_type");
    const requiredPlayers = parsePlayers(value.game.requiredPlayers);
    if (value.game.type !== "ついたてリレー" && (requiredPlayers.b !== 1 || requiredPlayers.w !== 1)) {
      throw new ProtocolFault(400, "invalid_players");
    }
    if (number >= requiredPlayers[color]) throw new ProtocolFault(400, "invalid_seat");
    game = { type: value.game.type, requiredPlayers };
  } else {
    if (!hasBase || !int(value.basePly, 0, 10000)) throw new ProtocolFault(400, "invalid_base_ply");
    basePly = value.basePly;
    if (value.game !== undefined) throw new ProtocolFault(400, "invalid_game");
  }

  const expectedStart = hasGame ? 0 : basePly + 1;
  if (ply < expectedStart || (!hasGame && ply <= basePly)) throw new ProtocolFault(409, "invalid_ply_range");
  const expectedCount = ply - expectedStart + 1;
  const keys = Object.keys(positions);
  if (keys.length !== expectedCount) throw new ProtocolFault(400, "incomplete_positions");
  const normalizedPositions = {};
  for (let index = expectedStart; index <= ply; index += 1) {
    const key = String(index);
    if (!Object.hasOwn(positions, key)) throw new ProtocolFault(400, "incomplete_positions");
    normalizedPositions[key] = parsePosition(positions[key]);
  }
  if (keys.some((key) => !/^(?:0|[1-9]\d*)$/.test(key) || Number(key) < expectedStart || Number(key) > ply)) {
    throw new ProtocolFault(400, "unexpected_position_key");
  }

  return {
    requestId,
    gameId,
    color,
    number,
    ply,
    basePly,
    game,
    positions: normalizedPositions,
  };
}

export function extractRequestIdentity(value) {
  if (!record(value)) throw new ProtocolFault(400, "invalid_request");
  if (!visibleId(value.requestId, 200) || !visibleId(value.gameId, 128)) {
    throw new ProtocolFault(400, "invalid_identity");
  }
  return { requestId: value.requestId, gameId: value.gameId };
}

export function parseCurrentTurn(payload) {
  const current = payload.positions[String(payload.ply)];
  const parsed = parseSfen(current.sfen);
  if (parsed.turn !== payload.color) throw new ProtocolFault(409, "not_your_turn");
  return current;
}

export function isRecord(value) {
  return record(value);
}
