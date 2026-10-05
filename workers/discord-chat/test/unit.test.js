import assert from "node:assert/strict";
import test from "node:test";

import {
  GATEWAY_INTENTS,
  DiscordSendError,
  gatewaySocketUrl,
  isAddressedMessage,
  isAddressedRoleMessage,
  isFatalGatewayClose,
  managedBotRoleId,
  stripBotMention,
} from "../src/discord.js";
import { DISCORD_CONTEXT, cleanReply, generateReply } from "../src/llm.js";
import { searchTerms } from "../src/memory.js";
import { DiscordBot } from "../src/bot.js";
import { handleVoiceCommit, handleVoiceReply } from "../src/index.js";

test("Durable Object runtime module is importable", () => {
  assert.equal(typeof DiscordBot, "function");
});

test("Discord send errors expose only a numeric HTTP status", () => {
  const error = new DiscordSendError(403);
  assert.equal(error.message, "discord_send_failed");
  assert.equal(error.status, 403);
});

test("gateway intent surface is guild messages plus message content only", () => {
  assert.equal(GATEWAY_INTENTS, 33281);
  assert.equal(gatewaySocketUrl("wss://gateway.discord.gg"), "wss://gateway.discord.gg/?v=10&encoding=json");
  assert.equal(isFatalGatewayClose(4014), true);
  assert.equal(isFatalGatewayClose(4000), false);
});

test("mention matching remains explicit and strips only the bot mention", () => {
  const message = { mentions: [{ id: "99" }], content: "<@99> こんにちは" };
  assert.equal(isAddressedMessage(message, "99"), true);
  assert.equal(isAddressedMessage({ mentions: [], content: "<@99> fallback" }, "99"), true);
  assert.equal(isAddressedMessage({ mentions: [], content: "<@!99> fallback" }, "99"), true);
  assert.equal(isAddressedMessage({ mentions: [], content: "hello" }, "99"), false);
  assert.equal(stripBotMention(message.content, "99"), "こんにちは");
  assert.equal(stripBotMention("<@!99>", "99"), "（呼びかけ）");
});

test("only the Discord-managed role for this bot is treated as addressed", () => {
  const roles = [
    { id: "100", name: "DoCiAI", managed: false, tags: {} },
    { id: "200", name: "DoCiAI", managed: true, tags: { bot_id: "99" } },
    { id: "300", name: "OtherBot", managed: true, tags: { bot_id: "77" } },
  ];
  assert.equal(managedBotRoleId(roles, "99"), "200");
  assert.equal(managedBotRoleId(roles, "88"), null);
  assert.equal(isAddressedRoleMessage({ mention_roles: ["200"], content: "<@&200> hello" }, "200"), true);
  assert.equal(isAddressedRoleMessage({ mention_roles: ["100"], content: "<@&100> hello" }, "200"), false);
  assert.equal(stripBotMention("<@&200> hello", "99", "200"), "hello");
});

test("Japanese bigrams and normalized latin terms support durable recall", () => {
  const terms = searchTerms("Ｃａｔ 猫の名前はタマ");
  assert.ok(terms.includes("cat"));
  assert.ok(terms.includes("猫の"));
  assert.ok(terms.includes("名前"));
});

test("Discord connection context requires polite desu-masu style", () => {
  assert.match(DISCORD_CONTEXT, /です・ます調/);
  assert.match(DISCORD_CONTEXT, /丁寧語/);
  assert.match(DISCORD_CONTEXT, /〜です/);
});

