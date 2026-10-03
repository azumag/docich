// Loaded only by bundle-integration.mjs, as an in-memory workerd module.
// The harness replaces this import with the actual Cf manifest entrypoint.
import worker, { GameState as BuiltGameState } from "./__CF_ENTRYPOINT__";

export class GameState extends BuiltGameState {
  constructor(state) {
    const fault = { failWrite: false };
    const storage = new Proxy(state.storage, {
      get(target, key) {
        if (key === "transaction") return (callback) => target.transaction((tx) => callback(new Proxy(tx, {
          get(transaction, member) {
            if (member === "put") return async (name, value) => {
              if (fault.failWrite && name.startsWith("session:")) {
                fault.failWrite = false;
                // Real SQLite storage rejects this after position/game writes.
                await transaction.put("bundle-test:invalid", () => {});
                throw new Error("SQLite unexpectedly accepted a function");
              }
              return transaction.put(name, value);
            };
            const value = Reflect.get(transaction, member, transaction);
            return typeof value === "function" ? value.bind(transaction) : value;
          },
        })));
        const value = Reflect.get(target, key, target);
        return typeof value === "function" ? value.bind(target) : value;
      },
    });
    super({ storage });
    this.realState = state;
    this.fault = fault;
  }

  async fetch(request) {
    if (new URL(request.url).pathname === "/__bundle_test/inspect") {
      const entries = Object.fromEntries(await this.realState.storage.list());
      const sqlite = [...this.realState.storage.sql.exec("SELECT 1 AS value")][0].value;
      return Response.json({ entries, sqlite });
    }
    const input = await request.clone().json();
    const id = input.payload?.requestId ?? "";
    const marker = `bundle-test:once:${id}`;
    if (!(await this.realState.storage.get(marker))) {
      if (id.startsWith("bundle-rollback:")) {
        await this.realState.storage.put(marker, true);
        this.fault.failWrite = true;
      } else if (id.startsWith("bundle-timeout:")) {
        await this.realState.storage.put(marker, true);
        await new Promise((resolve) => setTimeout(resolve, 3200));
      }
    }
    return super.fetch(request);
  }
}

export default worker;
