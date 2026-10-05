import assert from "node:assert/strict";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import { DiscordBot } from "../src/bot.js";
import { generateConversationReply } from "../src/conversation.js";
import { beginConversation, finishConversation, forgetScope, initializeMemory, markSending, validContext } from "../src/memory.js";

const event = (id, content = "猫の名前は？", scope = {}) => ({ id, guildId: "1", channelId: "10", authorId: "7",
  authorName: "合成話者", content, referenceId: null, createdAt: Date.now() / 1000, ...scope });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const tick = () => new Promise((resolve) => setImmediate(resolve));
function sqlite(t) {
  const db = new DatabaseSync(":memory:"); t.after(() => db.close());
  return { exec(query, ...params) {
    if (query.includes("CREATE TABLE")) { db.exec(query); return { toArray: () => [] }; }
    const rows = db.prepare(query).all(...params);
    return { toArray: () => rows, one: () => rows[0] };
  } };
}
function seed(sql, e = event("seed", "猫の名前はタマ")) {
  const seq = beginConversation(sql, e); assert.ok(markSending(sql, seq));
  assert.ok(finishConversation(sql, seq, "seed-reply-" + e.id, "覚えました")); return seq;
}
function rows(sql) { return sql.exec("SELECT * FROM conversations ORDER BY seq").toArray(); }
function ai(run) { return { DOCICH_PERSONA: "合成人格fixture", AI: { run } }; }
function completion(reply) { return { choices: [{ message: { content: reply }, finish_reason: "stop" }] }; }

async function botFixture(t, run, send = async () => ({ status: 200, id: "reply-id" })) {
  const sql = sqlite(t), stored = new Map(), work = new Set(), sent = [], logs = [];
  const state = { storage: { sql, get: async (key) => stored.get(key),
    put: async (record) => { for (const [k, v] of Object.entries(record)) stored.set(k, v); },
    delete: async (keys) => { for (const k of [].concat(keys)) stored.delete(k); }, setAlarm: async () => {} },
    waitUntil(promise) { work.add(promise); promise.finally(() => work.delete(promise)); },
  };
  const listeners = new Map(); let socket;
  t.mock.method(console, "log", (value) => logs.push(value));
  t.mock.method(globalThis, "fetch", async (url, options) => {
    if (url === "https://discord.com/api/v10/users/@me") return Response.json({ id: "99", username: "fixture-bot" });
    if (url === "https://discord.com/api/v10/gateway/bot") return Response.json({ url: "wss://gateway.example" });
    if (String(url).endsWith("/messages")) {
      const body = JSON.parse(options.body); sent.push(body);
      const result = await send(body); return Response.json({ id: result.id }, { status: result.status });
    }
    throw new Error("unexpected_network_fixture");
  });
  class Socket {
    readyState = 1;
    constructor() { socket = this; }
    addEventListener(name, callback) { listeners.set(name, callback); }
    send() {}
  }
  const previousSocket = Object.getOwnPropertyDescriptor(globalThis, "WebSocket");
  Object.defineProperty(globalThis, "WebSocket", { value: Socket, configurable: true, writable: true });
  t.after(() => previousSocket ? Object.defineProperty(globalThis, "WebSocket", previousSocket) : delete globalThis.WebSocket);
  const bot = new DiscordBot(state, { ...ai(run), DISCORD_BOT_TOKEN: "EXAMPLE_ONLY_DISCORD_FIXTURE_TOKEN" });
  await bot.ensureConnected();
  const dispatch = async (type, data) => {
    listeners.get("message")({ data: JSON.stringify({ op: 0, t: type, d: data }) });
    await tick();
  };
  const drain = async () => { while (work.size) await Promise.all([...work]); };
  const message = async (e) => dispatch("MESSAGE_CREATE", { id: e.id, guild_id: e.guildId, channel_id: e.channelId,
    author: { id: e.authorId, username: e.authorName }, content: "<@99> " + e.content,
    mentions: [{ id: "99" }], timestamp: new Date(e.createdAt * 1000).toISOString(), type: 0 });
  await dispatch("READY", { session_id: "fixture-session", resume_gateway_url: "wss://gateway.example", user: { id: "99" } });
  await drain(); assert.equal(socket.readyState, 1);
  return { bot, sql, sent, logs, message, dispatch, drain };
}

