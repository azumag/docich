# RADIO consumer bridge — preparation, not delivery

Refs #1767 / #829. Core baseline: docich `e12b491a557221edf65a2efd67d1a15d1a27df6f`
(PR #1852). Development base: `6497f364bf3e45c2c6717d8f4618485e675df134`
(#1853; its PAPER-only changes do not overlap this slice). This slice reuses `radio.script.generate_script`; it does not restore
closed #1795 or add another planner, collector, provider, parser or queue.

## Boundary added

`bin/docich-radio-script --agents <explicit-direct-chain>` consumes one UTF-8
JSON document from stdin and returns one UTF-8 JSON document on stdout. It is a
process boundary that the existing RADIO consumer can call; **the running
consumer is not switched by adding this launcher**. The existing `docich radio`
reference CLI and Soren workers are unchanged.

The caller must close stdin after writing one request and impose a subprocess
wall-clock timeout covering input, startup, preparation and output. The existing
core still shares at most 45 seconds between JEV, collection and generation.
The bridge does not create a background worker or implement retries.

```json
{"schema":"radio-script-request-v1","request_id":"example-01","topic":"A public topic","queries":["caller-owned public query"]}
```

Request limit: 16 KiB. The exact four fields are required. Duplicate keys, unknown
keys, invalid UTF-8, non-finite JSON constants and invalid request IDs are rejected.
`request_id` is a caller correlation token, **not a durable idempotency receipt**.
The request cannot set agents, credentials, environment, endpoint, provider,
voice, file paths, commands or queue location. Public-topic projection and query
limits remain in the existing core rather than a second authority policy.

`--agents` is operator configuration, not model output. The core validates its
existing concrete direct-model allowlist before classification. The launcher
pins this checkout's `src` and uses Python `-P`, matching the existing comment
classifier launcher's import boundary.

All three existing gates must be `1`:

- `DOCICH_RADIO_SCRIPT_DIRECT_ENABLED`
- `DOCICH_RADIO_RESEARCH_ROUTING_ENABLED`
- `DOCICH_ALLOW_REAL_AI`

Defaults stay off. A disabled launcher does not read stdin or import the core.
A missing research/real-AI gate while the script gate is enabled is an explicit
configuration error. No key or provider setting is inferred from the request.

## Response and consumer obligations

Response fields are `schema=radio-script-response-v1`, `request_id`, `status`,
`scope`, `script`, `materials`, and **`delivery=not_requested`**. On `ok` or
`partial`, `script` contains `body`, `summary`, `selected_news`; `materials` keeps
the existing `VerifiedWebMaterial.wire()` values including provenance and query
coverage. The actual UTF-8 serialized response is bounded to 128 KiB. Oversize
responses fail as a whole; text, hashes and evidence are never silently truncated.
Errors contain a bounded known status and no script/material payload or raw
provider exception. Incidental Python stdout/stderr during preparation is
suppressed; subprocess providers retain their existing captured-output contract.

Exit status 0 means **a preparation result exists**, not accepted, queued, spoken
or acknowledged. The consumer must parse and validate the response schema,
matching request ID, status and partial coverage, and apply its own reviewed
acceptance policy. A nonzero exit, malformed/truncated JSON or missing output
must hold the request without queue insertion or legacy/OpenCode fallback.
Do not treat a retried invocation as deduplicated: request ownership, inflight,
durable deduplication and acknowledgement remain consumer responsibilities.

## Confirmed existing delivery path and remaining hook

Source inspection used docich `src/docich/chat.py` and the parser reference
Soren `793990939dbfd262491846be52a804841d7aa56c`,
`broadcast/radio_engine.sh`. This reference is **not proof of the running VM's
revision or effective configuration**.

Current `_radio_generate_and_play` owns game/corner inflight and done markers,
active-game projection, persona/prepass, parsing, quality rewrites, fact-check,
announcements, generation metadata and history. It calls
`_enqueue_deferred_radio_talk`, marks done only after enqueue succeeds, and leaves
playback to `audio_worker`. News attribution sidecars are also handled there.
Directly passing the new body to `tts.run_tts` or `say_enqueue` would bypass that
RADIO scheduling/acceptance path and is not a valid connection.

Before the active hook, preserve or explicitly migrate:

1. Caller-owned typed topic/query construction and trusted persona/template input.
   #1852 accepts topic/query, not the existing assembled persona prompt. Passing
   the entire legacy prompt as the JEV topic is not an equivalent migration.
2. Quality and fact-check acceptance without silently re-enabling CLI-based
   prepass, rewrite or provider fallback. `partial` requires explicit handling.
3. Existing deferred queue/audio owner, voice and attribution metadata, inflight,
   history, failed enqueue, retry identity and done-after-ack semantics.

Epic #829 is authoritative for ownership: native logic stays in docich. An
interim Soren compatibility adapter is permitted only with a reviewed expiry and removal
condition; a new permanent Soren generation framework or parallel queue is not.
The adapter must be removed when the native consumer owns the above acceptance
and delivery contracts and old-reference/new-consumer parity regressions pass.
No transitional adapter is installed by this slice.

## Separate operational acceptance

Code/fixture tests do not constitute real API acceptance or feature enablement.
Before a real canary, confirm the current execution revision, assigned runtime
owner, explicit JEV route/model, search backend/provider, direct LLM chain,
per-stage request/retry ceilings and total spending cap. Use approved existing
credentials without displaying them; new credentials/scopes require separate
approval. First capture results outside production queues with real-run gates
scoped only to that invocation. Record timestamp, revision, stage results,
latency, source coverage and observed request/cost usage without raw secrets.

Production cutover additionally requires an agreed consumer/voice/queue mapping,
acceptance and rollback criteria, plus approval for the exact worker restart if
needed. Do not restart the shared stream/audio infrastructure. Use the existing
owner-only deployment gateway, not ad-hoc VM edits. Rollback stops new native
submissions while retaining already queued items, inflight ownership and history.

## Validation / handoff

`tests/test_radio_bridge.py` exercises the byte protocol and launcher with a
synthetic preparation seam. `tests/test_radio_bridge_core.py` calls the actual
#1852 core with synthetic classifier/collector/generator transports and checks
legacy-parser round-trip, provenance, partial, api-only and hold paths.
Existing planner/script regressions remain separate; no live API is required.

The canonical `DOCICH_HANDOFF_PATH` was unset in this execution environment;
canonical handoff, production execution, banner and audio were not inspected or
changed. GitHub/accessible source are the basis of this slice. Current issue
#1767 has no assignee; public implementation notes are under `azumag`. Direct
confirmation from an active runtime owner is still pending.
