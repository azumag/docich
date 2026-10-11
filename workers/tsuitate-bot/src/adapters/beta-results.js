// The bot guide does not specify the shape or identity of game:end. Correlate
// a terminal result with the public replay's exact game ID instead of assigning
// an unscoped push to whichever match happens to be current.
export const BETA_ORIGIN = "https://beta.tsuitate.info";
const GAME_ID = /^[A-Za-z0-9_-]{1,128}$/;
const MAX_REPLAY_BYTES = 1024 * 1024;
const REASONS = new Map([
  ["checkmate", "checkmate"], ["resign", "resign"], ["timeout", "timeout"],
  ["foul_limit", "foul_limit"], ["disconnect", "disconnect"], ["stalemate", "stalemate"],
]);

export function validBetaGameId(value) {
  return typeof value === "string" && GAME_ID.test(value);
}

function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// Read only known primitive references. Never evaluate JavaScript, revive
// arbitrary types, or reconstruct the opponent's board for the live brain.
export function parsePublicResult(raw, gameId, color) {
  if (!validBetaGameId(gameId) || !["b", "w"].includes(color)
      || !object(raw) || raw.type !== "data" || !Array.isArray(raw.nodes) || raw.nodes.length > 10) return null;
  const matches = [];
  for (const node of raw.nodes) {
    if (!object(node) || node.type !== "data" || !Array.isArray(node.data)
        || node.data.length === 0 || node.data.length > 50000) continue;
    const table = node.data;
    const ref = (index) => Number.isSafeInteger(index) && index >= 0 && index < table.length ? table[index] : undefined;
    const root = table[0];
    const game = object(root) ? ref(root.game) : null;
    if (!object(game) || ref(game.id) !== gameId) continue;
    const endedAt = ref(game.endedAt);
    if (!Array.isArray(endedAt) || endedAt.length !== 2 || endedAt[0] !== "Date"
        || typeof endedAt[1] !== "string" || !Number.isFinite(Date.parse(endedAt[1]))) continue;
    const result = ref(game.result);
    if (!["sente_win", "gote_win", "draw", "aborted"].includes(result)) continue;
    const startedAt = ref(game.startedAt);
    if (!Array.isArray(startedAt) || startedAt.length !== 2 || startedAt[0] !== "Date"
        || typeof startedAt[1] !== "string" || !Number.isFinite(Date.parse(startedAt[1]))
        || Date.parse(startedAt[1]) > Date.parse(endedAt[1])) continue;
    matches.push({
      gameId,
      outcome: result === "aborted" ? "unknown" : result === "draw" ? "draw" : (result === "sente_win") === (color === "b") ? "win" : "loss",
      reason: result === "aborted" ? "interrupted" : REASONS.get(ref(game.reason)) ?? "unknown",
      startedAt: new Date(startedAt[1]).toISOString(),
      endedAt: new Date(endedAt[1]).toISOString(),
      source: "public_replay",
    });
  }
  return matches.length === 1 ? matches[0] : null;
}

/** Public frontend data, not a promised stable API. Fail closed on any drift. */
export async function fetchPublicResult(gameId, color, { fetchImpl = fetch, timeoutMs = 5000 } = {}) {
  if (!validBetaGameId(gameId) || !["b", "w"].includes(color)) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let reader, body;
  try {
    const response = await fetchImpl(`${BETA_ORIGIN}/games/${encodeURIComponent(gameId)}/__data.json`, {
      // Use manual for the pinned workerd runtime. Reject redirect responses
      // below; never follow them to a different origin or send credentials.
      signal: controller.signal, redirect: "manual", credentials: "omit",
      headers: { accept: "application/json" },
    });
    body = response.body;
    if (!response.ok || !/^application\/json\b/i.test(response.headers.get("content-type") ?? "")) return null;
    reader = body?.getReader();
    if (!reader) return null;
    const chunks = [];
    let total = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_REPLAY_BYTES) return null;
      chunks.push(value);
    }
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return parsePublicResult(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)), gameId, color);
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
    if (reader) { try { await reader.cancel(); } catch { /* already closed */ } }
    else if (body) { try { await body.cancel(); } catch { /* rejected response */ } }
  }
}