test("text delivery commits only after acknowledged send, preserves recall/persona and dedup", async (t) => {
  const delivery = deferred(); let calls = 0, input;
  const f = await botFixture(t, async (_model, value) => { calls++; input = value; return completion("タマです"); },
    async () => { await delivery.promise; return { status: 200, id: "real-fixture-reply" }; });
  seed(f.sql);
  await f.message(event("current")); await tick();
  assert.equal(rows(f.sql).at(-1).state, "sending"); assert.equal(rows(f.sql).at(-1).reply, "");
  assert.equal(input.messages[0].content.startsWith("合成人格fixture"), true);
  assert.ok(input.messages.some((m) => m.content.includes("猫の名前はタマ")));
  assert.equal(input.tool_choice, "none"); assert.equal(f.sent.length, 1);
  delivery.resolve(); await f.drain();
  assert.equal(rows(f.sql).at(-1).state, "sent"); assert.equal(rows(f.sql).at(-1).reply_id, "real-fixture-reply");
  await f.message(event("current")); await f.drain(); assert.equal(calls, 1); assert.equal(f.sent.length, 1);
});
test("failed delivery is not remembered and never sends a second response", async (t) => {
  const f = await botFixture(t, async () => completion("fixture"), async () => ({ status: 403 }));
  await f.message(event("failed")); await f.drain();
  assert.equal(f.sent.length, 1); assert.equal(rows(f.sql)[0].state, "failed"); assert.equal(rows(f.sql)[0].content, "");
  assert.equal(f.logs.at(-1).stage, "discord_send"); assert.equal(f.logs.at(-1).discordStatus, 403);
  await f.message(event("failed")); await f.drain(); assert.equal(f.sent.length, 1);
});
for (const deletion of ["current", "seed"]) {
  test(`deletion of ${deletion} while generating suppresses send and scrubs memory`, async (t) => {
    const pending = deferred(); const f = await botFixture(t, async () => pending.promise); seed(f.sql);
    await f.message(event("current"));
    await f.dispatch("MESSAGE_DELETE", { id: deletion, guild_id: "1", channel_id: "10" });
    pending.resolve(completion("should not send")); await f.drain();
    assert.equal(f.sent.length, 0);
    const current = rows(f.sql).at(-1); assert.ok(["failed", "deleted"].includes(current.state)); assert.equal(current.reply, "");
  });
}
test("forget command retains scope deletion, ack-only reply and no model call", async (t) => {
  let calls = 0; const f = await botFixture(t, async () => { calls++; return completion("unused"); });
  seed(f.sql); seed(f.sql, event("other", "別scope", { authorId: "8" }));
  await f.message(event("forget", "記憶を削除")); await f.drain();
  assert.equal(calls, 0); assert.equal(f.sent.length, 1); assert.match(f.sent[0].content, /記憶を削除/);
  assert.ok(rows(f.sql).filter((r) => r.author_id === "7").every((r) => r.state === "deleted" && !r.content && !r.reply));
  assert.equal(rows(f.sql).find((r) => r.author_id === "8").state, "sent");
});
test("generation failure preserves stage diagnostic, failure notice and failed dedup record", async (t) => {
  const f = await botFixture(t, async () => { throw new Error("EXAMPLE_PRIVATE_MODEL_ERROR"); });
  await f.message(event("model-error")); await f.drain();
  assert.equal(rows(f.sql)[0].state, "failed"); assert.equal(f.sent.length, 1); assert.match(f.sent[0].content, /返答を作れません/);
  assert.equal(f.logs.at(-1).stage, "workers_ai"); assert.ok(!JSON.stringify(f.logs).includes("EXAMPLE_PRIVATE_MODEL_ERROR"));
});
test("memory context failure keeps its diagnostic stage and does not invoke the model", async (t) => {
  let calls = 0;
  const f = await botFixture(t, async () => { calls++; return completion("unused"); });
  const exec = f.sql.exec;
  t.mock.method(f.sql, "exec", (query, ...args) => {
    if (query.includes("seq<? AND state='sent'")) throw new Error("EXAMPLE_PRIVATE_SQL_ERROR");
    return exec(query, ...args);
  });
  await f.message(event("sql-error")); await f.drain();
  assert.equal(calls, 0); assert.equal(f.sent.length, 1); assert.equal(rows(f.sql)[0].state, "failed");
  assert.equal(f.logs.at(-1).stage, "memory_context"); assert.ok(!JSON.stringify(f.logs).includes("EXAMPLE_PRIVATE_SQL_ERROR"));
});
test("generation core returns deletion recheck sources without committing or delivering", async (t) => {
  const sql = sqlite(t); initializeMemory(sql); seed(sql);
  const e = event("core"); const seq = beginConversation(sql, e), stages = [];
  const before = rows(sql); const pending = deferred();
  const result = generateConversationReply(ai(async () => pending.promise), sql, e, seq, (stage) => stages.push(stage));
  forgetScope(sql, e.guildId, e.channelId, { messageIds: ["seed"] });
  pending.resolve(completion("fixture")); const generated = await result;
  assert.deepEqual(stages, ["memory_context", "workers_ai"]); assert.equal(generated.reply, "fixture");
  assert.equal(validContext(sql, seq, generated.context), false);
  assert.equal(before.at(-1).state, "pending"); assert.equal(rows(sql).at(-1).state, "pending");
});
test("internal voice reply reuses canonical memory read-only and does not persist the voice turn", async (t) => {
  let input;
  const f = await botFixture(t, async (_model, value) => {
    input = value;
    return completion("タマです");
  });
  seed(f.sql);
  const before = rows(f.sql).length;

  const response = await f.bot.fetch(new Request("https://discord-bot.internal/voice/reply", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      guildId: "1",
      channelId: "10",
      userId: "7",
      turnId: "voice-fixture-1",
      transcript: "猫の名前は？",
    }),
  }));

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { reply: "タマです" });
  assert.equal(rows(f.sql).length, before);
  assert.ok(input.messages.some((message) => message.content.includes("猫の名前はタマ")));
  assert.equal(f.logs.at(-1).status, "voice_reply_generated");
});

