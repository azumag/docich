import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { MAX_REPLAY_BYTES, collectRecords, fetchReplay, replayUrl } from "../scripts/collect-replays.mjs";

const FINAL =
  "lnsgkgsnl/7r1/pppppp1pp/6p2/9/2P6/PP1PPPPPP/7R1/LNSGKGSNL b Bb 5";
const MOVES = [["7g7f", "sente"], ["3c3d", "gote"], ["8h2b+", "sente"], ["8b2b", "gote"]];
const GAME_ID = "backfill-game";

function publicPayload() {
  const table = [{}];
  const push = (value) => { table.push(value); return table.length - 1; };
  const moves = MOVES.map(([usi, by]) => push({ usi: push(usi), byColor: push(by), ms: push(200), foulsBefore: push(0) }));
  const game = push({ id: push(GAME_ID), result: push("sente_win"), reason: push("checkmate"),
    moves: push(moves), foulAttempts: push([]), finalSfen: push(FINAL) });
  table[0].game = game;
  table[0].senteName = push("DoCiAI");
  table[0].goteName = push("opponent");
  return { type: "data", nodes: [{ type: "data", data: table }] };
}

function response(body, overrides = {}) {
  return {
    status: 200,
    headers: { get: (name) => (name === "content-length" ? String(Buffer.byteLength(body)) : null) },
    text: async () => body,
    ...overrides,
  };
}

test("replayUrl is fixed-host and rejects arbitrary ids", () => {
  assert.equal(replayUrl("abc-123_X"), "https://beta.tsuitate.info/games/abc-123_X/__data.json");
  for (const bad of ["", "../x", "x/y", "x y", 7, null]) {
    assert.throws(() => replayUrl(bad), /invalid_game_id/);
  }
});

test("fetchReplay refuses redirects and oversized bodies", async () => {
  const seen = [];
  const ok = await fetchReplay(GAME_ID, {
    fetchImpl: async (url, options) => { seen.push({ url, options }); return response(JSON.stringify(publicPayload())); },
  });
  assert.equal(typeof ok, "object");
  assert.equal(seen[0].options.redirect, "manual");
  assert.ok(seen[0].options.signal, "the request is time bounded");

  await assert.rejects(() => fetchReplay(GAME_ID, { fetchImpl: async () => response("", { status: 302 }) }),
    /replay_unavailable/);
  await assert.rejects(() => fetchReplay(GAME_ID, {
    fetchImpl: async () => response("{}", { headers: { get: () => String(MAX_REPLAY_BYTES + 1) } }),
  }), /replay_too_large/);
  await assert.rejects(() => fetchReplay(GAME_ID, { fetchImpl: async () => response("not json") }),
    /replay_invalid/);
});

test("collectRecords archives each new game once and reports failures", async () => {
  const outDir = await mkdtemp(join(tmpdir(), "collect-replays-"));
  const requests = [];
  const fetchImpl = async (url) => {
    requests.push(url);
    if (url.includes("missing")) return response("", { status: 404 });
    return response(JSON.stringify(publicPayload()));
  };
  const records = [
    { runId: "background-20261123221056000000", gameId: GAME_ID, brainVersion: "tsuitate-brain-v13" },
    { runId: "background-20261122221056000000", gameId: "missing-game", brainVersion: "tsuitate-brain-v13" },
    { runId: "background-20261121221056000000", gameId: "../escape", brainVersion: "tsuitate-brain-v13" },
  ];
  const first = await collectRecords({ records, outDir, fetchImpl });
  assert.deepEqual(first.archived, [GAME_ID]);
  assert.deepEqual(first.failed, [
    { gameId: "missing-game", error: "replay_unavailable" },
    { gameId: null, error: "invalid_game_id" },
  ]);
  const saved = JSON.parse(await readFile(join(outDir, `${GAME_ID}.json`), "utf8"));
  assert.equal(saved.brainVersion, "tsuitate-brain-v13");
  assert.equal(saved.color, "sente");
  assert.equal(requests.length, 2, "the invalid id is never fetched");

  const second = await collectRecords({ records, outDir, fetchImpl });
  assert.deepEqual(second.archived, []);
  assert.deepEqual(second.skipped, [GAME_ID]);
  assert.equal(requests.length, 3, "the still-missing game is retried, the archived one is not");
  assert.equal(requests.filter((url) => url.includes(GAME_ID)).length, 1);
});

test("collectRecords caps one batch", async () => {
  const outDir = await mkdtemp(join(tmpdir(), "collect-replays-limit-"));
  const records = Array.from({ length: 5 }, (_, index) => ({
    runId: `background-2026112322105600000${index}`, gameId: `${GAME_ID}-${index}`,
    brainVersion: "tsuitate-brain-v13",
  }));
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return response("", { status: 503 }); };
  await collectRecords({ records, outDir, limit: 2, fetchImpl });
  assert.equal(calls, 2);
});
