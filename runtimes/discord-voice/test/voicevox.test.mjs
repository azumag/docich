import test from 'node:test';
import assert from 'node:assert/strict';
import { InjectedVoicevoxTTS, voicevoxConfig, wavToPCM, VoicevoxContractError } from '../voicevox.mjs';
import { PCM, VoiceRuntime } from '../runtime.mjs';

const env = { VOICEVOX_URLS: 'http://windows.example:50021 https://mac.example:50021' };
const scope = { guildId: '1', channelId: '2', userId: '3' };
const context = (signal = new AbortController().signal, selected = scope) => ({ signal, scope: selected, format: PCM });
const json = () => new TextEncoder().encode(JSON.stringify({ accent_phrases: [], speedScale: 1, pitchScale: 0 }));
const defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise((r) => setImmediate(r));
function wav({ rate = 48000, channels = 1, frames = 960, samples = [1200], junk = false } = {}) {
  const extra = junk ? 10 : 0;
  const bytes = new Uint8Array(44 + frames * channels * 2 + extra);
  const view = new DataView(bytes.buffer);
  const tag = (p, s) => bytes.set(new TextEncoder().encode(s), p);
  tag(0, 'RIFF'); view.setUint32(4, bytes.length - 8, true); tag(8, 'WAVE');
  tag(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, channels, true); view.setUint32(24, rate, true);
  view.setUint32(28, rate * channels * 2, true); view.setUint16(32, channels * 2, true); view.setUint16(34, 16, true);
  if (junk) { tag(36, 'JUNK'); view.setUint32(40, 1, true); bytes[44] = 7; }
  tag(36 + extra, 'data'); view.setUint32(40 + extra, frames * channels * 2, true);
  for (let i = 0; i < frames * channels; i++) view.setInt16(44 + extra + i * 2, samples[i % samples.length], true);
  return bytes;
}
function fake({ config = {}, request } = {}) {
  const calls = [], bodies = [];
  const adapter = new InjectedVoicevoxTTS({ env: { ...env, ...config }, request: request ?? (async (r) => {
    calls.push(r); const body = r.url.includes('/audio_query?') ? json() : wav(); bodies.push(body);
    return { status: 200, body };
  }) });
  return { adapter, calls, bodies };
}

