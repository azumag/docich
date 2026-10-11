import { DiscordBot } from "./bot.js";

const OBJECT_NAME = "singleton";
const VOICE_MAX_BODY_BYTES = 16_384;

const validVoiceToken = (value) =>
  typeof value === "string" &&
  value.length >= 32 &&
  value.length <= 4096 &&
  !/\s/.test(value);

function sameToken(left, right) {
  if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) {
    return false;
  }
  let diff = 0;
  for (let index = 0; index < left.length; index += 1) {
    diff |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return diff === 0;
}

const json = (body, status = 200) =>
  Response.json(body, {
    status,
    headers: { "cache-control": "no-store" },
  });

function botStub(env) {
  const id = env.DISCORD_BOT.idFromName(OBJECT_NAME);
  return env.DISCORD_BOT.get(id);
}

async function handleVoiceRequest(request, env, internalPath) {
  const expected = env.DISCORD_VOICE_INTERNAL_TOKEN;
  if (!validVoiceToken(expected)) return new Response("not found", { status: 404 });

  const authorization = request.headers.get("authorization");
  const provided = authorization?.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!validVoiceToken(provided) || !sameToken(provided, expected)) {
    return json({ error: "unauthorized" }, 401);
  }

  if (!String(request.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) {
    return json({ error: "invalid_request" }, 415);
  }

  const declaredLength = Number(request.headers.get("content-length") ?? 0);
  if (Number.isFinite(declaredLength) && declaredLength > VOICE_MAX_BODY_BYTES) {
    return json({ error: "request_too_large" }, 413);
  }

  let body;
  try {
    body = await request.text();
  } catch {
    return json({ error: "invalid_request" }, 400);
  }
  if (new TextEncoder().encode(body).byteLength > VOICE_MAX_BODY_BYTES) {
    return json({ error: "request_too_large" }, 413);
  }

  try {
    const response = await botStub(env).fetch(new Request(
      "https://discord-bot.internal" + internalPath,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
      },
    ));
    return new Response(await response.text(), {
      status: response.status,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  } catch {
    return json({ error: "unavailable" }, 503);
  }
}

async function handleVoiceReply(request, env) {
  return handleVoiceRequest(request, env, "/voice/reply");
}

async function handleVoiceCommit(request, env) {
  return handleVoiceRequest(request, env, "/voice/commit");
}

export { DiscordBot, handleVoiceReply, handleVoiceCommit };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/voice/reply") {
      return handleVoiceReply(request, env);
    }
    if (request.method === "POST" && url.pathname === "/voice/commit") {
      return handleVoiceCommit(request, env);
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