test("voice playback commit persists only after explicit ack and is idempotent", async (t) => {
  const f = await botFixture(t, async () => completion("音声で返します"));
  const turn = {
    guildId: "1",
    channelId: "10",
    userId: "7",
    turnId: "voice-playback-fixture",
    transcript: "こんにちは",
  };

  const generated = await f.bot.fetch(new Request("https://discord-bot.internal/voice/reply", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(turn),
  }));
  assert.equal(generated.status, 200);
  assert.deepEqual(await generated.json(), { reply: "音声で返します" });
  assert.equal(rows(f.sql).length, 0);

  const commitBody = { ...turn, reply: "音声で返します" };
  const first = await f.bot.fetch(new Request("https://discord-bot.internal/voice/commit", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(commitBody),
  }));
  assert.equal(first.status, 200);
  assert.deepEqual(await first.json(), { status: "committed" });

  const stored = rows(f.sql);
  assert.equal(stored.length, 1);
  assert.equal(stored[0].state, "sent");
  assert.equal(stored[0].message_id, "voice:" + turn.turnId);
  assert.equal(stored[0].reply_id, "voice-playback:" + turn.turnId);
  assert.equal(stored[0].content, "こんにちは");
  assert.equal(stored[0].reply, "音声で返します");

  const duplicate = await f.bot.fetch(new Request("https://discord-bot.internal/voice/commit", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(commitBody),
  }));
  assert.equal(duplicate.status, 200);
  assert.deepEqual(await duplicate.json(), { status: "already_committed" });
  assert.equal(rows(f.sql).length, 1);

  const conflict = await f.bot.fetch(new Request("https://discord-bot.internal/voice/commit", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...commitBody, reply: "別の返答" }),
  }));
  assert.equal(conflict.status, 409);
  assert.deepEqual(await conflict.json(), { error: "commit_conflict" });
  assert.equal(rows(f.sql).length, 1);
});

