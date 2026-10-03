import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const plan = JSON.parse(readFileSync(new URL("../builds-plan.json", import.meta.url)));
const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url)));

// Offline model of the documented watch-path rules, NOT a deployed filter or
// proof of Cloudflare configuration. The settings remain a proposal.
function documentedBuildDecision(branch, paths, commits = 1) {
  if (branch !== plan.productionBranch && !plan.previewBuilds) return false;
  const bypass = plan.pathFilterBypass;
  if ((paths.length === 0 && bypass.emptyChanges)
      || paths.length >= bypass.minimumChangedFiles
      || commits >= bypass.minimumCommits) return true;
  const matches = (path, pattern) => new RegExp(`^${pattern.split("*")
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join(".*")}$`).test(path);
  return paths.some((path) => !plan.pathExcludes.some((p) => matches(path, p))
    && plan.pathIncludes.some((p) => matches(path, p)));
}

test("Builds proposal has only the requested Worker, root, branch and watch scope", () => {
  assert.equal(plan.status, "proposal-only");
  assert.equal(plan.repository, "azumag/docich");
  assert.equal(plan.workerName, "docich-tsuitate-bot");
  assert.equal(plan.rootDirectory, "workers/tsuitate-bot");
  assert.equal(plan.productionBranch, "main");
  assert.equal(plan.previewBuilds, false);
  assert.deepEqual(plan.pathIncludes, ["workers/tsuitate-bot/*"]);
  assert.deepEqual(plan.pathExcludes, []);
  assert.equal(plan.buildCommand, "npm run build:cf");
  assert.equal(plan.deployCommand,
    './node_modules/.bin/cf deploy --prebuilt --worker docich-tsuitate-bot --tag "$(git rev-parse HEAD)"');
  assert.deepEqual(plan.buildVariables,
    { NODE_VERSION: "24.18.0", CF_SEND_TELEMETRY: "false" });
});

test("normal pushes include nested target files and mixed target/unrelated changes", () => {
  for (const path of ["src/index.js", "test/fixtures/first.json", "README.md", "package.json"]) {
    assert.equal(documentedBuildDecision("main", [`workers/tsuitate-bot/${path}`]), true);
  }
  // File lists can contain the old and new names of a rename, or a deleted file.
  assert.equal(documentedBuildDecision("main", ["workers/tsuitate-bot/src/old.js", "docs/new.js"]), true);
  assert.equal(documentedBuildDecision("main", ["docs/x.md", "workers/tsuitate-bot/src/bot.js"]), true);
});

test("normal unrelated pushes, workflow-only and shared dependencies do not match", () => {
  for (const paths of [
    ["games/soviet_now/main.py"], ["docs/tsuitate-protocol.md"],
    [".github/workflows/tsuitate-bot-worker.yml"], ["package.json", "package-lock.json"],
    ["requirements-test.txt"], ["workers/other-bot/src/index.js"],
    ["workers/tsuitate-bot-other/src/index.js"], ["workers/tsuitate-bot.js"],
    ["README.md", "src/docich/tsuitate_protocol.py"],
  ]) assert.equal(documentedBuildDecision("main", paths), false, JSON.stringify(paths));
  for (const branch of ["feature/tsuitate", "main-copy", "pull/123/head"]) {
    assert.equal(documentedBuildDecision(branch, ["workers/tsuitate-bot/src/index.js"]), false);
    assert.equal(documentedBuildDecision(branch, [], 20), false);
  }
});

test("documented bypass boundaries make the strict no-unrelated-build guarantee unavailable", () => {
  assert.deepEqual(plan.pathFilterBypass,
    { emptyChanges: true, minimumChangedFiles: 3000, minimumCommits: 20 });
  assert.equal(documentedBuildDecision("main", []), true);
  const unrelated = Array.from({ length: 3000 }, (_, i) => `docs/file-${i}.md`);
  assert.equal(documentedBuildDecision("main", unrelated.slice(0, 2999), 19), false);
  assert.equal(documentedBuildDecision("main", unrelated), true);
  assert.equal(documentedBuildDecision("main", ["docs/x.md"], 19), false);
  assert.equal(documentedBuildDecision("main", ["docs/x.md"], 20), true);
});

// Execute the actual package command with stub executables. No real Cf command,
// npm dependency install, credentials, network request or deployment is used.
const stages = ["cf build", "npm test", "npm run test:workerd", "npm run test:bundle"];
for (const failStage of ["", ...stages]) {
  test(`build command ${failStage ? `stops after ${failStage} failure` : "runs all offline checks"}`, () => {
    const dir = mkdtempSync(join(tmpdir(), "tsuitate-build-command-"));
    try {
      const log = join(dir, "calls.txt");
      for (const name of ["cf", "npm"]) {
        writeFileSync(join(dir, name), `#!/bin/sh\nstage='${name} '"$*"\nprintf '%s\\n' "$stage" >> "$PIPELINE_LOG"\n[ "$stage" != "$FAIL_STAGE" ] || exit 17\n`, { mode: 0o700 });
      }
      const result = spawnSync("/bin/sh", ["-c", pkg.scripts["build:cf"]], {
        env: { PATH: `${dir}:/usr/bin:/bin`, PIPELINE_LOG: log, FAIL_STAGE: failStage },
        encoding: "utf8", timeout: 5000,
      });
      assert.ifError(result.error);
      assert.equal(result.status, failStage ? 17 : 0, result.stderr);
      const expected = failStage ? stages.slice(0, stages.indexOf(failStage) + 1) : stages;
      assert.deepEqual(readFileSync(log, "utf8").trim().split("\n"), expected);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
}
