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

## Production enablement

A successful canary is **not** permission to enable production. PAPER research
and narration remain independently controlled:

- `DOCICH_PAPER_RESEARCH_BACKEND=websearch`
- `DOCICH_PAPER_SCRIPT_DIRECT_ENABLED=1`

They should be changed only after their reviewed code is on main, the intended
credentials are installed through the existing secret mechanism, and the
one-shot canary succeeds on that same deployed revision. Rollback remains the
legacy RSS/Wikipedia research backend and existing `script_agents` chain.
