// Local fixtures only. This entrypoint is never referenced by Cloudflare config.
import io from "socket.io-client/dist/socket.io.js";
import { BetaArena } from "../src/arena/beta-arena.js";
import { SINGLETON_NAME } from "../src/arena/durable-controller.js";
import { META_KEY, CHECKPOINT_KEY, RECORD_KEY } from "../src/arena/durable-store.js";
import { CONTROL_PATH, handleBetaControl } from "../src/arena/control.js";

export class RuntimeBetaArena extends BetaArena {
  makeSocket() {
    return io(this.env.LOCAL_SOCKET_ORIGIN, { transports: ["websocket"],
      auth: { token: "fixture-only-not-a-credential" }, autoConnect: false,
      forceNew: true, multiplex: false, reconnection: false, timeout: 1000 });
  }
  async resolveResult(gameId) {
    if (!this.controller.session?.terminalSeen) return null;
    return { gameId, outcome: "win", reason: "checkmate", source: "public_replay",
      endedAt: new Date(Date.now() + 1000).toISOString() };
  }
  async evidence() {
    const saved = await this.ctx.storage.get(CHECKPOINT_KEY);
    const record = await this.ctx.storage.get(RECORD_KEY);
    return { sqlite: Boolean(this.ctx.storage.sql),
      pendingPersisted: Boolean(saved?.active?.gate.pending),
      recordSaved: Boolean(record?.completed),
      communicationInterrupted: record?.communicationInterrupted ?? saved?.active?.record?.communicationInterrupted,
      validForTraining: record?.validForTraining,
      decisions: record?.decisions.length ?? saved?.active?.record?.decisions.length ?? 0 };
  }
  alarmFixture() { return this.alarm(); }
  async rollbackFixture() {
    const prior = await this.ctx.storage.get(META_KEY);
    try {
      await this.ctx.storage.transaction(async (tx) => {
        await tx.put(META_KEY, { ...prior, state: "fixture_poison" });
        throw new Error("fixture_rollback");
      });
    } catch { /* deliberate local SQLite transaction rollback */ }
    return { rolledBack: JSON.stringify(prior) === JSON.stringify(await this.ctx.storage.get(META_KEY)) };
  }
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === CONTROL_PATH) return handleBetaControl(request, env);
    const name = path === "/wrong-singleton" ? "another-actor" : SINGLETON_NAME;
    const actor = env.BETA_ARENA.get(env.BETA_ARENA.idFromName(name));
    try {
      if (path === "/status") return Response.json(await actor.status());
      if (path === "/start" || path === "/wrong-singleton") return Response.json(await actor.start({ runId: "local-fixture" }));
      if (path === "/stop") return Response.json(await actor.stop());
      if (path === "/alarm") { await actor.alarmFixture(); return Response.json(await actor.status()); }
      if (path === "/evidence") return Response.json(await actor.evidence());
      if (path === "/rollback") return Response.json(await actor.rollbackFixture());
      return actor.fetch(request);
    } catch (error) {
      if (["arena_disabled", "token_not_configured", "not_singleton"].includes(error.message)) {
        return Response.json({ code: error.message }, { status: 409 });
      }
      throw error;
    }
  },
};
