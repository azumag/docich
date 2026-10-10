# Soren91 SDL software-render comparison

## Scope and evidence boundary

PR #2006 introduced `presentation.py --sdl-software-render` and enabled it for
FlyHome. Soren91 uses an SRT ffplay viewer and a second ffplay for contained
projection, so it is the next candidate. This change adds a **default-off**
`[soren91].sdl_software_render` switch. No shipped game configuration is enabled
by this change. Existing games, current processes, renderer-host selection,
remote capture, input, audio, SRT options and the shared encoder are unchanged.

The current Soren91 projection inherits the presenter's **15 fps** default;
FlyHome explicitly uses 30 fps. Do not copy FlyHome's CPU figures or 0.8–1-core
estimate into Soren91 acceptance. Passing tests proves configuration/command
wiring, not performance or A/V quality.

## Controlled opt-in

For an isolated, non-production rehearsal, add the following key to the
**existing** `[soren91]` table of that rehearsal's configuration (do not replace
the table or create a duplicate):

```toml
sdl_software_render = true
```

Only TOML booleans are accepted; strings, numbers and null-like values fail
validation. Enabling the switch requires a positive display viewport: the
uncontained direct-ffplay path is rejected rather than silently ignoring the
request. Omission or `false` preserves the previous command.

The adapter sends `--sdl-software-render` to the presenter, **before** `--`, not
to ffplay. The existing presenter sets both `SDL_RENDER_DRIVER=software` and
`SDL_FRAMEBUFFER_ACCELERATION=0` on the viewer and projection children. It does
not modify the parent environment or the Windows/Mac renderer's configuration.

SDL2 documents the renderer selection and screen-surface acceleration hints:
- https://wiki.libsdl.org/SDL2/SDL_HINT_RENDER_DRIVER
- https://wiki.libsdl.org/SDL2/SDL_HINT_FRAMEBUFFER_ACCELERATION

## Measurement gate before production enablement

Use a non-production display/audio sink and synthetic SRT input first. Keep
input codec, frame content, resolution, FPS, audio, window geometry, ffplay/SDL
versions and CPU allocation fixed between baseline and candidate. In particular,
retain Soren91's `-fflags nobuffer -flags low_delay` and 15 fps projection.
Check all four image edges, black padding, motion, colour and A/V synchronisation.
A successful process launch or window appearance alone is not acceptance.

For an authorised production observation, follow the existing read-only
`diagnostics` gateway and `runtime-diagnostics.md`. Do not add arbitrary exec,
print full command lines/environment, or restart a game to obtain a baseline.
Missing collector coverage is a separate reviewed diagnostics change, not
permission to bypass the gateway. Keep detailed operational evidence private.

Compare at least five minutes per condition after warm-up, with 10-second
samples, using the same renderer host and comparable input/workload. Record:

| Evidence | Required interpretation |
| --- | --- |
| Commit, game/runtime identity, PID and start time | Attribute samples to the same run; discard PID reuse or game switches. |
| Viewer and projection CPU separately | `100 * delta(utime + stime) / (CLK_TCK * elapsed_seconds)`; 100% means one core. Read `CLK_TCK`, do not assume 100. |
| llvmpipe thread CPU and LLVM presence | Read-only evidence of the rendering path; unreadable is unknown, not absent. Presence alone does not prove CPU cost. |
| CPU PSI `some avg10` median/max and load | Compare host contention independently of process CPU. Missing PSI is unknown, not zero. |
| Encoder/receiver/Chromium CPU and workload | Explain competing load; do not attribute unrelated changes to SDL. |
| Frame drops, motion, edges, colour and audio continuity/sync | Required alongside CPU reduction; preserve the shared encoder PID. |

Require a repeatable CPU reduction and no visual/audio regression before a
separate reviewed production config enables the key. Neither the Docker result
from FlyHome nor green CI substitutes for Soren91's evidence. Do not claim an
improvement in production until the changed processes have actually run.

## Deployment, rollback and remaining corners

Use the normal branch → PR → review/tests/CI → main → Actions → owner-only
gateway path. Never edit tracked production files or restart the broadcast
service to apply the flag. A deployed setting applies at the **next normal
Soren91 start**; existing ffplay processes remain unchanged. Roll back by setting
`false` (or removing the key) through the same reviewed flow, then use the next
normal start. Active A/V incidents use the existing authorised recovery procedure.

After Soren91, measure the projection ffplay in Hanjuku, NetHack, weather,
tsuitate and CLI corners individually. Do not blindly pass the current flag to
RetroArch: it also changes the **source process environment**, not just the
projection. A projection-only option may be appropriate for those adapters.

Main sorengame uses Chromium/Unity WebGL and direct FFmpeg capture, not this
presenter. Profile its renderer, encoder and background workers separately.
Do not disable WebGL or change gameplay timing, internal resolution or existing
render limits as part of this rollout. No sorengame optimisation or production
measurement is claimed by this change.

## Regression checks

```sh
python -m pytest -q tests/test_soren91_software_render.py \
  tests/test_presentation_software_render.py tests/test_soren91_adapter.py
```

The new tests cover strict opt-in validation, the uncontained-path boundary,
real presenter argument parsing, unchanged transport/audio/geometry/FPS and
absence of global environment or remote-bot changes. Existing presentation
subprocess tests cover both SDL variables reaching both local children.
