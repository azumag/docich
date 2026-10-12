# Local speech transcription

Resident mode uses a Windows-local faster-whisper service by default. All admitted speech is transcribed locally; the response LLM is still called only for utterances containing `同志`. No per-utterance STT API request or Cloudflare API token is required. Electricity and local CPU/RAM use remain.

The service loads one Japanese-capable multilingual `small` model once, uses CPU int8 with four threads, and serializes inference. A fixed Japanese prompt plus the decoder hotword `同志` supplies the spelling; it is not a wake detector and still requires actual transcription. It listens only on `127.0.0.1:8765`. Node sends bounded 48 kHz mono PCM16 WAVs in memory to `/transcribe`. Audio/transcripts are not written to disk and requests are not logged. Download model files during setup; runtime loads local files only.

## Setup

Use a dedicated Python venv (Python >=3.9) outside Git-tracked files:

```powershell
python -m venv <venv-path>
& <venv-path>\Scripts\python.exe -m pip install -r requirements-local-stt.txt
```

Download `Systran/faster-whisper-small` with `huggingface_hub.snapshot_download`, explicitly using `token=False` and a local model directory. Only model/config/tokenizer/vocabulary files are required. Never substitute an English-only `.en` model for Japanese. The tested model has a roughly 484 MB weight file; CPU performance depends on the host and concurrent workload.

```powershell
& <venv-path>\Scripts\python.exe -X utf8 local-stt-server.py --model-dir <model-directory>
```

Wait for `local_stt_ready`. Keep this process running, then start the voice host in another PowerShell. Resident mode defaults to local. For the older fixed-target acceptance mode explicitly set:

```powershell
$env:DOCICH_DISCORD_VOICE_STT_PROVIDER = 'local'
$env:DOCICH_DISCORD_VOICE_LOCAL_STT_URL = 'http://127.0.0.1:8765/transcribe'
```

The endpoint override admits numeric loopback HTTP addresses only, with exact `/transcribe` path and no credentials/query/fragment. There is no cloud fallback: local failure returns a sanitized failure and that utterance is dropped. Existing STT 10-second deadline, capture/queue bounds and wake/memory policies continue to apply. Disconnecting a request cancels the Node caller, but an in-progress native inference may finish on the local CPU before the server accepts another request.

The local service is a trusted same-machine component, like same-host VOICEVOX; there is no bearer credential on its loopback endpoint. Do not proxy or expose its port to the network. `/healthz` returns readiness only. It does not install a Windows service or restart an existing Bot.

`DOCICH_DISCORD_VOICE_STT_PROVIDER=cloudflare` explicitly selects the existing Cloudflare implementation, requiring its existing API credentials. The fixed-target mode retains its legacy default for compatibility. Unknown provider names fail closed.

## Responsiveness

Resident capture ends after 500ms silence (the fixed-target path keeps 700ms).
Its energy gate admits quieter PCM at RMS 200 instead of 500, while the 100ms
voiced minimum, Silero VAD, bot exclusion and authenticated participant checks
remain. This may admit more background sound; microphone/noise acceptance is
still required. The same multilingual small CPU model stays in use. Text-only
decoding and the fixed `同志` hotword improve spelling without forcing a wake.

The canonical `同志` still matches in the current utterance. Leading separated
`同士`, `どうし` and `ドウシ` also count as calls; `どうして`, `どうしよう` and
`友達同士` do not. Previous context never activates a response and transcripts
are kept unchanged. The STT queue wait and STT/LLM/TTS completion events include
numeric millisecond timings, without text, credentials, IDs or audio.
While STT is busy, adjacent queued utterances from the same human are combined
with a 200ms silent separator, up to the existing ten-second input limit. No
extra batch wait is added. Different speakers are never merged, queue/capture
bounds stay unchanged, and replaced PCM buffers are erased. This avoids paying
one full recognition pass for each short pause during a background conversation;
real microphone transcription and responsiveness still require acceptance.

## Validation

`python local-stt-server.test.py` verifies server WAV format, truncation and duration bounds without model dependencies. `local-stt.test.mjs` verifies an actual loopback WAV round trip, no authorization header, PCM retention, locality checks, response bounds and cancellation. Synthetic Japanese VOICEVOX-to-local-STT measurements are separate from noisy microphone/Discord VC acceptance. A successful short synthetic sample does not prove live accuracy or throughput with overlapping speakers.
