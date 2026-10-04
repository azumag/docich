import { DurableObject } from "cloudflare:workers";
import { DurableArenaController, SINGLETON_NAME } from "./durable-controller.js";
import { createWorkerSocket } from "./socket-worker.js";

/** Preparation only: this class is not exported/bound by the production Worker. */
export class BetaArena extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.controller = new DurableArenaController({ storage: ctx.storage, env,
      waitUntil: (promise) => ctx.waitUntil(promise),
      makeSocket: (token) => this.makeSocket(token),
      resolveResult: (...args) => this.resolveResult(...args),
      log: (event) => console.log(JSON.stringify(event)) });
  }

  makeSocket(token) { return createWorkerSocket(token); }
  async resolveResult(...args) {
    const { fetchPublicResult } = await import("../adapters/beta-results.js");
    return fetchPublicResult(...args);
  }

  // Future owner control must authenticate before invoking these internal RPCs.
  // There is deliberately no public HTTP start/stop and no webhook-HMAC bridge.
  async start(options) {
    if (!this.env.BETA_ARENA || this.ctx.id.toString() !== this.env.BETA_ARENA.idFromName(SINGLETON_NAME).toString()) {
      throw new Error("not_singleton");
    }
    return this.controller.start(options);
  }
  stop(options) { return this.controller.stop(options); }
  status() { return this.controller.status(); }
  alarm() { return this.controller.alarm(); }
  fetch() { return new Response("Not found", { status: 404 }); }
}
