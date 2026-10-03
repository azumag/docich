import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { readFile, writeFile, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { test } from "node:test";
import cloudflareConfig from "../cloudflare.config.ts";

// Call the same private functions used by the pinned Cf prebuilt deploy path.
// Fail on distribution changes instead of silently testing a different implementation.
const require = createRequire(import.meta.url);
const cfRoot = dirname(require.resolve("cf/package.json"));
assert.equal(JSON.parse(await readFile(join(cfRoot, "package.json"))).version, "1.0.0-beta.12");
const dist = join(cfRoot, "dist");
const containers = await readFile(join(dist, "containers-DRzgE46U.mjs"), "utf8");
assert.equal(createHash("sha256").update(containers).digest("hex"),
  "c674dea42ca21dcdc78e3b7f058033f6f4324ed04ad94fdee94702814c9273d6");
const instrumented = join(dist, `.self-binding-test-${randomUUID()}.mjs`);
const savedFetch = globalThis.fetch;
globalThis.fetch = () => { throw new Error("Network access is forbidden in deploy configuration tests"); };
let getRemoteConfigDiff;
try {
  // Only expose the existing function; no replacement implementation or deploy invocation.
  await writeFile(instrumented, containers + "\nexport { Tx as getRemoteConfigDiff };\n", { flag: "wx" });
  ({ getRemoteConfigDiff } = await import(pathToFileURL(instrumented)));
} finally {
  await rm(instrumented, { force: true });
}
const { i: convertBuildOutput } = await import(pathToFileURL(join(dist, "build-BKPVP5ip.mjs")));
const { x: getBindings } = await import(pathToFileURL(join(dist, "dist-CYFkGHYv.mjs")));
const { t: createWorkerUploadForm } = await import(pathToFileURL(join(dist, "chunk-KKDV4JPS-D3kwd1Nq.mjs")));
const root = dirname(dirname(fileURLToPath(import.meta.url)));

function normalized(worker = cloudflareConfig.worker) {
  return convertBuildOutput({
    config: { ...worker, manifest: { mainModule: "index.js" } },
    configPath: join(root, "cloudflare.config.ts"), bundleDir: join(root, "src"),
  }, { containers: [] }).wranglerConfig;
}
function uploadMetadata(config) {
  const form = createWorkerUploadForm({
    main: { name: "index.js", type: "esm", content: "export default {}" },
    exports: config.exports, keepSecrets: true,
  }, getBindings(config), { unsafe: config.unsafe });
  return JSON.parse(form.get("metadata"));
}
const selfBinding = { name: "GAME_STATE", class_name: "GameState" };

test("prebuilt deploy normalization matches existing same-Worker DO and final upload preserves bindings", () => {
  const local = normalized();
  assert.deepEqual(local.durable_objects.bindings, [selfBinding]);
  const remote = { ...structuredClone(local), durable_objects: { bindings: [selfBinding] } };
  const comparison = getRemoteConfigDiff(remote, local);
  assert.equal(comparison.nonDestructive, true);
  assert.equal(comparison.diff?.durable_objects, undefined);
  const metadata = uploadMetadata(local);
  assert.deepEqual(metadata.bindings.find(b => b.name === "GAME_STATE"),
    { name: "GAME_STATE", type: "durable_object_namespace", class_name: "GameState" });
  assert.deepEqual(metadata.bindings.find(b => b.name === "BOT_ID"),
    { name: "BOT_ID", type: "plain_text", text: "DoCiAI" });
  assert.deepEqual(metadata.bindings.find(b => b.name === "CF_VERSION_METADATA"),
    { name: "CF_VERSION_METADATA", type: "version_metadata" });
  assert.deepEqual(metadata.keep_bindings, ["secret_text", "secret_key"]);
  assert.deepEqual(metadata.exports, local.exports);
  assert.deepEqual(metadata.exports.GameState, { type: "durable-object", storage: "sqlite" });
  assert.equal(Object.hasOwn(metadata, "durable_objects"), false);
});

test("strict comparison still detects changed class and external Worker; external upload keeps script_name", () => {
  const local = normalized();
  for (const binding of [{ ...selfBinding, name: "OTHER_STATE" }, { ...selfBinding, class_name: "OtherState" },
    { ...selfBinding, script_name: "another-worker" }]) {
    assert.equal(getRemoteConfigDiff({ ...structuredClone(local), durable_objects: { bindings: [binding] } }, local).nonDestructive, false);
  }
  const externalWorker = structuredClone(cloudflareConfig.worker);
  externalWorker.env.GAME_STATE.worker = "another-worker";
  const external = normalized(externalWorker);
  assert.equal(external.durable_objects.bindings[0].script_name, "another-worker");
  assert.equal(uploadMetadata(external).bindings.find(b => b.name === "GAME_STATE").script_name, "another-worker");
});

test("previous unsafe metadata override does not fix strict comparison or upload bindings", () => {
  const old = normalized();
  old.durable_objects.bindings[0].script_name = old.name;
  old.unsafe = { metadata: { durable_objects: { bindings: [selfBinding] } } };
  assert.equal(getRemoteConfigDiff({ ...structuredClone(old), durable_objects: { bindings: [selfBinding] } }, old).nonDestructive, false);
  assert.equal(uploadMetadata(old).bindings.find(b => b.name === "GAME_STATE").script_name, old.name);
});

test.after(() => { globalThis.fetch = savedFetch; });
