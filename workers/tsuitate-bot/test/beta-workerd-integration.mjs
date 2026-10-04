import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";
import { build } from "esbuild";

// All transports and credentials below are explicit local fixtures.
const require = createRequire(import.meta.url);
const { WebSocketServer } = require("ws");
const directory = dirname(fileURLToPath(import.meta.url));
const temporary = await mkdtemp(join(tmpdir(), "tsuitate-beta-workerd-"));
let mf, python, pythonError = "", connections = 0, queueJoins = 0;
const peers = new Set(), packets = [], endedGames = new Set();
const nullSyncGames = new Set(), resultLookups = new Map();
const pause = (ms) => new Promise((done) => setTimeout(done, ms));
const view = (gameId) => ({ gameId, yourColor: "sente",
  yourPieces: [{ square: "5i", role: "king" }, { square: "5g", role: "pawn" }, { square: "2h", role: "rook" }],
  yourHand: {}, turn: "sente", moveNumber: 1,
  clocks: { senteMs: 300000, goteMs: 300000, running: endedGames.has(gameId) ? null : "sente", serverTime: 100 },
  fouls: { you: 0, opponent: 0 }, youInCheck: false, opponentInCheck: false,
  status: endedGames.has(gameId) ? "ended" : "playing" });
const server = createServer(async (incoming, outgoing) => {
  try {
    const replay = /^\/public-result\/games\/(local-workerd-game-\d+)\/__data\.json$/.exec(incoming.url);
    if (incoming.method === "GET" && replay) {
      const gameId = replay[1], attempts = (resultLookups.get(gameId) ?? 0) + 1;
      resultLookups.set(gameId, attempts);
      if (!endedGames.has(gameId) || (nullSyncGames.has(gameId) && attempts < 3)) {
        outgoing.writeHead(503); outgoing.end(); return;
      }
      outgoing.writeHead(200, { "content-type": "application/json" });
      outgoing.end(JSON.stringify({ type: "data", nodes: [{ type: "data", data: [
        { game: 1 }, { id: 2, result: 3, reason: 4, startedAt: 5, endedAt: 6 },
        gameId, "sente_win", "checkmate", ["Date", "2026-10-03T00:00:00.000Z"],
        ["Date", new Date(Date.now() + 1000).toISOString()],
      ] }] })); return;
    }
    const chunks = []; for await (const chunk of incoming) chunks.push(chunk);
    const response = await mf.dispatchFetch("http://local-only.test/beta-control", {
      method: incoming.method, headers: incoming.headers, body: Buffer.concat(chunks) });
    outgoing.writeHead(response.status, { "content-type": "application/json" });
    outgoing.end(await response.text());
  } catch { outgoing.writeHead(503); outgoing.end('{"error":"local_fixture_unavailable"}'); }
});
const socketServer = new WebSocketServer({ server });
const push = (peer, event, payload) => peer.send(`42${JSON.stringify([event, payload])}`);
socketServer.on("connection", (peer) => {
  connections++; peers.add(peer); peer.gameId = `local-workerd-game-${Math.max(1, queueJoins)}`;
  peer.on("close", () => peers.delete(peer));
  peer.send(`0${JSON.stringify({ sid: "local-fixture", upgrades: [], pingInterval: 25000, pingTimeout: 20000, maxPayload: 100000 })}`);
  peer.on("message", (bytes) => {
    const packet = bytes.toString();
    if (packet.startsWith("40")) { peer.send('40{"sid":"local-fixture"}'); return; }
    const match = /^42(\d+)(\[.*\])$/.exec(packet); if (!match) return;
    const [event, payload] = JSON.parse(match[2]); packets.push({ event, payload });
    const ack = (value) => peer.send(`43${match[1]}${JSON.stringify([value])}`);
    if (event === "queue:join") {
      peer.gameId = `local-workerd-game-${++queueJoins}`;
      push(peer, "game:active", { gameId: null }); ack({ ok: true });
      push(peer, "match:found", { gameId: peer.gameId, yourColor: "sente" });
    } else if (event === "game:sync") ack({ state: nullSyncGames.has(payload.gameId) ? null : view(payload.gameId) });
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
  const localOrigin = `http://127.0.0.1:${server.address().port}`;
  const output = join(temporary, "worker.js");
  await build({ entryPoints: [join(directory, "beta-workerd-worker.js")], outfile: output,
    bundle: true, platform: "browser", format: "esm", conditions: ["browser"],
    external: ["cloudflare:workers", "node:crypto"], logLevel: "silent" });
  // Miniflare v5 needs inline bundle plus resourcePersistencePath for SQLite
  // restart evidence; its v4 converter drops durableObjectsPersist.
  const script = await readFile(output, "utf8");
  const options = (token = "fixture-only-not-a-credential") => ({ ...convertV4MiniflareOptions({ name: "beta-local-test-only", modules: true, script,
    compatibilityDate: "2026-09-08", compatibilityFlags: ["nodejs_compat"],
    durableObjects: { BETA_ARENA: { className: "RuntimeBetaArena", useSQLite: true } },
    bindings: { BETA_CONTROL_SECRET: "fixture-only-control-capability-not-credential",
      TSUITATE_BOT_TOKEN: token, LOCAL_SOCKET_ORIGIN: localOrigin } }),
    resourcePersistencePath: join(temporary, "state") });
  mf = new Miniflare(options(""));
  assert.equal((await json("/status")).state, "stopped");
  assert.equal((await mf.dispatchFetch("http://local-only.test/beta-control", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: '{"action":"start","runId":"one"}' })).status, 401);
  assert.equal(connections, 0);
  python = spawn("python3", [join(directory, "beta-webui-fixture.py"), localOrigin], {
    env: { PATH: process.env.PATH, PYTHONPATH: resolve(directory, "../../../src"), PYTHONNOUSERSITE: "1" },
    stdio: ["ignore", "pipe", "pipe"] });
  python.stderr.on("data", (chunk) => { pythonError += chunk; });
  let stdout = "";
  python.stdout.on("data", (chunk) => { stdout += chunk; });
  await until(() => stdout.includes("\n") || python.exitCode !== null);
  assert.equal(python.exitCode, null, pythonError);
  const webOrigin = `http://127.0.0.1:${JSON.parse(stdout.split("\n")[0]).port}`;
  const headers = { Authorization: "Bearer fixture-only-operator-not-credential", Origin: webOrigin,
    "Content-Type": "application/json" };
  const csrf = (await (await fetch(webOrigin + "/api/csrf", { headers })).json()).csrf_token;
  async function owner(action = "status", runId, override = {}) {
    const response = await fetch(webOrigin + "/api/tsuitate-beta", action === "status" ?
      { headers: { ...headers, ...override } } : { method: "POST",
        headers: { ...headers, "X-CSRF-Token": csrf, ...override }, body: JSON.stringify({ action, runId, confirm: true }) });
    return { status: response.status, ...await response.json() };
  }
  assert.equal((await owner("start", "local-fixture")).error, "token_not_configured");
  assert.equal((await owner("start", "local-fixture", { Authorization: "Bearer fixture-only-viewer-not-credential" })).status, 403);
  assert.equal((await owner("start", "local-fixture", { "X-CSRF-Token": "" })).status, 403);
  assert.equal(connections, 0);
  await mf.dispose(); mf = new Miniflare(options());
  assert.equal((await json("/wrong-singleton")).code, "not_singleton");
  const starts = await Promise.all([owner("start", "local-fixture"), owner("start", "local-fixture")]);
  assert.ok(starts.every((result) => result.status === 200), JSON.stringify(starts));
  await until(() => packets.filter((packet) => packet.event === "game:move").length === 1);
  assert.equal((await json("/evidence")).pendingPersisted, true);
  assert.equal((await json("/evidence")).sqlite, true);
  assert.equal((await json("/rollback")).rolledBack, true);
  await json("/alarm"); await json("/alarm"); assert.equal(connections, 1); assert.equal(queueJoins, 1);
  assert.equal((await owner("stop", "local-fixture")).state, "draining");
  const savedActorIds = await mf.listDurableObjectIds("RuntimeBetaArena"); assert.ok(savedActorIds.length > 0);
  await mf.dispose(); mf = new Miniflare(options());
  assert.deepEqual(await mf.listDurableObjectIds("RuntimeBetaArena"), savedActorIds);
  assert.equal((await owner()).state, "draining"); assert.equal(connections, 1);
  assert.equal((await json("/evidence")).pendingPersisted, true);
  assert.equal((await json("/alarm")).state, "draining");
  await until(() => connections === 2);
  await until(async () => (await json("/evidence")).communicationInterrupted === true);
  assert.equal(queueJoins, 1); assert.equal(packets.filter((packet) => packet.event === "game:move").length, 1);
  endedGames.add("local-workerd-game-1"); for (const peer of peers) push(peer, "game:state", view(peer.gameId));
  await until(async () => (await owner()).readyForNextRun === true);
  assert.equal((await json("/evidence")).recordSaved, true);
  assert.equal((await json("/evidence")).validForTraining, false);
  await owner("start", "local-fixture"); await json("/alarm"); assert.equal(queueJoins, 1);
  assert.equal((await owner("start", "second-fixture")).status, 200);
  await until(() => queueJoins === 2 && packets.filter((packet) => packet.event === "game:move").length === 2);
  await owner("start", "local-fixture"); await owner("stop", "local-fixture");
  assert.equal((await owner()).runId, "second-fixture"); assert.equal((await owner()).stopRequested, false);
  assert.equal((await owner("start", "third-fixture")).error, "run_locked");
  endedGames.add("local-workerd-game-2"); nullSyncGames.add("local-workerd-game-2");
  for (const peer of peers) push(peer, "game:end", { gameId: "unrelated-game", fullBoard: "fixture-private" });
  await until(async () => (await owner()).readyForNextRun === true);
  assert.equal((await json("/evidence")).recordSaved, true);
  assert.equal(resultLookups.get("local-workerd-game-2"), 3);
  await mf.dispose(); mf = new Miniflare(options());
  assert.equal((await owner()).state, "finished"); assert.equal((await json("/evidence")).recordSaved, true);
  await owner("start", "second-fixture"); await json("/alarm");
  assert.equal(queueJoins, 2); assert.equal(connections, 3);
  console.log(JSON.stringify({ workerd: "passed", ownerWebUi: true, serviceHmac: true,
    viewerAndCsrfDenied: true, initialStopped: true, manualRuns: 2, maxGamesPerRun: 1,
    coldResume: true, noDuplicateRecruitment: true, terminalRestart: true, sqliteRollback: true,
    delayedReplayWithNullSync: true,
    betaConnected: false, cloudflareResourceCreated: false }));
} finally {
  if (python && python.exitCode === null) { python.kill("SIGTERM"); await once(python, "exit"); }
  if (mf) await mf.dispose();
  for (const peer of socketServer.clients) peer.terminate();
  await new Promise((done) => socketServer.close(done));
  await new Promise((done) => server.close(done));
  await rm(temporary, { recursive: true, force: true });
}
