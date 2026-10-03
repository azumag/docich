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
// Recover the exact original mapper solely for the empty-diff regression.
const remoteOriginal = "{name:t.name,class_name:t.class_name,script_name:t.script_name,environment:t.environment}";
const remoteReplacement = "{name:t.name,class_name:t.class_name,...t.script_name===void 0?{}:{script_name:t.script_name},...t.environment===void 0?{}:{environment:t.environment}}";
const mapperSource = await readFile(join(dist, "dist-CYFkGHYv.mjs"), "utf8");
assert.ok(mapperSource.includes(remoteReplacement), "Remote normalization patch must be installed");
const originalMapperSource = mapperSource.replace(remoteReplacement, remoteOriginal);
assert.equal(createHash("sha256").update(originalMapperSource).digest("hex"),
  "f695897b2f55004257db1321ab279c33d6036e9a53f1509198400ed373233417");
const originalMapperPath = join(dist, `.original-remote-mapper-test-${randomUUID()}.mjs`);
let originalConstructWranglerConfig;
try {
  await writeFile(originalMapperPath, originalMapperSource, { flag: "wx" });
  ({ u: originalConstructWranglerConfig } = await import(pathToFileURL(originalMapperPath)));
} finally {
  await rm(originalMapperPath, { force: true });
}
const { u: constructWranglerConfig, x: getBindings } = await import(pathToFileURL(join(dist, "dist-CYFkGHYv.mjs")));
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
const apiSelfBinding = {
  name: "GAME_STATE", type: "durable_object_namespace", class_name: "GameState",
  namespace_id: "synthetic-existing-namespace",
};
function remoteFromApi(local, binding = apiSelfBinding, construct = constructWranglerConfig) {
  return construct({
    name: local.name, entrypoint: local.main, compatibility_date: local.compatibility_date,
    compatibility_flags: local.compatibility_flags, observability: local.observability,
    subdomain: { enabled: local.workers_dev, previews_enabled: local.preview_urls },
    routes: [], domains: [], schedules: [],
    bindings: [
      binding,
      { name: "BOT_ID", type: "plain_text", text: "DoCiAI" },
      { name: "CF_VERSION_METADATA", type: "version_metadata" },
    ],
  });
}


test("prebuilt deploy normalization matches existing same-Worker DO and final upload preserves bindings", () => {
  const local = normalized();
  assert.deepEqual(local.durable_objects.bindings, [selfBinding]);
  assert.equal(Object.hasOwn(apiSelfBinding, "script_name"), false);
  assert.equal(Object.hasOwn(apiSelfBinding, "environment"), false);
  const remote = remoteFromApi(local);
  assert.equal(Object.hasOwn(remote.durable_objects.bindings[0], "script_name"), false);
  assert.equal(Object.hasOwn(remote.durable_objects.bindings[0], "environment"), false);
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
    { ...selfBinding, script_name: "another-worker" }, { ...selfBinding, environment: "different-environment" }]) {
    assert.equal(getRemoteConfigDiff(remoteFromApi(local, { ...apiSelfBinding, ...binding }), local).nonDestructive, false);
  }
  const externalWorker = structuredClone(cloudflareConfig.worker);
  externalWorker.env.GAME_STATE.worker = "another-worker";
  const external = normalized(externalWorker);
  assert.equal(external.durable_objects.bindings[0].script_name, "another-worker");
  const externalRemote = remoteFromApi(external, { ...apiSelfBinding, script_name: "another-worker" });
  assert.equal(externalRemote.durable_objects.bindings[0].script_name, "another-worker");
  assert.equal(getRemoteConfigDiff(externalRemote, external).nonDestructive, true);
  const environmentRemote = remoteFromApi(local, { ...apiSelfBinding, environment: "different-environment" });
  assert.equal(environmentRemote.durable_objects.bindings[0].environment, "different-environment");
  assert.equal(uploadMetadata(external).bindings.find(b => b.name === "GAME_STATE").script_name, "another-worker");
});

test("absent optional keys reproduce the empty destructive diff", () => {
  const local = normalized();
  const remote = remoteFromApi(local, apiSelfBinding, originalConstructWranglerConfig);
  assert.equal(Object.hasOwn(remote.durable_objects.bindings[0], "script_name"), true);
  assert.equal(Object.hasOwn(remote.durable_objects.bindings[0], "environment"), true);
  assert.equal(remote.durable_objects.bindings[0].script_name, undefined);
  assert.equal(remote.durable_objects.bindings[0].environment, undefined);
  const comparison = getRemoteConfigDiff(remote, local);
  assert.equal(comparison.nonDestructive, false);
  assert.match(comparison.diff.toString(), /bindings/);
  assert.doesNotMatch(comparison.diff.toString(), /script_name|environment/);
  assert.equal(uploadMetadata(local).bindings.find(b => b.name === "GAME_STATE").script_name, undefined);
});

test("previous unsafe metadata override does not fix strict comparison or upload bindings", () => {
  const old = normalized();
  old.durable_objects.bindings[0].script_name = old.name;
  old.unsafe = { metadata: { durable_objects: { bindings: [selfBinding] } } };
  assert.equal(getRemoteConfigDiff(remoteFromApi(old), old).nonDestructive, false);
  assert.equal(uploadMetadata(old).bindings.find(b => b.name === "GAME_STATE").script_name, old.name);
});

test.after(() => { globalThis.fetch = savedFetch; });
