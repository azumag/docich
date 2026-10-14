const OWNER = "azumag";
const REPO = "docich";
const WORKFLOW = "vm-storage-monitor.yml";
const REF = "main";
const API_VERSION = "2026-03-10";

export const SUCCESS_FRESH_MS = 60 * 60 * 1000;
export const RECENT_ATTEMPT_MS = 20 * 60 * 1000;

const ACTIVE_STATUSES = new Set([
  "queued",
  "in_progress",
  "pending",
  "requested",
  "waiting",
]);

function parseTimestamp(value) {
  if (typeof value !== "string" || value.length === 0) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function successTimestamp(run) {
  return (
    parseTimestamp(run?.updated_at) ??
    parseTimestamp(run?.run_started_at) ??
    parseTimestamp(run?.created_at)
  );
}

function attemptTimestamp(run) {
  return (
    parseTimestamp(run?.run_started_at) ??
    parseTimestamp(run?.created_at) ??
    parseTimestamp(run?.updated_at)
  );
}

function isFresh(timestamp, nowMs, windowMs) {
  if (timestamp === null) return false;
  const age = nowMs - timestamp;
  return age >= 0 && age <= windowMs;
}

export function decideDispatch(runs, nowMs = Date.now()) {
  if (!Array.isArray(runs)) {
    throw new Error("github_runs_schema_invalid");
  }

  for (const run of runs) {
    if (
      run?.conclusion === "success" &&
      isFresh(successTimestamp(run), nowMs, SUCCESS_FRESH_MS)
    ) {
      return { dispatch: false, reason: "recent_success" };
    }
  }

  for (const run of runs) {
    const timestamp = attemptTimestamp(run);
    if (!isFresh(timestamp, nowMs, RECENT_ATTEMPT_MS)) continue;

    if (ACTIVE_STATUSES.has(run?.status)) {
      return { dispatch: false, reason: "recent_inflight" };
    }
    return { dispatch: false, reason: "recent_attempt" };
  }

  return { dispatch: true, reason: "monitor_stale" };
}

function githubHeaders(token) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "X-GitHub-Api-Version": API_VERSION,
    "User-Agent": "docich-vm-monitor-watchdog",
  };
}

export async function runWatchdog(env, fetchImpl = fetch, nowMs = Date.now()) {
  const token = env?.GITHUB_ACTIONS_TOKEN;
  if (typeof token !== "string" || token.length === 0) {
    throw new Error("github_actions_token_missing");
  }

  const runsUrl =
    `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/` +
    `${WORKFLOW}/runs?branch=${REF}&per_page=20`;
  const runsResponse = await fetchImpl(runsUrl, {
    method: "GET",
    headers: githubHeaders(token),
  });
  if (!runsResponse.ok) {
    throw new Error(`github_runs_read_failed_${runsResponse.status}`);
  }

  const payload = await runsResponse.json();
  if (!payload || !Array.isArray(payload.workflow_runs)) {
    throw new Error("github_runs_schema_invalid");
  }

  const decision = decideDispatch(payload.workflow_runs, nowMs);
  if (!decision.dispatch) {
    return { dispatched: false, reason: decision.reason };
  }

  const dispatchUrl =
    `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/` +
    `${WORKFLOW}/dispatches`;
  const dispatchResponse = await fetchImpl(dispatchUrl, {
    method: "POST",
    headers: {
      ...githubHeaders(token),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: REF }),
  });
  if (dispatchResponse.status !== 204) {
    throw new Error(`github_dispatch_failed_${dispatchResponse.status}`);
  }

  return { dispatched: true, reason: decision.reason };
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(
      runWatchdog(env).then((result) => {
        console.log(
          JSON.stringify({
            event: "vm_monitor_watchdog",
            dispatched: result.dispatched,
            reason: result.reason,
          }),
        );
      }),
    );
  },
};
