import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";
import { build } from "esbuild";

// ws is Wrangler's local test dependency, never a Worker transport/deployment.
const require = createRequire(import.meta.url);
const { WebSocketServer } = require("ws");
const directory = dirname(fileURLToPath(import.meta.url));
const temporary = await mkdtemp(join(tmpdir(), "tsuitate-beta-workerd-"));
const server = createServer();
const socketServer = new WebSocketServer({ server });
const peers = new Set(), packets = [];
let connections = 0, ended = false, mf;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const view = () => ({ gameId: "local-workerd-game", yourColor: "sente",
  yourPieces: [{ square: "5i", role: "king" }, { square: "5g", role: "pawn" }, { square: "2h", role: "rook" }],
  yourHand: {}, turn: "sente", moveNumber: 1,
  clocks: { senteMs: 300000, goteMs: 300000, running: "sente", serverTime: 100 },
  fouls: { you: 0, opponent: 0 }, youInCheck: false, opponentInCheck: false, status: ended ? "ended" : "playing" });
const push = (peer, event, payload) => peer.send(`42${JSON.stringify([event, payload])}`);
socketServer.on("connection", (peer) => {
  connections++; peers.add(peer); peer.on("close", () => peers.delete(peer));
  peer.send(`0${JSON.stringify({ sid: "local-fixture", upgrades: [], pingInterval: 25000, pingTimeout: 20000, maxPayload: 100000 })}`);
  peer.on("message", (bytes) => {
    const packet = bytes.toString();
    if (packet.startsWith("40")) { peer.send('40{"sid":"local-fixture"}'); return; }
    const match = /^42(\d+)(\[.*\])$/.exec(packet);
    if (!match) return;
    const [event, payload] = JSON.parse(match[2]); packets.push({ event, payload });
    const ack = (value) => peer.send(`43${match[1]}${JSON.stringify([value])}`);
    if (event === "queue:join") {
      // Reproduce the notification that aborted the first Mac canary.
      push(peer, "game:active", { gameId: null }); ack({ ok: true });
      push(peer, "match:found", { gameId: "local-workerd-game", yourColor: "sente" });
    } else if (event === "game:sync") ack({ state: view() });
    else if (event === "game:move" || event === "queue:leave") ack({ ok: true });
  });
});

async function json(path) { return (await mf.dispatchFetch(`http://local-only.test${path}`)).json(); }
async function until(predicate) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) { if (await predicate()) return; await pause(20); }
  throw new Error("local_fixture_timeout");
}

try {
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  const output = join(temporary, "worker.js");
  await build({ entryPoints: [join(directory, "beta-workerd-worker.js")], outfile: output,
    bundle: true, platform: "browser", format: "esm", conditions: ["browser"],
    external: ["cloudflare:workers", "node:crypto"], logLevel: "silent" });
  // Inline bundle avoids the local Miniflare v5 scriptPath/module-loader startup
  // failure. The same source still runs in real workerd with SQLite storage.
  const script = await readFile(output, "utf8");
  const options = (enabled) => ({ ...convertV4MiniflareOptions({ name: "beta-local-test-only", modules: true, script,
    compatibilityDate: "2026-09-08", compatibilityFlags: ["nodejs_compat"],
    durableObjects: { BETA_ARENA: { className: "RuntimeBetaArena", useSQLite: true } },
    bindings: {
      BETA_ARENA_ENABLED: enabled, TSUITATE_BOT_TOKEN: "fixture-only-not-a-credential",
      LOCAL_SOCKET_ORIGIN: `http://127.0.0.1:${server.address().port}` } }),
    // v5's converter drops v4 durableObjectsPersist. Use the v5 persistence
    // option explicitly, otherwise a restart test accidentally gets fresh state.
    isolatedResourcePersistencePath: join(temporary, "state") });
  mf = new Miniflare(options("false"));
  assert.equal((await json("/status")).state, "stopped");
  assert.equal((await json("/start")).code, "arena_disabled"); assert.equal(connections, 0);
  assert.equal((await mf.dispatchFetch("http://local-only.test/")).status, 404);
  await mf.dispose(); mf = new Miniflare(options("true"));
  assert.equal((await json("/wrong-singleton")).code, "not_singleton"); assert.equal(connections, 0);
  await Promise.all([json("/start"), json("/start")]);
  await until(() => packets.filter((packet) => packet.event === "game:move").length === 1);
  assert.equal((await json("/evidence")).pendingPersisted, true);
  assert.equal((await json("/evidence")).sqlite, true);
  assert.equal((await json("/rollback")).rolledBack, true);
  await json("/alarm"); await json("/alarm");
  assert.equal(connections, 1);
  assert.equal(packets.filter((packet) => packet.event === "queue:join").length, 1);
  assert.equal((await json("/stop")).state, "draining");
  // Cold actor restore from persisted SQLite: sync only; unknown move is held.
  await mf.dispose(); mf = new Miniflare(options("true"));
  const restoredStatus = await json("/alarm");
  assert.equal(restoredStatus.state, "draining", "SQLite actor metadata must survive runtime restart");
  await until(() => connections === 2);
  await until(async () => (await json("/evidence")).communicationInterrupted === true);
  assert.equal(packets.filter((packet) => packet.event === "queue:join").length, 1);
  assert.equal(packets.filter((packet) => packet.event === "game:move").length, 1);
  assert.equal((await json("/status")).state, "draining");
  ended = true;
  for (const peer of peers) push(peer, "game:state", view());
  await until(async () => (await json("/status")).state === "finished");
  const evidence = await json("/evidence");
  assert.equal(evidence.recordSaved, true); assert.equal(evidence.decisions, 1);
  assert.equal(evidence.validForTraining, false);
  await json("/start"); await json("/alarm");
  assert.equal((await json("/status")).completedGames, 1); assert.equal(connections, 2);
  console.log(JSON.stringify({ workerd: "passed", socketIoVersion: "4.8.4", initialStopped: true,
    maxGames: 1, coldResume: true, terminalRecord: true, sqliteRollback: true,
    betaConnected: false, cloudflareResourceCreated: false }));
} finally {
  if (mf) await mf.dispose();
  for (const peer of socketServer.clients) peer.terminate();
  await new Promise((resolve) => socketServer.close(resolve));
  await new Promise((resolve) => server.close(resolve));
  await rm(temporary, { recursive: true, force: true });
}
