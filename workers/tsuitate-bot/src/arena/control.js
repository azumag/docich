import { SINGLETON_NAME } from "./durable-controller.js";

export const CONTROL_PATH = "/beta-control";
export const CONTROL_PREFIX = "beta-control-v1\nPOST\n/beta-control\n";
const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const SAFE_CODES = new Set(["token_not_configured", "run_locked", "run_mismatch", "invalid_start_options",
  "recovery_not_available", "recovery_checkpoint_invalid", "terminal_result_unavailable"]);
const json = (status, body) => Response.json(body, { status, headers: { "cache-control": "no-store" } });

async function boundedBody(request) {
  const reader = request.body?.getReader();
  if (!reader) throw new Error("invalid_request");
  const chunks = []; let length = 0;
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; void reader.cancel().catch(() => {}); }, 1000);
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (timedOut) throw new Error("control_timeout");
      if (done) break;
      length += value.length;
      if (length > 4096) throw new Error("body_too_large");
      chunks.push(value);
    }
    const raw = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.length; }
    return raw;
  } finally { clearTimeout(timer); void reader.cancel().catch(() => {}); }
}

async function deadline(operation, ms) {
  let timer;
  try { return await Promise.race([operation, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("control_timeout")), ms);
  })]); } finally { clearTimeout(timer); }
}

/** Fixed service capability; never accepts browser/Webhook authentication. */
export async function handleBetaControl(request, env, now = Date.now()) {
  const secret = env.BETA_CONTROL_SECRET;
  if (typeof secret !== "string" || new TextEncoder().encode(secret).length < 32 || secret.length > 4096
      || secret === env.WEBHOOK_SECRET || secret === env.TSUITATE_BOT_TOKEN) {
    return json(503, { error: "control_not_configured" });
  }
  if (request.method !== "POST") return json(405, { error: "method_not_allowed" });
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    return json(415, { error: "content_type_required" });
  }
  const timestamp = request.headers.get("X-Beta-Control-Timestamp");
  const signature = request.headers.get("X-Beta-Control-Signature");
  if (!/^\d{1,12}$/.test(timestamp ?? "") || Math.abs(now / 1000 - Number(timestamp)) >= 300
      || !/^sha256=[a-f0-9]{64}$/.test(signature ?? "")) return json(401, { error: "control_authentication_failed" });
  try {
    const raw = await boundedBody(request);
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
    const prefix = new TextEncoder().encode(`${CONTROL_PREFIX}${timestamp}.`);
    const signed = new Uint8Array(prefix.length + raw.length); signed.set(prefix); signed.set(raw, prefix.length);
    const bytes = Uint8Array.from(signature.slice(7).match(/../g), (pair) => parseInt(pair, 16));
    if (!await crypto.subtle.verify("HMAC", key, bytes, signed)) return json(401, { error: "control_authentication_failed" });
    const payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
    if (!payload || typeof payload !== "object" || Array.isArray(payload)
        || !["status", "start", "stop", "reconcile"].includes(payload.action)
        || Object.keys(payload).some((key) => !["action", "runId"].includes(key))
        || (payload.action === "status" ? Object.hasOwn(payload, "runId") : typeof payload.runId !== "string" || !ID.test(payload.runId))) {
      return json(400, { error: "invalid_control_request" });
    }
    if (!env.BETA_ARENA) return json(503, { error: "control_not_configured" });
    const actor = env.BETA_ARENA.get(env.BETA_ARENA.idFromName(SINGLETON_NAME));
    const operation = payload.action === "status" ? actor.status() : actor[payload.action]({ runId: payload.runId });
    return json(200, await deadline(operation, 2500));
  } catch (error) {
    const code = error?.message;
    if (SAFE_CODES.has(code)) return json(409, { error: code });
    if (code === "terminal_storage_failure") return json(503, { error: code });
    if (code === "body_too_large") return json(413, { error: code });
    if (code === "control_timeout") return json(504, { error: code });
    if (error instanceof SyntaxError || error instanceof TypeError) return json(400, { error: "invalid_control_request" });
    return json(503, { error: "control_unavailable" });
  }
}
