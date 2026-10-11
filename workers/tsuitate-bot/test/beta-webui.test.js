import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import { test } from "node:test";

test("actual WebUI start/stop calls produce JSON, operator, CSRF and confirmation headers/body", async () => {
  const html = await readFile(new URL("../../../src/docich/webui_resources/index.html", import.meta.url), "utf8");
  const apiSource = html.slice(html.indexOf("let CSRF_TOKEN = null;"), html.indexOf("\nfunction chainKeys("));
  const betaSource = html.slice(html.indexOf("let BETA_STATE=null"), html.indexOf("async function loadCorners()"));
  assert.ok(apiSource.includes("async function api("));
  assert.ok(!apiSource.includes("</script>"));
  const stored = new Map([["webui_token", "fixture-only-operator-not-credential"]]);
  const elements = new Map();
  const requests = [];
  const state = { state: "stopped", runId: null, readyForNextRun: true };
  const context = vm.createContext({
    sessionStorage: { getItem: (k) => stored.get(k) ?? null, setItem: (k,v) => stored.set(k,v), removeItem: (k) => stored.delete(k) },
    crypto: { randomUUID: () => "fixture-run" }, window: { confirm: () => true }, toast: () => {},
    $: (id) => { if (!elements.has(id)) elements.set(id, {}); return elements.get(id); },
    fetch: async (path, options = {}) => {
      requests.push({ path, options: structuredClone(options) });
      if (path === "/api/csrf") return { ok: true, status: 200, json: async () => ({ csrf_token: "fixture-csrf" }) };
      if (options.method === "POST") {
        const body = JSON.parse(options.body);
        state.runId = body.runId; state.state = body.action === "start" ? "playing" : "draining";
        state.readyForNextRun = false;
      }
      return { ok: true, status: 200, text: async () => JSON.stringify(state) };
    },
  });
  vm.runInContext(apiSource + "\n" + betaSource, context);
  const handlers = html.slice(html.indexOf('$("#beta-refresh").onclick'), html.indexOf('  const rSave='));
  assert.ok(handlers.includes('$("#beta-start").onclick'));
  vm.runInContext(handlers, context);
  await vm.runInContext('loadBetaStatus();', context);
  await elements.get("#beta-start").onclick();
  await elements.get("#beta-stop").onclick();
  const posts = requests.filter((r) => r.options.method === "POST");
  assert.equal(posts.length, 2);
  for (const [index, request] of posts.entries()) {
    assert.equal(request.path, "/api/tsuitate-beta");
    assert.equal(request.options.headers["Content-Type"], "application/json");
    assert.equal(request.options.headers.Authorization, "Bearer fixture-only-operator-not-credential");
    assert.equal(request.options.headers["X-CSRF-Token"], "fixture-csrf");
    assert.deepEqual(JSON.parse(request.options.body), { action: index === 0 ? "start" : "stop", runId: "fixture-run", confirm: true });
  }
});
