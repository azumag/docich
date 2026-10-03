import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import cloudflareConfig from "../cloudflare.config.ts";

test("Cloudflare config exports SQLite GameState and binds it to this Worker", () => {
  const { worker } = cloudflareConfig;

  assert.equal(worker.name, "docich-tsuitate-bot");
  assert.equal(worker.entrypoint, "src/index.js");
  assert.equal(worker.compatibilityDate, "2026-09-08");
  assert.deepEqual(worker.exports.GameState, {
    type: "durable-object",
    storage: "sqlite",
  });
  assert.deepEqual(worker.env.GAME_STATE, {
    type: "durable-object",
    worker: "docich-tsuitate-bot",
    exportName: "GameState",
  });
});

test("version preview URLs stay disabled without overriding the production workers.dev route", () => {
  assert.equal(cloudflareConfig.worker.previewUrls, false);
  assert.equal(Object.hasOwn(cloudflareConfig.worker, "workersDev"), false);
});

test("Cloudflare config sets the production Bot ID and keeps the secret binding value-less", () => {
  const { env } = cloudflareConfig.worker;

  assert.deepEqual(env.BOT_ID, {
    type: "text",
    value: ":DoCiAI",
  });
  assert.equal(env.WEBHOOK_SECRET.type, "secret");
  assert.equal(Object.hasOwn(env.WEBHOOK_SECRET, "value"), false);
});

test("test-only runtime keeps its fixture Bot ID", async () => {
  const runtimeConfig = await readFile(new URL("../wrangler.runtime.toml", import.meta.url), "utf8");

  assert.match(runtimeConfig, /^BOT_ID = "fixture-bot-id"$/m);
  assert.doesNotMatch(runtimeConfig, /:DoCiAI/);
});