test("public voice reply bridge requires a separate secret and strips it before the Durable Object", async () => {
  const secret = "s".repeat(48);
  const seen = [];
  const env = {
    DISCORD_VOICE_INTERNAL_TOKEN: secret,
    DISCORD_BOT: {
      idFromName(name) {
        assert.equal(name, "singleton");
        return "fixture-id";
      },
      get(id) {
        assert.equal(id, "fixture-id");
        return {
          async fetch(request) {
            seen.push(request);
            return new URL(request.url).pathname === "/voice/commit"
              ? Response.json({ status: "committed" })
              : Response.json({ reply: "fixture" });
          },
        };
      },
    },
  };

  const unauthorized = await handleVoiceReply(new Request("https://worker.example/voice/reply", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
  }), env);
  assert.equal(unauthorized.status, 401);
  assert.equal(seen.length, 0);

  const requestBody = JSON.stringify({
    guildId: "1",
    channelId: "10",
    userId: "7",
    turnId: "fixture-turn",
    transcript: "こんにちは",
  });
  const response = await handleVoiceReply(new Request("https://worker.example/voice/reply", {
    method: "POST",
    headers: {
      authorization: "Bearer " + secret,
      "content-type": "application/json",
    },
    body: requestBody,
  }), env);

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { reply: "fixture" });
  assert.equal(seen.length, 1);
  assert.equal(new URL(seen[0].url).pathname, "/voice/reply");
  assert.equal(seen[0].headers.get("authorization"), null);
  assert.equal(await seen[0].text(), requestBody);

  const commitBody = JSON.stringify({
    guildId: "1",
    channelId: "10",
    userId: "7",
    turnId: "fixture-turn",
    transcript: "こんにちは",
    reply: "fixture",
  });
  const commit = await handleVoiceCommit(new Request("https://worker.example/voice/commit", {
    method: "POST",
    headers: {
      authorization: "Bearer " + secret,
      "content-type": "application/json",
    },
    body: commitBody,
  }), env);
  assert.equal(commit.status, 200);
  assert.deepEqual(await commit.json(), { status: "committed" });
  assert.equal(seen.length, 2);
  assert.equal(new URL(seen[1].url).pathname, "/voice/commit");
  assert.equal(seen[1].headers.get("authorization"), null);
  assert.equal(await seen[1].text(), commitBody);
});

test("voice reply bridge is absent when the secret is not configured", async () => {
  const response = await handleVoiceReply(new Request("https://worker.example/voice/reply", {
    method: "POST",
    headers: {
      authorization: "Bearer " + "s".repeat(48),
      "content-type": "application/json",
    },
    body: "{}",
  }), {
    DISCORD_BOT: {
      idFromName() { throw new Error("must not route"); },
      get() { throw new Error("must not route"); },
    },
  });
  assert.equal(response.status, 404);
});

test("Workers AI backend sends the canonical conversation shape without tools", async () => {
  let call;
  const env = {
    AI: {
      async run(model, input) {
        call = { model, input };
        return { choices: [{ message: { content: "<think>hidden</think>こんにちは" } }] };
      },
    },
    WORKERS_AI_MODEL: "@cf/deepseek-ai/deepseek-v4-flash-0731",
    DOCICH_PERSONA: "canonical persona",
  };
  const result = await generateReply(env, [], {
    id: "100",
    guildId: "1",
    channelId: "10",
    authorId: "7",
    authorName: "話し手",
    content: "元気？",
    referenceId: null,
  });
  assert.equal(result, "こんにちは");
  assert.equal(call.model, "@cf/deepseek-ai/deepseek-v4-flash-0731");
  assert.equal(call.input.tool_choice, "none");
  assert.equal(call.input.max_tokens, 500);
  assert.equal(call.input.messages[0].role, "system");
  assert.match(call.input.messages[0].content, /^canonical persona/);
  assert.equal(JSON.parse(call.input.messages.at(-1).content).text, "元気？");
});

test("Workers AI retries once with a larger budget after an empty length-limited reply", async () => {
  const tokenBudgets = [];
  const env = {
    AI: {
      async run(_model, input) {
        tokenBudgets.push(input.max_tokens);
        if (tokenBudgets.length === 1) {
          return { choices: [{ message: { content: "" }, finish_reason: "length" }] };
        }
        return { choices: [{ message: { content: "こんにちは、同志。ご挨拶ありがとうございます。" }, finish_reason: "stop" }] };
      },
    },
    WORKERS_AI_MODEL: "@cf/deepseek-ai/deepseek-v4-flash-0731",
    DOCICH_PERSONA: "canonical persona",
  };
  const result = await generateReply(env, [], {
    id: "101",
    guildId: "1",
    channelId: "10",
    authorId: "7",
    authorName: "話し手",
    content: "hello",
    referenceId: null,
  });
  assert.equal(result, "こんにちは、同志。ご挨拶ありがとうございます。");
  assert.deepEqual(tokenBudgets, [500, 900]);
});

test("unsafe or empty model output is rejected and long output is bounded", () => {
  assert.throws(() => cleanReply("<analysis>private"), /empty_model_reply/);
  assert.throws(() => cleanReply(null), /invalid_model_reply/);
  assert.equal(cleanReply("x".repeat(1000)).length, 901);
});
