# Soren Linux / FFmpeg direct-stream status

> Updated: 2026-08-15
> Runtime repository: [`azumag/soviet_now`](https://github.com/azumag/soviet_now)
> Architecture repository: [`azumag/docich`](https://github.com/azumag/docich)

This page used to be a pre-cutover plan. The Linux and FFmpeg-direct migration
is now the live production topology. It remains a useful record of ownership,
rollback, and the separate future migration from Soren's monolithic runtime to
the generic docich multi-game controller.

## Current status

| Area | Status |
|---|---|
| Oracle A1 / Ubuntu / Xvfb | live on display `:99` |
| FFmpeg direct publisher | live under `soren-runtime.service` |
| PulseAudio | `soren_null.monitor` carries game/BGM/SE/TTS |
| OBS | retained as rollback path, not the active backend |
| 1280×720 at about 30 fps | live structured probes pass |
| A/V synchronization | live probe pass |
| native Twitch captions | live; user enabled CC and captions displayed/cycled/cleared |
| radio output guard | live; analysis/tool/search progress is rejected from on-air text |
| VOICEVOX/say reliability | retry, foreground priority, and deferred-radio handling live |
| continuous gameplay | live; game count advances across GAME OVER and improvements |
| improvement supervisor | live singleton under the system service |
| generic docich ownership | not cut over; remains isolated on `:98` by default |

The production service is the system unit `soren-runtime.service`. The runtime
directory `/home/ubuntu/soren` is a deployed loose-file tree, so every deployed
source change must also be committed and pushed in soviet_now. Never overwrite a
newer VM file from an older checkout.

## Production topology

```text
Xvfb :99 (1280x720)
  └─ Chrome / Unity WebGL + Playwright/CDP strategy
       ├─ game audio, BGM, SE, VOICEVOX -> soren_null
       └─ x11grab video
              └─ custom FFmpeg direct publisher
                   ├─ overlay composition
                   ├─ AAC <- soren_null.monitor
                   ├─ H.264 libx264
                   └─ CEA-608/A53 captions <- local Unix socket
                          └─ local RTMP relay / protected push target -> Twitch

soren-runtime.service
  └─ start_all.sh --supervisor
       ├─ soren_loop
       ├─ improve_daemon
       ├─ radio/audio/chat/prediction workers
       ├─ bridge / game process
       └─ direct stream process
```

The FFmpeg publisher does not expose the external stream key in repository
configuration. Root-managed relay/push configuration remains outside Git and
must not be printed into logs, issues, Wiki, or support output.

## Completed implementation series

| PR | Result |
|---|---|
| [soviet_now #95](https://github.com/azumag/soviet_now/pull/95) | Linux OBS portability and rollback groundwork |
| [soviet_now #97](https://github.com/azumag/soviet_now/pull/97) | FFmpeg direct backend, relay, probes, cutover/rollback tooling |
| [soviet_now #98](https://github.com/azumag/soviet_now/pull/98) | synchronized Japanese audio + English native Twitch captions; output and TTS guards |
| [soviet_now #99](https://github.com/azumag/soviet_now/pull/99) | latest GitHub/local/VM integration and production retry fixes |
| [soviet_now #100](https://github.com/azumag/soviet_now/pull/100) | improvement daemon added to supervisor and GNU/BSD status portability |
| [soviet_now #101](https://github.com/azumag/soviet_now/pull/101) | durable failed-improvement input snapshot and retry-lock recovery |
| [soviet_now #102](https://github.com/azumag/soviet_now/pull/102) | version-check every native-caption socket response |
| [soviet_now #103](https://github.com/azumag/soviet_now/pull/103) | exact caption-plan schema and hard 32-column x 2-line boundary |

The reusable caption implementation now also lives in docich. See
`docs/twitch_closed_captions.md` and `native/ffmpeg/README.md`.

## Audio and caption timing

| Source | Playback | Stream input |
|---|---|---|
| game | Chrome / PulseAudio | `soren_null.monitor` |
| BGM | persistent player | `soren_null.monitor` |
| SE | queued player | `soren_null.monitor` |
| TTS | VOICEVOX -> say/audio queue | `soren_null.monitor` |

Japanese audio is primary. English caption work runs as a best-effort auxiliary
path:

1. build and validate a private caption plan;
2. prepare a page before playback;
3. commit at playback start;
4. clear after playback;
5. ignore an old execution's late clear.

Translation or Unix-socket failure must not delay, cancel, or restart audio.
The custom filter embeds A/53 caption data through `libx264 -a53cc 1`.

## Improvement runtime

Production keeps the main game running during an improvement. The improvement
daemon is a first-class supervised worker and must remain a singleton child of
`soren-runtime.service`.

The configured production cycle currently waits for 100 accumulated games.
`failed_no_apply` is not success: the strategy hash did not change. After PR
#101, a valid input batch is snapshotted before the job. If the normal lock is
lost during failure harvest, only a batch for the current strategy hash is
restored; stale hashes and partial normal batches are rejected. Backoff then
controls the retry while gameplay continues.

Operational checks should use structured state only:

- `game_count.txt` and `game_state.json` for forward progress;
- `tmp/state/accumulated_games.json` for cycle count and strategy hash;
- `tmp/state/improve_state.json` for status, PID, phase, progress, and timestamp;
- presence/age of `tmp/improve.lock`, retry snapshot, and backoff files;
- daemon PID parent/cgroup for supervisor ownership.

Do not copy raw model output, improvement logs, prompts, keys, or generated
speech content into tickets or Wiki pages.

## Rollout and rollback contract

For any production source change:

1. fetch and integrate current soviet_now `main` in an isolated worktree;
2. compare each VM target with the known deployed baseline;
3. stop if the VM is newer or semantically different;
4. run focused tests and syntax checks locally;
5. merge the PR and update local `main`;
6. stage only the allowlisted files on the VM;
7. verify checksums and syntax before replacement;
8. retain an exact backup and automatic rollback trap;
9. restart only when required and explicitly authorized;
10. verify service, workers, game progress, audio, FFmpeg, A/V sync, and captions.

OBS remains the emergency broadcast rollback. A source-only hot reload should
not restart the stream when the relevant supervised shell reloads its modules.

## Relationship to docich multi-game foundation

The generic docich runtime is intentionally separate:

```text
Soren production  :99 + soren_null + live FFmpeg + full supervisor
docich default    :98 + docich_sink + stream.mode=null + per-game adapters
```

Moving live sorengame under docich requires a game-only entry point in
soviet_now. `start_all.sh` cannot be used as a browser adapter command because
it would duplicate display, audio, stream, worker, and supervisor ownership.
The full migration gate is recorded in `docs/games/sorengame.md`.

## Open administrative item

[soviet_now Issue #96](https://github.com/azumag/soviet_now/issues/96) remains
open even though direct streaming is operational. Its historical acceptance
artifacts and closure decision should be reconciled in soviet_now; this doc does
not silently close or redefine that issue.
