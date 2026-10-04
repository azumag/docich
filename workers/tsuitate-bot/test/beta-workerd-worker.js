// Local fixtures only. This entrypoint is never referenced by Cloudflare config.
import io from "socket.io-client/dist/socket.io.js";
import { BetaArena } from "../src/arena/beta-arena.js";
import { SINGLETON_NAME } from "../src/arena/durable-controller.js";
import { META_KEY, CHECKPOINT_KEY, RECORD_KEY } from "../src/arena/durable-store.js";
import { CONTROL_PATH, handleBetaControl } from "../src/arena/control.js";
import { fetchPublicResult } from "../src/adapters/beta-results.js";

export class RuntimeBetaArena extends BetaArena {
  makeSocket() {
    return io(this.env.LOCAL_SOCKET_ORIGIN, { transports: ["websocket"],
      auth: { token: "fixture-only-not-a-credential" }, autoConnect: false,
      forceNew: true, multiplex: false, reconnection: false, timeout: 1000 });
  }
  async resolveResult(gameId, color, options = {}) {
    return fetchPublicResult(gameId, color, { ...options, fetchImpl: (url, options) => {
      const parsed = new URL(url);
      if (parsed.origin !== "https://beta.tsuitate.info") throw new Error("fixture_origin_mismatch");
      return fetch(this.env.LOCAL_SOCKET_ORIGIN + "/public-result" + parsed.pathname, options);
    } });
  }
  async evidence() {
    const saved = await this.ctx.storage.get(CHECKPOINT_KEY);
    const record = await this.ctx.storage.get(RECORD_KEY);
    return { sqlite: Boolean(this.ctx.storage.sql),
      pendingPersisted: Boolean(saved?.active?.gate.pending),
      recordSaved: Boolean(record?.completed),
      communicationInterrupted: record?.communicationInterrupted ?? saved?.active?.record?.communicationInterrupted,
      validForTraining: record?.validForTraining,
      brainVersion: record?.brainVersion ?? saved?.active?.record?.brainVersion,
      decisions: record?.decisions.length ?? saved?.active?.record?.decisions.length ?? 0 };
  }
  alarmFixture() { return this.alarm(); }
  async pauseTerminalFixture() {
    const session = this.controller.session;
    await session.enqueue(() => session.pause("terminal_unconfirmed"));
    await this.controller.tail;
    // A synthetic v2 checkpoint under the current runtime: recovery must keep
    // its attribution and must never restore the live brain/socket.
    await this.ctx.storage.transaction(async (tx) => {
      const meta = await tx.get(META_KEY), saved = await tx.get(CHECKPOINT_KEY);
      meta.brainVersion = "tsuitate-brain-v2";
      saved.active.record.brainVersion = meta.brainVersion;
      await tx.put(META_KEY, meta); await tx.put(CHECKPOINT_KEY, saved);
    });
    return this.controller.status();
  }
  async recoveryRollbackFixture() {
    const settle = this.controller.settle;
    try {
      this.controller.settle = async (tx, meta) => {
        await settle.call(this.controller, tx, meta);
        throw new Error("fixture_terminal_cleanup_rollback");
      };
      await this.controller.reconcile({ runId: "recovery-fixture" });
      return { rolledBack: false };
    } catch {
      const saved = await this.ctx.storage.get(CHECKPOINT_KEY);
      return { rolledBack: (await this.controller.status()).state === "paused"
        && !(await this.ctx.storage.get(RECORD_KEY)) && Boolean(saved?.active?.gate.pending) };
    } finally { this.controller.settle = settle; }
  }
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
      if (path === "/pause-terminal") return Response.json(await actor.pauseTerminalFixture());
      if (path === "/recovery-rollback") return Response.json(await actor.recoveryRollbackFixture());
      return actor.fetch(request);
    } catch (error) {
      if (["token_not_configured", "not_singleton"].includes(error.message)) {
        return Response.json({ code: error.message }, { status: 409 });
      }
      throw error;
    }
  },
};
