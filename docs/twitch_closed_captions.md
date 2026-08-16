# Twitch native closed captions

> Current as of 2026-08-16. This document is the cross-repository architecture
> record for [docich Issue #1](https://github.com/azumag/docich/issues/1).

## Outcome

docich now contains a reusable FFmpeg-native closed-caption path for synchronized
Japanese speech audio and English Twitch captions. The live Soren integration
has also been proven on the production stream:

- the broadcaster explicitly selected closed captions enabled in Twitch;
- the stream was restarted with the custom FFmpeg path active;
- English captions appeared, changed with speech chunks, and cleared;
- long radio translations stayed within the 32-column × 2-line transport bound;
- caption translation or IPC failure did not interrupt Japanese audio.

Twitch viewers may still need to enable captions in the player menu. Twitch's
operator guidance is linked from the
[Guide to Closed Captions](https://help.twitch.tv/s/article/guide-to-closed-captions?language=ja).

## Component boundary

```text
Japanese speech chunks
  ├─> TTS / VOICEVOX audio ---------------------------> PulseAudio -> FFmpeg
  └─> strict English caption planner
         └─> private plan.json (0600, atomic replace)
                └─> prepare -> commit -> clear
                       Unix socket acknowledgements
                              └─> FFmpeg docichcc filter
                                      └─> AV_FRAME_DATA_A53_CC
                                              └─> libx264 -a53cc 1
                                                      └─> H.264 SEI -> Twitch
```

Audio is the primary path. Caption planning and socket control are auxiliary and
must never gate playback. The timing contract is:

- prepare before audio starts;
- commit at the audio start boundary;
- clear after playback finishes;
- ignore a stale clear whose `executionId` no longer owns the displayed page.

## Repository responsibilities

| Area | docich | soviet_now / Soren production |
|---|---|---|
| FFmpeg filter and pinned build | canonical reusable source | compatibility copy currently deployed |
| Caption schema and Unix client | `src/docich/captions.py` | integrated with radio and speech queues |
| Generic stream opt-in/fail-open | `src/docich/stream.py` | direct-stream runtime and custom binary |
| Japanese TTS timing | integration contract only | VOICEVOX/say queue implementation |
| Output/thinking guard | strict caption parser | all on-air radio/comment speech paths |
| Production process ownership | none by default | `soren-runtime.service` on display `:99` |

Until Soren consumes a versioned docich build artifact, any native-filter or
protocol change must be ported to both repositories in the same change window.

## Configuration

The safe default is disabled:

```toml
[stream]
ffmpeg_bin = "ffmpeg"

[captions]
enabled = false
socket_path = ""
```

Runtime overrides:

| Variable | Meaning |
|---|---|
| `DOCICH_FFMPEG_BIN` | custom FFmpeg containing `docichcc` |
| `DOCICH_CC_ENABLED` | `1/true/on` enables the request |
| `DOCICH_CC_SOCKET` | absolute Unix socket path |
| `DOCICH_CC_TRANSLATION_URL` | loopback-only OpenAI-compatible endpoint |
| `DOCICH_CC_TRANSLATION_MODELS` | ordered translation model names |
| `DOCICH_CC_TRANSLATION_TIMEOUT_SEC` | per-request timeout, 0.1–120 seconds |
| `DOCICH_CC_TRANSLATION_ATTEMPTS` | malformed-output retries, 1–5 |

With no explicit socket path, docich uses
`$XDG_RUNTIME_DIR/docich/ffmpeg-cc.sock`, falling back to
`/run/user/<uid>/docich/ffmpeg-cc.sock`.
At stream launch it creates the immediate parent as `0700` and rejects a
symlink, foreign owner, or group/other access. This validation also fails open
to the captionless A/V command.

`bin/docich doctor` checks the binary and required FFmpeg features.
`bin/docich status` distinguishes requested from active captions and prints the
actual fail-open reason while masking the RTMP key.

## Caption planning

Create one UTF-8 Japanese chunk per line and either provide an aligned JSON
string array for deterministic tests or use the loopback translation runtime:

```bash
bin/docich caption plan \
  --chunks-file /run/user/1001/docich/speech.txt \
  --translations-file /run/user/1001/docich/translations.json \
  --execution-id speech-123 \
  --output /run/user/1001/docich/speech-123.plan.json
```

The planner does not impose a fixed chunk-count cap. Each normalized translation
must fit the declared page shape. Production asks for roughly 32 English
characters and enforces the absolute 64-character default page limit. The
FFmpeg protocol still exposes 32 page slots (`0`–`31`); an unbounded speech
sequence is mapped to a reusable slot with `sequence % 32` after the previous
chunk has committed.

For the built-in local MiniMax-compatible route, the request sets deterministic
JSON mode and disables reasoning for the simple translation task. Regardless of
provider behavior, the parser accepts only:

```json
{"translations":["Caption one.","Caption two."]}
```

Surrounding analysis, Web-search/tool progress, markdown, duplicate keys,
non-strings, non-ASCII residue, and over-length text are rejected. A non-empty
translation array may contain only the available ordered prefix; the matching
speech prefix is captioned and the untranslated tail continues audio-only.
Extra translations are truncated to the speech chunk count, while an empty
array remains an error. There is no substring extraction fallback. This is the
trust boundary that prevents thinking or tool traces from becoming captions.

## FFmpeg socket operations

```bash
bin/docich caption send prepare --plan /path/to/plan.json --chunk 0 --page 0
bin/docich caption send commit  --plan /path/to/plan.json --chunk 0 --page 0
bin/docich caption send clear   --plan /path/to/plan.json
bin/docich caption send reset
```

For speech sequences beyond the first 32 chunks, pass the unbounded sequence
identity separately; docich maps it onto the reusable protocol slot:

```bash
bin/docich caption send prepare --plan /path/to/plan.json --chunk 32 --page 0 --sequence 32
bin/docich caption send commit  --plan /path/to/plan.json --chunk 32 --page 0 --sequence 32
```

Every operation waits for a matching acknowledgement. A request is capped at
4 KiB; the response must be valid versioned JSON. A rejected or unavailable
socket yields a caption error for the caller to record, not a stream restart.

## FFmpeg command and fail-open behavior

When capability checks pass, docich adds:

```text
-vf docichcc=socket=/run/user/<uid>/docich/ffmpeg-cc.sock
-c:v libx264 -a53cc 1
```

If the configured custom binary is absent, lacks `docichcc`, or lacks the
`libx264 a53cc` option, `resolve_runtime()` rebuilds the normal audio/video
command without the filter. The stream and TTS remain available. This is a
fail-open caption policy, not a silent claim that captions are active;
`doctor`/`status` expose the reason.

## Verification matrix

| Gate | Evidence |
|---|---|
| Planner schema and bounds | unit tests for malformed/extra/thinking output, partial prefixes, long chunk lists, ASCII normalization, page limits |
| IPC ownership | stale socket replacement and live-socket collision proof |
| Ordering | prepare/commit/clear acknowledgements, reusable sequence slots, and stale-clear rejection |
| Transport | A/53 SEI inspection plus libcaption decode from generated MPEG-TS |
| Load | 20-cycle stress proof with latency bound and first/last decode |
| Generic runtime | opt-in, capability detection, fail-open, secret redaction tests |
| Live Soren path | production captions displayed/cycled/cleared while Japanese audio continued |

Build and proof commands are in `native/ffmpeg/README.md`.
