import test from "node:test";
import assert from "node:assert/strict";

import {
  RECENT_ATTEMPT_MS,
  SUCCESS_FRESH_MS,
  decideDispatch,
  runWatchdog,
} from "./worker.mjs";

const NOW = Date.parse("2026-09-14T12:00:00Z");

function iso(msAgo) {
  return new Date(NOW - msAgo).toISOString();
}

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return payload;
    },
  };
}

test("recent successful monitor suppresses fallback dispatch", () => {
  const result = decideDispatch(
    [
      {
        status: "completed",
        conclusion: "success",
        created_at: iso(SUCCESS_FRESH_MS - 1),
        updated_at: iso(SUCCESS_FRESH_MS - 1),
      },
    ],
    NOW,
  );
  assert.deepEqual(result, { dispatch: false, reason: "recent_success" });
});

test("stale successful monitor requests fallback dispatch", () => {
  const result = decideDispatch(
    [
      {
        status: "completed",
        conclusion: "success",
        created_at: iso(SUCCESS_FRESH_MS + 1),
        updated_at: iso(SUCCESS_FRESH_MS + 1),
      },
    ],
    NOW,
  );
  assert.deepEqual(result, { dispatch: true, reason: "monitor_stale" });
});

test("recent failed attempt prevents retry storm", () => {
  const result = decideDispatch(
    [
      {
        status: "completed",
        conclusion: "failure",
        created_at: iso(RECENT_ATTEMPT_MS - 1),
      },
    ],
    NOW,
  );
  assert.deepEqual(result, { dispatch: false, reason: "recent_attempt" });
});

test("recent in-flight attempt prevents duplicate dispatch", () => {
  const result = decideDispatch(
    [
      {
        status: "in_progress",
        conclusion: null,
        created_at: iso(RECENT_ATTEMPT_MS - 1),
      },
    ],
    NOW,
  );
  assert.deepEqual(result, { dispatch: false, reason: "recent_inflight" });
});

test("stale failure permits recovery dispatch", () => {
  const result = decideDispatch(
    [
      {
        status: "completed",
        conclusion: "failure",
        created_at: iso(RECENT_ATTEMPT_MS + 1),
      },
    ],
    NOW,
  );
  assert.deepEqual(result, { dispatch: true, reason: "monitor_stale" });
});

test("watchdog dispatches main only when monitor is stale", async () => {
  const requests = [];
  const fetchImpl = async (url, options) => {
    requests.push({ url, options });
    if (options.method === "GET") {
      return jsonResponse(200, {
        workflow_runs: [
          {
            status: "completed",
            conclusion: "success",
            created_at: iso(SUCCESS_FRESH_MS + 1),
            updated_at: iso(SUCCESS_FRESH_MS + 1),
          },
        ],
      });
    }
    return jsonResponse(204, null);
  };

  const result = await runWatchdog(
    { GITHUB_ACTIONS_TOKEN: "test-token" },
    fetchImpl,
    NOW,
  );

  assert.deepEqual(result, { dispatched: true, reason: "monitor_stale" });
  assert.equal(requests.length, 2);
  assert.match(requests[0].url, /vm-storage-monitor\.yml\/runs\?/);
  assert.match(requests[1].url, /vm-storage-monitor\.yml\/dispatches$/);
  assert.equal(requests[1].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[1].options.body), { ref: "main" });
  assert.equal(
    requests[1].options.headers.Authorization,
    "Bearer test-token",
  );
});

test("watchdog does not dispatch after a recent success", async () => {
  const requests = [];
  const fetchImpl = async (url, options) => {
    requests.push({ url, options });
    return jsonResponse(200, {
      workflow_runs: [
        {
          status: "completed",
          conclusion: "success",
          created_at: iso(5 * 60 * 1000),
          updated_at: iso(5 * 60 * 1000),
        },
      ],
    });
  };

  const result = await runWatchdog(
    { GITHUB_ACTIONS_TOKEN: "test-token" },
    fetchImpl,
    NOW,
  );

  assert.deepEqual(result, { dispatched: false, reason: "recent_success" });
  assert.equal(requests.length, 1);
});

test("GitHub read failure is fail-closed and never dispatches", async () => {
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    return jsonResponse(503, {});
  };

  await assert.rejects(
    runWatchdog({ GITHUB_ACTIONS_TOKEN: "test-token" }, fetchImpl, NOW),
    /github_runs_read_failed_503/,
  );
  assert.equal(calls, 1);
});

test("missing token fails without making a request", async () => {
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    throw new Error("unexpected request");
  };

  await assert.rejects(
    runWatchdog({}, fetchImpl, NOW),
    /github_actions_token_missing/,
  );
  assert.equal(calls, 0);
});
