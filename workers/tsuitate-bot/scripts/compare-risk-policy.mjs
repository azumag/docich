#!/usr/bin/env node
/** Private JSON input; stdout is an aggregate report, never a game transcript. */
import { open } from "node:fs/promises";
import { createHash } from "node:crypto";
import { compareRiskPolicies } from "../src/brain/risk-comparison.js";

const MAX_BYTES = 5 * 1024 * 1024;
let file;
try {
  if (process.argv.length !== 3) throw new Error("usage");
  file = await open(process.argv[2], "r");
  const stat = await file.stat();
  if (!stat.isFile() || stat.size < 1 || stat.size > MAX_BYTES) throw new Error("size");
  const bytes = Buffer.alloc(MAX_BYTES + 1);
  let length = 0;
  while (length < bytes.length) {
    const { bytesRead } = await file.read(bytes, length, bytes.length - length, null);
    if (!bytesRead) break;
    length += bytesRead;
  }
  if (length > MAX_BYTES) throw new Error("size");
  const content = bytes.subarray(0, length);
  const input = JSON.parse(content.toString("utf8"));
  const report = compareRiskPolicies(input);
  report.inputSha256 = createHash("sha256").update(content).digest("hex");
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
} catch {
  // Do not echo parser messages: they can contain private input and paths.
  process.stderr.write("Comparison failed. Use one readable JSON dataset (schemaVersion=1, 1-1000 cases, <=5 MiB).\n");
  process.exitCode = 1;
} finally {
  await file?.close();
}