test("malformed voice commit never creates memory", async (t) => {
  let calls = 0;
  const f = await botFixture(t, async () => {
    calls += 1;
    return completion("unused");
  });
  const response = await f.bot.fetch(new Request("https://discord-bot.internal/voice/commit", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      guildId: "1",
      channelId: "10",
      userId: "7",
      turnId: "bad commit with spaces",
      transcript: "こんにちは",
      reply: "返答",
    }),
  }));
  assert.equal(response.status, 400);
  assert.equal(calls, 0);
  assert.equal(rows(f.sql).length, 0);
});

test("internal voice reply discards generation when recalled memory is deleted during the model call", async (t) => {
  const pending = deferred();
  const f = await botFixture(t, async () => pending.promise);
  seed(f.sql);

  const responsePromise = f.bot.fetch(new Request("https://discord-bot.internal/voice/reply", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      guildId: "1",
      channelId: "10",
      userId: "7",
      turnId: "voice-fixture-delete",
      transcript: "猫の名前は？",
    }),
  }));

  await tick();
  forgetScope(f.sql, "1", "10", { messageIds: ["seed"] });
  pending.resolve(completion("古い記憶からの返答"));
  const response = await responsePromise;

  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { error: "context_changed" });
  assert.equal(rows(f.sql).length, 1);
  assert.equal(rows(f.sql)[0].state, "deleted");
});

test("internal voice reply bounds concurrent model calls and releases capacity", async (t) => {
  const gates = [deferred(), deferred()];
  let calls = 0;
  const f = await botFixture(t, async () => {
    const index = calls++;
    if (index < gates.length) return gates[index].promise;
    return completion("復帰しました");
  });

  const makeRequest = (turnId) => f.bot.fetch(new Request("https://discord-bot.internal/voice/reply", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      guildId: "1",
      channelId: "10",
      userId: "7",
      turnId,
      transcript: "こんにちは",
    }),
  }));

  const first = makeRequest("voice-concurrent-1");
  const second = makeRequest("voice-concurrent-2");
  await tick();
  const third = await makeRequest("voice-concurrent-3");

  assert.equal(calls, 2);
  assert.equal(third.status, 429);
  assert.deepEqual(await third.json(), { error: "busy" });
  assert.equal(third.headers.get("retry-after"), "1");

  gates[0].resolve(completion("一つ目です"));
  gates[1].resolve(completion("二つ目です"));
  assert.equal((await first).status, 200);
  assert.equal((await second).status, 200);

  const fourth = await makeRequest("voice-concurrent-4");
  assert.equal(fourth.status, 200);
  assert.deepEqual(await fourth.json(), { reply: "復帰しました" });
  assert.equal(calls, 3);
});

test("internal voice reply rejects malformed scope before invoking the model", async (t) => {
  let calls = 0;
  const f = await botFixture(t, async () => {
    calls += 1;
    return completion("unused");
  });
  const response = await f.bot.fetch(new Request("https://discord-bot.internal/voice/reply", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      guildId: "bad",
      channelId: "10",
      userId: "7",
      turnId: "voice-fixture-invalid",
      transcript: "こんにちは",
    }),
  }));
  assert.equal(response.status, 400);
  assert.equal(calls, 0);
  assert.equal(rows(f.sql).length, 0);
});

test("fake voice caller uses same persona/core with its own limits; text 901 characters remains intact", async (t) => {
  const sql = sqlite(t); initializeMemory(sql); seed(sql);
  for (const [id, guildId, historyPresent] of [["voice-one", "1", true], ["voice-other", "2", false]]) {
    const e = event(id, "合成transcript", { guildId }); const seq = beginConversation(sql, e);
    let input;
    const { reply, context } = await generateConversationReply(ai(async (_model, value) => { input = value; return completion("x".repeat(1000)); }), sql, e, seq);
    assert.equal(reply.length, 901); assert.ok(validContext(sql, seq, context));
    assert.equal(input.messages.some((m) => m.content.includes("猫の名前はタマ")), historyPresent);
    assert.equal(rows(sql).at(-1).state, "pending"); // caller has not acknowledged delivery
    // Voice policies reject explicitly; core never clips text or silently chooses TTS/stage limits.
    const voiceReply = (limit) => { if (reply.length > limit) throw new Error("voice_reply_limit"); return reply; };
    assert.throws(() => voiceReply(900), /voice_reply_limit/); assert.throws(() => voiceReply(200), /voice_reply_limit/);
  }
});
