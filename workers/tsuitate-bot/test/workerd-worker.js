import worker, { GameState } from "../src/index.js";

const textEncoder = new TextEncoder();

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

async function requestKey(requestId) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", textEncoder.encode(requestId)));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

/** Test-only Durable Object wrapper; production GameState remains unchanged. */
export class RuntimeGameState extends GameState {
  async fetch(request) {
    const url = new URL(request.url);

    if (request.method === "POST" && url.pathname === "/__runtime_test/rollback") {
      const storage = this.state.storage;
      await storage.put("runtime-test:rollback:stable", "before");
      let transactionThrew = false;
      let storageWriteThrew = false;
      try {
        await storage.transaction(async (tx) => {
          await tx.put("runtime-test:rollback:stable", "inside");
          await tx.put("runtime-test:rollback:transient", "uncommitted");
          try {
            await tx.put("runtime-test:rollback:invalid", () => {});
          } catch (error) {
            storageWriteThrew = true;
            throw error;
          }
          throw new Error("storage unexpectedly accepted a non-serializable function");
        });
      } catch {
        transactionThrew = true;
      }
      const stable = await storage.get("runtime-test:rollback:stable");
      const transient = await storage.get("runtime-test:rollback:transient");
      return jsonResponse(200, { transactionThrew, storageWriteThrew, stable, transient: transient ?? null });
    }

    if (request.method === "POST" && url.pathname === "/__runtime_test/receipt") {
      const requestId = url.searchParams.get("requestId");
      if (!requestId) return jsonResponse(400, { error: "request_id_required" });
      const receipt = await this.state.storage.get(`request:${await requestKey(requestId)}`);
      return jsonResponse(200, { receipt: receipt ?? null });
    }

    if (request.method === "POST" && url.pathname === "/process") {
      let input;
      try {
        input = await request.clone().json();
      } catch {
        input = null;
      }
      const requestId = input?.payload?.requestId;
      if (typeof requestId === "string" && requestId.startsWith("workerd-latecommit:")) {
        const delayKey = `runtime-test:latecommit:${requestId}`;
        if (!(await this.state.storage.get(delayKey))) {
          await this.state.storage.put(delayKey, true);
          // Exceed the production RPC budget (2.5 s) so the webhook times out
          // while this real workerd Durable Object can still finish the write.
          await sleep(3200);
        }
      }
    }

    return super.fetch(request);
  }
}

async function fetchRuntimeTestRoute(request, env) {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/__runtime_test/health") {
    return new Response("ok", { headers: { "cache-control": "no-store" } });
  }
  if (request.method !== "GET" || !["/__runtime_test/rollback", "/__runtime_test/receipt"].includes(url.pathname)) {
    return jsonResponse(404, { error: "not_found" });
  }
  const gameId = url.searchParams.get("gameId");
  if (!gameId) return jsonResponse(400, { error: "game_id_required" });
  const stub = env.GAME_STATE.get(env.GAME_STATE.idFromName(gameId));
  const target = new URL(`https://game-state.internal${url.pathname}`);
  const requestId = url.searchParams.get("requestId");
  if (requestId) target.searchParams.set("requestId", requestId);
  return stub.fetch(new Request(target, { method: "POST" }));
}

export default {
  fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/__runtime_test/")) return fetchRuntimeTestRoute(request, env);
    return worker.fetch(request, env, ctx);
  },
};
