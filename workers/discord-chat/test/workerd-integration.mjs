import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const workerDirectory = dirname(dirname(fileURLToPath(import.meta.url)));
const config = await readFile(join(workerDirectory, "wrangler.runtime.toml"), "utf8");
assert.match(config, /^compatibility_date = "2026-10-03"$/m);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function availablePort() {
  const server = createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const { port } = server.address();
  server.close();
  await once(server, "close");
  return port;
}

async function stopProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, "exit");
  child.kill("SIGTERM");
  await Promise.race([exited, sleep(2500)]);
  if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
}

async function startRuntime() {
  const tempDirectory = await mkdtemp(join(tmpdir(), "discord-chat-workerd-"));
  const port = await availablePort();
  const child = spawn("wrangler", [
    "dev", "--config", "wrangler.runtime.toml", "--local",
    "--ip", "127.0.0.1", "--port", String(port), "--inspector-port", "0",
    "--persist-to", join(tempDirectory, "state"),
    "--show-interactive-dev-session=false", "--log-level", "error",
  ], {
    cwd: workerDirectory,
    env: {
      ...process.env,
      WRANGLER_LOG_PATH: join(tempDirectory, "wrangler.log"),
      WRANGLER_WRITE_LOGS: "false",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.setEncoding("utf8").on("data", (chunk) => { output += chunk; });
  child.stderr.setEncoding("utf8").on("data", (chunk) => { output += chunk; });
  const baseUrl = "http://127.0.0.1:" + String(port);
  const deadline = Date.now() + 20000;
  try {
    while (Date.now() < deadline) {
      if (child.exitCode !== null || child.signalCode !== null) {
        throw new Error("Wrangler exited before ready.\n" + output);
      }
      try {
        const response = await fetch(baseUrl + "/production-health", { signal: AbortSignal.timeout(500) });
        if (response.status === 200) return { child, baseUrl, tempDirectory, output: () => output };
      } catch {
        // Local workerd may need several seconds to start.
      }
      await sleep(100);
    }
    throw new Error("Timed out waiting for local workerd.\n" + output);
  } catch (error) {
    await stopProcess(child);
    await rm(tempDirectory, { recursive: true, force: true });
    throw error;
  }
}

let runtime;
try {
  runtime = await startRuntime();
  const health = await fetch(runtime.baseUrl + "/production-health");
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), {
    configured: false,
    connected: false,
    ready: false,
    pending: 0,
    fatal: null,
  });

  const memory = await fetch(runtime.baseUrl + "/memory-probe", { method: "POST" });
  assert.equal(memory.status, 200);
  assert.deepEqual(await memory.json(), {
    recalled: true,
    separated: true,
    scrubbed: true,
  });
  console.log("PASS production Durable Object constructs on SQLite-backed workerd");
  console.log("PASS durable memory recalls same scope, separates guilds, and scrubs deleted content");
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  if (runtime) {
    await stopProcess(runtime.child);
    await rm(runtime.tempDirectory, { recursive: true, force: true });
  }
}
