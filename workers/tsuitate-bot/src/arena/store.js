import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, readdir, rename, unlink } from "node:fs/promises";
import { join, resolve } from "node:path";
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
  }

  async acquire() {
    await mkdir(join(this.directory, "games"), { recursive: true, mode: 0o700 });
    let lock;
    try { lock = await open(join(this.directory, "runner.lock"), "wx", 0o600); }
    catch (error) { if (error.code === "EEXIST") throw new Error("runner_locked"); throw error; }
    try { await lock.writeFile(JSON.stringify({ pid: process.pid, lockId: this.lockId })); }
    finally { await lock.close(); }
    this.locked = true;
    await this.rebuildDataset();
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
    if (!this.locked) throw new Error("store_not_locked");
    const record = normalizeGameRecord(raw);
    if (!record) throw new Error("invalid_game_record");
    const key = createHash("sha256").update(`${record.site}\n${record.gameId}`).digest("hex");
    const path = join(this.directory, "games", `${key}.json`);
    const text = JSON.stringify(record) + "\n";
    let previous;
    try { previous = await readFile(path, "utf8"); } catch (error) { if (error.code !== "ENOENT") throw error; }
    if (previous !== undefined && previous !== text) throw new Error("conflicting_game_record");
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

  async release() {
    if (!this.locked) return;
    const path = join(this.directory, "runner.lock");
    const lock = JSON.parse(await readFile(path, "utf8"));
    if (lock.lockId !== this.lockId) throw new Error("runner_lock_changed");
    await unlink(path);
    this.locked = false;
  }
}
