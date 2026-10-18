/** WebSocket client message validation for the arena (fail-closed).
 *
 * The size limit counts UTF-16 code units (string .length): at most 1024
 * characters, which bounds any single frame well under 4 KiB of UTF-8.
 *
 * Every inbound frame is parsed here before the room looks at it. Anything
 * that is not exactly one of the known shapes is rejected with a fixed error
 * code; the raw body never reaches logs or error messages.
 */
export const MAX_MESSAGE_CHARS = 1024;

export const ERROR_CODES = {
  TOO_LARGE: "too_large",
  INVALID_JSON: "invalid_json",
  INVALID_MESSAGE: "invalid_message",
  UNKNOWN_TYPE: "unknown_type",
};

const MAX_NAME_CHARS = 20;
const CONTROL_AND_INVISIBLE = /[\u0001-\u001f\u007f\u200b\u200c\u200d\ufeff]/g;

function cleanName(value) {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") return null;
  // Strip control characters instead of rejecting common chat artifacts.
  const stripped = value.replace(CONTROL_AND_INVISIBLE, "");
  if (stripped.length > MAX_NAME_CHARS) return null;
  return stripped;
}

/**
 * @param {string|ArrayBuffer} raw
 * @returns {{ok: true, message: object}|{ok: false, code: string}}
 */
export function parseClientMessage(raw) {
  if (raw instanceof ArrayBuffer) {
    if (raw.byteLength > MAX_MESSAGE_CHARS) return { ok: false, code: ERROR_CODES.TOO_LARGE };
    raw = new TextDecoder().decode(raw);
  }
  if (typeof raw !== "string") return { ok: false, code: ERROR_CODES.INVALID_MESSAGE };
  if (raw.length > MAX_MESSAGE_CHARS) return { ok: false, code: ERROR_CODES.TOO_LARGE };
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, code: ERROR_CODES.INVALID_JSON };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, code: ERROR_CODES.INVALID_MESSAGE };
  }
  const type = parsed.type;
  if (typeof type !== "string") return { ok: false, code: ERROR_CODES.INVALID_MESSAGE };
  switch (type) {
    case "hello": {
      if ("name" in parsed) {
        const name = cleanName(parsed.name);
        if (name === null) return { ok: false, code: ERROR_CODES.INVALID_MESSAGE };
        return { ok: true, message: { type: "hello", name } };
      }
      return { ok: true, message: { type: "hello", name: "" } };
    }
    case "move": {
      if (typeof parsed.coord !== "string" || !/^[a-h][1-8]$/.test(parsed.coord)) {
        return { ok: false, code: ERROR_CODES.INVALID_MESSAGE };
      }
      return { ok: true, message: { type: "move", coord: parsed.coord } };
    }
    case "resign":
    case "rematch":
    case "ping":
      return { ok: true, message: { type } };
    default:
      return { ok: false, code: ERROR_CODES.UNKNOWN_TYPE };
  }
}
