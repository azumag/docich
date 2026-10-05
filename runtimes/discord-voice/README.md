# Discord voice: offline turn coordinator (#1628)

This directory contains the synthetic/offline foundation for [Issue #1628](https://github.com/azumag/docich/issues/1628) plus a separately gated **live Discord join/playback bootstrap** for temporary Windows operation. The offline coordinator remains fake-only and does not receive network audio. The live bootstrap only logs into Discord, joins one explicitly configured Guild Voice channel with DAVE-capable `@discordjs/voice`, optionally plays a bounded generated PCM test tone, and stays connected until shutdown. It does not receive or record audio, run STT, call the conversation core, invoke TTS, or deploy itself as a service.

The current text Bot remains in `workers/discord-chat`: Workers AI, canonical persona and SQLite Durable Object memory, with public HTTP limited to `/healthz`. This slice does not import or change that Bot, its intents, model, prompt, memory or endpoint. The offline application reuses the internal conversation core and canonical public persona with synthetic AI only. The tests exercise fixed replies and transient scoped history; real persona/LLM behavior and a shared long-term memory database remain unaccepted.

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
- TTS: `synthesize(reply, {format, scope, signal})` returns owned `Int16Array` PCM.

Every asynchronous operation must honor `AbortSignal`. Transport `stop()` must synchronously stop emitting playback; `disconnect()` must stop admission. The coordinator races deadlines/cancellation, observes failures without printing adapter exceptions, and ignores late results. A late returned accessible TTS buffer is also zeroed using the typed-array intrinsic. Detached/invalid views and overridden cleanup methods cannot poison the turn queue. A transferred buffer destination is no longer accessible through the detached handle; its adapter owner must erase that destination. The runtime does not claim to erase inaccessible transferred memory. This protects subsequent stages from stale output; it cannot force an external provider to stop billing or prove that an uncooperative adapter has released its own resources. A future live adapter therefore requires independent cancellation/cleanup and DAVE/transport acceptance tests before removing the fake-only fence.

The runtime owns copies of inbound PCM and clears them on completion, rejection, revocation, expiry or disconnect; it does not modify the caller's input frame. STT must not retain raw data after completion/abort. TTS transfers buffer ownership to the runtime and must not reuse that buffer. Tests retain references only to verify clearing. There is no raw-audio persistence or runtime transcript/history store. Temporary transcripts/replies exist only in the pipeline; JavaScript strings cannot be reliably zeroed. Fake conversation history is test-only RAM. Sharing text and voice memory, transcript retention and deletion semantics remain explicit future decisions; the scope boundary is always the full `(guild, channel, user)` tuple.

`status()` reports only mode, fake session state, phase and bounded counts. `connected: true` means **fake session open**, not Discord connected. Event logging passes only a fixed `{event}` record, never IDs, audio, transcript, prompt, tokens or exception text. There is no built-in console logger. Events include session open/close, utterance start/finish/drop, queue full, STT/LLM/TTS/playback stages and cancellation/interruption. Logging failure cannot alter runtime behavior. No endpoint or production health/diagnostics registry is added.

## Temporary Windows live host: join + playback slice

For the current temporary host, use a local Windows machine rather than the production VM. Discord requires DAVE-capable clients/apps for normal voice channels from March 2026, so this slice uses pinned `@discordjs/voice 0.19.2` instead of implementing Discord Voice UDP/DAVE directly. It also uses `discord.js 14.27.0` only for Gateway/Guild voice state integration and `opusscript 0.0.8` for Opus encoding. FFmpeg is not required because the test path feeds 48 kHz signed PCM16 stereo directly.

The live host deliberately starts **self-deafened**. Discord therefore does not provide inbound user audio to this slice. That keeps participant audio outside the process until the later STT/consent slice explicitly changes the receive policy. The existing text Bot can continue separately; this process requests only the `Guilds` and `GuildVoiceStates` Gateway intents and does not read messages.

Install dependencies in the voice runtime directory:

```powershell
cd runtimes/discord-voice
npm install
npm run check:live
```

`check:live` performs no Discord login. It verifies that the pinned voice package loads, AES-256-GCM is available, the DAVE-capable stack can initialize, and the raw PCM/Opus pipeline can be constructed.

For an actual test server/channel, set the values only in the current PowerShell process. Do not put the token in Git, Issue/PR text, shell history, or command-line arguments.

```powershell
$env:DOCICH_DISCORD_VOICE_ENABLED = "1"
$env:DOCICH_DISCORD_TOKEN = "<set privately>"
$env:DOCICH_DISCORD_VOICE_GUILD_ID = "<guild snowflake>"
$env:DOCICH_DISCORD_VOICE_CHANNEL_ID = "<voice-channel snowflake>"
$env:DOCICH_DISCORD_VOICE_TEST_TONE = "1"
npm run start:live
```

`DOCICH_DISCORD_VOICE_TEST_TONE=1` is optional and defaults to off. When enabled, the Bot plays one short generated tone after the connection reaches Ready. It then remains connected until Ctrl+C (SIGINT); SIGTERM is also handled where the host provides POSIX-style SIGTERM semantics. Resumable Discord voice disconnects are left to the library; a stable disconnect gets at most three bounded explicit rejoin attempts before the process exits. No IDs, token, endpoint, Discord error body, transcript or audio are written to stdout/stderr.

### Slice 2: one-speaker receive + Cloudflare Whisper

Inbound audio remains **off by default**. To test Slice 2, explicitly select one Discord user and supply a Workers AI REST credential in the current process:

```powershell
$env:DOCICH_DISCORD_VOICE_RECEIVE_ENABLED = "1"
$env:DOCICH_DISCORD_VOICE_RECEIVE_USER_ID = "<target user snowflake>"
$env:DOCICH_DISCORD_VOICE_CF_ACCOUNT_ID = "<Cloudflare account id>"
$env:DOCICH_DISCORD_VOICE_CF_API_TOKEN = "<Workers AI token>"
npm run start:live
```

With receive enabled, the voice connection uses `selfDeaf: false`, but the runtime subscribes only to the explicitly selected user. A speaking event from any other user is ignored without opening an audio subscription. The selected user's Opus packets are decoded in RAM to 48 kHz mono PCM16, bounded to 10 seconds, and a short RMS gate rejects less than 100 ms of voiced audio before any provider call. The receive stream ends after Discord voice reports 700 ms of silence.

Completed utterances are sent to Cloudflare Workers AI `@cf/openai/whisper-large-v3-turbo` with Japanese transcription and provider-side VAD enabled. The REST payload is bounded but necessarily contains a transient JavaScript representation of the WAV bytes; no raw audio file is written. Runtime-owned PCM/WAV/response byte buffers are cleared after use where JavaScript exposes writable byte storage. Provider transport, billing, and remote retention remain governed by Cloudflare; this runtime does not claim remote erasure.

Normal logs contain only fixed lifecycle events such as `utterance_started`, `utterance_finished`, `stt_started`, `stt_completed`, and fixed failure/cancellation events. They do not include Guild/Channel/User IDs or transcript text. For a temporary owner-only acceptance session, transcript output can be explicitly enabled with `DOCICH_DISCORD_VOICE_TRANSCRIPT_DEBUG=1`; leave it unset for normal operation. Slice 2 does not yet pass the transcript into the DoCiAI conversation core or synthesize a reply. Those remain Slice 3/4 work.

The Bot needs only the permissions required to see the configured server/channel and **Connect / Speak** in that voice channel. Message Content, member-list and Presence privileged intents are not used by this live process. The Windows CI job installs the same pinned dependencies and executes `check:live` plus the offline contracts without any Discord credentials.

Slice 1 provides DAVE-capable VC join/outbound playback, and this Slice 2 adds an opt-in one-speaker receive/STT path. Offline contracts still do **not** prove a real Discord inbound-audio session, real Workers AI credentials/latency, VOICEVOX conversation replies, shared memory, live barge-in, multi-user attribution or the 30-minute acceptance test.

## Future live boundary

Discord voice uses a separate UDP connection for receiving/transmitting voice data and requires DAVE E2EE support for voice calls starting March 1, 2026. [Discord voice connection documentation](https://docs.discord.com/developers/topics/voice-connections). Workers `node:dgram` is an importable non-functional stub, so importing a UDP package does not make a Workers voice transport operational. [Cloudflare Node.js compatibility](https://developers.cloudflare.com/workers/runtime-apis/nodejs/#non-functional-stub-modules).

The Windows live bootstrap delegates Voice Gateway/UDP, DAVE and outbound Opus transport to the pinned Discord libraries. Inbound audio stays disabled by default; Slice 2 can explicitly undeafen and subscribe to one configured user, decode Opus, and invoke the bounded Whisper boundary. A later slice still needs production-grade VAD acceptance, participant-consent UX, VOICEVOX transport, safe conversation-core integration, shared-memory policy and live barge-in/multi-user acceptance. The existing public `/healthz` is not a conversation API. This slice does not deploy a host, register a Windows service, alter the current text Worker, or enable recording.

Issue #1628 still requires credentialed real-VC acceptance for join/receive/Japanese STT, canonical-persona response, Discord TTS, reconnect and the 30-minute live test. Passing these contracts demonstrates code boundaries and deterministic failure behavior, not those live acceptance criteria.


## Separate injected VOICEVOX boundary (offline tested)

`voicevox.mjs` reuses the HTTP protocol of canonical `src/docich/speech.py:195,646` and the external endpoint choice in Soren `say_enqueue.sh` / `voicevox_tts.sh`. The existing path is two synchronous HTTP responses, not an asynchronous callback service: POST `/audio_query?text=...&speaker=...`, then POST `/synthesis?speaker=...` with the query JSON to receive WAV. [Official engine API](https://voicevox.github.io/voicevox_engine/api/). No callback schema, new service, engine, library, retry queue or VM-local synthesis is introduced.

`new InjectedVoicevoxTTS({env, selectedURL, request})` requires an explicit settings snapshot and an injected transport. It does not read `process.env`, `.env`, secret files or shared state and provides no default `fetch` or executable entry point. `kind: 'voicevox-injected'` keeps it outside the coordinator's fake-only fence. The conversation core, Discord transport and real TTS remain unconnected.

Configuration mirrors the existing public names: `VOICEVOX_URLS` is a comma/whitespace ordered chain; `selectedURL` is an optional endpoint chosen by the trusted existing chooser and must belong to that chain. Otherwise the first external endpoint is selected. Explicit loopback/unspecified entries are excluded, including local fallback in a shared chain; an empty external chain fails closed. Legacy implicit localhost defaults are intentionally not inherited. Credentials in URLs, paths, query strings and fragments are rejected. These syntax checks do not prove DNS resolution, Windows/Mac ownership or deployment authorization; a future real transport must restrict trusted configured destinations, redirects and actual network access. This adapter never probes `/version` or changes shared endpoint locks/backoff/readiness.

`VOICEVOX_SPEAKER` defaults to canonical public style 3; the trusted caller must supply the selected persona style (no persona selection here). `VOICEVOX_TIMEOUT` defaults to 30 seconds, with a stricter **total** budget across both requests (0.001–30 seconds), and parent cancellation can shorten it. `VOICEVOX_MAX_CHARS` defaults to canonical 200, bounded to 900: longer text fails rather than invoking the canonical chunk/file/pronunciation pipeline. `VOICEVOX_PITCH` is added to the returned pitch, while `VOICEVOX_TEMPO` and `VOICEVOX_INTONATION` set speed/intonation, matching canonical semantics within bounded values. Query output is requested as 48kHz mono. No alternate endpoint or retry follows a request failure; retry ownership and shared queue coordination must be selected before live integration, especially after ambiguous synthesis completion.

The injected `request({url, method, headers, body, signal, maxBytes})` resolves `{status, body: Uint8Array}`. `maxBytes` is 64KiB for query and 5,760,256 bytes for WAV; the transport must enforce it while receiving, before allocation, and honor cancellation. The canonical client uses only `Content-Type: application/json`; this adapter adds no Authorization or new credential scheme. This is not proof that deployed network endpoints require no authentication. Any existing authenticated transport must remain privately configured at the transport boundary; the actual deployed authentication/access contract has not been inspected or exercised.

`synthesize(reply, {format: PCM, scope, signal})` requires valid full guild/channel/user scope. Scope is local validation context, not an authorization grant, HTTP field or provider metadata. Per-call closures and AbortControllers isolate results/cancellation; there is no cross-scope cache/history. Transport resolution belongs to that call, with no synthetic callback correlation field. HTTP bodies transfer ownership to the adapter, are cleared by the typed-array intrinsic after consumption/rejection (including late results), and must never be reused by the transport. A detached handle cannot clear its transferred destination: its owner remains responsible. Providers that ignore cancellation can continue work until they finish; the adapter bounds its own wait and suppresses/clears their late results, but cannot prove external release or billing cancellation.

WAV conversion accepts bounded little-endian RIFF/WAVE with one 16-byte PCM `fmt ` and one `data` chunk, signed PCM16, mono/stereo, 24kHz or 48kHz, at most 30 seconds. Chunk padding, RIFF length, byte rate, alignment, duplicates and data sizes are checked; unsupported formats fail closed. Stereo is averaged, 24kHz is linearly interpolated to 48kHz (last sample held), and trailing zero padding reaches a 960-sample/20ms frame. Output is a separately owned `Int16Array`. This deterministic narrow converter is not a general codec/resampler or a real audio-quality acceptance result.

No logger is provided. Public exceptions have fixed codes and no provider cause, reply, scope, endpoint, credential or exception message. Text/query strings in transient JS/transport memory cannot be reliably erased; the trusted transport must not log URLs (query contains text), body, headers, raw exceptions or provider bytes. Tests use generated WAV/JSON, injected transport only, and cover type/size/format limits, conversion samples, cancellation, total deadline, late bytes, concurrency and fixed private-error handling. No actual endpoint, VC, recording, external synthesis, billing, installation, permissions or deployment is exercised.


## Start the composed offline runtime

Node.js 22.18+ (built-in SQLite); no dependency installation:

```sh
node runtimes/discord-voice/cli.mjs --offline --demo
node runtimes/discord-voice/cli.mjs --offline
```

The demo runs two full join/activation/receive-end/core/VOICEVOX-WAV/playback/leave sessions and exits. Interactive mode consumes one JSON control command per stdin line:

```jsonl
{"command":"join","guildId":"1","channelId":"2"}
{"command":"activate","userId":"3","turnId":"fixture-1","receive":true,"respond":true}
{"command":"receive","userId":"3","frames":6}
{"command":"end","userId":"3"}
{"command":"idle"}
{"command":"cancel","userId":"3"}
{"command":"leave"}
```

`status` is also available. `receive` generates fixed 48kHz PCM in RAM, never reads a microphone/file/audio payload. `end` adds 400ms synthetic silence; explicit activation remains mandatory, response defaults off, and a completed utterance consumes activation. `cancel` revokes the user's capture/queued/active turn. Commands can be sent without `idle` to exercise cancellation while a turn runs. `idle` waits for the current pipeline. `leave` aborts and closes the session; another explicit `join` reconnects with fresh activation and no old memory. EOF, SIGINT and SIGTERM call leave and do not reopen a session.

`app.mjs` composes the existing coordinator, `workers/discord-chat/src/conversation.js`/`llm.js` and `voicevox.mjs`. The CLI loads the canonical public persona file once, with no copied prompt. STT and model responses are fixed synthetic fixtures. VOICEVOX uses the existing two-POST/WAV contract against an injected **fake** request function; `windows.example` is fixture configuration, not a contacted server. The wrapper's fake kind describes that entire synthetic transport composition; it does not enable real providers or relax the coordinator's live fence. Playback is an in-memory acknowledgement, with no speaker/Discord/audio worker output. The actual text Bot/Workers AI/DO/token/config remain untouched.

Session memory uses a new `:memory:` SQLite database per join, with the existing memory schema/core/recall/scope functions. It is entirely separate from text Durable Object storage. Successful synthetic playback acknowledges the turn before remembering it; generation/TTS/playback failure or cancellation clears failed content and never becomes recalled history. Only the same guild/channel/user recalls that session's replies. At most 64 admitted conversation turns are stored per session (failures also count); additional turns fail until leave/rejoin. Leave closes the database and drops pending/sequence state. There is no transcript/audio file, persistent DB, shared text-memory mapping or recording. Temporary JS strings and an uncooperative injected fixture's retained references cannot be securely erased; runtime-owned PCM/accessible late HTTP bytes are cleared by existing ownership rules.

Application voice policy explicitly rejects a generated reply above **200 characters** to match the selected TTS default; the shared text core still keeps its 901-character contract. Nothing silently clips/splits the answer. The coordinator's default 5-second stage budget (10-second cap) is passed as the TTS total budget too, rather than silently accepting a 30-second TTS operation inside a shorter stage. Actual speech duration, STT/LLM latency, longer response handling and audio quality have not been accepted with real providers.

Only fixed event names, command names, result codes and bounded status counters appear on stdout. Raw controls/IDs, persona, transcript/reply, URLs, provider messages and credentials are not printed. Unknown keys (including raw audio/endpoint/credential fields) and malformed commands are rejected with fixed codes. Controls are local fixture commands, not a public/authenticated API or an authorization grant.

Missing/unknown mode or endpoint options still fail the **offline** CLI with a fixed code; `cli.mjs` remains synthetic-only. Live Discord join/playback is intentionally a separate `live.mjs` entry point with an explicit enable flag and environment-only credentials. It does not expose a remote auth endpoint and still has no recording, external STT/LLM/TTS, persistent voice memory or automatic host deployment.
