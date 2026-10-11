// Offline fixture generator. Supply the Worker source directory from the pinned commit.
import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

const sourceCommit = "68743456798c6088e66e9b343e7451037c1f55ef";
const sourceBlobs = {
  "src/index.js": "438c8ab64c15bd95728b9d8b49fa43531672b608",
  "src/protocol.js": "5e2d5e5f391d083daabc86d3698c50450e055a99",
  "src/brain/index.js": "5aa63220b726f92774db6df5fc506d3053ba02cb",
  "src/adapters/webhook.js": "3dbee937e57d433d16cc2efb2e6369cd18e8ecbb",
};
const upgradeCommit = "bc0f1ac2954a90e0567e198551ab1c2541be9b93";
const upgradeSourceBlobs = {
  ...sourceBlobs,
  "src/index.js": "9c2f0f93909d51a73c3f70ad62f45c8c8d0905fc",
  "src/protocol.js": "9f1dc1ad93c29fff559773f0d38b64b86bfb4295",
};
assert.ok(process.argv[2] && process.argv[3], "usage: node test/fixtures/generate-legacy-state.mjs <6874345-worker-source> <bc0f1ac-worker-source>");
const sourceDirectory = resolve(process.argv[2]);
const upgradeDirectory = resolve(process.argv[3]);
for (const [directory, blobs] of [[sourceDirectory, sourceBlobs], [upgradeDirectory, upgradeSourceBlobs]]) {
  for (const [path, sha] of Object.entries(blobs)) {
    const bytes = await readFile(join(directory, path));
    const actual = createHash("sha1").update(`blob ${bytes.length}\0`).update(bytes).digest("hex");
    assert.equal(actual, sha, `pinned source mismatch: ${path}`);
  }
}
const { GameState, handleWebhook } = await import(pathToFileURL(join(sourceDirectory, "src/index.js")));
const { GameState: UpgradeState, handleWebhook: upgradeHandler } = await import(pathToFileURL(join(upgradeDirectory, "src/index.js")));
const storage = {
  values: new Map(),
  async transaction(callback) {
    const staged = structuredClone(this.values);
    const outcome = await callback({
      get: async (key) => structuredClone(staged.get(key)),
      put: async (key, value) => { staged.set(key, structuredClone(value)); },
    });
    this.values = staged;
    return outcome;
  },
};
const state = new GameState({ storage });
const environment = {
  WEBHOOK_SECRET: "test-only-not-a-deployable-secret",
  GAME_STATE: { idFromName: (name) => name, get: () => ({ fetch: (request) => state.fetch(request) }) },
};
async function post(rawBody, handler = handleWebhook, target = state) {
  const timestamp = "1000000";
  const bytes = Buffer.from(rawBody);
  const signature = createHmac("sha256", environment.WEBHOOK_SECRET)
    .update(`${timestamp}.`).update(bytes).digest("hex");
  const response = await handler(new Request("https://worker.test/webhook", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Tsuitate-Bot-Id": "fixture-bot-id",
      "X-Tsuitate-Timestamp": timestamp,
      "X-Tsuitate-Signature": `sha256=${signature}`,
      "x-amz-content-sha256": createHash("sha256").update(bytes).digest("hex"),
    },
    body: bytes,
  }), {
    ...environment,
    GAME_STATE: { idFromName: (name) => name, get: () => ({ fetch: (request) => target.fetch(request) }) },
  }, { nowSeconds: 1_000_000 });
  return { status: response.status, body: await response.json() };
}
const fixture = { sourceCommit, sourceBlobs, upgradeCommit, upgradeSourceBlobs };
const originalLog = console.log;
console.log = () => {};
try {
  for (const [name, input] of [["afterInitial", "initial-request.json"], ["afterIncremental", "incremental-request.json"]]) {
    const payload = JSON.parse(await readFile(new URL(input, import.meta.url), "utf8"));
    const rawBody = JSON.stringify(payload);
    const response = await post(rawBody);
    assert.equal(response.status, 200);
    const entries = structuredClone([...storage.values.entries()]);
    assert.deepEqual(await post(rawBody), response, "the real old class must replay its receipt");
    assert.deepEqual([...storage.values.entries()], entries);
    fixture[name] = { rawBody, response, entries };
  }
  storage.values = new Map(structuredClone(fixture.afterInitial.entries));
  const upgrade = new UpgradeState({ storage });
  assert.deepEqual(await post(fixture.afterInitial.rawBody, upgradeHandler, upgrade), {
    status: 409, body: { error: "request_id_reused" },
  });
  const rawBody = fixture.afterIncremental.rawBody;
  const response = await post(rawBody, upgradeHandler, upgrade);
  assert.deepEqual(response, { status: 409, body: { error: "bot_identity_mismatch" } });
  fixture.afterDeniedUpgrade = { rawBody, response, entries: structuredClone([...storage.values.entries()]) };
} finally {
  console.log = originalLog;
}
await writeFile(new URL("legacy-game-state-6874345.json", import.meta.url), `${JSON.stringify(fixture, null, 2)}\n`);
console.log("Generated snapshots using the verified 6874345 and bc0f1ac Worker classes.");
