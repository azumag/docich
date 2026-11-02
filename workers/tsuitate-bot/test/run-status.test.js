import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";
import { BRAIN_VERSION, LINEAR_PROFILE } from "../src/brain/index.js";
import { CONTROL_PREFIX, handleBetaControl } from "../src/arena/control.js";
import { DurableArenaController } from "../src/arena/durable-controller.js";
import { META_KEY, RECORD_KEY, runKey, recordKey } from "../src/arena/durable-store.js";

const secret = "fixture-only-control-capability-not-credential";
const now = 1791100000000;

class Storage {
  constructor() { this.data = new Map(); this.tail = Promise.resolve(); }
  async get(key) { return structuredClone(this.data.get(key)); }
  async put(key, value) { this.data.set(key, structuredClone(value)); }
  async delete() {}
  async setAlarm() {}
  async deleteAlarm() {}
  transaction(operation) {
    const result = this.tail.then(async () => {
      const tx = new Storage(); tx.data = structuredClone(this.data);
      const value = await operation(tx); this.data = tx.data; return value;
    });
    this.tail = result.catch(() => {});
    return result;
  }
}

function terminal(gameId = "public-game") {
  return {
    schemaVersion: 1, kind: "tsuitate_game", site: "beta.tsuitate.info",
    ruleset: "tsuitate-9x9", rulesKey: "beta-300+3-f10", gameId, color: "b",
    startedAt: "2026-10-29T00:15:00.000Z", endedAt: "2026-10-29T00:25:00.000Z",
    brainVersion: BRAIN_VERSION, profile: LINEAR_PROFILE, decisions: [],
    completed: true, historyComplete: true, outcome: "win", reason: "checkmate",
    resultSource: "public_replay", resultConfidence: "verified", validForTraining: true,
  };
}

function controller(storage) {
  return new DurableArenaController({
    storage, env: { TSUITATE_BOT_TOKEN: "fixture-only-not-a-credential" },
    makeSocket: () => { throw new Error("network_not_expected"); }, resolveResult: async () => null,
  });
}

function signed(payload) {
  const raw = JSON.stringify(payload); const timestamp = String(now / 1000);
  const signature = createHmac("sha256", secret).update(CONTROL_PREFIX + timestamp + ".").update(raw).digest("hex");
  return new Request("https://local-only.test/beta-control", { method: "POST", body: raw,
    headers: { "content-type": "application/json", "X-Beta-Control-Timestamp": timestamp,
      "X-Beta-Control-Signature": `sha256=${signature}` } });
}

test("runStatus returns current nonterminal receipt without PlayerView or terminal data", async () => {
  const storage = new Storage();
  await storage.put(META_KEY, {
    version: 1, state: "playing", runId: "hourly-2026102900", generation: 1,
    gameId: "private-live-game", completedGames: 0, reservedGames: 1,
    stopRequested: false, errorCode: null, settled: false,
    brainVersion: BRAIN_VERSION, profile: LINEAR_PROFILE,
  });
  const status = await controller(storage).runStatus({ runId: "hourly-2026102900" });
  assert.equal(status.state, "playing");
  assert.equal(status.gameId, "private-live-game");
  assert.equal(status.terminalResult, null);
  assert.equal(Object.hasOwn(status, "playerView"), false);
});

test("runStatus verifies a historical finished receipt against record and reverse game owner", async () => {
  const storage = new Storage(); const runId = "hourly-2026102900"; const gameId = "public-game";
  await storage.put(META_KEY, {
    version: 1, state: "finished", runId: "hourly-2026102901", generation: 2,
    gameId: "newer-game", completedGames: 1, reservedGames: 1,
    stopRequested: false, errorCode: null, settled: true,
    brainVersion: BRAIN_VERSION, profile: LINEAR_PROFILE,
  });
  const receipt = {
    state: "finished", runId, gameId, completedGames: 1, reservedGames: 1,
    maxGames: 1, queueWaitSeconds: 60, stopRequested: false, errorCode: null,
    brainVersion: BRAIN_VERSION, readyForNextRun: true,
  };
  await storage.put(runKey(runId), receipt);
  await storage.put(recordKey(runId), terminal(gameId));
  await storage.put(`beta:game:${gameId}`, runId);
  const status = await controller(storage).runStatus({ runId });
  assert.equal(status.gameId, gameId);
  assert.deepEqual(status.terminalResult, {
    outcome: "win", reason: "checkmate", endedAt: "2026-10-29T00:25:00.000Z",
    moveNumber: 0, resultConfidence: "verified",
  });

  await storage.put(`beta:game:${gameId}`, "different-run");
  await assert.rejects(controller(storage).runStatus({ runId }), /recovery_checkpoint_invalid/);
});

test("run control action is authenticated and calls only read-only runStatus", async () => {
  const calls = [];
  const actor = {
    async runStatus(options) { calls.push(options); return { state: "finished", runId: options.runId }; },
  };
  const env = { BETA_CONTROL_SECRET: secret, BETA_ARENA: {
    idFromName(name) { assert.equal(name, "beta:DoCiAI"); return name; }, get() { return actor; },
  } };
  const response = await handleBetaControl(signed({ action: "run", runId: "hourly-2026102900" }), env, now);
  assert.equal(response.status, 200);
  assert.deepEqual(calls, [{ runId: "hourly-2026102900" }]);
  assert.equal((await handleBetaControl(signed({ action: "run" }), env, now)).status, 400);
  assert.equal((await handleBetaControl(signed({ action: "run", runId: "bad id" }), env, now)).status, 400);
});
