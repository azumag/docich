import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const cfPackagePath = require.resolve("cf/package.json");
const cfPackage = JSON.parse(await readFile(cfPackagePath, "utf8"));
assert.equal(cfPackage.version, "1.0.0-beta.12", "Recheck the compatibility patch before upgrading Cf");
const cfRequire = createRequire(cfPackagePath);
// The config package has import-only exports. Follow Cf's Node package search paths
// so both npm's hoisted and nested layouts patch the instance used by the CLI.
let configRoot;
let configPackage;
for (const searchPath of cfRequire.resolve.paths("@cloudflare/config")) {
  const candidate = join(searchPath, "@cloudflare/config");
  try {
    configPackage = JSON.parse(await readFile(join(candidate, "package.json"), "utf8"));
    configRoot = candidate;
    break;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}
assert.ok(configRoot, "Cannot find Cf's config dependency");
const entrypoint = join(configRoot, "dist/index.mjs");
assert.equal(configPackage.version, "0.23.0", "Recheck the compatibility patch before upgrading config");

const original = `\t\t\tcase "durable-object":
\t\t\t\tdurableObjectBindings.push({
\t\t\t\t\tname,
\t\t\t\t\tclass_name: binding.exportName,
\t\t\t\t\tscript_name: binding.worker
\t\t\t\t});`;
const replacement = original.replace("script_name: binding.worker", "...(binding.worker === config.name ? {} : { script_name: binding.worker })");
const pristineHash = "0df9b57f2c3ae208bd7233fcf99f07f2d5d31225b4adc8d7ea152d7438b6a54f";
const hash = (text) => createHash("sha256").update(text).digest("hex");
const source = await readFile(entrypoint, "utf8");
// Idempotence is checked against the complete original distribution, not just a marker.
const unpatched = source.includes(replacement) ? source.replace(replacement, original) : source;
assert.equal(hash(unpatched), pristineHash, "Unexpected config distribution; refusing to patch");
assert.equal(unpatched.split(original).length, 2, "Expected exactly one Durable Object conversion branch");
const patched = unpatched.replace(original, replacement);
if (source !== patched) await writeFile(entrypoint, patched);
console.log("Cf self-binding compatibility patch verified (beta.12 / config 0.23.0)");
