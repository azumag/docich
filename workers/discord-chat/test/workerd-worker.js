import { DiscordBot } from "../src/bot.js";
import {
  beginConversation,
  finishConversation,
  forgetScope,
  initializeMemory,
  markSending,
  memoryContext,
} from "../src/memory.js";

export { DiscordBot };

export class RuntimeMemory {
  constructor(state) {
    this.sql = state.storage.sql;
    initializeMemory(this.sql);
  }

  async fetch(request) {
    if (request.method !== "POST" || new URL(request.url).pathname !== "/probe") {
      return new Response("not found", { status: 404 });
    }
    const first = {
      id: "workerd-100",
      guildId: "1",
      channelId: "10",
      authorId: "7",
      authorName: "話し手",
      content: "猫の名前はタマ",
      referenceId: null,
      createdAt: 1,
    };
    const firstSeq = beginConversation(this.sql, first);
    if (!firstSeq || !markSending(this.sql, firstSeq)
        || !finishConversation(this.sql, firstSeq, "workerd-200", "覚えました")) {
      return Response.json({ error: "seed_failed" }, { status: 500 });
    }

    const sameScope = {
      ...first,
      id: "workerd-101",
      content: "猫の名前は？",
      createdAt: 2,
    };
    const sameSeq = beginConversation(this.sql, sameScope);
    const recalled = memoryContext(this.sql, sameScope, sameSeq);

    const otherScope = {
      ...first,
      id: "workerd-102",
      guildId: "2",
      content: "猫の名前は？",
      createdAt: 3,
    };
    const otherSeq = beginConversation(this.sql, otherScope);
    const separated = memoryContext(this.sql, otherScope, otherSeq);

    forgetScope(this.sql, "1", "10", { authorId: "7" });
    const retained = this.sql.exec(
      "SELECT COUNT(*) AS count FROM conversations WHERE content<>'' OR reply<>'' OR author_name<>''",
    ).one();
    const terms = this.sql.exec("SELECT COUNT(*) AS count FROM terms").one();

    return Response.json({
      recalled: recalled.messages.some((message) => message.content.includes("猫の名前はタマ")),
      separated: separated.messages.length === 0,
      scrubbed: Number(retained.count) === 1 && Number(terms.count) === 0,
    });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/production-health") {
      const id = env.PRODUCTION_BOT.idFromName("singleton");
      return env.PRODUCTION_BOT.get(id).fetch("https://production.internal/status");
    }
    if (url.pathname === "/memory-probe" && request.method === "POST") {
      const id = env.MEMORY_TEST.idFromName("singleton");
      return env.MEMORY_TEST.get(id).fetch(new Request("https://memory.internal/probe", { method: "POST" }));
    }
    return new Response("not found", { status: 404 });
  },
};
