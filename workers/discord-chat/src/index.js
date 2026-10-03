import { DiscordBot } from "./bot.js";

const OBJECT_NAME = "singleton";

function botStub(env) {
  const id = env.DISCORD_BOT.idFromName(OBJECT_NAME);
  return env.DISCORD_BOT.get(id);
}

export { DiscordBot };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
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
        return Response.json({ connected: false, ready: false, pending: 0, fatal: "unavailable" }, {
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
