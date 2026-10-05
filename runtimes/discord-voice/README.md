# Discord voice: offline turn coordinator (#1628)

This is the synthetic, offline foundation for [Issue #1628](https://github.com/azumag/docich/issues/1628). It is not a working Discord voice Bot or a deployable service. Importing the module does nothing. There are no credentials, built-in network clients, file writers, startup command, new permissions, provider dependencies, Docker image or deployment hooks. The separate injected VOICEVOX boundary below is tested offline. Only adapters with `kind: 'fake'` are accepted in this slice.

The current text Bot remains in `workers/discord-chat`: Workers AI, canonical persona and SQLite Durable Object memory, with public HTTP limited to `/healthz`. This slice does not import or change that Bot, its intents, model, prompt, memory or endpoint. The conversation adapter is a future integration boundary; the tests use fixed synthetic replies and transient scoped history, not the real persona/LLM or a shared long-term memory database.

## Run the offline contracts

Node.js 22.18+; no installation required:

```sh
node --test runtimes/discord-voice/test/*.test.mjs
node --test workers/discord-chat/test/*.test.js
```

The tests generate signed 16-bit mono PCM at 48 kHz, 960 samples / 20 ms per frame, and use fixed transcripts. They cover silence, short noise, length/queue/capture bounds, duplicate frames and turns, exclusion of bots/unactivated users/other channels, scope separation, cancellation at every provider/playback stage, barge-in, expired activation, late results and redacted errors. No audio collection, recording, external STT/LLM/TTS or VC participation occurs.

## Explicit activation, default off

`VoiceRuntime.connect({guildId, channelId})` opens only a fake session and returns an epoch. It does not admit audio. To admit **one** utterance, the trusted caller must use:

```js
runtime.activate({
  session, guildId, channelId, userId, turnId,
  receive: true,
  respond: true,
});
```

Both flags default to false. `receive: true` alone can exercise synthetic capture but does not call STT, the conversation core, TTS or playback. Ending/discarding an utterance consumes activation. Silence does not collect PCM before speech; an idle/capturing activation expires automatically after 15 seconds. Calling `revoke(scope)` erases that user's capture and queued audio and cancels their active work. `disconnect()` revokes all activations, drops the queue, aborts work and stops playback. Reopening requires fresh activation and a new epoch; frames/activations from an old epoch are rejected.

No wake word, always-listening mode, slash command, UI or automatic activation policy has been selected or implemented. A future authenticated control adapter must implement the owner's chosen trigger and prove authorization and participant consent before receiving audio. An arbitrary transcript or audio packet must never grant activation. Inbound frames must carry the authenticated speaker's scope, `isBot: false`, the current epoch and a monotonically increasing sequence. The Bot's own user ID is excluded even if that flag is false; missing classification and other bots are rejected. Only the selected guild/channel is admitted.

## State and limits

```text
inactive -> explicitly armed -> capturing -> queued -> STT -> conversation -> TTS -> playback
                |                 |            |          |          |         |
                +-----------------+------------+----------+----------+---------+
                                revoke / disconnect / expiry / failure -> discard
```

Speech detection here is a deterministic RMS threshold (500 in signed 16-bit units), not a production VAD validated against real microphones/noise. At least 100 ms of voiced samples and 400 ms of trailing silence end a turn. PCM frames are validated before copying. An utterance over 10 seconds is discarded instead of being truncated into a response. The caller may reduce/adjust limits only within the hard caps checked by the constructor.

| Resource | Default / maximum |
| --- | --- |
| Armed/capturing speakers | 4 / 4 |
| Pending turns, including active turn | 4 / 4 |
| PCM per captured/queued turn | 10 seconds, 960,000 bytes |
| Activation lifetime (includes capture) | 15 seconds / 30 seconds |
| Recent activation dedupe entries | 256 / 256 |
| Transcript / response | 2000 / 900 characters |
| TTS PCM | 30 seconds, 2,880,000 bytes |
| Each adapter stage, including playback | 5 seconds / 10 seconds |

One turn is processed at a time. Queue saturation discards the completed new utterance without invoking providers. Capture capacity is bounded independently. Dedupe is transient: `(guild, channel, user, turnId)` is remembered for the bounded recent window; sequences reject reordered/repeated frames inside a capture. It is not durable exactly-once delivery, and replay protection across process restarts is not claimed. The trusted caller must issue fresh opaque `turnId`s; IDs contain no transcript or credentials.

While playback is active, speech from an **already explicitly activated** human immediately aborts playback and calls transport `stop()`. An unactivated speaker cannot interrupt. The new utterance then follows the normal bounded queue. This tests the interrupt mechanism, not a final UX or natural simultaneous-speaker handling. New speech during STT/LLM/TTS queues normally; no additional unselected auto-listening behavior is introduced.

## Adapter interface and cancellation

`interfaces.d.ts` specifies the boundary types; `test/fakes.mjs` supplies only synthetic test implementations. The methods are:

- Transport: `connect(channel, {signal})`, `play(pcm, {format, signal})`, synchronous idempotent `stop()` / `disconnect()`.
- STT: `transcribe(pcm, {format, scope, signal})` returns a transcript.
- Conversation: `reply({scope, turnId, transcript}, {signal})` returns text.
- TTS: `synthesize(reply, {format, signal})` returns owned `Int16Array` PCM.

Every asynchronous operation must honor `AbortSignal`. Transport `stop()` must synchronously stop emitting playback; `disconnect()` must stop admission. The coordinator races deadlines/cancellation, observes failures without printing adapter exceptions, and ignores late results. A late returned accessible TTS buffer is also zeroed using the typed-array intrinsic. Detached/invalid views and overridden cleanup methods cannot poison the turn queue. A transferred buffer destination is no longer accessible through the detached handle; its adapter owner must erase that destination. The runtime does not claim to erase inaccessible transferred memory. This protects subsequent stages from stale output; it cannot force an external provider to stop billing or prove that an uncooperative adapter has released its own resources. A future live adapter therefore requires independent cancellation/cleanup and DAVE/transport acceptance tests before removing the fake-only fence.

The runtime owns copies of inbound PCM and clears them on completion, rejection, revocation, expiry or disconnect; it does not modify the caller's input frame. STT must not retain raw data after completion/abort. TTS transfers buffer ownership to the runtime and must not reuse that buffer. Tests retain references only to verify clearing. There is no raw-audio persistence or runtime transcript/history store. Temporary transcripts/replies exist only in the pipeline; JavaScript strings cannot be reliably zeroed. Fake conversation history is test-only RAM. Sharing text and voice memory, transcript retention and deletion semantics remain explicit future decisions; the scope boundary is always the full `(guild, channel, user)` tuple.

`status()` reports only mode, fake session state, phase and bounded counts. `connected: true` means **fake session open**, not Discord connected. Event logging passes only a fixed `{event}` record, never IDs, audio, transcript, prompt, tokens or exception text. There is no built-in console logger. Events include session open/close, utterance start/finish/drop, queue full, STT/LLM/TTS/playback stages and cancellation/interruption. Logging failure cannot alter runtime behavior. No endpoint or production health/diagnostics registry is added.

## Future live boundary

Discord voice uses a separate UDP connection for receiving/transmitting voice data and requires DAVE E2EE support for voice calls starting March 1, 2026. [Discord voice connection documentation](https://docs.discord.com/developers/topics/voice-connections). Workers `node:dgram` is an importable non-functional stub, so importing a UDP package does not make a Workers voice transport operational. [Cloudflare Node.js compatibility](https://developers.cloudflare.com/workers/runtime-apis/nodejs/#non-functional-stub-modules).

A later separately approved long-lived runtime can implement Voice Gateway/UDP, authenticated speaker attribution, DAVE, Opus decode/encode and transport cleanup at the transport boundary. STT/TTS choices, fees, host, privileges, activation UX, memory sharing and a safe authenticated conversation-core integration must be selected before any live integration. The existing public `/healthz` is not a conversation API. This PR does not resurrect the old Python/Docker Bot for production, install voice libraries/FFmpeg, add Gateway intents, deploy a host or alter the current text Worker.

Issue #1628's VC join, real Japanese STT, canonical-persona response, Discord TTS, reconnect and 30-minute live acceptance criteria remain open. Passing these offline contracts demonstrates the coordinator slice only.


## Separate injected VOICEVOX boundary (offline tested)

`voicevox.mjs` reuses the HTTP protocol of canonical `src/docich/speech.py:195,646` and the external endpoint choice in Soren `say_enqueue.sh` / `voicevox_tts.sh`. The existing path is two synchronous HTTP responses, not an asynchronous callback service: POST `/audio_query?text=...&speaker=...`, then POST `/synthesis?speaker=...` with the query JSON to receive WAV. [Official engine API](https://voicevox.github.io/voicevox_engine/api/). No callback schema, new service, engine, library, retry queue or VM-local synthesis is introduced.

`new InjectedVoicevoxTTS({env, selectedURL, request})` requires an explicit settings snapshot and an injected transport. It does not read `process.env`, `.env`, secret files or shared state and provides no default `fetch` or executable entry point. `kind: 'voicevox-injected'` keeps it outside the coordinator's fake-only fence. The conversation core, Discord transport and real TTS remain unconnected.

Configuration mirrors the existing public names: `VOICEVOX_URLS` is a comma/whitespace ordered chain; `selectedURL` is an optional endpoint chosen by the trusted existing chooser and must belong to that chain. Otherwise the first external endpoint is selected. Explicit loopback/unspecified entries are excluded, including local fallback in a shared chain; an empty external chain fails closed. Legacy implicit localhost defaults are intentionally not inherited. Credentials in URLs, paths, query strings and fragments are rejected. These syntax checks do not prove DNS resolution, Windows/Mac ownership or deployment authorization; a future real transport must restrict trusted configured destinations, redirects and actual network access. This adapter never probes `/version` or changes shared endpoint locks/backoff/readiness.

`VOICEVOX_SPEAKER` defaults to canonical public style 3; the trusted caller must supply the selected persona style (no persona selection here). `VOICEVOX_TIMEOUT` defaults to 30 seconds, with a stricter **total** budget across both requests (0.001–30 seconds), and parent cancellation can shorten it. `VOICEVOX_MAX_CHARS` defaults to canonical 200, bounded to 900: longer text fails rather than invoking the canonical chunk/file/pronunciation pipeline. `VOICEVOX_PITCH` is added to the returned pitch, while `VOICEVOX_TEMPO` and `VOICEVOX_INTONATION` set speed/intonation, matching canonical semantics within bounded values. Query output is requested as 48kHz mono. No alternate endpoint or retry follows a request failure; retry ownership and shared queue coordination must be selected before live integration, especially after ambiguous synthesis completion.

The injected `request({url, method, headers, body, signal, maxBytes})` resolves `{status, body: Uint8Array}`. `maxBytes` is 64KiB for query and 5,760,256 bytes for WAV; the transport must enforce it while receiving, before allocation, and honor cancellation. The canonical client uses only `Content-Type: application/json`; this adapter adds no Authorization or new credential scheme. This is not proof that deployed network endpoints require no authentication. Any existing authenticated transport must remain privately configured at the transport boundary; the actual deployed authentication/access contract has not been inspected or exercised.

`synthesize(reply, {format: PCM, scope, signal})` requires valid full guild/channel/user scope. Scope is local validation context, not an authorization grant, HTTP field or provider metadata. Per-call closures and AbortControllers isolate results/cancellation; there is no cross-scope cache/history. Transport resolution belongs to that call, with no synthetic callback correlation field. HTTP bodies transfer ownership to the adapter, are cleared by the typed-array intrinsic after consumption/rejection (including late results), and must never be reused by the transport. A detached handle cannot clear its transferred destination: its owner remains responsible. Providers that ignore cancellation can continue work until they finish; the adapter bounds its own wait and suppresses/clears their late results, but cannot prove external release or billing cancellation.

WAV conversion accepts bounded little-endian RIFF/WAVE with one 16-byte PCM `fmt ` and one `data` chunk, signed PCM16, mono/stereo, 24kHz or 48kHz, at most 30 seconds. Chunk padding, RIFF length, byte rate, alignment, duplicates and data sizes are checked; unsupported formats fail closed. Stereo is averaged, 24kHz is linearly interpolated to 48kHz (last sample held), and trailing zero padding reaches a 960-sample/20ms frame. Output is a separately owned `Int16Array`. This deterministic narrow converter is not a general codec/resampler or a real audio-quality acceptance result.

No logger is provided. Public exceptions have fixed codes and no provider cause, reply, scope, endpoint, credential or exception message. Text/query strings in transient JS/transport memory cannot be reliably erased; the trusted transport must not log URLs (query contains text), body, headers, raw exceptions or provider bytes. Tests use generated WAV/JSON, injected transport only, and cover type/size/format limits, conversion samples, cancellation, total deadline, late bytes, concurrency and fixed private-error handling. No actual endpoint, VC, recording, external synthesis, billing, installation, permissions or deployment is exercised.
