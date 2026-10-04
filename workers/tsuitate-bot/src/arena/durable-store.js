import { normalizeGameRecord } from "../training/index.js";

export const META_KEY = "beta:meta";
export const CHECKPOINT_KEY = "beta:checkpoint";
export const RECORD_KEY = "beta:terminal";
// SQLite DO key + value limit is 2 MB. Leave serialization/key headroom.
const MAX_BYTES = 1024 * 1024;

function bounded(value) {
  if (new TextEncoder().encode(JSON.stringify(value)).length > MAX_BYTES) throw new Error("checkpoint_too_large");
  return value;
}

/** One reserved run, one terminal record; writes are fenced across restores. */
export class DurableArenaStore {
  constructor(storage, runId, generation) {
    this.storage = storage;
    this.runId = runId;
    this.generation = generation;
  }

  async transaction(operation) {
    return this.storage.transaction(async (tx) => {
      const meta = await tx.get(META_KEY);
      if (meta?.runId !== this.runId || meta.generation !== this.generation) throw new Error("stale_session");
      return operation(tx, meta);
    });
  }

  async save(value) {
    bounded(value);
    await this.transaction(async (tx, meta) => {
      if (value?.version !== 1) throw new Error("invalid_checkpoint");
      if (meta.state === "finished" && value.active !== null) throw new Error("run_finished");
      const gameId = value.active?.gameId ?? value.finishedRecord?.gameId;
      if (gameId && meta.gameId && gameId !== meta.gameId) throw new Error("unexpected_game");
      if (gameId) {
        meta.gameId = gameId;
        meta.state = meta.stopRequested ? "draining" : "playing";
        await tx.put(META_KEY, meta);
      }
      await tx.put(CHECKPOINT_KEY, value);
    });
  }

  async finish(raw) {
    const record = normalizeGameRecord(raw);
    if (!record?.completed || record.site !== "beta.tsuitate.info") throw new Error("invalid_game_record");
    bounded(record);
    await this.transaction(async (tx, meta) => {
      if (meta.gameId && record.gameId !== meta.gameId) throw new Error("unexpected_game");
      const prior = await tx.get(RECORD_KEY);
      if (prior && JSON.stringify(prior) !== JSON.stringify(record)) throw new Error("conflicting_game_record");
      await tx.put(RECORD_KEY, record);
      await tx.put(META_KEY, { ...meta, gameId: record.gameId, state: "finished", completedGames: 1, errorCode: null });
    });
  }
}
