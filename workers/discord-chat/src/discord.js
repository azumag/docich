export const GATEWAY_VERSION = 10;
export const GATEWAY_INTENTS = (1 << 0) | (1 << 9) | (1 << 15);
export const GATEWAY_URL = "wss://gateway.discord.gg";

export const OPCODE = Object.freeze({
  DISPATCH: 0,
  HEARTBEAT: 1,
  IDENTIFY: 2,
  RESUME: 6,
  RECONNECT: 7,
  INVALID_SESSION: 9,
  HELLO: 10,
  HEARTBEAT_ACK: 11,
});

const FATAL_CLOSE_CODES = new Set([4004, 4010, 4011, 4012, 4013, 4014]);

export class DiscordSendError extends Error {
  constructor(status) {
    super("discord_send_failed");
    this.name = "DiscordSendError";
    this.status = Number(status);
  }
}

export function gatewaySocketUrl(base) {
  const url = new URL(base || GATEWAY_URL);
  url.searchParams.set("v", String(GATEWAY_VERSION));
  url.searchParams.set("encoding", "json");
  return url.toString();
}

export function isFatalGatewayClose(code) {
  return FATAL_CLOSE_CODES.has(Number(code));
}

export function stripBotMention(content, botId, roleId = null) {
  const id = String(botId);
  let text = String(content ?? "")
    .split("<@" + id + ">").join("")
    .split("<@!" + id + ">").join("");
  if (roleId) {
    text = text.split("<@&" + String(roleId) + ">").join("");
  }
  return text.trim() || "（呼びかけ）";
}

export function isAddressedMessage(message, botId) {
  const id = String(botId);
  if (Array.isArray(message?.mentions)
      && message.mentions.some((user) => String(user?.id) === id)) {
    return true;
  }
  const content = typeof message?.content === "string" ? message.content : "";
  return content.includes("<@" + id + ">") || content.includes("<@!" + id + ">");
}

export function managedBotRoleId(roles, botId) {
  const id = String(botId);
  if (!Array.isArray(roles)) return null;
  const role = roles.find((item) => item?.managed === true
    && String(item?.tags?.bot_id ?? "") === id
    && typeof item?.id === "string");
  return role?.id ?? null;
}

export function isAddressedRoleMessage(message, roleId) {
  if (!roleId) return false;
  const id = String(roleId);
  if (Array.isArray(message?.mention_roles)
      && message.mention_roles.some((value) => String(value) === id)) {
    return true;
  }
  const content = typeof message?.content === "string" ? message.content : "";
  return content.includes("<@&" + id + ">");
}

export async function sendDiscordReply(token, event, text) {
  const url = "https://discord.com/api/v10/channels/"
    + encodeURIComponent(String(event.channelId)) + "/messages";
  const response = await fetch(url, {
    method: "POST",
    headers: {
      authorization: "Bot " + token,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      content: text,
      message_reference: {
        message_id: String(event.id),
        channel_id: String(event.channelId),
        guild_id: String(event.guildId),
        fail_if_not_exists: false,
      },
      allowed_mentions: { parse: [], replied_user: false },
      flags: 4,
    }),
  });
  if (!response.ok) throw new DiscordSendError(response.status);
  const body = await response.json();
  if (!body || typeof body.id !== "string") throw new DiscordSendError(0);
  return body.id;
}
