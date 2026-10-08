# Resident VC mode: invitation, wake word and recent context

This opt-in mode is implemented locally alongside the existing fixed-target acceptance host. It is not an already-deployed service. It requires the reviewed Worker voice-context extension and the existing fixed bridge secret; it does not provision or recover credentials.

## User experience

1. A server manager joins a normal voice channel and runs `/join` in that server. The Bot resolves that member's current VC; no Guild, channel, or receive-user IDs are entered at the terminal.
2. The Bot stays in that VC until `/leave` or host shutdown. Repeating `/join` is idempotent; it refuses to move an existing session into a different channel implicitly.
3. The join response announces that speech will be transcribed and temporarily retained as context. Only current human members of that VC are admitted; bots, unknown users, and users in other channels are excluded.
4. Every admitted utterance is transcribed. Ordinary utterances only update context. An utterance containing the literal word `同志` triggers a reply to that utterance, with recent same-VC conversation available as untrusted data. Homophones such as `同士` are not aliases.
5. Background speech does not interrupt the reply. A newly transcribed wake utterance cancels the older reply and supersedes any pending reply. Detection therefore has STT latency, unlike immediate speech-onset interruption in the old acceptance mode.
6. Only successfully played wake/reply turns are committed to existing long-term conversational memory. Background context is not written to that database. `/leave` discards the session's background context; it does not erase previously delivered conversations.

## Context and resource bounds

Pending owner preference, the default is a session-local RAM window of 30 minutes, at most 256 utterances / 64,000 characters per VC. Expired entries are pruned by a timer even during silence. Leaving or stopping clears the buffer. No raw audio or transcript file is created. JavaScript strings cannot be reliably zeroed in memory.

The reply request includes at most the latest 12 prior utterances / 2,000 total characters. The current wake utterance remains a separate field. Server validation rejects malformed/oversized context. Scope comes from the authenticated local session, not spoken text; each VC session owns a separate buffer. The Worker treats context as conversation data, never system instructions, and does not persist it during reply or commit.

At most eight guild sessions, one VC per guild, four simultaneous captures per VC, one active STT plus four queued utterances, and one active reply plus one replacement are admitted. Each captured utterance remains bounded to 10 seconds. When overloaded, excess audio/wake work is discarded rather than accumulated without limit. This is bounded multi-speaker admission, not a claim of perfect overlapping-speech recognition.

## Host and Discord setup (not executed by offline tests)

Use the existing process-only Discord Token, Workers AI credentials, fixed `DOCICH_DISCORD_VOICE_CHAT_TOKEN`, `DOCICH_DISCORD_VOICE_CHAT_URL` ending in `/voice/reply`, and VOICEVOX settings from the main README. For intentional same-host VOICEVOX set `DOCICH_DISCORD_VOICE_VOICEVOX_ALLOW_LOOPBACK=1`. Set `DOCICH_DISCORD_VOICE_RESIDENT_ENABLED=1` only for this mode. Transcript/reply debug output is forcibly disabled for resident sessions.

The slash commands have default and runtime `ManageGuild` permission checks. The Bot also needs Connect and Speak in the selected VC. The app must support guild application commands. Invitation alone does not start a stopped Windows host, and this change does not install a Windows service or change the production text Bot.

Command registration is a separate explicit Discord application change:

```powershell
npm run register:resident
```

Registration creates only absent `join` / `leave` commands. It refuses existing names with different descriptions and never bulk-overwrites unrelated commands. Do not run this command as part of an offline review or without authorization for the application change.

After registration, Worker compatibility, and credentials are ready:

```powershell
$env:DOCICH_DISCORD_VOICE_RESIDENT_ENABLED = '1'
npm run start:resident
```

The host waits for `/join`; it does not auto-join a configured Guild/VC. It shares one Gateway client across resident sessions. A manual move to another VC stops the session so old context cannot cross channel boundaries. Normal transport recovery reuses the existing bounded rejoin behavior. Ctrl+C stops all sessions gracefully.

## Verification boundary

`npm run test:live-contracts` includes fake tests for invitation-derived channel selection, repeated join/leave, shutdown races, human-only speaker admission, leaving before delayed STT, wake/context separation, expiration, bounded replacement, actual live-pipeline composition, interrupt suppression of memory commit, and sanitized events. Worker tests prove context is model input only and malformed context is rejected.

Local tests are not proof of actual DAVE receive quality, Japanese wake detection in room noise, application-command delivery, real audio playback, reconnect, or 30-minute operation. Those require credentialed VC acceptance. No Worker deployment, command registration, Bot restart, or fixed-secret change is implied by this document.
