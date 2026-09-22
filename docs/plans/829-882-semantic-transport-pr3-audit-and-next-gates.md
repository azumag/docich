# #829 / #882: post-PR2 audit and the remaining gates

This is a status/planning doc, not an implementation PR. It exists so the
next PR does not have to re-derive scope from scratch, and so the one real
open design question (control-plane script shape) gets a reviewed decision
instead of a unilateral one.

## State as of 2026-09-22

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

### "### compatibility adapter" regression list

All covered except one item, which is *intentionally* not yet true:

> adapterがendpoint/model/key/validatorを独自実装しない

soviet_now#492 adds `docich_transport()` as a second, delegating path, but
**keeps** the file's own `request_once`/`http_worker`/fixed `ENDPOINT` as the
default and rollback path, per #942's own plan doc ("must not be removed in
this PR or before the later adapter/canary gates pass"). This checklist item
is only fully satisfied at rollout step 6 (legacy HTTP removal), after a
production canary — not before. Not a regression, just not done yet.

### "### control plane" regression list — **nothing started**

> direct -> vercel -> direct / disable / stale runtimeからcanonical設定を再読込 /
> effective route/model/credential presence確認 / secret leakなし /
> unrelated PID維持 / #829 purpose mode/question-setを変更しない

No `configure_jev_*`/`disable_jev` owner-only operation exists yet. This is
the biggest remaining gap and the next real PR. See design question below.

### "## 実API canary" — blocked, not attempted

No `TYPESAFE_API_KEY` / `DOCICH_JEV_VERCEL_API_KEY` is available outside the
production secret store; this needs an owner running the canary directly
once a control-plane path exists to do it safely (or a documented manual
procedure). Nothing here can be done from an unattended coding session.

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

## Wiring `diagnostics.describe()` into the real collector

docich#965 lands the pure projection only. Wiring it into
`ops/vm_actions/collect_diagnostics.py` needs a decision on *whose*
environment is authoritative — the chat worker's own `/proc/<pid>/environ`
(via a helper mirroring `configure_comment_classifier_jev._process_env`,
restricted to a fixed key allowlist so no unrelated secret is ever read) is
the only runtime that currently could read `DOCICH_SEMANTIC_BACKEND` at all,
since docich's own transport has no long-running process of its own. That
wiring should land together with (or right after) `configure_jev_route.py`,
so the new diagnostics section has something real to report on day one
instead of permanently reading `"legacy"`.

## Suggested order for what's left

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

Neither #829 nor #882 is complete at any point before step 6.
