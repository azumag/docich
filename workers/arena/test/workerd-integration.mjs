/** workerd integration test: run the bundled arena worker under miniflare.
 *
 * Mirrors workers/tsuitate-bot's local-runtime approach: everything happens in
 * a temporary directory against local SQLite persistence; no Cloudflare
 * account, no network beyond loopback, nothing deployed. The bundle is built
 * from the real entrypoint so the production module graph is what gets
 * exercised, including the real WebSocketPair and hibernation API.
 */
import assert from "node:assert/strict";
import { build } from "esbuild";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

const directory = dirname(dirname(fileURLToPath(import.meta.url)));
const temporary = await mkdtemp(join(tmpdir(), "arena-workerd-"));
const origin = "http://arena.local.test";

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function until(predicate, label) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await sleep(20);
  }
  throw new Error(`workerd fixture timeout: ${label}`);
}

const output = join(temporary, "worker.js");
await build({
  entryPoints: [join(directory, "src", "worker.js")],
  outfile: output,
  bundle: true,
  platform: "browser",
  format: "esm",
  conditions: ["browser"],
  external: ["cloudflare:workers"],
  logLevel: "silent",
});
const script = await readFile(output, "utf8");

const options = {
  ...convertV4MiniflareOptions({
    name: "arena-local-test-only",
    modules: true,
    script,
    compatibilityDate: "2026-09-08",
    compatibilityFlags: ["nodejs_compat"],
    durableObjects: {
      ARENA_ROOM: { className: "OthelloRoom", useSQLite: true },
      ARENA_LOBBY: { className: "ArenaLobby", useSQLite: true },
    },
  }),
  // Miniflare v5 needs the inline bundle plus resourcePersistencePath for
  // SQLite-backed Durable Objects (same approach as tsuitate-bot).
  resourcePersistencePath: join(temporary, "state"),
};

let mf = new Miniflare(options);
let webSocket = null;

async function json(path, init) {
  const response = await mf.dispatchFetch(`${origin}${path}`, init);
  return { status: response.status, body: await response.json() };
}

/** Open a spectator/player WebSocket and collect parsed messages. */
async function connect(roomId) {
  const response = await mf.dispatchFetch(`${origin}/api/othello/rooms/${roomId}/ws`, {
    headers: { Upgrade: "websocket" },
  });
  assert.ok(response.webSocket, "dispatchFetch must return a client WebSocket");
  const socket = response.webSocket;
  socket.accept();
  const messages = [];
  socket.addEventListener("message", (event) => {
    try {
      messages.push(JSON.parse(event.data));
    } catch {
      messages.push({ type: "unparseable" });
    }
  });
  webSocket = socket;
  return { socket, messages };
}

const statesOf = (messages) => messages.filter((message) => message.type === "state");
const lastState = (messages) => statesOf(messages).at(-1)?.state ?? null;

try {
  // Pages: the broadcast HTML standard markers must reach the wire.
  const landing = await mf.dispatchFetch(`${origin}/`);
  assert.equal(landing.status, 200);
  const landingHtml = await landing.text();
  for (const marker of ['lang="ja"', 'translate="no"', 'name="google" content="notranslate"']) {
    assert.ok(landingHtml.includes(marker), `landing must contain ${marker}`);
  }

  // Room creation under the real lobby and room Durable Objects.
  const created = await json("/api/othello/rooms", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ level: "normal" }),
  });
  assert.equal(created.status, 200);
  const roomId = created.body.roomId;
  assert.match(roomId, /^[0-9a-f]{16}$/);

  const snapshot = await json(`/api/othello/rooms/${roomId}`);
  assert.equal(snapshot.status, 200);
  assert.equal(snapshot.body.snapshot.status, "waiting");
  assert.equal(snapshot.body.snapshot.kind, "arena_othello");

  // The full human-vs-AI flow across a real workerd WebSocket.
  const player = await connect(roomId);
  player.socket.send(JSON.stringify({ type: "hello", name: "workerd同志" }));
  await until(() => player.messages.some((message) => message.type === "joined"), "joined");
  await until(() => lastState(player.messages)?.status === "playing", "game started");

  // d3 is legal on the first move; the AI answers inside the same broadcast.
  player.socket.send(JSON.stringify({ type: "move", coord: "d3" }));
  await until(() => (lastState(player.messages)?.moveNumber ?? 0) >= 2, "ai reply");
  const afterMove = lastState(player.messages);
  assert.equal(afterMove.turn, "black");
  assert.equal(afterMove.lastMove.color, "white");
  assert.equal(afterMove.board[19], "B"); // d3

  // A second client is a spectator and cannot take the seat or move.
  const spectator = await connect(roomId);
  await until(() => spectator.messages.some((message) => message.type === "welcome"), "welcome");
  spectator.socket.send(JSON.stringify({ type: "hello", name: "二番手" }));
  await until(() => spectator.messages.some((message) => message.type === "error"), "seat_taken");
  assert.equal(spectator.messages.find((message) => message.type === "error").code, "seat_taken");
  spectator.socket.send(JSON.stringify({ type: "move", coord: "c3" }));
  await until(() => spectator.messages.some((message) => message.type === "error" && message.code === "not_seated"), "not_seated");

  // Resign ends the game through the real storage path. The exact score after
  // two plies depends on the random room id (AI seed), so only the shape is
  // asserted here.
  player.socket.send(JSON.stringify({ type: "resign" }));
  await until(() => lastState(player.messages)?.status === "ended", "resign");
  const ended = lastState(player.messages);
  assert.equal(ended.result.winner, "white");
  assert.equal(ended.result.reason, "resign");
  assert.ok(Number.isInteger(ended.result.black) && Number.isInteger(ended.result.white));
  assert.deepEqual(ended.wins, { human: 0, ai: 1, draw: 0 });

  // Restart persistence: a fresh miniflare instance over the same state still
  // sees the room (SQLite-backed DO contract).
  await mf.dispose();
  mf = new Miniflare(options);
  const after = await (async () => {
    const response = await mf.dispatchFetch(`${origin}/api/othello/rooms/${roomId}`);
    return { status: response.status, body: await response.json() };
  })();
  assert.equal(after.status, 200);
  assert.equal(after.body.snapshot.status, "ended");
  assert.equal(after.body.snapshot.gameNumber, 1);

  console.log("workerd integration: OK");
} finally {
  if (webSocket) {
    try {
      webSocket.close();
    } catch {
      // already closed
    }
  }
  await mf.dispose();
  await rm(temporary, { recursive: true, force: true });
}
