import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, readdir, rename, unlink } from "node:fs/promises";
import { join, resolve } from "node:path";
import { hostname } from "node:os";
import { normalizeGameRecord } from "../training/index.js";

async function atomicWrite(path, text) {
  const temporary = `${path}.${randomUUID()}.tmp`;
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(text); await handle.sync(); } finally { await handle.close(); }
  await rename(temporary, path);
}

/** One writer per directory. Terminal JSON files are the source of games.jsonl. */
export class ArenaStore {
  constructor(directory) {
    this.directory = resolve(directory);
    this.lockId = randomUUID();
    this.locked = false;
    this.mutations = Promise.resolve();
  }

  async acquire() {
    await mkdir(join(this.directory, "games"), { recursive: true, mode: 0o700 });
    // Serialize creation and recovery. A crashed guard remains fail-closed and
    // requires manual inspection; elapsed time never grants ownership.
    const guardPath = join(this.directory, "runner.lock.guard");
    let guard;
    try { guard = await open(guardPath, "wx", 0o600); }
    catch (error) { if (error.code === "EEXIST") throw new Error("runner_locked"); throw error; }
    try {
      await this.#acquireOwnerLock();
    } finally {
      await guard.close();
      await unlink(guardPath);
    }
    await this.rebuildDataset();
  }

  async #acquireOwnerLock() {
    const path = join(this.directory, "runner.lock");
    let previous;
    try { previous = await readFile(path, "utf8"); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
    if (previous !== undefined) {
      let owner;
      try { owner = JSON.parse(previous); } catch { throw new Error("runner_locked"); }
      if (owner?.hostname !== hostname() || !Number.isSafeInteger(owner.pid) || owner.pid <= 0
          || typeof owner.lockId !== "string") throw new Error("runner_locked");
      let dead = false;
      try { process.kill(owner.pid, 0); }
      catch (error) { dead = error.code === "ESRCH"; }
      // A reused PID, permission error, unknown host or legacy lock stays locked.
      if (!dead || await readFile(path, "utf8") !== previous) throw new Error("runner_locked");
      await unlink(path);
    }
    let lock;
    try { lock = await open(path, "wx", 0o600); }
    catch (error) { if (error.code === "EEXIST") throw new Error("runner_locked"); throw error; }
    try { await lock.writeFile(JSON.stringify({ pid: process.pid, hostname: hostname(), lockId: this.lockId })); await lock.sync(); }
    finally { await lock.close(); }
    this.locked = true;
  }

  async load() {
    try { return JSON.parse(await readFile(join(this.directory, "checkpoint.json"), "utf8")); }
    catch (error) { if (error.code === "ENOENT") return null; throw new Error("invalid_checkpoint"); }
  }

  async save(checkpoint) {
    if (!this.locked) throw new Error("store_not_locked");
    await atomicWrite(join(this.directory, "checkpoint.json"), JSON.stringify(checkpoint) + "\n");
  }

  async finish(raw) {
    return this.#serialize(() => this.#finish(raw));
  }

  #serialize(operation) {
    const result = this.mutations.then(operation);
    this.mutations = result.catch(() => {});
    return result;
  }

  async #finish(raw) {
    if (!this.locked) throw new Error("store_not_locked");
    const record = normalizeGameRecord(raw);
    if (!record) throw new Error("invalid_game_record");
    const key = createHash("sha256").update(`${record.site}\n${record.gameId}`).digest("hex");
    const path = join(this.directory, "games", `${key}.json`);
    const text = JSON.stringify(record) + "\n";
    let previous;
    try { previous = await readFile(path, "utf8"); } catch (error) { if (error.code !== "ENOENT") throw error; }
    if (previous !== undefined) {
      const normalizedPrevious = normalizeGameRecord(JSON.parse(previous));
      if (!normalizedPrevious || JSON.stringify(normalizedPrevious) + "\n" !== text) throw new Error("conflicting_game_record");
    }
    if (previous === undefined) await atomicWrite(path, text);
    await this.rebuildDataset();
    return record;
  }

  async rebuildDataset() {
    const directory = join(this.directory, "games");
    const names = (await readdir(directory)).filter((name) => /^[a-f0-9]{64}\.json$/.test(name)).sort();
    const records = [];
    for (const name of names) {
      const record = normalizeGameRecord(JSON.parse(await readFile(join(directory, name), "utf8")));
      if (!record) throw new Error("invalid_game_record");
      records.push(JSON.stringify(record));
    }
    await atomicWrite(join(this.directory, "games.jsonl"), records.length ? records.join("\n") + "\n" : "");
  }

  /** Only the result resolver can fill a missing outcome; keep all live evidence. */
  async refreshUnknownResults(resolveResult) {
    return this.#serialize(() => this.#refreshUnknownResults(resolveResult));
  }

  async #refreshUnknownResults(resolveResult) {
    if (!this.locked) throw new Error("store_not_locked");
    const directory = join(this.directory, "games");
    const names = (await readdir(directory)).filter((name) => /^[a-f0-9]{64}\.json$/.test(name)).sort();
    let updated = 0;
    for (const name of names) {
      const path = join(directory, name);
      const text = await readFile(path, "utf8");
      const previous = normalizeGameRecord(JSON.parse(text));
      if (!previous) throw new Error("invalid_game_record");
      if (previous.site !== "beta.tsuitate.info" || !previous.completed
          || previous.outcome !== "unknown" || previous.reason !== "unknown") continue;
      let result;
      try { result = await resolveResult(previous.gameId, previous.color); } catch { continue; }
      if (result?.gameId !== previous.gameId || result.source !== "public_replay"
          || !["win", "loss", "draw"].includes(result.outcome) || result.reason === "unknown") continue;
      const record = normalizeGameRecord({ ...previous, outcome: result.outcome, reason: result.reason,
        startedAt: result.startedAt, endedAt: result.endedAt,
        resultSource: "public_replay", resultConfidence: "verified", validForTraining: true });
      if (!record) continue;
      // Do not replace a confirmed record changed while the lookup was pending.
      if (await readFile(path, "utf8") !== text) continue;
      await atomicWrite(path, JSON.stringify(record) + "\n");
      updated += 1;
    }
    await this.rebuildDataset();
    return updated;
  }

  async release() {
    if (!this.locked) return;
    const path = join(this.directory, "runner.lock");
    const lock = JSON.parse(await readFile(path, "utf8"));
    if (lock.lockId !== this.lockId) throw new Error("runner_lock_changed");
    await unlink(path);
    this.locked = false;
  }
}
