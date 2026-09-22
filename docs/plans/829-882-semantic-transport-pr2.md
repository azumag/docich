# #829 / #882: first semantic transport extraction (no cutover)

## Reviewed baseline and scope

- docich main: `2b23a0a82bb32b8b0b09b45828286909126b5fdf`.
- Legacy golden: `docs/plans/829-pr0-baseline-inventory.md` and
  `tests/fixtures/jev_direct_golden.json`.
- Legacy source: `soviet_now@9af382fbbaae9a79ed4d5921a0f6b9cbf3ef8e23`,
  `lib/comment_classifier_jev.py`, Git blob
  `1d3ef4b5d4d5b6138cb132a93c0ea4d0cc5dc432`.
- Parallel LLM dispatcher work is PR #941. This package neither imports that
  dispatcher nor changes its entry points; it can be reviewed independently.

The only new implementation owner is `src/docich/semantic_decision/`.
`routes.py`, `validator.py` and `transport.py` are shared by both issues. There
is one HTTP worker, not separate direct/Vercel clients or separate validators.
This PR does not implement the larger comment/radio pipeline or semantic policy.

## Contract

`request_once(request, *, route=None, env=None, timeout_ms=None)` is an internal
client seam. It accepts the existing model/state/questions choice request and
returns an allowlisted status, validated data on success, and secret-free `meta`.
It performs no file writes, scheduling, fallback or generation.

The registry fixes endpoint, requested model and credential selector:

| Route | Endpoint | Requested model | Credential selector |
| --- | --- | --- | --- |
| direct | `https://api.typesafe.ai/v1/systemone` | `jev-1.13.0` | `TYPESAFE_API_KEY` |
| vercel (prototype) | `https://ai-gateway.vercel.sh/typesafe/v1/systemone` | `typesafe-ai/jev` | `DOCICH_JEV_VERCEL_API_KEY` |

Unset route reads `DOCICH_JEV_ROUTE`, defaulting to direct. Unset timeout reads
`DOCICH_JEV_TIMEOUT_MS`, defaulting to 1500ms (50..5000ms). There is no arbitrary
endpoint/model environment override, credential cross-fallback or route failover.
Legacy `COMMENT_CLASSIFIER_JEV_*` configuration is still owned by the unchanged
legacy consumer; the test bridge passes its existing timeout explicitly.

A request is serialised before spawning, with a 32768-byte cap. The only child
credential is the selected key in a fresh environment, never argv or stdin.
The child uses `python -I`, the reviewed worker file, TLS verification, no proxy,
no redirect and zero retries. Its response is capped at 131072 bytes. One absolute
wall budget covers serialisation, process acquisition, DNS, connect and body read.
TERM/INT cancellation kills the detached process group and reaps the direct child.
POSIX/main-thread operation is explicit; unsupported contexts fail before spawning.

Choice validation is per request question/criteria, not a copied comment rubric.
It rejects duplicate JSON keys, nonfinite/bool numbers, missing/unknown answer IDs,
invalid type/choice/probabilities/confidence/model/usage and unsupported request
contracts. It drops provider-echoed context and extras. Validation keeps the
`type=choice` discriminator, so validating in the child, parent and compatibility
consumer is safe and idempotent; the legacy consumer still receives its normal
category-only row output. Other question types need a separate reviewed extension.

`meta` contains route, requested/resolved model, latency, retry_count, usage and
`cost_usd=None`. Unknown cost is not represented as zero or derived from an old
price. No raw body, author, context, headers or exception text enters metadata.
There is no new persistent metrics store in this extraction.

## Compatibility and rollback boundary

**No production consumer is switched in this PR.** `docich chat`, `docich radio`,
`docich ai`, existing classifier invocation/stdout, shell heuristic, notification
protection, state/cooldown, queue, credentials and deployment configuration are
unchanged. The `games/soviet_now` gitlink and source are not modified. There is no
new CLI/control-plane operation and no runtime reload, VM operation or API request.

The compatibility bridge exists only inside tests. It injects the common transport
through the legacy classifier's existing `transport=` seam. Tests require identical
synthetic direct requests, category-only rows, .70 threshold behaviour, row-level
low-confidence fallback, notification protection and batch fallback on failures.
A keyless isolated process proves that the legacy rollback path does not import
or require docich. The first CI step also runs the core before any game checkout.

Reverting this additive PR removes the unused package/tests; no production route
or state needs restoring. The legacy HTTP implementation is retained solely as
the pre-cutover/rollback path, not as the destination for new provider features.
It must not be removed in this PR or before the later adapter/canary gates pass.

## Vercel and subsequent gates

The Vercel profile is an explicit, mock-tested prototype, **not live API proof**.
Only the literal reviewed response model `typesafe-ai/jev` is currently accepted
for that profile. A patch ID or different schema is rejected, not guessed or
normalised. A synthetic owner-authorised canary must establish the actual resolved
model/answer/usage contract; add any justified allowlist change as a reviewed diff.
The native evaluate API is not treated as this TypeSafe-compatible route.

Later PRs must separately cover direct live canary, Vercel live canary and any
reviewed schema delta, purpose/client/CLI adapters, owner-only configure/disable,
effective diagnostics, consumer cutover and eventual legacy HTTP removal. Route
rollback must remain explicit (vercel -> direct), and Jev disable must return to
the existing heuristic, never to a long-running legacy generation classifier.
Neither #829 nor #882 is completed by this extraction.

## Validation and handoff

Executed locally on Python 3.13.5: `python3 -m pytest -q
 tests/test_semantic_decision.py tests/test_semantic_comment_compat.py`:
**115 passed**. The locally materialised legacy source was verified against the
Git blob above. Tests use only synthetic keys/rows, fake HTTP and temporary state.
Real process tests cover DNS/connect/body stalls, spawn budget, SIGTERM/SIGINT,
restored handlers, reaped direct children and no live descendants.

The separate path-filtered CI workflow runs the core without a game checkout,
then requires the pinned legacy file (no silent compatibility skip), and runs the
compatibility suite plus PR-0 golden checks. No production workflow is modified.

Full repository tests, fresh remote CI, independent review, live canary and
production behaviour are not claimed by this local result. Full git cloning was
unavailable in this execution environment. `handoff.md` was not present through
the repository API; this plan and PR body are the handoff. VM banners/audio and
operational handoff/ops_brief generation were not run; no operational access was
expanded. No merge, deployment, secret read or configuration change was performed.
