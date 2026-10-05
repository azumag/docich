import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import worker from "../src/index.js";
import { DiscordBot } from "../src/bot.js";
import {
  beginConversation,
  finishConversation,
  forgetScope,
  initializeMemory,
  markSending,
} from "../src/memory.js";
import {
  VoiceBridgeError,
  authorizeVoiceBridge,
  generateVoicePreviewReply,
  readVoiceBridgeJson,
  validateVoiceBridgeInput,
} from "../src/voice-bridge.js";

const SECRET = "s".repeat(64);
const INPUT = Object.freeze({
  version: 1,
  requestId: "voice_turn_1",
  guildId: "123456789012345678",
  channelId: "223456789012345678",
  userId: "323456789012345678",
  transcript: "こんにちは。前の話を覚えていますか。",
});

function memoryDb() {
  const db = new DatabaseSync(":memory:");
  const sql = {
    exec(query, ...params) {
      if (query.includes("CREATE TABLE")) {
        db.exec(query);
        return { toArray: () => [], one: () => undefined };
      }
      const rows = db.prepare(query).all(...params);
      return {
        toArray: () => rows,
        one: () => rows[0],
      };
    },
  };
  initializeMemory(sql);
  return { db, sql };
}

function seedSent(sql, {
  id,
  guildId = INPUT.guildId,
  channelId = INPUT.channelId,
  authorId = INPUT.userId,
  content,
  reply,
}) {
  const seq = beginConversation(sql, {
    id,
    guildId,
    channelId,
    authorId,
    authorName: "テストユーザー",
    content,
    referenceId: null,
    createdAt: 1_700_000_000,
  });
  assert.ok(Number.isSafeInteger(seq));
  assert.equal(markSending(sql, seq), true);
  assert.equal(finishConversation(sql, seq, "reply-" + id, reply), true);
  return seq;
}

function countConversations(sql) {
  return Number(sql.exec("SELECT COUNT(*) AS count FROM conversations").one().count);
}

test("voice bridge auth is explicit and does not accept a wrong bearer", async () => {
  const ok = new Request("https://example.invalid/internal/voice/reply", {
    method: "POST",
    headers: { authorization: "Bearer " + SECRET },
  });
  assert.equal(
    await authorizeVoiceBridge(ok, { DOCICH_VOICE_BRIDGE_TOKEN: SECRET }),
    true,
  );

  const wrong = new Request("https://example.invalid/internal/voice/reply", {
    method: "POST",
    headers: { authorization: "Bearer " + "x".repeat(64) },
  });
  assert.equal(
    await authorizeVoiceBridge(wrong, { DOCICH_VOICE_BRIDGE_TOKEN: SECRET }),
    false,
  );

  await assert.rejects(
    authorizeVoiceBridge(ok, {}),
    (error) =>
      error instanceof VoiceBridgeError &&
      error.code === "voice_bridge_unconfigured" &&
      error.status === 503,
  );
});

test("voice bridge body is bounded and request fields are strict", async () => {
  const request = new Request("https://example.invalid/internal/voice/reply", {
    method: "POST",
    headers: { "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify(INPUT),
  });
  assert.deepEqual(
    validateVoiceBridgeInput(await readVoiceBridgeJson(request)),
    INPUT,
  );

  assert.throws(
    () => validateVoiceBridgeInput({ ...INPUT, endpoint: "https://evil.invalid" }),
    (error) =>
      error instanceof VoiceBridgeError &&
      error.code === "voice_bridge_invalid_request",
  );

  const oversized = new Request("https://example.invalid/internal/voice/reply", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...INPUT, transcript: "あ".repeat(9000) }),
  });
  await assert.rejects(
    readVoiceBridgeJson(oversized),
    (error) =>
      error instanceof VoiceBridgeError &&
      error.code === "voice_bridge_body_too_large" &&
      error.status === 413,
  );
});

