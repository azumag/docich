# TwiCa common foreground

Issue #1236. This is the complete opt-in integration, not only a renderer library.

## Ownership and video path

The native `soviet_now/direct_stream.sh` selects `docich.twica_stream` only when
`DOCICH_TWICA_COMMON_ENABLED=1`. Otherwise the existing runner is unchanged.
The adapter imports the native config, command builder, caption and reconnect
helpers and retains the same stream lock, audio source, destination relay, and
control stdin. It adds one inherited RGBA FD to FFmpeg and composites after X11
capture, before captions and encoding. Each game/presenter remains unchanged.

The renderer has the full output viewport. Composition shifts only X by one
third of the output width, placing its former centre at width*5/6. Pixel rounding
is at most half a pixel. Y, scale and the original TwiCa animation are preserved.
White pixels are not keyed out; native straight alpha is preserved. An expired,
missing, invalid or failed snapshot yields transparent pixels after one second.
The feeder never waits for a renderer frame and does not own FFmpeg's stdin.

The stream owns one renderer supervisor across games and encoder reconnects.
The renderer owns one persistent Chromium page and its existing TwiCa queue and
sound. Its environment has an explicit PulseAudio sink. No per-game renderer is
created. Only bounded `twica-` session checkpoints are retained in its private
profile; protocol-level delivery semantics still belong to TwiCa. This does not
claim exactly-once delivery through every browser crash or durable migration of
an in-flight React queue from the old browser.

## Guarded transition and rollback

The persistent private ownership state has `legacy`, `draining`, and `common`
phases. A generation identifies every transition. Updated native/shared page
hosts register PID/birth/boot identities and acknowledge only fixed metadata.
When common ownership is requested, old frames are navigated to `about:blank`
and removed, not CSS-hidden. This stops their subscriptions and effect sound.

Activation requires a healthy feed that has sent frames, a preflighted standby
renderer, upgraded legacy proxy endpoints, and zero-frame retirement ACKs from
all live consumers. An old proxy cannot be treated as an absent consumer.
Rollback first drains and closes the common browser; legacy is resumed only
following an exact-generation standby ACK. Ambiguous state remains blocked;
it is not repaired by starting a second consumer. Renderer failure affects only
TwiCa, not the encoder, Xvfb, game or common rails.

## Deployment and first activation

Normal code deployment does not install renderer dependencies, set flags or
restart the stream. Merge the paired Soren PR first, update the parent's gitlink
to the merged SHA, run the combined CI, then deploy via the regular parent VM
control plane. Do not use a pending branch SHA as the final production pin.

The `TwiCa common overlay operation` workflow has only four fixed operations:

- `prepare`: install the isolated `.venv-twica` dependencies/browser and set the
  opt-in flag, preserving other existing `.env` content with a private backup.
  It does not install OS packages or restart any running service.
- `status`: report only fixed active/legacy/degraded/blocked categories.
- `activate`: request common ownership after the runtime readiness/retirement gates.
- `rollback`: close the common renderer and return ownership to updated legacy hosts.

Writes require the owner, protected main, the existing VM environment and the
explicit `production` confirmation. The workflow uses the existing gateway's
withheld-output execution lane with one reviewed helper, never arbitrary user
commands or URLs. The gateway's access policy is unchanged. The separate manifest
records the subordinate renderer and its fixed diagnostics; no new independent
worker is registered in the game supervisor.

**First introduction requires a planned encoder replacement**, because an already
running FFmpeg cannot acquire a new input/filter graph. After prepare, both old
Node display hosts also need to load the updated guard before activation. Their
readiness route must confirm this; page reload with a captured old config does
not prove a code reload. Schedule the one-time change for an idle TwiCa interval
and preserve unrelated processes. This PR does not issue that restart. Thereafter
normal game changes, renderer recovery and ownership rollback do not restart the
encoder. To remove the adapter itself, another planned input-graph change is needed.

Rollback restores the previous architecture, including its known non-Soren
visibility limitation. It is a recovery option, not acceptance of the new feature.
The runtime `owner.json` is not deleted as a recovery shortcut. Keep the flag
until rollback has confirmed no common browser remains.

## Configuration and diagnostics

Defaults: `DOCICH_TWICA_FPS=15`, frame TTL=1000ms,
`DOCICH_TWICA_STATE_DIR=/home/ubuntu/soren/tmp/state/twica-common`,
`DOCICH_TWICA_FRAME_DIR=$XDG_RUNTIME_DIR/docich-twica-common`,
`DOCICH_TWICA_PYTHON=/home/ubuntu/docich/.venv-twica/bin/python`.
The URL is inherited from `SOREN_DIRECT_TWICA_OVERLAY_URL`, never placed in argv
or health output. Existing explicit TwiCa disable settings are respected.
Use RAM-backed frame storage to avoid full-frame disk writes. A 1280x720 RGBA
snapshot is about 3.7MB; measure CPU, capture latency and memory on the target VM
before making claims about live performance. No performance improvement is implied.

`python3 -m docich.twica_control status` gives fixed owner, freshness, consumer
count and frame-state fields. Native `direct_stream status` retains its normal
schema and adds a `twica_common` summary. Logs must not contain the configured URL,
event contents, authentication headers, environment values or browser exceptions.

## Verification

The dedicated CI checks out the exact paired Soren gitlink and runs Python unit,
real Chromium alpha/animation, ownership transitions, persistent checkpoint,
FFmpeg output pixels, caption/audio command preservation, and isolated X11 +
PulseAudio + native `direct_stream.sh record` integration. That integration changes
a native foreground window, verifies a card over the window, restarts only the
owned renderer and checks the encoder PID and audio/video streams are maintained.
Fixtures contain no production data. The audio fixture is silent, so production
TwiCa effect-sound correctness is not claimed by that test.

Native CI runs guard and existing direct/shared/layout/proxy regression tests.
Production acceptance remains separate: use real cards, long names, effects and
multi-card draws across game changes, check audio once per event and compare CPU,
memory, delay, frame continuity and actual final output. Code tests, merge, deploy,
activation and live visual acceptance must be reported separately.
