import { generateConversationReply } from "./conversation.js";
import { validSources } from "./memory.js";

const MAX_BODY_BYTES = 8192;
const MAX_TRANSCRIPT_CHARS = 2000;
const MAX_TRANSCRIPT_BYTES = 6000;
const VOICE_AUTHOR_NAME = "音声ユーザー";

export class VoiceBridgeError extends Error {
  constructor(code, status = 400) {
    super(code);
    this.name = "VoiceBridgeError";
    this.code = code;
    this.status = status;
  }
}

const fail = (code, status = 400) => {
  throw new VoiceBridgeError(code, status);
};

const validSnowflake = (value) =>
  typeof value === "string" &&
  /^[1-9][0-9]{0,19}$/.test(value) &&
  BigInt(value) < 2n ** 64n;

const validRequestId = (value) =>
  typeof value === "string" &&
  /^[A-Za-z0-9_-]{1,64}$/.test(value);

const secretValue = (env) => {
  const value = env?.DOCICH_VOICE_BRIDGE_TOKEN;
  return typeof value === "string" &&
    value.length >= 32 &&
    value.length <= 4096 &&
    !/\s/.test(value)
    ? value
    : null;
};

const erase = (value) => {
  if (!ArrayBuffer.isView(value)) return;
  try {
    Uint8Array.prototype.fill.call(value, 0);
  } catch {
    // Detached/foreign storage remains owned by its creator.
  }
};

async function digest(value) {
  const bytes = new TextEncoder().encode(value);
  try {
    return new Uint8Array(
      await globalThis.crypto.subtle.digest("SHA-256", bytes),
    );
  } finally {
    erase(bytes);
  }
}

function equalBytes(left, right) {
  if (!(left instanceof Uint8Array) || !(right instanceof Uint8Array)) return false;
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left[i] ^ right[i];
  return diff === 0;
}

export function voiceBridgeConfigured(env) {
  return secretValue(env) !== null;
}

export async function authorizeVoiceBridge(request, env) {
  const secret = secretValue(env);
  if (!secret) fail("voice_bridge_unconfigured", 503);

  const header = request?.headers?.get?.("authorization");
  if (
    typeof header !== "string" ||
    !header.startsWith("Bearer ") ||
    header.length > 4103
  ) {
    return false;
  }
  const candidate = header.slice("Bearer ".length);
  if (!candidate || /\s/.test(candidate)) return false;

  const [expected, actual] = await Promise.all([
    digest(secret),
    digest(candidate),
  ]);
  try {
    return equalBytes(expected, actual);
  } finally {
    erase(expected);
    erase(actual);
  }
}

export async function readVoiceBridgeJson(request) {
  const type = String(request?.headers?.get?.("content-type") ?? "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (type !== "application/json") fail("voice_bridge_invalid_content_type", 415);
  if (!request.body || typeof request.body.getReader !== "function") {
    fail("voice_bridge_invalid_body", 400);
  }

  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!(value instanceof Uint8Array)) fail("voice_bridge_invalid_body", 400);
      total += value.byteLength;
      if (total > MAX_BODY_BYTES) {
        await reader.cancel();
        fail("voice_bridge_body_too_large", 413);
      }
      chunks.push(value);
    }

    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }

    try {
      const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      return JSON.parse(text);
    } catch {
      fail("voice_bridge_invalid_json", 400);
    } finally {
      erase(bytes);
    }
  } finally {
    for (const chunk of chunks) erase(chunk);
    reader.releaseLock?.();
  }
}

export function validateVoiceBridgeInput(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail("voice_bridge_invalid_request", 400);
  }
  const allowed = new Set([
    "version",
    "requestId",
    "guildId",
    "channelId",
    "userId",
    "transcript",
  ]);
  if (
    Object.keys(value).some((key) => !allowed.has(key)) ||
    value.version !== 1 ||
    !validRequestId(value.requestId) ||
    !validSnowflake(value.guildId) ||
    !validSnowflake(value.channelId) ||
    !validSnowflake(value.userId) ||
    typeof value.transcript !== "string"
  ) {
    fail("voice_bridge_invalid_request", 400);
  }

  const transcript = value.transcript.trim();
  let transcriptBytes;
  try {
    transcriptBytes = new TextEncoder().encode(transcript);
    if (
      !transcript ||
      transcript.length > MAX_TRANSCRIPT_CHARS ||
      transcriptBytes.byteLength > MAX_TRANSCRIPT_BYTES
    ) {
      fail("voice_bridge_invalid_request", 400);
    }
  } finally {
    erase(transcriptBytes);
  }

  return Object.freeze({
    version: 1,
    requestId: value.requestId,
    guildId: value.guildId,
    channelId: value.channelId,
    userId: value.userId,
    transcript,
  });
}

export async function generateVoicePreviewReply(
  env,
  sql,
  input,
  setStage = () => {},
) {
  const request = validateVoiceBridgeInput(input);
  const event = Object.freeze({
    id: "voice-" + request.requestId,
    guildId: request.guildId,
    channelId: request.channelId,
    authorId: request.userId,
    authorName: VOICE_AUTHOR_NAME,
    content: request.transcript,
    referenceId: null,
    createdAt: Date.now() / 1000,
  });

  const { reply, context } = await generateConversationReply(
    env,
    sql,
    event,
    Number.MAX_SAFE_INTEGER,
    setStage,
  );
  if (!validSources(sql, context)) {
    fail("voice_context_changed", 409);
  }

  return Object.freeze({
    version: 1,
    requestId: request.requestId,
    reply,
  });
}
