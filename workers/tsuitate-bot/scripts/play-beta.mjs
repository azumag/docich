import { randomInt } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { LINEAR_PROFILE, validateProfile } from "../src/brain/index.js";
import { BETA_ORIGIN } from "../src/adapters/beta-results.js";
import { ArenaStore } from "../src/arena/store.js";
import { BetaSession } from "../src/arena/beta-session.js";

const HELP = `ついたて将棋 beta 対局 runner

  npm run play:beta -- --games 20 --directory ../../run/tsuitate-beta
  npm run play:beta -- --games 50 --profile base.json --profile candidate.json
  npm run play:beta -- --export-profile base.json

--games N                今回の最大対局数（既定10、1..10000）
--directory PATH         記録・再開checkpoint（既定 ../../run/tsuitate-beta）
--profile PATH           JSONのbrain profile。複数指定時は対局開始前に無作為に選択
--queue-wait-seconds N   対戦相手を待つ上限（既定120、1..86400）
--export-profile PATH    既定の学習用profileを新しいファイルに保存し終了

接続には環境変数 TSUITATE_BOT_TOKEN が必要です。対局中のSIGINT/SIGTERMは
次の参加を止め、進行中の一局を完走して保存します。同じdirectoryで再開すると
送信中だった手を保持して同期します。通信結果が不明な手は自動再送しません。
`;

function argumentsFor(argv) {
  const options = { games: 10, directory: "../../run/tsuitate-beta", profiles: [], queueWaitMs: 120000 };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--help" || flag === "-h") { options.help = true; continue; }
    if (!["--games", "--directory", "--profile", "--queue-wait-seconds", "--export-profile"].includes(flag)
        || !argv[index + 1] || argv[index + 1].startsWith("--")) throw new Error("invalid_arguments");
    const value = argv[++index];
    if (flag === "--directory") options.directory = value;
    else if (flag === "--profile") options.profiles.push(value);
    else if (flag === "--export-profile") options.exportProfile = value;
    else {
      const number = Number(value);
      if (!/^\d+$/.test(value) || !Number.isSafeInteger(number) || number < 1
          || number > (flag === "--games" ? 10000 : 86400)) throw new Error("invalid_arguments");
      if (flag === "--games") options.games = number;
      else options.queueWaitMs = number * 1000;
    }
  }
  return options;
}

export async function main(argv = process.argv.slice(2)) {
  const options = argumentsFor(argv);
  if (options.help) { console.log(HELP); return 0; }
  if (options.exportProfile) {
    await writeFile(options.exportProfile, JSON.stringify(LINEAR_PROFILE, null, 2) + "\n", { flag: "wx", mode: 0o600 });
    console.log(JSON.stringify({ event: "profile_exported", profileId: LINEAR_PROFILE.id }));
    return 0;
  }
  const token = process.env.TSUITATE_BOT_TOKEN;
  if (typeof token !== "string" || !/^tsb_[A-Za-z0-9_-]{8,512}$/.test(token)) throw new Error("bot_token_required");
  const profiles = [];
  for (const path of options.profiles) {
    const profile = validateProfile(JSON.parse(await readFile(path, "utf8")));
    if (!profile) throw new Error("invalid_brain_profile");
    profiles.push(profile);
  }
  if (profiles.length === 0) profiles.push(LINEAR_PROFILE);
  const { io } = await import("socket.io-client");
  const store = new ArenaStore(options.directory);
  let session;
  let stopping = false;
  const drain = () => { stopping = true; if (session) void session.enqueue(() => session.drain()); };
  process.on("SIGINT", drain);
  process.on("SIGTERM", drain);
  try {
    await store.acquire();
    let saved = await store.load();
    if (saved && saved.version !== 1) throw new Error("invalid_checkpoint");
    // Recover the crash window between a terminal file and checkpoint clearing.
    if (saved?.finishedRecord) {
      await store.finish(saved.finishedRecord);
      await store.save({ version: 1, active: null });
      saved = null;
    }
    for (let count = 0; count < options.games && !stopping; count += 1) {
      const profile = profiles[randomInt(profiles.length)];
      // A fresh connection for each new match prevents an unscoped old push or
      // old acknowledgement from being attached to the next game.
      const socket = io(BETA_ORIGIN, {
        auth: { token }, transports: ["websocket"], autoConnect: false,
        forceNew: true, multiplex: false, reconnection: true,
        reconnectionAttempts: 12, reconnectionDelay: 1000, reconnectionDelayMax: 5000,
        timeout: 5000,
        // Do not set retries/ackTimeout: game:move is never automatically retried.
      });
      session = new BetaSession({ socket, store, profile, checkpoint: saved?.active ?? null,
        queueWaitMs: options.queueWaitMs, log: (event) => console.log(JSON.stringify(event)) });
      saved = null;
      const result = await session.start();
      session = null;
      if (result.status === "paused") return 1;
      if (result.status !== "finished" || result.stopping || stopping) break;
      if (count + 1 < options.games) await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    return 0;
  } finally {
    process.off("SIGINT", drain);
    process.off("SIGTERM", drain);
    if (session) session.close();
    await store.release();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const SAFE_CODES = new Set(["invalid_arguments", "bot_token_required", "invalid_brain_profile", "invalid_checkpoint",
    "runner_locked", "runner_lock_changed", "invalid_game_record", "conflicting_game_record"]);
  try { process.exitCode = await main(); }
  catch (error) {
    console.error(JSON.stringify({ event: "runner_failed", code: SAFE_CODES.has(error.message) ? error.message : "local_failure" }));
    process.exitCode = 1;
  }
}
