# #829 / #882: post-PR2 audit and the remaining gates

This is a status/planning doc, not an implementation PR. It exists so the
next PR does not have to re-derive scope from scratch, and so the one real
open design question (control-plane script shape) gets a reviewed decision
instead of a unilateral one.

## Final state (2026-10-08): every remaining gate closed

This section supersedes the "State as of 2026-09-22" snapshot below and the
"remaining gates" audit that follows it. Verified against `origin/main` =
`bd2053d06d2cc869a49580ec29bebdc2e65e1a51` and against the production VM's
read-only `diagnostics` operation (same day), not from memory.

| step in "Suggested order" below | state | evidence |
|---|---|---|
| 1. review/merge soviet_now#492 + docich#965 | **done** | both merged 2026-09-22 |
| 2. `games/soviet_now` gitlink bump | **done** | #990; gitlink = `fadbb7ce8baf3403c65cb8aa5b15702ddafa5e3d` |
| 3. `configure_jev_route.py` owner-only control plane | **done** | #969 — `configure_jev_route_direct` / `configure_jev_route_vercel` / `disable_jev_route` in `.github/workflows/vm-operations.yml`, sharing the fixed-script/secret-on-stdin contract |
| 3. `collect_diagnostics.py` wiring for `diagnostics.describe()` | **done** | #977, #987, #1025 — the `semantic_decision` section is projected from the live chat worker's environ, keyed on the live `COMMENT_CLASSIFIER_BACKEND` gate |
| 4. owner-run real-API canary, `route=direct` | **done** | `Jev route canary` run [35824279547](https://github.com/azumag/docich/actions/runs/35824279547) (2026-09-23, `routes=both`): direct `status=ok`, resolved `jev-1.13.0`, 4/4 synthetic-fixture agreement |
| 5. production enable + `route=vercel` | **done** | `configure_jev_route_vercel` (#969) + owner-configured `direct,vercel` failover (#1031). Production diagnostics now report `backend=jev`, `comment_classifier_backend=jev`, `route=direct`, `fallback_route=vercel`, `credential=present`, `fallback_credential=present` |
| 6. remove soviet_now legacy HTTP | **done** | `azumag/soviet_now` `main` no longer contains `lib/comment_classifier_jev.py` (HTTP 404); the classifier now lives in `src/docich/comment_classifier/` (#988) |

The Vercel route was probed in the **same** canary run [35824279547] with the
same 4-comment synthetic fixture and returned `status=ok`, resolved
`typesafe-ai/jev`, 4/4 agreement — no schema difference, so the issue's
"schema差異があれば暗黙補完せずroute contractとしてレビュー" branch was never
triggered.

The compatibility-adapter checklist item this doc flagged as *intentionally
not yet true* — "adapterがendpoint/model/key/validatorを独自実装しない" — is now
satisfied trivially: there is no adapter file left to violate it.

Left to other issues, **not** #882 gates:

- Live-traffic route/latency/fallback/usage/cost measurement belongs to #678
  ("精度・遅延・費用の実測"). `docich.comment_classifier.report` already emits
  `route_counts`, `failover_batches`, per-batch latency quantiles and
  known/unknown usage and cost (never 0 for unknown), so the tool exists; only
  the production burn-in report is outstanding and it is #678's.
- `DOCICH_JEV_TIMEOUT_MS` stays unmanaged: the classifier always passes its own
  timeout, so this env default is still dead (open sub-question 3 below).

## State as of 2026-09-22 (historical; superseded by "Final state" above)

Merged into docich `main`:

- #937 — PR-0 baseline golden fixtures.
- #941 — native docich LLM dispatch (#829 PR-1; unrelated to this package).
- #942 — `src/docich/semantic_decision/{transport,routes,validator}.py`, the
  shared choice-request transport. See
  `docs/plans/829-882-semantic-transport-pr2.md` for its own scope note.

Open, awaiting review:

- azumag/soviet_now#492 — the soviet_now-side compatibility adapter
  (`docich_transport`/`_resolve_transport` in `lib/comment_classifier_jev.py`,
  opt-in via `DOCICH_SEMANTIC_BACKEND=jev`, `route=direct` only, legacy HTTP
  kept as the unchanged default/rollback path). No production behaviour
  change from this PR alone; no owner-only enablement performed.
- docich#965 — `src/docich/semantic_decision/diagnostics.py`, a pure,
  secret-free `describe(env)` projection matching #882's own "##
  diagnostics" JSON shape. Not wired into
  `ops/vm_actions/collect_diagnostics.py` yet (see below).

## Audit: #882's own checklists against current test coverage

### "### docich core" regression list — all items covered by
`tests/test_semantic_decision.py` (merged in #942), spot-checked by name:

golden/idempotent validation, route-unset defaulting to direct, invalid
route/timeout never spawning, fixed direct/vercel endpoint+model, key
cross-contamination rejected, redirect/proxy policy, HTTP status→reason
mapping, DNS/connect/body stall bounded+reaped, malformed/duplicate JSON,
missing/invalid response fields, unknown question/label rejected, parent
cancel reaps children, route switch does not change the input projection.
`persona/game/user/history` sentinel exclusion is a *consumer* projection
concern (`build_request`), not core; it is covered end-to-end by docich's
own `tests/test_semantic_comment_compat.py` and now duplicated against the
real soviet_now file by soviet_now#492's own test suite.

**Nothing outstanding here.**

### "### compatibility adapter" regression list — **done** (see "Final state")

All covered except one item, which was *intentionally* not yet true at the
time this doc was written:

> adapterがendpoint/model/key/validatorを独自実装しない

That item is now satisfied: rollout step 6 (legacy HTTP removal) has happened
— `lib/comment_classifier_jev.py` no longer exists in soviet_now `main`, so
there is no adapter of its own to violate the checklist.

### "### control plane" regression list — **done** (#969; see "Final state")

> direct -> vercel -> direct / disable / stale runtimeからcanonical設定を再読込 /
> effective route/model/credential presence確認 / secret leakなし /
> unrelated PID維持 / #829 purpose mode/question-setを変更しない

`configure_jev_route.py` + the three `vm-operations` steps now exist (`#969`),
the diagnostics wiring reads the effective route (`#977`/`#1025`), and
production diagnostics report the effective route/model/credential presence.
**Nothing outstanding here.**

### "## 実API canary" — **done** (run 35824279547; see "Final state")

Both routes passed one owner-dispatched synthetic canary with the same
fixture (`direct` and `vercel`, both `status=ok`, 4/4 agreement). No schema
difference was found, so no route-contract review was needed.

## Design question for the next PR: control-plane script shape

The issue text offers two shapes and does not pick one:

```text
configure_jev_direct / configure_jev_vercel / disable_jev   (route-level, #882)
                    -- or --
configure_semantic_routing                                   (shared with #829's
                                                                purpose mode/question-set op)
```

Recommendation: a **new, separate** `ops/vm_actions/configure_jev_route.py`,
not an extension of the existing `ops/vm_actions/configure_comment_classifier_jev.py`
(#678's script). Reasoning:

- #678's script owns `COMMENT_CLASSIFIER_BACKEND`/`COMMENT_CLASSIFIER_JEV_*`/
  `TYPESAFE_API_KEY` — soviet_now's own purpose-level enable/disable and its
  pre-#882 credential. #882 must not silently start co-owning those keys.
- The new script would own only `DOCICH_SEMANTIC_BACKEND`/`DOCICH_JEV_ROUTE`/
  `DOCICH_JEV_VERCEL_API_KEY` — the docich-canonical, route-level keys.
- Both scripts would still need to restart the same chat worker (soviet_now
  re-execs the classifier as a subprocess per batch, but that subprocess
  inherits the long-lived worker's already-exported shell env — see the
  existing rollback note in soviet_now's `docs/comment_classifier_jev.md`:
  editing `.env` alone does not affect a long-lived shell's already-exported
  vars). The new script should **import and reuse**
  `configure_comment_classifier_jev.restart_chat_worker`, not fork a second
  copy of that PID/cmdline/environ verification logic.
- Precedent: `restart_chat_worker`'s own effect is *not* independently unit
  tested in `ops/vm_actions/tests/test_configure_comment_classifier_jev.py`
  today (only the env-rewrite functions are); only real owner-only execution
  proves the restart path. The new script inherits the same limitation, not
  a new one.

Open sub-questions worth a reviewer's opinion rather than a unilateral call:

1. Should `--route=direct` require `TYPESAFE_API_KEY` to already be present
   (fail closed) or is that out of this script's concern entirely (soviet_now
   docs already state the precondition)?
2. Does `--disable` need to scrub `DOCICH_JEV_VERCEL_API_KEY` from the env
   file the same way `TYPESAFE_API_KEY` removal is documented today, even
   though `DOCICH_SEMANTIC_BACKEND=` alone already stops delegation?
3. `DOCICH_JEV_TIMEOUT_MS` is currently **dead** for the soviet_now consumer:
   soviet_now#492's adapter always passes `timeout_ms=` explicitly from its
   own `COMMENT_CLASSIFIER_JEV_TIMEOUT_MS`, so docich's own env-based
   default is never read on that path. Worth deciding whether this script
   should manage it at all before a second consumer exists that needs it.

## Wiring `diagnostics.describe()` into the real collector — **done** (#977/#987/#1025)

docich#965 landed the pure projection only. The wiring into
`ops/vm_actions/collect_diagnostics.py` is now shipped: the collector projects
the live chat worker's own `/proc/<pid>/environ` through a fixed key
allowlist, and #1025 re-keyed the whole section on the live
`COMMENT_CLASSIFIER_BACKEND` gate (retiring the never-read
`DOCICH_SEMANTIC_BACKEND`). Production reports it under the
`semantic_decision` key.

## Suggested order for what's left (all steps now done — see "Final state")

1. Review/merge soviet_now#492 and docich#965 (independent of each other).
2. Bump the `games/soviet_now` gitlink in docich to #492's merge commit, in
   its own small PR, once #492 is on soviet_now `main`.
3. `configure_jev_route.py` (owner-only control plane) + the
   `collect_diagnostics.py` wiring for `diagnostics.describe()`, once the
   design questions above have an answer.
4. Owner-run synthetic canary, `route=direct` first (existing credential),
   confirmed via the new diagnostics section and #882's own "## 実API
   canary" checklist.
5. Only after step 4: enable `DOCICH_SEMANTIC_BACKEND=jev` in production via
   the new script, burn in, then repeat 4-5 for `route=vercel` with its own
   credential.
6. Remove soviet_now's own legacy HTTP implementation from
   `lib/comment_classifier_jev.py` (rollout step 6), only after step 5 has
   run in production without rollback.

Neither #829 nor #882 is complete at any point before step 6. Step 6 has now
happened (see "Final state" at the top), so **#882's scope is complete**;
#829's own remaining units are tracked in `829-staged-migration-status.md`.
