# Metadata-triggered VM repair dispatch

This dispatcher consumes only `tmp/diag_events/*.json`, using the exact five-field safe metadata schema emitted by Soren's existing ingest. It never opens the raw diagnostic spool or interpolates event text into commands. A timer dispatches at most one job each minute; **the existing ChatGPT hourly scheduled task still owns PR review and merge**. This timer neither reviews nor merges PRs.

The deployment gateway/stage helper remains the authority for live-state matching, the shared deployment lock, pre/post health validation, rollback, and pending-repair reconciliation. A Git branch must be committed and successfully pushed before staging. The dispatcher saves `staged` before publishing a ready PR; a GitHub failure retries publication on a later dispatch, without regenerating the candidate or reverting healthy live changes. It looks up the unique branch before creation to recover an ambiguous successful create. The stage helper must make applying the same event/candidate idempotent, including a crash between successful staging and saving the worker job.

## Trusted configuration

Install code, configuration, policy and fixed executable commands as root-owned, with no group/other write permissions, including their parent directories. Configuration has `enabled: false` by default until real diagnostics, tests and a generator are installed and independently verified. No model provider, credential or production repair policy is supplied here. An unconfigured installation fails closed.

Worker configuration fields:

- `enabled`: explicit boolean true to dispatch.
- `policy`: absolute root-owned policy JSON path.
- `gateway_config`: separate trusted gateway configuration, default `/etc/azumag-vm-ops.json`.
- `state_dir`: private, mode 0700 directory for jobs and candidates.
- `events_dir`: absolute path to the existing safe `tmp/diag_events` directory; never the raw spool.
- `stage_helper`: absolute path to trusted `stage_repair.py`.

Policy fields shared with the stage helper:

- `source_repo`: fixed local Git repository; fetches `origin/main` for objects, but uses the currently deployed parent revision's `games/soviet_now` gitlink as the repair base.
- `github_repo`: fixed `owner/repository`; used for pushes and PR publication.
- `repair_kind`: fixed lowercase identifier, e.g. `game_audio`.
- `allowed_paths`: exact existing regular tracked file paths. No additions, deletions or symlinks.
- `projection`: `games/soviet_now`.
- `health_command`: fixed absolute argv; exit 0 = healthy, 1 = reproduced fault, all other codes = diagnostic failure. It runs on the host and therefore must be independently trusted, bounded and free of mutations.
- `generator_mode`: `sandbox` (default) or explicitly configured `broker`.
- `generator_command`: fixed absolute argv, root-owned executable; sandbox executables must be under `/usr` or `/bin`, while a broker can be installed under `/usr/local/libexec`.
- `test_command`: fixed absolute argv; runs against candidate files in an isolated environment.
- Optional `health_timeout` (30), `generator_timeout` (120), `test_timeout` (120).

In default `sandbox` mode the generator and tests run inside Linux Bubblewrap with no network, read-only `/usr`, `/bin`, libraries and candidate checkout at `/candidate`; a private `/tmp`, empty environment apart from PATH/HOME, and no host home, runtime directory, credentials or configuration. The candidate itself is read-only to these commands. Commands receive no report content or diagnostic output. The generator must print exactly:

```json
{"replacements":{"allowed/relative/file":"complete UTF-8 replacement content"}}
```

The trusted dispatcher validates the paths and writes the replacements. Output is bounded to 1 MiB, each replacement to 256 KiB, and command duration is limited. This mode supports deterministic repair generators. It deliberately does not invoke an unconstrained agent shell or supply model credentials.

Explicit `broker` mode invokes a root-owned trusted adapter outside the candidate sandbox. It receives only stdin JSON `{"repair_kind":"fixed_policy_kind","files":{"allowed/path":"UTF-8 content"}}`, bounded to 1 MiB in total. The worker passes no event, viewer text, source, candidate absolute path or inherited environment. The adapter runs in a fresh empty temporary working directory with only PATH and a temporary HOME; it must configure its own approved text-only OpenCode connection and deny tool execution. The broker has host privileges and is therefore part of the trusted installation: this mode must never point to candidate code or an arbitrary agent executable. Its output uses the same strict replacement JSON schema. Candidate testing and staging remain sandboxed regardless of generation mode.

## Installation and operations

Install Python 3, Git, GitHub CLI (`/usr/bin/gh`) and the root-owned AppArmor-enabled Bubblewrap copy (`/usr/local/bin/bwrap`). Provision GitHub authentication outside this repository using the operator's normal secure process. It is available only to host publication, not sandboxed code. Copy the unit examples to the system unit directory, adjust the fixed installed paths and service identity to match the stage gateway's permission model, and load them. Enable the timer **only after** trusted policy and sandbox/probe tests pass. The examples are intentionally not installed or enabled automatically.

Private job files contain only event metadata, phase, commit IDs, branch, repair ID, PR URL and fixed error codes; command output and errors are not saved or sent to GitHub. Git changes themselves are the review evidence. Jobs in `needs_attention` require inspection, preserving their candidate and state. Successful jobs remain `awaiting_review` locally; the gateway owns final reconciliation after main deployment. There is no local PR polling. Event files and job records are not deleted automatically; retention must be configured separately without deleting active recovery records.

Run focused tests with `python3 -m unittest discover -s ops/self_repair/tests`. Linux integration must additionally prove real Bubblewrap isolation, staged rollback, publication failure recovery and the normal main deployment path before enabling production dispatch.

