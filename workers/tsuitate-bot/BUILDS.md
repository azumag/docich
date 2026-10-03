# Cloudflare Workers Builds

This directory contains the Worker source and Cloudflare project configuration. `builds-plan.json` is a repository-side proposal for Workers Builds settings; Cloudflare does not read or apply it automatically. The GitHub Actions workflow validates the Worker and does not deploy it.

## Project settings

The project root is `workers/tsuitate-bot`, the Worker name is `docich-tsuitate-bot`, and the configured compatibility date is `2026-09-21`. `cloudflare.config.ts` sets `worker.previewUrls` to `false`; the pinned Cf build output test asserts this value survives configuration. This controls version preview URLs and is separate from the `workers.dev` route. See [Cf project configuration](https://developers.cloudflare.com/cf/projects/cloudflare-config/).

The plan targets the `main` production branch and disables branch previews. It scopes ordinary build watch events to `workers/tsuitate-bot/*` with no excludes. Paths are repository-relative, including when a project root is set. Cloudflare's documented watch filter has bypass cases: pushes with zero changed files, at least 3000 changed files, or at least 20 commits can build regardless of the path match. These cases prevent a strict guarantee that every unrelated change is skipped. See [Build watch paths](https://developers.cloudflare.com/workers/ci-cd/builds/build-watch-paths/) and [Build branches](https://developers.cloudflare.com/workers/ci-cd/builds/build-branches/).

## Build and deploy commands

With the project root as the working directory, the build command is:

```sh
npm run build:cf
```

It runs `cf build`, `npm test`, `npm run test:workerd`, and `npm run test:bundle` sequentially. Any failed command stops the chain before the separate deployment step. The deploy command in the plan uses the already-built output:

```sh
./node_modules/.bin/cf deploy --prebuilt --worker docich-tsuitate-bot --tag "$(git rev-parse HEAD)"
```

Workers Builds root directory and commands are configured in Cloudflare, not by this JSON file. Check its build result independently; passing local fixtures or GitHub Actions does not prove an upload or live webhook response. See [Cf build and prebuilt deploy](https://developers.cloudflare.com/cf/projects/) and [Workers Builds configuration](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/).

## Runtime configuration

`BOT_ID` in source remains a placeholder and `WEBHOOK_SECRET` is declared as a secret binding without a value. Live requests require a matching Bot ID and HMAC secret. Local values belong in the ignored `.dev.vars` file. Secret values must not be added to source, fixture logs, or build output.

## Verification

The Worker Actions job runs `cf build`, Node fixtures, test-only workerd integration checks, and tests against the actual Cf-generated bundle. The bundle test checks its manifest, `previewUrls`, SQLite Durable Object export, `GAME_STATE` binding, HMAC behavior, history transactions, and timeouts. `test:workerd` uses `wrangler.runtime.toml` for local SQLite transaction tests. Neither test path deploys the Worker or calls Cloudflare APIs. The Cf Build Output reader is a beta interface; rerun these checks when updating the pinned Cf CLI.
