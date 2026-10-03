import {
  beginConversation,
  failConversation,
  finishConversation,
  forgetScope,
  initializeMemory,
  markSending,
  memoryContext,
  validContext,
} from "./memory.js";
import {
  DiscordSendError,
  GATEWAY_INTENTS,
  OPCODE,
  gatewaySocketUrl,
  isAddressedMessage,
  isFatalGatewayClose,
  sendDiscordReply,
  stripBotMention,
} from "./discord.js";
import { generateReply } from "./llm.js";

const FAILURE_REPLY = "今は返答を作れませんでした。少し後でもう一度メンションしてください。";
const BUSY_REPLY = "今は返答待ちが多いため、少し後でもう一度メンションしてください。";
const FORGOTTEN_REPLY = "このチャンネルであなたと交わした会話の記憶を削除しました。";
const MAX_PENDING = 32;
const RESET_SESSION_CLOSE_CODES = new Set([1000, 1001, 4003, 4005, 4007, 4009]);

function safeLog(env, status, extra = {}) {
  try {
    console.log({
      event: "discord_gateway",
      status,
      codeVersion: env?.CF_VERSION_METADATA?.id ?? "local",
      ...extra,
    });
  } catch {
    // Diagnostics must never affect the bot.
  }
}

export class DiscordBot {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.sql = state.storage.sql;
    this.ws = null;
    this.connecting = null;
    this.ready = false;
    this.heartbeatTimer = null;
    this.heartbeatInterval = null;
    this.awaitingHeartbeatAck = false;
    this.pendingCount = 0;
    this.queue = Promise.resolve();
    this.botUserId = null;
    initializeMemory(this.sql);
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/ensure") {
      const result = await this.ensureConnected();
      return Response.json(result);
    }
    if (request.method === "GET" && url.pathname === "/status") {
      return Response.json(await this.status());
    }
    return new Response("not found", { status: 404 });
  }

  async alarm() {
    try {
      await this.ensureConnected();
    } catch {
      await this.#scheduleReconnect(5000);
    }
  }

  #configured() {
    const token = this.env.DISCORD_BOT_TOKEN;
    return typeof token === "string" && token.length >= 20 && !/\s/.test(token);
  }

  async status() {
    const fatal = await this.state.storage.get("fatal_reason");
    const fatalUntil = Number(await this.state.storage.get("fatal_until") ?? 0);
    return {
      configured: this.#configured(),
      connected: this.ws?.readyState === 1,
      ready: this.ready,
      pending: this.pendingCount,
      fatal: typeof fatal === "string" && fatalUntil > Date.now() ? fatal : null,
    };
  }

  async ensureConnected() {
    if (!this.#configured()) return { status: "unconfigured" };
    const fatal = await this.state.storage.get("fatal_reason");
    const fatalUntil = Number(await this.state.storage.get("fatal_until") ?? 0);
    if (fatal && fatalUntil > Date.now()) {
      return { status: "fatal_cooldown", reason: fatal, retryAt: fatalUntil };
    }
    if (fatal || fatalUntil) {
      await this.state.storage.delete(["fatal_reason", "fatal_until"]);
    }
    if (this.ws && (this.ws.readyState === 0 || this.ws.readyState === 1)) {
      return { status: this.ready ? "ready" : "connecting" };
    }
    if (this.connecting) return this.connecting;
    this.connecting = this.#connect()
      .catch(async () => {
        await this.#scheduleReconnect(5000);
        return { status: "retry_scheduled" };
      })
      .finally(() => { this.connecting = null; });
    return this.connecting;
  }

  async #connect() {
    const token = this.#token();
    const identity = await this.#currentBotIdentity(token);
    const storedBotId = await this.state.storage.get("bot_user_id");
    if (storedBotId && String(storedBotId) !== identity.id) {
      await this.#clearSession();
    }
    this.botUserId = identity.id;
    await this.state.storage.put({
      bot_user_id: identity.id,
      bot_username: identity.username,
    });
    safeLog(this.env, "bot_identity_verified", { botUsername: identity.username });

    const sessionId = await this.state.storage.get("gateway_session_id");
    const resumeUrl = await this.state.storage.get("resume_gateway_url");
    let base = sessionId && resumeUrl ? resumeUrl : await this.#gatewayUrl(token);
    const ws = new WebSocket(gatewaySocketUrl(base));
    this.ws = ws;
    this.ready = false;
    this.awaitingHeartbeatAck = false;

    ws.addEventListener("open", () => safeLog(this.env, "socket_open"));
    ws.addEventListener("message", (event) => {
      this.state.waitUntil(this.#onGatewayMessage(ws, event.data));
    });
    ws.addEventListener("close", (event) => {
      this.state.waitUntil(this.#onGatewayClose(ws, event.code));
    });
    ws.addEventListener("error", () => {
      safeLog(this.env, "socket_error");
    });
    return { status: "connecting" };
  }

  async #currentBotIdentity(token) {
    const response = await fetch("https://discord.com/api/v10/users/@me", {
      headers: { authorization: "Bot " + token },
    });
    if (!response.ok) throw new Error("discord_identity_failed");
    const body = await response.json();
    if (!body || typeof body.id !== "string" || typeof body.username !== "string") {
      throw new Error("discord_identity_invalid");
    }
    return { id: body.id, username: body.username };
  }

  async #gatewayUrl(token) {
    const cached = await this.state.storage.get("gateway_url");
    if (typeof cached === "string" && cached.startsWith("wss://")) return cached;
    const response = await fetch("https://discord.com/api/v10/gateway/bot", {
      headers: { authorization: "Bot " + token },
    });
    if (!response.ok) throw new Error("gateway_discovery_failed");
    const body = await response.json();
    if (!body || typeof body.url !== "string" || !body.url.startsWith("wss://")) {
      throw new Error("gateway_discovery_invalid");
    }
    await this.state.storage.put("gateway_url", body.url);
    return body.url;
  }

  #token() {
    const token = this.env.DISCORD_BOT_TOKEN;
    if (typeof token !== "string" || token.length < 20 || /\s/.test(token)) {
      throw new Error("discord_token_unavailable");
    }
    return token;
  }

  #sendGateway(op, d) {
    if (!this.ws || this.ws.readyState !== 1) throw new Error("gateway_not_open");
    const payload = JSON.stringify({ op, d });
    if (new TextEncoder().encode(payload).byteLength > 4096) throw new Error("gateway_payload_too_large");
    this.ws.send(payload);
  }

  async #onGatewayMessage(ws, raw) {
    if (ws !== this.ws || typeof raw !== "string") return;
    let packet;
    try {
      packet = JSON.parse(raw);
    } catch {
      ws.close(4002, "invalid json");
      return;
    }
    if (Number.isInteger(packet.s)) await this.state.storage.put("gateway_seq", packet.s);

    switch (packet.op) {
      case OPCODE.HELLO:
        await this.#onHello(packet.d);
        break;
      case OPCODE.HEARTBEAT_ACK:
        this.awaitingHeartbeatAck = false;
        break;
      case OPCODE.HEARTBEAT:
        await this.#sendHeartbeat(false);
        break;
      case OPCODE.RECONNECT:
        ws.close(4000, "resume");
        break;
      case OPCODE.INVALID_SESSION:
        if (!packet.d) await this.#clearSession();
        ws.close(4000, "invalid session");
        await this.#scheduleReconnect(1000 + Math.floor(Math.random() * 4000));
        break;
      case OPCODE.DISPATCH:
        await this.#onDispatch(packet.t, packet.d);
        break;
      default:
        break;
    }
  }

  async #onHello(data) {
    const interval = Number(data?.heartbeat_interval);
    if (!Number.isFinite(interval) || interval < 1000) {
      this.ws?.close(4000, "bad heartbeat");
      return;
    }
    this.heartbeatInterval = interval;
    this.#clearHeartbeatTimer();
    const jitter = Math.floor(Math.random() * interval);
    this.heartbeatTimer = setTimeout(() => {
      this.state.waitUntil(this.#sendHeartbeat(true));
    }, jitter);

    const token = this.#token();
    const sessionId = await this.state.storage.get("gateway_session_id");
    const seq = await this.state.storage.get("gateway_seq");
    if (sessionId && Number.isInteger(seq)) {
      this.#sendGateway(OPCODE.RESUME, {
        token,
        session_id: sessionId,
        seq,
      });
    } else {
      this.#sendGateway(OPCODE.IDENTIFY, {
        token,
        intents: GATEWAY_INTENTS,
        properties: {
          os: "cloudflare-workers",
          browser: "docich",
          device: "docich",
        },
      });
    }
  }

  async #sendHeartbeat(scheduleNext) {
    if (!this.ws || this.ws.readyState !== 1) return;
    if (scheduleNext && this.awaitingHeartbeatAck) {
      this.ws.close(4000, "heartbeat timeout");
      return;
    }
    const seq = await this.state.storage.get("gateway_seq");
    this.#sendGateway(OPCODE.HEARTBEAT, Number.isInteger(seq) ? seq : null);
    this.awaitingHeartbeatAck = true;
    if (scheduleNext && this.heartbeatInterval) {
      this.#clearHeartbeatTimer();
      this.heartbeatTimer = setTimeout(() => {
        this.state.waitUntil(this.#sendHeartbeat(true));
      }, this.heartbeatInterval);
    }
  }

  #clearHeartbeatTimer() {
    if (this.heartbeatTimer !== null) clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  async #onGatewayClose(ws, code) {
    if (ws !== this.ws) return;
    this.#clearHeartbeatTimer();
    this.ws = null;
    this.ready = false;
    this.awaitingHeartbeatAck = false;
    if (isFatalGatewayClose(code)) {
      const reason = "gateway_close_" + String(code);
      const fatalUntil = Date.now() + 15 * 60 * 1000;
      await this.state.storage.put({
        fatal_reason: reason,
        fatal_until: fatalUntil,
      });
      await this.state.storage.setAlarm(fatalUntil);
      safeLog(this.env, "fatal_close", { code: Number(code) });
      return;
    }
    if (RESET_SESSION_CLOSE_CODES.has(Number(code))) await this.#clearSession();
    safeLog(this.env, "closed", { code: Number(code) });
    await this.#scheduleReconnect(1500);
  }

  async #clearSession() {
    await this.state.storage.delete([
      "gateway_session_id",
      "resume_gateway_url",
      "gateway_seq",
      "bot_user_id",
      "bot_username",
    ]);
    this.botUserId = null;
  }

  async #scheduleReconnect(delayMs) {
    const fatal = await this.state.storage.get("fatal_reason");
    const fatalUntil = Number(await this.state.storage.get("fatal_until") ?? 0);
    if (fatal && fatalUntil > Date.now()) {
      await this.state.storage.setAlarm(fatalUntil);
      return;
    }
    await this.state.storage.setAlarm(Date.now() + Math.max(1000, Number(delayMs) || 1000));
  }

  async #onDispatch(type, data) {
    if (type === "READY") {
      if (typeof data?.session_id !== "string" || typeof data?.resume_gateway_url !== "string"
          || typeof data?.user?.id !== "string") {
        this.ws?.close(4000, "invalid ready");
        return;
      }
      this.botUserId = data.user.id;
      await this.state.storage.put({
        gateway_session_id: data.session_id,
        resume_gateway_url: data.resume_gateway_url,
        bot_user_id: data.user.id,
        bot_username: typeof data.user.username === "string" ? data.user.username : "",
      });
      await this.state.storage.delete(["fatal_reason", "fatal_until"]);
      this.ready = true;
      safeLog(this.env, "ready", {
        botUsername: typeof data.user.username === "string" ? data.user.username : "",
      });
      return;
    }
    if (type === "RESUMED") {
      this.ready = true;
      if (!this.botUserId) this.botUserId = await this.state.storage.get("bot_user_id");
      safeLog(this.env, "resumed");
      return;
    }
    if (type === "MESSAGE_CREATE") {
      await this.#acceptMessage(data);
      return;
    }
    if (type === "MESSAGE_DELETE") {
      this.#forgetMessages(data?.guild_id, data?.channel_id, [data?.id]);
      return;
    }
    if (type === "MESSAGE_DELETE_BULK") {
      this.#forgetMessages(data?.guild_id, data?.channel_id, Array.isArray(data?.ids) ? data.ids : []);
      return;
    }
    if (type === "MESSAGE_UPDATE" && Object.hasOwn(data ?? {}, "content")) {
      this.#forgetMessages(data?.guild_id, data?.channel_id, [data?.id]);
    }
  }

  #forgetMessages(guildId, channelId, ids) {
    if (!guildId || !channelId) return;
    const cleanIds = ids.filter((id) => typeof id === "string" && id.length > 0);
    if (!cleanIds.length) return;
    forgetScope(this.sql, guildId, channelId, { messageIds: cleanIds });
  }

  async #acceptMessage(message) {
    safeLog(this.env, "message_create_received");
    if (!message?.guild_id || !message?.channel_id || !message?.id) {
      safeLog(this.env, "message_ignored_missing_scope");
      return;
    }
    if (message.author?.bot || message.webhook_id) {
      safeLog(this.env, "message_ignored_automated");
      return;
    }
    if (![0, 19].includes(Number(message.type ?? 0))) {
      safeLog(this.env, "message_ignored_type");
      return;
    }
    if (typeof message.content !== "string") {
      safeLog(this.env, "message_ignored_content");
      return;
    }
    const botId = this.botUserId || await this.state.storage.get("bot_user_id");
    if (!botId) {
      safeLog(this.env, "message_ignored_missing_bot_id");
      return;
    }
    if (!isAddressedMessage(message, botId)) {
      safeLog(this.env, "message_ignored_no_mention", {
        mentionCount: Array.isArray(message.mentions) ? message.mentions.length : 0,
        contentHasAnyUserMention: /<@!?\d+>/.test(message.content),
      });
      return;
    }
    safeLog(this.env, "mention_received");
    const timestamp = Date.parse(message.timestamp);
    if (!Number.isFinite(timestamp)) return;
    const ageSeconds = (Date.now() - timestamp) / 1000;
    if (ageSeconds < 0 || ageSeconds > 120) return;

    const event = {
      id: message.id,
      guildId: message.guild_id,
      channelId: message.channel_id,
      authorId: message.author?.id,
      authorName: message.member?.nick || message.author?.global_name || message.author?.username || "",
      content: stripBotMention(message.content, botId),
      referenceId: message.message_reference?.message_id ?? null,
      createdAt: timestamp / 1000,
    };
    if (!event.authorId) return;

    if (this.pendingCount >= MAX_PENDING) {
      this.state.waitUntil(sendDiscordReply(this.#token(), event, BUSY_REPLY).catch(() => {}));
      return;
    }
    this.pendingCount += 1;
    const task = this.queue
      .then(() => this.#processMention(event))
      .finally(() => { this.pendingCount -= 1; });
    this.queue = task.catch(() => {});
    this.state.waitUntil(task);
  }

  async #processMention(event) {
    let seq = null;
    let attemptedSend = false;
    let stage = "memory_begin";
    try {
      seq = beginConversation(this.sql, event);
      if (seq === null) return;
      if (event.content.trim() === "記憶を削除") {
        forgetScope(this.sql, event.guildId, event.channelId, { authorId: event.authorId });
        attemptedSend = true;
        await sendDiscordReply(this.#token(), event, FORGOTTEN_REPLY);
        return;
      }
      stage = "memory_context";
      const context = memoryContext(this.sql, event, seq);
      stage = "workers_ai";
      const reply = await generateReply(this.env, context.messages, event);
      if (!validContext(this.sql, seq, context)) {
        failConversation(this.sql, seq);
        return;
      }
      if (!markSending(this.sql, seq)) return;
      attemptedSend = true;
      stage = "discord_send";
      const replyId = await sendDiscordReply(this.#token(), event, reply);
      stage = "memory_finish";
      finishConversation(this.sql, seq, replyId, reply);
      safeLog(this.env, "reply_sent");
    } catch (error) {
      if (seq !== null) failConversation(this.sql, seq);
      let noticeDiscordStatus = null;
      if (!attemptedSend) {
        try {
          await sendDiscordReply(this.#token(), event, FAILURE_REPLY);
        } catch (noticeError) {
          if (noticeError instanceof DiscordSendError) noticeDiscordStatus = noticeError.status;
        }
      }
      safeLog(this.env, "reply_failed", {
        stage,
        discordStatus: error instanceof DiscordSendError ? error.status : null,
        noticeDiscordStatus,
      });
    }
  }
}