## OpenCode text-only adapter

`broker.py /etc/docich-self-repair-model.json` uses the existing VM OpenCode 1.x client and provider authentication, but runs in a fresh empty directory with an explicit `soren-self-repair` agent, all permissions denied and one step. It passes source on stdin, never raw chat, and refuses tool/error events. The model policy contains `model`, `opencode_home`, and optional `timeout`; provision it root-owned with non-writable parents. Example existing route: `opencode/muse-spark-1.3-contributor-free`, home `/home/ubuntu`. No credentials are copied into the policy.

The inline permission override follows [OpenCode configuration precedence](https://opencode.ai/docs/config/) and [agent permissions](https://opencode.ai/docs/agents/). Candidate execution remains inside Bubblewrap. Model output alone is never deployment authorization.

## Dispatch bounds and retention

The worker accepts at most **one new report per timer tick**, only when its event timestamp is within the last 15 minutes (at most 60 seconds of future clock skew). Queued reports are checked again before diagnostics. Hash deduplication defaults to one hour; root policy `cooldown_seconds` may set 0–86400 seconds. Directory scanning reads at most 512 entries; exceeding this limit rejects new ingestion with `event_scan_capacity` in the private dispatch status. There is a hard limit of 1,000 persisted jobs. At capacity new ingestion stops with `job_capacity`, while existing jobs can finish; unexpected excess stops dispatch with `job_capacity_exceeded`.

These are fail-closed operational limits, not automatic deletion rules. Active jobs, review-pending jobs, candidates and recovery data are never removed. A full event directory or job store requires operator-directed archival/retention before more reports can enter. Freshness checks prevent old archived events from being replayed. Install the service as the existing `ubuntu` runtime owner, never root; the supplied unit makes User/Group explicit and adds basic host hardening. Bubblewrap requires working unprivileged user namespaces under these restrictions; confirm this in installation tests.

## Initial production policy and explicit limits

The first policy is `event_overlay_python_syntax`, limited to `generate_event_overlay.py`. A syntax error is the only automatic-repair trigger. Candidate tests render a synthetic event inside the sandbox. Live post-validation requires syntactically valid source AND a live HTML document generated after the source update and within the last 30 seconds with the expected overlay containers. Missing/stale HTML, arbitrary layout problems, sound, subtitles and other symptoms currently return unsupported/diagnostic failure; they do not authorize modifications. Add independently tested policies before expanding coverage. No game/encoder/worker restart is performed.

Register its root-owned policy path in `/etc/azumag-vm-ops.json` under `repair_policies.event_overlay_python_syntax`. Both stage and main deployment resolve that same policy for post-validation. A reinstalled gateway preserves existing registrations. Main must be merged by the existing owner-authorized schedule; the workflow actor/protection gate has not been relaxed. For a Soren repair PR, the schedule must also create/review/merge the docich gitlink bump; a Soren-only merge does not finalize the live ledger.

At 512 existing diagnostic event entries or 1000 private jobs the worker stops accepting new work and records a capacity status. It never deletes report archives or recovery records. Activation should be supervised until retention/notification integration is extended; this is a bounded initial rollout, not unattended indefinite operation.

## Observed rollout gate (2026-09-07)

VM tests verified existing safe diagnostic ingestion and legacy dispatcher disabled, but the deployed docich gitlink `7ad8cb5` does not contain the same ingest/lockdown code as live Soren. `generate_event_overlay.py` also differs between live and that gitlink. Do not enable the timer or reset live to the stale source. Synchronize/review the actual runtime baseline first, preserving those changes; verify safe ingest present, legacy dispatch disabled, target preimages/modes equal, policy registration, actual health and event-store capacity. The new worker fails closed on this mismatch. No blanket gitlink bump or legacy reactivation is included in this PR.

## Prompt-injection activation blocker (2026-09-07)

Treat viewer messages, source comments/strings, diagnostic artifacts and generated patches as untrusted data, including claims to be an operator, reviewer or system instruction. None may change policy, commands, paths, permissions or approval criteria. Raw chat is excluded from the model request; source text can still carry indirect injections. Prompt wording and OpenCode tool denial are defense in depth, not proof that replacement code is safe.

**Production activation remains blocked on generated-code execution containment.** The current path allowlist, syntax/render checks and Bubblewrap candidate test do not prevent malicious code inside an allowed file from behaving differently after deployment. The production runtime executes outside that candidate sandbox. A correct health result does not authorize arbitrary model-generated code, and the later hourly review cannot undo pre-review execution or data disclosure. This is an unresolved implementation gap, not an enforced content-safety gate. Do not enable this worker merely because CI passes or the source baseline is synchronized.

Before activation, implement and independently review a boundary that either restricts pre-review repairs to trusted, predefined operations with validated data parameters, or contains the deployed code itself with enforced filesystem/network/process permissions. Unrestricted generated executable changes must receive review before host execution until that boundary exists. Keep the existing hourly review and CI; do not substitute a model's self-approval, a text blacklist or a timeout for authorization.

Required adversarial validation must cover source-embedded role impersonation and encoded instructions, allowed-file code that behaves benignly in tests but attempts host access after deployment, requests to change health checks/policy or approval rules, and repeated reports intended to exhaust resources. Prove rejection or containment at the actual execution boundary. Existing functional tests and the tool-free OpenCode smoke test do not constitute this validation.