test("preview reply reuses same-scope memory without writing the voice turn", async (t) => {
  const { db, sql } = memoryDb();
  t.after(() => db.close());

  seedSent(sql, {
    id: "same",
    content: "同じscopeの記憶",
    reply: "同じscopeの返答",
  });
  seedSent(sql, {
    id: "other",
    guildId: "423456789012345678",
    content: "別Guildの秘密",
    reply: "別Guildの返答",
  });
  const before = countConversations(sql);

  let modelInput;
  const result = await generateVoicePreviewReply(
    {
      DOCICH_PERSONA: "丁寧なテスト人格です。",
      AI: {
        async run(_model, input) {
          modelInput = input;
          return {
            choices: [{
              message: { content: "覚えています。音声向けのテスト返答です。" },
              finish_reason: "stop",
            }],
          };
        },
      },
    },
    sql,
    INPUT,
  );

  assert.equal(result.version, 1);
  assert.equal(result.requestId, INPUT.requestId);
  assert.equal(result.reply, "覚えています。音声向けのテスト返答です。");
  assert.equal(countConversations(sql), before);

  const serialized = JSON.stringify(modelInput.messages);
  assert.match(serialized, /同じscopeの記憶/);
  assert.doesNotMatch(serialized, /別Guildの秘密/);
  assert.match(serialized, /こんにちは。前の話を覚えていますか。/);
});

test("preview reply is discarded if a recalled memory is deleted during generation", async (t) => {
  const { db, sql } = memoryDb();
  t.after(() => db.close());

  seedSent(sql, {
    id: "remembered",
    content: "生成中に消える記憶",
    reply: "過去の返答",
  });

  await assert.rejects(
    generateVoicePreviewReply(
      {
        DOCICH_PERSONA: "丁寧なテスト人格です。",
        AI: {
          async run() {
            forgetScope(sql, INPUT.guildId, INPUT.channelId, {
              authorId: INPUT.userId,
            });
            return {
              choices: [{
                message: { content: "この返答は破棄されます。" },
                finish_reason: "stop",
              }],
            };
          },
        },
      },
      sql,
      INPUT,
    ),
    (error) =>
      error instanceof VoiceBridgeError &&
      error.code === "voice_context_changed" &&
      error.status === 409,
  );
});

test("public worker authenticates before forwarding a sanitized voice request", async () => {
  let forwarded = null;
  const stub = {
    async fetch(request) {
      forwarded = await request.json();
      return Response.json({
        version: 1,
        requestId: forwarded.requestId,
        reply: "内部DOの返答です。",
      });
    },
  };
  const env = {
    DOCICH_VOICE_BRIDGE_TOKEN: SECRET,
    DISCORD_BOT: {
      idFromName(name) {
        assert.equal(name, "singleton");
        return "object-id";
      },
      get(id) {
        assert.equal(id, "object-id");
        return stub;
      },
    },
  };

  const response = await worker.fetch(new Request(
    "https://voice.example/internal/voice/reply",
    {
      method: "POST",
      headers: {
        authorization: "Bearer " + SECRET,
        "content-type": "application/json",
      },
      body: JSON.stringify(INPUT),
    },
  ), env);
  assert.equal(response.status, 200);
  assert.deepEqual(forwarded, INPUT);
  assert.equal(response.headers.get("cache-control"), "no-store");

  forwarded = null;
  const denied = await worker.fetch(new Request(
    "https://voice.example/internal/voice/reply",
    {
      method: "POST",
      headers: {
        authorization: "Bearer " + "x".repeat(64),
        "content-type": "application/json",
      },
      body: JSON.stringify(INPUT),
    },
  ), env);
  assert.equal(denied.status, 401);
  assert.equal(forwarded, null);
});

test("Durable Object voice route generates a preview without committing memory", async (t) => {
  const { db, sql } = memoryDb();
  t.after(() => db.close());
  seedSent(sql, {
    id: "do-memory",
    content: "DOに保存済みの記憶",
    reply: "DOの過去返答",
  });
  const before = countConversations(sql);

  const kv = new Map();
  const state = {
    storage: {
      sql,
      async get(key) { return kv.get(key); },
      async put(key, value) {
        if (typeof key === "object") {
          for (const [entryKey, entryValue] of Object.entries(key)) kv.set(entryKey, entryValue);
        } else {
          kv.set(key, value);
        }
      },
      async delete(keys) {
        for (const key of Array.isArray(keys) ? keys : [keys]) kv.delete(key);
      },
      async setAlarm() {},
    },
    waitUntil() {},
  };
  const bot = new DiscordBot(state, {
    DOCICH_PERSONA: "丁寧なテスト人格です。",
    AI: {
      async run() {
        return {
          choices: [{
            message: { content: "DOから生成した音声向け返答です。" },
            finish_reason: "stop",
          }],
        };
      },
    },
  });

  const response = await bot.fetch(new Request(
    "https://discord-bot.internal/voice/reply",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(INPUT),
    },
  ));
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.reply, "DOから生成した音声向け返答です。");
  assert.equal(countConversations(sql), before);
  assert.equal((await bot.status()).pending, 0);
});
