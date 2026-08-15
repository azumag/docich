# Native Twitch closed captions for FFmpeg

This directory contains the reusable native-caption transport used by docich.
It adds a `docichcc` video filter to a pinned FFmpeg build. The filter accepts
caption control messages over a local Unix socket, attaches CEA-608 data to
video frames, and lets `libx264 -a53cc 1` carry that data in H.264 SEI for
Twitch native closed captions.

The public controller is `bin/docich caption`; callers do not need to speak the
socket protocol directly.

## Source and production ownership

- `azumag/docich` is the canonical reusable source for the filter, build recipe,
  controller, tests, and architecture documentation.
- The current live Soren broadcast still runs the compatibility copy merged in
  `azumag/soviet_now` PR #98. That copy and this directory must remain
  semantically synchronized until Soren consumes a versioned docich artifact.
- The generic docich runtime does not take control of the live Soren display,
  audio bus, or stream. See `docs/games/sorengame.md`.

## Pinned dependencies

`build.sh` verifies the exact source commits before building:

| Dependency | Tag | Commit |
|---|---|---|
| FFmpeg | `n6.1.1` | `e38092ef9395d7049f871ef4d5411eb410e283e0` |
| libcaption | `v0.8` | `e8b6261090eb3f2012427cc6b151c923f82453db` |

The filter links against libcaption. License text is installed from
`THIRD_PARTY_NOTICES.md` with the custom FFmpeg build.

## Build

The build host needs a C/C++ toolchain, CMake, pkg-config, Git, GNU make, and
libx264 development files. The default build root is disposable and outside the
repository:

```bash
native/ffmpeg/build.sh /tmp/docich-cc-build
```

The last output line is the custom FFmpeg path, normally:

```text
/tmp/docich-cc-build/ffmpeg-install/bin/ffmpeg
```

The build fails unless it can verify `docichcc`, `x11grab`, PulseAudio input,
`libx264`, the `a53cc` encoder option, RTMP support, and the local `ts2srt`
verification tool.

## Configure docich

Captions are opt-in. Point docich at the custom binary and enable CC:

```bash
export DOCICH_FFMPEG_BIN=/tmp/docich-cc-build/ffmpeg-install/bin/ffmpeg
export DOCICH_CC_ENABLED=1
export DOCICH_CC_SOCKET="$XDG_RUNTIME_DIR/docich/ffmpeg-cc.sock"
bin/docich doctor
bin/docich status
```

Equivalent TOML keys are `stream.ffmpeg_bin`, `captions.enabled`, and
`captions.socket_path`. If the configured binary is missing or lacks either
`docichcc` or `libx264 a53cc`, docich deliberately starts the normal audio/video
command without captions. A caption failure must not stop or restart the audio
path.

## Local proofs

The fixed proof covers a stale socket, refusal to replace a live socket,
prepare/commit/clear acknowledgements, rejection of a stale execution clear,
H.264 A/53 SEI, and decoded caption text:

```bash
DOCICH_CC_FFMPEG_BIN=/tmp/docich-cc-build/ffmpeg-install/bin/ffmpeg \
  native/ffmpeg/poc.sh /tmp/docich-cc-poc.ts
```

The stress proof sends 20 caption cycles and checks latency plus first/last
decoded captions:

```bash
DOCICH_CC_FFMPEG_BIN=/tmp/docich-cc-build/ffmpeg-install/bin/ffmpeg \
  native/ffmpeg/stress_poc.sh /tmp/docich-cc-stress.ts
```

## Control sequence

For each spoken chunk the caller uses a stable execution ID:

1. `caption plan` validates aligned Japanese chunks and English translations.
2. `caption send prepare` stages a page but does not display it.
3. `caption send commit` displays that exact page when audio playback starts.
4. `caption send clear` removes it when playback ends.

`executionId` prevents an older completion from clearing a newer caption. Text
is normalized to conservative ASCII and bounded to the configured CEA-608 page
shape (production default: 32 columns × 2 lines × 1 page).

## Security and failure boundaries

- The socket path must be absolute, under the Unix path length limit, and use a
  restricted character set.
- Before stream launch, docich creates the immediate socket directory as `0700`
  and verifies it is a real directory owned by the runtime user. An unsafe
  directory disables captions for that launch while audio/video continue.
- The filter removes an owned stale socket but refuses to unlink a socket that
  has a live owner.
- Messages are newline-delimited, versioned JSON capped at 4 KiB. Caption text
  is base64-encoded ASCII inside the control message.
- Translation accepts only an exact JSON schema. Reasoning, tool traces,
  markdown fences, prose, or extra keys are rejected and never extracted.
- Caption planning and socket errors are expected fail-open errors for the
  broadcast caller: audio/video continue without that caption.
