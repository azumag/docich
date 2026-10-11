import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

// #1571: the Workers Builds failure was `npm run build:cf` opening
// /opt/buildhome/repo/package.json (ENOENT) because the project root was
// the repository root instead of workers/tsuitate-bot. The repository root
// package.json is a fail-safe that delegates into this directory; the real
// build definition stays here.
const rootPkg = JSON.parse(readFileSync(new URL("../../../package.json", import.meta.url)));

test("repository root delegates the Cf build into the Worker directory", () => {
  assert.equal(rootPkg.private, true);
  assert.equal(rootPkg.scripts["build:cf"], "cd workers/tsuitate-bot && npm run build:cf");
});

test("the delegation runs the Worker build with the Worker directory as cwd", () => {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), "docich-root-delegate-")));
  try {
    mkdirSync(join(dir, "workers/tsuitate-bot"), { recursive: true });
    writeFileSync(join(dir, "package.json"), JSON.stringify(rootPkg));
    const log = join(dir, "calls.txt");
    writeFileSync(join(dir, "npm"),
      `#!/bin/sh\nprintf '%s %s\\n' "$(pwd)" "$*" >> "$PIPELINE_LOG"\n`, { mode: 0o700 });
    const result = spawnSync("/bin/sh", ["-c", rootPkg.scripts["build:cf"]], {
      cwd: dir,
      env: { PATH: `${dir}:/usr/bin:/bin`, PIPELINE_LOG: log },
      encoding: "utf8", timeout: 5000,
    });
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(readFileSync(log, "utf8").trim().split("\n"),
      [`${join(dir, "workers/tsuitate-bot")} run build:cf`]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