test('existing explicit endpoint selection, speaker and timeout snapshot; no process env reads', () => {
  const config = voicevoxConfig({ ...env, VOICEVOX_SPEAKER: '14', VOICEVOX_TIMEOUT: '5', VOICEVOX_TEMPO: '1.2' }, 'https://mac.example:50021/');
  assert.equal(config.endpoint, 'https://mac.example:50021'); assert.equal(config.speaker, 14);
  assert.equal(config.timeoutMs, 5000); assert.equal(config.tempo, 1.2); assert.ok(Object.isFrozen(config));
  assert.equal(voicevoxConfig(env).speaker, 3); assert.equal(voicevoxConfig(env).timeoutMs, 30000);
  assert.equal(voicevoxConfig({ ...env, VOICEVOX_URLS: `${env.VOICEVOX_URLS},http://127.0.0.1:50021` }).endpoint, 'http://windows.example:50021');
  assert.throws(() => voicevoxConfig(env, 'http://127.0.0.1:50021'), /invalid_config/);
});
for (const bad of [undefined, '', 'http://localhost:50021', 'http://127.1:50021', 'http://[::1]:50021',
  'http://0.0.0.0:50021', 'http://user:secret@windows.example', 'ftp://windows.example',
  'https://mac.example/path', 'http://mac.example?token=secret', 'http://mac.example#secret']) {
  test(`reject missing/local/unsafe endpoint ${String(bad).replace(/secret/g, 'fixture')}`, () => {
    assert.throws(() => voicevoxConfig({ VOICEVOX_URLS: bad }), /^VoicevoxContractError: invalid_config$/);
  });
}
test('no unconfigured selection or invalid numeric limits', () => {
  assert.throws(() => voicevoxConfig(env, 'https://other.example'), /invalid_config/);
  for (const config of [{ VOICEVOX_TIMEOUT: 'Infinity' }, { VOICEVOX_TIMEOUT: '31' }, { VOICEVOX_SPEAKER: '-1' },
    { VOICEVOX_MAX_CHARS: '901' }, { VOICEVOX_TEMPO: 'secret' }]) assert.throws(() => voicevoxConfig({ ...env, ...config }), /invalid_config/);
});
test('two canonical POSTs request 48k mono; returned PCM owns memory and WAV/query bytes erased', async () => {
  const f = fake({ config: { VOICEVOX_SPEAKER: '14' } });
  const pcm = await f.adapter.synthesize('合成fixture & ?', context());
  assert.equal(f.calls.length, 2); assert.equal(pcm.length, 960); assert.equal(pcm[0], 1200);
  const first = new URL(f.calls[0].url);
  assert.equal(first.pathname, '/audio_query'); assert.equal(first.searchParams.get('text'), '合成fixture & ?');
  assert.equal(first.searchParams.get('speaker'), '14'); assert.equal(f.calls[0].body, '');
  assert.equal(f.calls[1].url, 'http://windows.example:50021/synthesis?speaker=14');
  const q = JSON.parse(f.calls[1].body); assert.equal(q.outputSamplingRate, 48000); assert.equal(q.outputStereo, false);
  assert.equal(q.speedScale, 1); assert.equal(q.pitchScale, 0); assert.equal(q.intonationScale, 1);
  for (const call of f.calls) {
    assert.deepEqual(call.headers, { 'Content-Type': 'application/json' });
    assert.equal(call.method, 'POST'); assert.equal(call.scope, undefined); assert.equal(call.headers.Authorization, undefined);
    assert.ok(call.signal.aborted); assert.ok(Object.isFrozen(call));
  }
  assert.ok(f.bodies.every((b) => b.every((v) => v === 0)));
  assert.equal(pcm[0], 1200); // returned PCM was not erased with HTTP storage
});
test('48k stereo averages channels, 24k interpolation, frame padding, offset view and odd RIFF chunks', () => {
  assert.equal(wavToPCM(wav({ channels: 2, samples: [-3000, 1000] }))[0], -1000);
  const bytes = wav({ rate: 24000, frames: 2, samples: [-1000, 1000], junk: true });
  const wrapped = new Uint8Array(bytes.length + 7); wrapped.set(bytes, 3);
  const out = wavToPCM(wrapped.subarray(3, 3 + bytes.length));
  assert.equal(out.length, 960); assert.deepEqual([...out.slice(0, 5)], [-1000, 0, 1000, 1000, 0]);
});
test('WAV strict format, chunk bounds, duplicates, alignment and 30s/byte ceiling', () => {
  const bads = [new Uint8Array(), new Uint8Array(5_760_257), wav({ rate: 44100 }), wav({ channels: 3 }), wav({ frames: 0 }), wav({ frames: 48000 * 30 + 1 })];
  for (const [offset, value, width] of [[0, 0, 4], [4, 1, 4], [16, 18, 4], [20, 3, 2], [28, 1, 4], [32, 1, 2], [34, 32, 2], [40, 99999999, 4]]) {
    const b = wav(); const v = new DataView(b.buffer); width === 2 ? v.setUint16(offset, value, true) : v.setUint32(offset, value, true); bads.push(b);
  }
  const odd = wav(); new DataView(odd.buffer).setUint32(40, 1919, true); bads.push(odd);
  const duplicate = new Uint8Array(44 + 16); duplicate.set(wav({ frames: 0 }));
  duplicate.set(new TextEncoder().encode('data'), 44); new DataView(duplicate.buffer).setUint32(4, duplicate.length - 8, true); bads.push(duplicate);
  const detached = wav(); structuredClone(detached.buffer, { transfer: [detached.buffer] }); bads.push(detached);
  for (const b of bads) assert.throws(() => wavToPCM(b), /^VoicevoxContractError: invalid_wav$/);
  assert.equal(wavToPCM(wav({ frames: 48000 * 30 })).length, 48000 * 30);
});
test('scope, format, text and already-cancelled requests reject before transport', async () => {
  const f = fake();
  for (const c of [{ ...context(), scope: undefined }, context(undefined, { ...scope, userId: '0' }),
    { ...context(), format: { ...PCM, sampleRate: 24000 } }, { ...context(), signal: {} }]) await assert.rejects(f.adapter.synthesize('fixture', c), /invalid_context/);
  for (const text of ['', ' ', 'x'.repeat(201), {}]) await assert.rejects(f.adapter.synthesize(text, context()), /invalid_text/);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(f.adapter.synthesize('fixture', context(controller.signal)), /tts_cancelled/);
  assert.equal(f.calls.length, 0);
});
test('HTTP status, wrong type/oversize and invalid query fail closed with no retry', async () => {
  for (const response of [{ status: 500, body: json() }, { status: 200, body: 'private' },
    { status: 200, body: new Uint8Array(65537) }, { status: 200, body: new TextEncoder().encode('{private') },
    { status: 200, body: new TextEncoder().encode('null') }, { status: 200, body: new TextEncoder().encode('{"detail":"private"}') }]) {
    let calls = 0; const f = fake({ request: async () => { calls++; return response; } });
    await assert.rejects(f.adapter.synthesize('fixture', context()), /tts_failed/);
    assert.equal(calls, 1); if (response.body instanceof Uint8Array) assert.ok(response.body.every((v) => v === 0));
  }
});
test('synthesis failure never retries or invokes another endpoint; cleans invalid audio', async () => {
  let calls = 0; const bad = new Uint8Array(50).fill(9);
  const f = fake({ request: async () => ({ status: 200, body: ++calls === 1 ? json() : bad }) });
  await assert.rejects(f.adapter.synthesize('fixture', context()), /invalid_wav/);
  assert.equal(calls, 2); assert.ok(bad.every((v) => v === 0));
});
for (const stage of ['query', 'synthesis']) {
  test(`cancel ${stage}, ignore and scrub late result; no unhandled rejection`, async () => {
    const pending = defer(); const controller = new AbortController(); const calls = [];
    const f = fake({ request: async (r) => { calls.push(r); return stage === 'query' || calls.length === 2 ? pending.promise : { status: 200, body: json() }; } });
    const result = f.adapter.synthesize('fixture', context(controller.signal));
    await tick(); controller.abort(); await assert.rejects(result, /tts_cancelled/);
    const late = stage === 'query' ? json() : wav(); pending.resolve({ status: 200, body: late });
    await tick(); assert.ok(late.every((v) => v === 0)); assert.equal(calls.length, stage === 'query' ? 1 : 2);
    assert.ok(calls.every((r) => r.signal.aborted));
  });
}
test('total deadline aborts injected transport and scrubs its uncooperative late response', async () => {
  const pending = defer(); let signal;
  const f = fake({ config: { VOICEVOX_TIMEOUT: '0.01' }, request: async (r) => { signal = r.signal; return pending.promise; } });
  await assert.rejects(f.adapter.synthesize('fixture', context()), /tts_cancelled/);
  assert.ok(signal.aborted); const late = json(); pending.resolve({ status: 200, body: late }); await tick(); assert.ok(late.every((v) => v === 0));
});
test('concurrent scopes keep responses/cancel separate, IDs never enter HTTP requests', async () => {
  const pending = [defer(), defer()]; let synth = 0; const calls = [];
  const f = fake({ request: async (r) => { calls.push(r); return r.url.includes('/audio_query?') ? { status: 200, body: json() } : pending[synth++].promise; } });
  const cancel = new AbortController(); const one = f.adapter.synthesize('one', context(cancel.signal));
  const two = f.adapter.synthesize('two', context(undefined, { guildId: '11', channelId: '22', userId: '33' }));
  await tick(); cancel.abort(); await assert.rejects(one, /tts_cancelled/);
  const a = wav({ samples: [100] }), b = wav({ samples: [200] });
  pending[1].resolve({ status: 200, body: b }); assert.equal((await two)[0], 200);
  pending[0].resolve({ status: 200, body: a }); await tick(); assert.ok(a.every((v) => v === 0));
  assert.ok(calls.every((r) => !('scope' in r) && !('requestId' in r)));
});
test('provider secrets/messages/causes, hostile exception fields and logger remain private', async () => {
  const secret = 'EXAMPLE_PRIVATE_REPLY_TOKEN_URL';
  for (const error of [new Error(secret), new VoicevoxContractError(secret),
    Object.defineProperty(new VoicevoxContractError('tts_failed'), 'message', { get() { throw new Error(secret); } })]) {
    const f = fake({ request: async () => { throw error; } });
    await assert.rejects(f.adapter.synthesize(secret, context()), (e) => {
      assert.equal(e.message, 'tts_failed'); assert.equal(e.cause, undefined); assert.ok(!String(e.stack).includes(secret)); return true;
    });
  }
});
test('provider message getters are read once; changing values and unexpected types stay private', async () => {
  const secret = 'EXAMPLE_PRIVATE_PROVIDER_TOKEN_URL';
  for (const initial of ['tts_failed', 'invalid_wav', secret, undefined, null, 17, 17n, Symbol(secret), {},
    { toString() { throw new Error(secret); } }, new String('tts_failed')]) {
    let reads = 0;
    const error = Object.defineProperty(new VoicevoxContractError('tts_failed'), 'message', {
      get() { return ++reads === 1 ? initial : secret; },
    });
    const f = fake({ request: async () => { throw error; } });
    await assert.rejects(f.adapter.synthesize('fixture', context()), (e) => {
      assert.equal(e.message, initial === 'invalid_wav' ? 'invalid_wav' : 'tts_failed');
      assert.equal(reads, 1); assert.equal(e.cause, undefined);
      assert.ok(!String(e.stack).includes(secret)); return true;
    });
  }
});
test('real adapter stays outside fake-only coordinator fence', () => {
  const adapter = fake().adapter;
  assert.equal(adapter.kind, 'voicevox-injected');
  assert.throws(() => { adapter.kind = 'fake'; }, TypeError);
  assert.throws(() => new VoiceRuntime({ transport: { kind: 'fake' }, stt: { kind: 'fake' }, conversation: { kind: 'fake' }, tts: adapter }), /offline_adapters_required/);
});
test('settings are snapshotted and canonical pitch is additive', async () => {
  const settings = { ...env, VOICEVOX_PITCH: '0.05', VOICEVOX_TEMPO: '1.2', VOICEVOX_INTONATION: '0.8' };
  let query;
  const adapter = new InjectedVoicevoxTTS({ env: settings, request: async (r) => {
    if (r.url.includes('/audio_query?')) return { status: 200, body: new TextEncoder().encode(JSON.stringify({ accent_phrases: [], speedScale: 1, pitchScale: 0.1 })) };
    query = JSON.parse(r.body); return { status: 200, body: wav() };
  } });
  settings.VOICEVOX_PITCH = 'private'; settings.VOICEVOX_URLS = 'http://localhost';
  await adapter.synthesize('fixture', context());
  assert.ok(Math.abs(query.pitchScale - 0.15) < 1e-9); assert.equal(query.speedScale, 1.2); assert.equal(query.intonationScale, 0.8);
});
test('synthesis byte limit and error status erase owned response without retry', async () => {
  for (const status of [200, 503]) {
    let calls = 0; const body = status === 200 ? new Uint8Array(5_760_257).fill(1) : wav();
    const f = fake({ request: async (r) => {
      calls++; assert.equal(r.maxBytes, calls === 1 ? 65536 : 5_760_256);
      return calls === 1 ? { status: 200, body: json() } : { status, body };
    } });
    await assert.rejects(f.adapter.synthesize('fixture', context()), /tts_failed/);
    assert.equal(calls, 2); assert.ok(body.every((v) => v === 0));
  }
});
test('HTTP byte cleanup ignores overridden fill and safely rejects detached bytes', async () => {
  const body = json(); Object.defineProperty(body, 'fill', { get() { throw new Error('EXAMPLE_PRIVATE'); } });
  let calls = 0; const f = fake({ request: async () => ({ status: 200, body: ++calls === 1 ? body : wav() }) });
  await f.adapter.synthesize('fixture', context()); assert.ok(body.every((v) => v === 0));
  const detached = json(); structuredClone(detached.buffer, { transfer: [detached.buffer] });
  await assert.rejects(fake({ request: async () => ({ status: 200, body: detached }) }).adapter.synthesize('fixture', context()), /tts_failed/);
});
test('deadline budget includes query plus synthesis and late synthesis is scrubbed', async () => {
  const late = defer(); let calls = 0;
  const f = fake({ config: { VOICEVOX_TIMEOUT: '0.1' }, request: async () => {
    if (++calls === 1) { await new Promise((r) => setTimeout(r, 5)); return { status: 200, body: json() }; }
    return late.promise;
  } });
  await assert.rejects(f.adapter.synthesize('fixture', context()), /tts_cancelled/);
  assert.equal(calls, 2); const body = wav(); late.resolve({ status: 200, body }); await tick(); assert.ok(body.every((v) => v === 0));
});
test('late transport rejection after cancellation remains observed', async () => {
  let reject;
  const waiting = new Promise((_, r) => { reject = r; });
  const controller = new AbortController();
  const result = fake({ request: async () => waiting }).adapter.synthesize('fixture', context(controller.signal));
  await tick(); controller.abort(); await assert.rejects(result, /tts_cancelled/);
  reject(new Error('EXAMPLE_PRIVATE')); await tick();
});
