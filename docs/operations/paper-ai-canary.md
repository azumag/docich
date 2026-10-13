# PAPER AI canary

`docich paper-ai-canary` is a one-shot, non-publishing acceptance probe for
the low-cost PAPER path introduced by #1767.

It validates this sequence without starting the PAPER corner:

```text
synthetic BTC/JPY holding in TemporaryDirectory
  -> Cloudflare Web Search
  -> docich WebBroker verified public body
  -> PAPER research DTO
  -> fixed Cloudflare Workers AI direct model
  -> strict JSON validation
  -> hash/count-only result
```

## Safety boundary

The canary does **not** read or write the production trading directory. It does
not enqueue speech, publish overlay text, change a strategy, create an order,
modify a PAPER ledger, start/stop a corner, or enable any production feature
flag. The synthetic state is deleted with its temporary directory after the
single run.

Native AI dispatch queues, telemetry, backoff and failure streaks also use
that temporary directory. The canary overrides inherited state paths and its
improvement-gate state path in a private environment copy, so it neither reads
the production improvement gate nor alters shared dispatcher state.

Search snippets/descriptions are not accepted as evidence. The existing PAPER
Web Search adapter requires fetched WebBroker bodies. The canary sends the
bounded public research DTO to the model and tells it to treat every document
instruction as untrusted data.

Model output is never printed or persisted. Successful output contains only
status, source count, whether an asset background was verified, the fixed model
identifier, output character count and SHA-256.

The generated prompt is capped at 16 KiB before the direct provider is called; an oversized canary fails without starting inference.

## Dry run

Dry-run is the default and performs no network or provider call:

```console
bin/docich --config config/docich.soren-live.toml paper-ai-canary
```

It prints only secret-free capability presence. It never prints credential
values or secret file paths.

## Real one-shot execution

Real execution additionally requires `--execute` and
`DOCICH_ALLOW_REAL_AI=1`.

Required capabilities:

- `DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID`
- `DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN` — use a dedicated Cloudflare API token with Account > Workers AI > Read and Account > AI Gateway > Read
- `DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID`
- exactly one of `CLOUDFLARE_API_TOKEN` or `CLOUDFLARE_API_TOKEN_FILE`

The Web Search token is intentionally separate from the direct generation
credential. The canary does not copy one credential into the other namespace.
The canary pins Web Search to provider `ceramic` and gateway id `default`;
a stored BYOK alias or alternate search provider is ignored for this acceptance
probe.

The fixed direct model is
`cloudflare-api:cf/qwen/qwen3-30b-a3b-fp8`. The CLI does not accept an
arbitrary model, endpoint, URL, command, output path or provider fallback.

```console
DOCICH_ALLOW_REAL_AI=1 \
  bin/docich --config config/docich.soren-live.toml paper-ai-canary --execute
```

A successful result is shaped like:

```json
{
  "asset_background": true,
  "direct_agent": "cloudflare-api:cf/qwen/qwen3-30b-a3b-fp8",
  "output_chars": 120,
  "output_sha256": "<sha256>",
  "production_state_written": false,
  "publishing": false,
  "research_backend": "websearch_verified_body",
  "source_count": 2,
  "status": "ok"
}
```

The numeric example is illustrative, not a recorded live result.

## Owner-only production canary

For the production VM, do not use arbitrary `exec`. After this reviewed code is
on protected main and deployed, use the fixed VM operation:

```console
gh workflow run "VM operations" --repo azumag/docich --ref main \
  -f operation=paper_ai_canary \
  -f target=production \
  -f ref=main \
  -f confirm=production
```

The workflow sends only `ops/vm_actions/run_paper_ai_canary.sh` from trusted
main to the existing production exec gateway. The helper rejects arguments,
requires the production checkout HEAD to equal the immutable workflow SHA,
requires tracked files to be clean, reads only the fixed
`/home/ubuntu/soren/.env`, then rebuilds the process environment from scratch
with `env -i`. Only the Web Search account/token, Workers AI account/token
(or token-file path), fixed PATH/LANG/PYTHONPATH and the real-AI gate survive
into docich; Discord/OpenCode/other-provider credentials and ambient proxy
variables do not. Gateway production exec keeps the canary stdout/stderr in its
private mode-0600 VM log and the workflow suppresses remote stdout.

The operation does not install or create credentials. Missing Cloudflare search
or direct-AI capability therefore fails closed. Installing/rotating those
credentials remains a separate owner action.

A successful real canary atomically replaces
`$HOME/.config/docich/paper-ai-canary.json` with a mode-0600, secret-free
receipt. The receipt contains the exact deployed docich SHA, timestamp,
verified-source count, direct model identifier, output length/hash and
`publishing=false`; it never contains model text or credential values. A new
canary invalidates the previous receipt before making network/provider calls,
so a failed re-check cannot leave an older success as the current acceptance
record.

## Production enablement and rollback

A successful canary does **not** automatically enable production. After the
reviewed enable/rollback code is on protected main and deployed, use the
separate owner-only fixed operation:

```console
gh workflow run "VM operations" --repo azumag/docich --ref main \
  -f operation=paper_ai_enable \
  -f target=production \
  -f ref=main \
  -f confirm=production
```

`paper_ai_enable` requires a canary receipt from the **same current deployed
SHA**, no older than 24 hours. It re-reads only the fixed owner-managed
`/home/ubuntu/soren/.env`, validates the required Cloudflare capabilities,
then atomically creates `$HOME/.config/docich/paper-ai.env` mode 0600. The
file contains only the reviewed PAPER capabilities and flags:

- `DOCICH_PAPER_RESEARCH_BACKEND=websearch`
- Cloudflare Web Search backend/provider/gateway/account/token
- `DOCICH_PAPER_SCRIPT_DIRECT_ENABLED=1`
- `DOCICH_PAPER_IMPROVE_DIRECT_ENABLED=1`
- Workers AI account and exactly one direct token or token-file path

The canonical rotation unit, its legacy-compatible unit name, and the dedicated
PAPER unit load this file as an **optional** `EnvironmentFile`. Missing file
therefore means the existing legacy behavior. No active service/corner is
restarted by enable: the next oneshot PAPER/rotation invocation reads the new
capability file.

PAPER improvement runs that are detached with `systemd-run` do not put secret
values in transient-unit argv or properties. A fixed reviewed wrapper reads the
same `paper-ai.env` inside the child immediately before exec.

Rollback is a separate fixed operation:

```console
gh workflow run "VM operations" --repo azumag/docich --ref main \
  -f operation=paper_ai_disable \
  -f target=production \
  -f ref=main \
  -f confirm=production
```

`paper_ai_disable` removes only the fixed regular
`$HOME/.config/docich/paper-ai.env` file and refuses symlinks or unexpected
types. It does not stop an already-running PAPER corner. Future invocations
fall back to Google News RSS/Wikipedia, existing `script_agents`, and existing
`improve_agents`.

Activation is exact-SHA-gated at the time of enable. A later ordinary docich
deploy does not silently rewrite or delete the capability file; if a fresh
acceptance is desired after relevant AI-path changes, run the canary again
before a new enable decision.
