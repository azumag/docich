#!/usr/bin/env node
/**
 * Backfill archived Tsuitate games from the finished-run list.
 *
 * The owner-only Worker control lists finished runs stored in Cloudflare
 * (`beta:run:<runId>` receipts) as ``{runId, gameId, brainVersion}``. This
 * script fetches each game's *public* replay and hands it to replay-lab's
 * fail-closed archiver. It never reads Worker records, observations or any
 * credential, and it never writes outside ``--out``.
 */
import { readFile, readdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { archiveGame } from "./replay-lab.mjs";

export const REPLAY_HOST = "beta.tsuitate.info";
export const MAX_REPLAY_BYTES = 256 * 1024;
export const REPLAY_TIMEOUT_MS = 5000;
export const MAX_GAMES = 20;
const GAME_ID = /^[A-Za-z0-9_-]{1,128}$/;

export function replayUrl(gameId) {
  if (typeof gameId !== "string" || !GAME_ID.test(gameId)) throw new Error("invalid_game_id");
  return `https://${REPLAY_HOST}/games/${encodeURIComponent(gameId)}/__data.json`;
}

/** GET one public replay: fixed host, no redirect, bounded size and time. */
export async function fetchReplay(gameId, { fetchImpl = fetch } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REPLAY_TIMEOUT_MS);
  try {
    const response = await fetchImpl(replayUrl(gameId), {
      redirect: "manual",
      signal: controller.signal,
      headers: { accept: "application/json" },
    });
    if (response.status !== 200) throw new Error("replay_unavailable");
    const length = Number(response.headers.get("content-length"));
    if (Number.isFinite(length) && length > MAX_REPLAY_BYTES) throw new Error("replay_too_large");
    const text = await response.text();
    if (Buffer.byteLength(text, "utf8") > MAX_REPLAY_BYTES) throw new Error("replay_too_large");
    return JSON.parse(text);
  } catch (error) {
    if (["replay_unavailable", "replay_too_large", "invalid_game_id"].includes(error.message)) throw error;
    throw new Error("replay_invalid");
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Archive every listed game that is not already on disk. One failure never
 * stops the batch; the fixed error code is reported instead of a raw body.
 */
export async function collectRecords({ records, outDir, limit = MAX_GAMES, fetchImpl = fetch }) {
  if (!Array.isArray(records)) throw new Error("invalid_records");
  const existing = new Set();
  try {
    for (const name of await readdir(outDir)) {
      if (name.endsWith(".json")) existing.add(name.slice(0, -5));
    }
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const summary = { archived: [], skipped: [], failed: [] };
  // `limit` caps *new* archives, not the scan: the newest entries are usually
  // already archived, and slicing first would starve older unarchived games.
  for (const record of records) {
    const gameId = record?.gameId;
    if (typeof gameId !== "string" || !GAME_ID.test(gameId)) {
      summary.failed.push({ gameId: null, error: "invalid_game_id" });
      continue;
    }
    if (existing.has(gameId)) {
      summary.skipped.push(gameId);
      continue;
    }
    if (summary.archived.length >= limit) break;
    try {
      const payload = await fetchReplay(gameId, { fetchImpl });
      const result = await archiveGame({
        publicPayload: payload,
        outDir,
        brainVersion: typeof record.brainVersion === "string" ? record.brainVersion : null,
      });
      if (result.written) {
        existing.add(gameId);
        summary.archived.push(gameId);
      } else {
        summary.skipped.push(gameId);
      }
    } catch (error) {
      summary.failed.push({ gameId, error: String(error.message ?? error) });
    }
  }
  return summary;
}

async function main(argv) {
  const args = { out: null, records: null, limit: MAX_GAMES };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--records") { args.records = argv[index + 1]; index += 1; }
    else if (flag === "--out") { args.out = argv[index + 1]; index += 1; }
    else if (flag === "--limit") { args.limit = Number(argv[index + 1]); index += 1; }
  }
  if (!args.out || !args.records || !Number.isSafeInteger(args.limit) || args.limit < 1) {
    console.error("usage: collect-replays.mjs --records <list.json> --out <dir> [--limit 20]");
    return 2;
  }
  const listing = JSON.parse(await readFile(args.records, "utf8"));
  if (!listing || !Array.isArray(listing.records)) {
    console.error(JSON.stringify({ error: "invalid_records" }));
    return 2;
  }
  const summary = await collectRecords({ records: listing.records, outDir: args.out, limit: args.limit });
  console.log(JSON.stringify(summary));
  return summary.failed.length ? 1 : 0;
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; })
    .catch((error) => { console.error(JSON.stringify({ error: String(error.message ?? error) })); process.exitCode = 1; });
}
