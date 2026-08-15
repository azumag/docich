# Soren / docich operational handoff

> Updated: 2026-08-15 JST
> Runtime source: [`azumag/soviet_now`](https://github.com/azumag/soviet_now)
> Multi-game and caption source: [`azumag/docich`](https://github.com/azumag/docich)
> Never place stream keys, tokens, API keys, prompts, generated speech, or raw
> model/improvement logs in this document.

## Current outcome

The live Soren broadcast is running on the Oracle VM with FFmpeg direct
streaming, Japanese VOICEVOX audio, and English native Twitch captions. The
game continues after GAME OVER and also continues while improvement work is in
progress. Radio/search/tool reasoning is filtered from on-air output. The say
path has bounded retry, foreground priority, and deferred radio recovery.

docich now aligns that Soren work with the reusable multi-game foundation:

- generic display/audio/stream lifecycle remains isolated on `:98`;
- sorengame remains owned by soviet_now production on `:99`;
- docich contains the canonical reusable caption planner, Unix IPC client,
  native FFmpeg filter, pinned build, proof scripts, configuration, and tests;
- the generic stream fails open to normal audio/video if caption capability is
  unavailable;
- `start_all.sh` is explicitly excluded from the browser-adapter contract.

## Production identity

| Item | Value |
|---|---|
| SSH identity | `ubuntu@129.146.54.105` (`soren-prod-vnic`) |
| Runtime directory | `/home/ubuntu/soren` |
| Service | system `soren-runtime.service` |
| Display | `:99` |
| Audio bus | `soren_null.monitor` |
| Caption socket | `/run/user/1001/docich/ffmpeg-cc.sock` |
| Custom FFmpeg | `/home/ubuntu/build/docich-cc-c13837ddf/bin/ffmpeg` |
| Caption opt-in | `DOCICH_CC_ENABLED=1` |

The external RTMP push target is root-managed outside the repository. Do not
read or reproduce it during routine verification.

## Merged implementation

| PR | Main result |
|---|---|
| [soviet_now #98](https://github.com/azumag/soviet_now/pull/98) | output guard, synchronized VOICEVOX/English CC, fail-open audio, MiniMax JSON/reasoning controls, say retry/fairness |
| [soviet_now #99](https://github.com/azumag/soviet_now/pull/99) | current GitHub/local/VM integration and production retry fixes |
| [soviet_now #100](https://github.com/azumag/soviet_now/pull/100) | improve daemon supervisor ownership and GNU/BSD status fixes |
| [soviet_now #101](https://github.com/azumag/soviet_now/pull/101) | durable failed-improvement batch snapshot and retry-lock restoration |
| [soviet_now #102](https://github.com/azumag/soviet_now/pull/102) | native-caption response protocol version validation |
| [soviet_now #103](https://github.com/azumag/soviet_now/pull/103) | exact plan schema and hard 32-column x 2-line validation |

Current soviet_now `main` after #103:
`b647179983bca7520ad3a53a83d218fa7a8aa36e`.

The local `main` at `/Volumes/satelite/work_satelite/soren` is fast-forwarded to
that commit. Its unrelated untracked files are intentionally preserved.

## Live evidence from this rollout

- Twitch captions required explicit player/broadcaster enablement and a stream
  restart. After that, captions displayed, changed, and cleared in the live
  stream while audio continued.
- FFmpeg ran at about 29.94 fps and the A/V sync probe passed after deployment.
- The service had one supervised improve daemon. The daemon was a direct child
  of the service main process; duplicate detached daemons were removed.
- The game counter advanced through multiple GAME OVER cycles, disproving the
  earlier stopped-at-GAME-OVER state.
- A real 100-game improvement attempt ended `failed_no_apply`. Gameplay kept
  advancing, but the retry lock/backoff were absent and the batch was lost.
  PR #101 was merged and hot-deployed before the next threshold. It preserves a
  separate batch snapshot and restores only a current-hash eligible retry.

The production cycle is intentionally `MIN_GAMES_BEFORE_IMPROVE=100`; the two
fresh-objective early-trigger flags observed during this rollout are disabled.
Do not lower the threshold simply to make a test fire. Verify the next natural
threshold with structured state.

## Safe verification

Use only aggregate/structured fields for routine monitoring:

- service active state and MainPID;
- daemon PID, parent PID, and cgroup;
- game count and `game_state.json.state`;
- accumulated count, current strategy-hash prefix, Russia/Soviet counts, and
  best type;
- improve status/PID/phase/progress/timestamps;
- presence and age of improve lock, retry snapshot, and backoff;
- worker count, FFmpeg fps, A/V probe, caption socket readiness, and audio queue
  health.

Do not dump `.env`, process arguments containing a stream key, raw model output,
raw improve logs, private radio text, or full production history.

## Deployment rule

The VM runtime is not a normal Git worktree. A production change is complete
only when both sides are synchronized:

1. compare target VM files with the last known repository baseline;
2. stop if the VM has a newer or unexplained difference;
3. commit, push, review, and merge the source change;
4. stage only the named files on the VM;
5. verify checksum and syntax before replacement;
6. keep a timestamped/exact backup and rollback trap;
7. restart only when necessary and authorized;
8. verify live service, game, stream, audio, and captions separately.

Local tests do not prove VM deployment, and VM deployment without a pushed
source commit is not a completed change.

## Test status and known debt

- The focused runtime/config/continuous-gameplay/retry suites for PR #101 pass
  (48 tests).
- docich's complete stdlib suite passes after this integration (244 tests),
  including Unix-socket protocol and tampered-plan rejection coverage.
- The large legacy soviet_now `tests.test_escape_mechanisms` suite is not a
  clean release gate for the current runtime: the last full run executed 383
  tests with 105 failures and 3 errors, mostly stale static fixtures and a
  missing `sorengame/build/index.html`. Focused new tests pass, but the legacy
  suite still needs a separate fixture-alignment effort.

## Remaining gates

1. Observe the next natural improvement threshold. Confirm retry snapshot,
   running PID/state, continued game advancement, and either a strategy apply or
   preserved lock/backoff after failure.
2. Keep the docich native filter and the soviet_now compatibility copy in sync
   until a versioned artifact dependency replaces the copy.
3. Keep docich Issue #1 linked to the merged implementation, README, Wiki, and
   production evidence so the administrative record matches the deployed work.
4. Treat moving sorengame production ownership into docich as a separate
   cutover requiring a game-only Soren entry point and rollback proof.

## Documentation

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/games/sorengame.md`](docs/games/sorengame.md)
- [`docs/twitch_closed_captions.md`](docs/twitch_closed_captions.md)
- [`docs/soren_linux_migration_plan.md`](docs/soren_linux_migration_plan.md)
- [`native/ffmpeg/README.md`](native/ffmpeg/README.md)
