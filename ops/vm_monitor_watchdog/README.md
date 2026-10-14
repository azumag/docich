# VM monitor external watchdog

GitHub Actions `schedule` delivery is best-effort. The production VM monitor has repeatedly shown multi-hour gaps where no scheduled workflow run is created, while the same workflow succeeds immediately through owner-triggered execution. This watchdog provides an independent scheduler without moving VM credentials out of GitHub Actions.

## Design

- Keep `.github/workflows/vm-storage-monitor.yml` and its existing `schedule` triggers unchanged.
- Run this Cloudflare Worker from Cron Triggers at minutes `5,20,35,50` UTC.
- On each invocation, read the recent runs for `vm-storage-monitor.yml`.
- Do nothing when a successful run completed within the last 60 minutes.
- Do nothing when any workflow attempt started within the last 20 minutes, preventing retry storms and duplicate dispatch while a run is still active.
- Otherwise invoke the workflow's existing `workflow_dispatch` entry point with `ref=main`.
- GitHub remains the only place that stores VM SSH credentials. The watchdog only gets permission to inspect and dispatch this one GitHub Actions workflow.

The existing workflow already requires the protected `main` ref and owner-triggered manual execution. Therefore the fine-grained token used by this watchdog must belong to `azumag`; using another actor will cause the workflow job guard to reject the dispatch.

## GitHub token

Create a fine-grained personal access token with access to **only** `azumag/docich` and repository permission:

- Actions: Read and write

No Contents write, Issues write, VM SSH secret, or arbitrary repository mutation permission is required by the watchdog.

Store the token only as the Cloudflare Worker secret `GITHUB_ACTIONS_TOKEN`.

## Deploy

From `ops/vm_monitor_watchdog`:

```sh
npx wrangler secret put GITHUB_ACTIONS_TOKEN
npx wrangler deploy
```

The checked-in `wrangler.toml` installs the four-times-hourly Cron Trigger. The Worker makes only a GitHub API read during healthy operation; it creates an Actions run only after the monitor has been stale for more than 60 minutes.

## Verification

Before enabling the Cron Trigger in production:

```sh
node --test worker.test.mjs
npx wrangler dev --test-scheduled
```

Then verify both cases:

1. With a successful monitor run less than 60 minutes old, a scheduled invocation logs `dispatched:false` and does not create a workflow run.
2. Once the latest successful monitor is older than 60 minutes and there has been no attempt for 20 minutes, one invocation logs `dispatched:true` and GitHub creates a `workflow_dispatch` run on protected `main`.

Do not add VM credentials or a broad GitHub token to the Worker.

## Failure behavior

- GitHub run-list API failure: fail closed, do not dispatch.
- Invalid GitHub response schema: fail closed, do not dispatch.
- Dispatch failure: mark the Worker invocation failed; the next Cron invocation may retry after the recent-attempt guard allows it.
- Secrets and GitHub response bodies are never logged. Logs contain only the fixed watchdog event, boolean dispatch result, and fixed reason.

## Rollback

Disable or remove the Cloudflare Cron Trigger (or delete the Worker). The native GitHub `schedule` and manual `workflow_dispatch` paths remain unchanged.
