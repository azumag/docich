# sorengame integration contract

> Current as of 2026-08-15. This page separates the live Soren broadcast from
> docich's generic multi-game viewer so that the two runtimes cannot accidentally
> control the same display, audio bus, browser, or stream.

## Current production owner

The live sorengame broadcast is owned by
[`azumag/soviet_now`](https://github.com/azumag/soviet_now), not by the docich
CLI. On the production VM it runs as the system service
`soren-runtime.service` from `/home/ubuntu/soren` and owns:

- Xvfb display `:99`;
- the `soren_null` PulseAudio bus;
- Chrome/Unity gameplay and Playwright/CDP input;
- strategy, improvement, radio, comment, and audio workers;
- the FFmpeg direct stream and native Twitch captions.

Stopping, switching, or starting a docich game must not alter any of those
resources. Production changes remain governed by the soviet_now repository and
its VM synchronization rule.

## What the docich entry does today

`config/games/sorengame.toml` is a local viewer definition for the generic
`browser` adapter:

```toml
[browser]
url = "http://127.0.0.1:8080"
kiosk = true
binary = "auto"

[agent]
enabled = false
```

It opens the local WebGL server on docich's isolated display `:98`. It is useful
for adapter development, screenshots, and a future migration rehearsal. It is
not a second production controller, and it does not start `soviet_now`.

```bash
bin/docich up
bin/docich start sorengame
```

The local URL must already be served. If nothing is listening on port 8080, the
browser page will fail normally; docich does not infer or launch a Soren server.

## Why `start_all.sh` is not a launch command

Do not configure the browser adapter with a command such as:

```toml
launch_command = ["bash", "-lc", "cd /home/ubuntu/soren && ./start_all.sh"]
```

`start_all.sh` owns the whole Soren production runtime, including display,
audio, stream, workers, and supervisors. Calling it from a docich game window
would create duplicate ownership and defeat `docich switch` semantics.

The integration remains viewer-only until soviet_now exposes an explicit
**game-only** entry point with this contract:

1. receives `DISPLAY`, `PULSE_SERVER`, and `PULSE_SINK` from docich;
2. starts only the WebGL server/browser/game bridge needed for play;
3. does not start Xvfb, PulseAudio, FFmpeg/OBS, radio/audio workers, or another
   supervisor;
4. exits cleanly on SIGTERM without touching unrelated processes;
5. stores its game-only state under a caller-selected runtime directory.

Only after that contract has its own tests may docich set `launch_command` and
become the owner of display/audio/stream for a migrated sorengame instance.

## Caption mapping

The current production caption implementation entered soviet_now in PR #98.
docich now hosts the canonical reusable implementation:

| Soren production concept | docich configuration/API |
|---|---|
| custom FFmpeg binary | `stream.ffmpeg_bin` / `DOCICH_FFMPEG_BIN` |
| caption opt-in | `captions.enabled` / `DOCICH_CC_ENABLED` |
| Unix socket | `captions.socket_path` / `DOCICH_CC_SOCKET` |
| bilingual plan | `bin/docich caption plan` |
| audio-bound lifecycle | `caption send prepare/commit/clear` |
| caption failure behavior | fail open to unchanged audio/video |

See `docs/twitch_closed_captions.md` and `native/ffmpeg/README.md`.

## Migration gate

Moving production ownership from soviet_now to docich is a separate release,
not a side effect of merging this foundation. It requires:

- a game-only Soren entry point;
- an isolated rehearsal on `:98` and `docich_sink`;
- proof that `docich switch` leaves FFmpeg connected;
- game, BGM, SE, TTS, caption, and A/V-sync acceptance;
- explicit production cutover and rollback approval;
- updated systemd ownership with no duplicate supervisor.

Until every gate passes, the safe topology is:

```text
soviet_now production :99 / soren_null / live FFmpeg
docich rehearsal      :98 / docich_sink / stream.mode=null
```
