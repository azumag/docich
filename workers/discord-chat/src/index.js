import { DiscordBot } from "./bot.js";
import {
  VoiceBridgeError,
  authorizeVoiceBridge,
  readVoiceBridgeJson,
  validateVoiceBridgeInput,
} from "./voice-bridge.js";

const OBJECT_NAME = "singleton";

function botStub(env) {
  const id = env.DISCORD_BOT.idFromName(OBJECT_NAME);
  return env.DISCORD_BOT.get(id);
}

export { DiscordBot };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/internal/voice/reply") {
      try {
        if (!await authorizeVoiceBridge(request, env)) {
          return Response.json({ error: "voice_bridge_unauthorized" }, {
            status: 401,
            headers: { "cache-control": "no-store" },
          });
        }
        const input = validateVoiceBridgeInput(await readVoiceBridgeJson(request));
        const response = await botStub(env).fetch(new Request(
          "https://discord-bot.internal/voice/reply",
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(input),
          },
        ));
        return new Response(response.body, {
          status: response.status,
          headers: {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
          },
        });
      } catch (error) {
        if (error instanceof VoiceBridgeError) {
          return Response.json({ error: error.code }, {
            status: error.status,
            headers: { "cache-control": "no-store" },
          });
        }
        return Response.json({ error: "voice_bridge_unavailable" }, {
          status: 503,
          headers: { "cache-control": "no-store" },
        });
      }
    }
    if (request.method === "GET" && url.pathname === "/healthz") {
      try {
        const response = await botStub(env).fetch("https://discord-bot.internal/status");
        return new Response(await response.text(), {
          status: response.status,
          headers: {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
          },
        });
      } catch {
        return Response.json({
          configured: typeof env.DISCORD_BOT_TOKEN === "string" && env.DISCORD_BOT_TOKEN.length >= 20,
          connected: false, ready: false, pending: 0, fatal: "unavailable",
        }, {
          status: 503,
          headers: { "cache-control": "no-store" },
        });
      }
    }
    return new Response("not found", { status: 404 });
  },

  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(
      botStub(env)
        .fetch(new Request("https://discord-bot.internal/ensure", { method: "POST" }))
        .then((response) => {
          if (!response.ok) throw new Error("discord_bot_ensure_failed");
        }),
    );
  },
};
