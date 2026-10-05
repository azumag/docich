import assert from 'node:assert/strict';
import test from 'node:test';
import { setImmediate as tick } from 'node:timers/promises';
import { VoiceRuntime, PCM, VoiceContractError } from '../runtime.mjs';
import { adapters, gate } from './fakes.mjs';

const scope = { guildId: '1', channelId: '10', userId: '7' };
const limits = { minSpeechMs: 40, silenceMs: 40, maxUtteranceMs: 200 };
async function fixture(t, options = {}) {
  const fake = adapters(), events = [];
  const runtime = new VoiceRuntime({ ...fake, limits, log: (event) => events.push(event), ...options });
  t.after(() => runtime.disconnect());
  const session = await runtime.connect(scope);
  const arm = (turnId, userScope = scope, flags = { receive: true, respond: true }) =>
    runtime.activate({ ...userScope, session, turnId, ...flags });
  const frame = (sequence, speech = true, overrides = {}) => ({ ...scope, session, sequence,
    isBot: false, samples: new Int16Array(PCM.samples).fill(speech ? 1000 : 0), ...overrides });
  const utterance = (turnId, userScope = scope) => {
    assert.equal(arm(turnId, userScope), 'armed');
    assert.equal(runtime.receive(frame(0, true, userScope)), 'capturing');
    runtime.receive(frame(1, true, userScope));
    runtime.receive(frame(2, false, userScope));
    return runtime.receive(frame(3, false, userScope));
  };
  return { runtime, fake, session, arm, frame, utterance, events };
}

test('defaults admit no audio or response; explicit receive alone does not call providers', async (t) => {
  const f = await fixture(t);
  assert.equal(f.runtime.status().receiveEnabled, false);
  assert.equal(f.runtime.receive(f.frame(0)), 'disabled');
  assert.equal(f.arm('off', scope, {}), 'disabled');
  assert.equal(f.arm('capture-only', scope, { receive: true }), 'armed');
  for (let i = 0; i < 3; i++) f.runtime.receive(f.frame(i, i < 2));
  assert.equal(f.runtime.receive(f.frame(3, false)), 'response_disabled');
  await f.runtime.idle();
  assert.equal(f.fake.sttInputs.length, 0);
  assert.equal(f.fake.plays.length, 0);
});

test('synthetic PCM -> fixed STT -> scoped fake core -> TTS -> playback consumes activation once', async (t) => {
  const f = await fixture(t);
  assert.equal(f.utterance('turn-1'), 'queued');
  await f.runtime.idle();
  assert.equal(f.fake.requests.length, 1);
  assert.equal(f.fake.plays.length, 1);
  assert.deepEqual(f.fake.requests[0].scope, scope);
  assert.equal(f.runtime.receive(f.frame(4)), 'disabled');
  assert.equal(f.fake.sttInputs[0].every((value) => value === 0), true);
  assert.equal(f.runtime.status().pending, 0);
  assert.ok(f.events.some((event) => event.event === 'playback_completed'));
});

test('silence and short noise never reach STT', async (t) => {
  const f = await fixture(t);
  f.arm('noise');
  assert.equal(f.runtime.receive(f.frame(0, false)), 'silence');
  f.runtime.receive(f.frame(1));
  f.runtime.receive(f.frame(2, false));
  assert.equal(f.runtime.receive(f.frame(3, false)), 'too_short');
  await f.runtime.idle();
  assert.equal(f.fake.sttInputs.length, 0);
});

test('overlong speech is rejected without truncating it into an answer', async (t) => {
  const f = await fixture(t);
  f.arm('long');
  for (let i = 0; i < 10; i++) assert.equal(f.runtime.receive(f.frame(i)), 'capturing');
  assert.equal(f.runtime.receive(f.frame(10)), 'too_long');
  assert.equal(f.runtime.status().captures, 0);
  assert.equal(f.fake.requests.length, 0);
});

test('duplicate frames and turn activations cannot produce duplicate replies', async (t) => {
  const f = await fixture(t);
  f.arm('same');
  f.runtime.receive(f.frame(0));
  assert.equal(f.runtime.receive(f.frame(0)), 'duplicate');
  f.runtime.receive(f.frame(1)); f.runtime.receive(f.frame(2, false)); f.runtime.receive(f.frame(3, false));
  await f.runtime.idle();
  assert.equal(f.arm('same'), 'duplicate');
  assert.equal(f.fake.requests.length, 1);
});

test('bots, missing speaker classification, unactivated humans and other channels/guilds are excluded', async (t) => {
  const f = await fixture(t);
  f.arm('human');
  for (const invalid of [{ isBot: true }, { isBot: undefined }, { userId: '999' },
    { channelId: '11' }, { guildId: '2' }]) assert.equal(f.runtime.receive(f.frame(0, true, invalid)), 'ignored');
  assert.equal(f.runtime.receive(f.frame(0, true, { userId: '8' })), 'disabled');
  assert.equal(f.arm('other-channel', { ...scope, channelId: '11' }), 'ignored');
  assert.equal(f.arm('self', { ...scope, userId: '999' }), 'ignored');
  assert.equal(f.fake.requests.length, 0);
});

test('fake conversation contexts are separated by guild/channel/user, no text memory is imported', async (t) => {
  const f = await fixture(t);
  f.utterance('u7-first'); await f.runtime.idle();
  f.utterance('u8', { ...scope, userId: '8' }); await f.runtime.idle();
  f.utterance('u7-second'); await f.runtime.idle();
  assert.equal(f.fake.requests[1].context.length, 0);
  assert.equal(f.fake.requests[2].context.length, 1);
  for (const next of [{ ...scope, channelId: '11' }, { ...scope, guildId: '2' }]) {
    await f.runtime.disconnect();
    const session = await f.runtime.connect(next);
    f.runtime.activate({ ...next, session, turnId: 'scope-' + next.guildId + '-' + next.channelId, receive: true, respond: true });
    for (let i = 0; i < 4; i++) f.runtime.receive(f.frame(i, i < 2, { ...next, session }));
    await f.runtime.idle();
    assert.equal(f.fake.requests.at(-1).context.length, 0);
  }
});

test('queue includes active turn and enforces a hard bound', async (t) => {
  const f = await fixture(t, { limits: { ...limits, maxQueue: 2 } });
  f.fake.stt.gate = gate();
  assert.equal(f.utterance('one'), 'queued');
  await tick();
  assert.equal(f.utterance('two'), 'queued');
  assert.equal(f.utterance('overflow'), 'busy');
  assert.equal(f.runtime.status().pending, 2);
  f.fake.stt.gate.release(); await f.runtime.idle();
  assert.equal(f.fake.requests.length, 2);
});

test('capture slots are bounded independently of the processing queue', async (t) => {
  const f = await fixture(t, { limits: { ...limits, maxCaptures: 2 } });
  assert.equal(f.arm('one'), 'armed');
  assert.equal(f.arm('two', { ...scope, userId: '8' }), 'armed');
  assert.equal(f.arm('three', { ...scope, userId: '9' }), 'busy');
  f.runtime.revoke(scope);
  assert.equal(f.arm('three', { ...scope, userId: '9' }), 'armed');
});

for (const phase of ['stt', 'llm', 'tts', 'playback']) {
  test('disconnect cancels ' + phase + ', drops queue and rejects old-epoch frames', async (t) => {
    const f = await fixture(t);
    const adapter = { stt: f.fake.stt, llm: f.fake.conversation, tts: f.fake.tts, playback: f.fake.transport }[phase];
    const blocker = gate(); adapter[phase === 'playback' ? 'playGate' : 'gate'] = blocker;
    f.utterance('active'); await tick();
    assert.equal(f.runtime.status().phase, phase);
    f.utterance('queued');
    await f.runtime.disconnect();
    blocker.release(); await tick();
    assert.equal(f.runtime.status().pending, 0);
    assert.equal(f.runtime.status().receiveEnabled, false);
    assert.equal(f.fake.plays.length, phase === 'playback' ? 1 : 0);
    assert.ok(f.fake.sttInputs.every((pcm) => pcm.every((value) => value === 0)));
    const session = await f.runtime.connect(scope);
    assert.notEqual(session, f.session);
    assert.equal(f.runtime.receive(f.frame(8)), 'ignored');
    assert.equal(f.arm('stale'), 'ignored');
  });
}

test('authorized human speech interrupts playback before the next response', async (t) => {
  const f = await fixture(t);
  const blocker = gate(); f.fake.transport.playGate = blocker;
  f.utterance('playing'); await tick();
  assert.equal(f.runtime.status().phase, 'playback');
  // Unactivated speakers cannot stop the bot.
  assert.equal(f.runtime.receive(f.frame(0, true, { userId: '8' })), 'disabled');
  f.arm('interrupt');
  f.runtime.receive(f.frame(0));
  assert.equal(f.fake.plays[0].signal.aborted, true);
  assert.ok(f.events.some((event) => event.event === 'playback_interrupted'));
  f.fake.transport.playGate = null; blocker.release(); await tick();
  for (let i = 1; i < 4; i++) f.runtime.receive(f.frame(i, i < 2));
  await f.runtime.idle();
  assert.equal(f.fake.plays.length, 2);
});

test('revoke scrubs capture and cancels in-flight work for only the selected user', async (t) => {
  const f = await fixture(t);
  f.fake.stt.gate = gate();
  f.utterance('u7'); await tick();
  f.utterance('u8', { ...scope, userId: '8' });
  f.arm('capture-again'); f.runtime.receive(f.frame(0));
  f.runtime.revoke(scope);
  f.fake.stt.gate.release(); await f.runtime.idle();
  assert.equal(f.fake.requests.length, 1);
  assert.equal(f.fake.requests[0].scope.userId, '8');
  assert.equal(f.runtime.status().captures, 0);
});

test('expired activation cannot retain raw PCM or become a delayed response', async (t) => {
  let now = 0;
  const f = await fixture(t, { now: () => now, limits: { ...limits, activationMs: 200 } });
  f.arm('expires'); f.runtime.receive(f.frame(0));
  now = 201;
  assert.equal(f.runtime.receive(f.frame(1)), 'expired');
  assert.equal(f.runtime.status().captures, 0);
  assert.equal(f.fake.sttInputs.length, 0);
});

test('activation timer drops an idle capture even when no more frames arrive', async (t) => {
  const f = await fixture(t, { limits: { ...limits, activationMs: 20 } });
  f.arm('timer'); f.runtime.receive(f.frame(0));
  await new Promise((resolve) => setTimeout(resolve, 35));
  assert.ok(f.events.some((event) => event.event === 'activation_expired'));
  assert.equal(f.runtime.status().captures, 0);
});

test('timeout stops a noncooperative fake; a late result cannot advance to LLM/playback', async (t) => {
  const f = await fixture(t, { limits: { ...limits, stageTimeoutMs: 10 } });
  const blocker = gate();
  f.fake.stt.transcribe = async (pcm) => { f.fake.sttInputs.push(pcm); await blocker.promise; return 'late private transcript'; };
  f.utterance('late'); await f.runtime.idle();
  blocker.release(); await tick();
  assert.equal(f.fake.requests.length, 0);
  assert.equal(f.fake.plays.length, 0);
  assert.equal(f.fake.sttInputs[0].every((value) => value === 0), true);
});

for (const stage of ['stt', 'llm', 'tts', 'playback']) {
  test(stage + ' errors expose fixed events only and never auto-retry', async (t) => {
    const f = await fixture(t);
    const [adapter, method] = { stt: [f.fake.stt, 'transcribe'], llm: [f.fake.conversation, 'reply'],
      tts: [f.fake.tts, 'synthesize'], playback: [f.fake.transport, 'play'] }[stage];
    let calls = 0;
    adapter[method] = async () => { calls++; throw new Error('synthetic-token private transcript 1234567890'); };
    f.utterance('fail'); await f.runtime.idle();
    assert.equal(calls, 1);
    assert.ok(f.events.some((event) => event.event === stage + '_failed'));
    assert.doesNotMatch(JSON.stringify([f.events, f.runtime.status()]), /synthetic-token|private transcript|1234567890|guildId|channelId|userId/);
    assert.ok(f.events.every((event) => Object.keys(event).join() === 'event'));
  });
}

test('invalid and oversized PCM/STT/LLM/TTS outputs fail closed', async (t) => {
  const f = await fixture(t);
  f.arm('bad-frame');
  for (const bad of [{ samples: new Int16Array(2000) }, { samples: [1] }, { sequence: NaN }]) {
    assert.equal(f.runtime.receive(f.frame(0, true, bad)), 'invalid_frame');
  }
  f.runtime.revoke(scope);
  for (const [adapter, method, result, turnId] of [
    [f.fake.stt, 'transcribe', '', 'empty'], [f.fake.stt, 'transcribe', 'x'.repeat(2001), 'long-text'],
    [f.fake.conversation, 'reply', 'x'.repeat(901), 'long-reply'],
    [f.fake.tts, 'synthesize', new Int16Array(960 * 1501), 'long-audio'],
  ]) {
    const original = adapter[method]; adapter[method] = async () => result;
    f.utterance(turnId); await f.runtime.idle(); adapter[method] = original;
  }
  assert.equal(f.fake.plays.length, 0);
});

test('construction has no connection/IO; real adapters and unbounded settings are rejected', () => {
  const fake = adapters(); let connections = 0;
  fake.transport.connect = () => { connections++; throw new Error('must not run'); };
  const runtime = new VoiceRuntime(fake);
  assert.equal(connections, 0);
  assert.equal(runtime.status().connected, false);
  assert.throws(() => new VoiceRuntime({ ...fake, transport: { ...fake.transport, kind: 'real' } }), VoiceContractError);
  for (const bad of [{ maxQueue: 0 }, { maxQueue: 5 }, { unknown: 1 }, { maxUtteranceMs: 99999 }]) {
    assert.throws(() => new VoiceRuntime({ ...fake, limits: bad }), /invalid_limits/);
  }
});

test('concurrent connect/disconnect cannot resurrect an old synthetic session', async (t) => {
  const fake = adapters(), blocker = gate();
  fake.transport.connect = async () => { await blocker.promise; return { botUserId: '999' }; };
  const runtime = new VoiceRuntime(fake); t.after(() => runtime.disconnect());
  const connecting = runtime.connect(scope);
  const closing = runtime.disconnect();
  await assert.rejects(runtime.connect(scope), /session_already_open/);
  await closing; blocker.release();
  assert.equal(await connecting, null);
  assert.equal(runtime.status().connected, false);
});

test('late TTS buffer is scrubbed after disconnect and can never be played', async (t) => {
  const f = await fixture(t), blocker = gate();
  const lateAudio = new Int16Array(960).fill(1234);
  f.fake.tts.synthesize = async () => { await blocker.promise; return lateAudio; };
  f.utterance('late-audio'); await tick();
  assert.equal(f.runtime.status().phase, 'tts');
  await f.runtime.disconnect(); blocker.release(); await tick();
  assert.equal(lateAudio.every((value) => value === 0), true);
  assert.equal(f.fake.plays.length, 0);
});

test('owned input copies and returned TTS buffers are cleared, caller frames remain unchanged', async (t) => {
  const f = await fixture(t), returnedAudio = new Int16Array(960).fill(1234);
  f.fake.tts.synthesize = async () => returnedAudio;
  f.arm('buffer-ownership');
  const firstFrame = f.frame(0);
  f.runtime.receive(firstFrame);
  for (let i = 1; i < 4; i++) f.runtime.receive(f.frame(i, i < 2));
  await f.runtime.idle();
  assert.equal(firstFrame.samples.every((sample) => sample === 1000), true);
  assert.equal(returnedAudio.every((sample) => sample === 0), true);
});

test('invalid scopes/activation/session cannot enable collection; dedupe metadata is bounded', async (t) => {
  const f = await fixture(t, { limits: { ...limits, maxSeen: 2 } });
  assert.throws(() => f.arm('bad', { ...scope, userId: '../private' }), /invalid_scope/);
  assert.throws(() => f.arm('bad', scope, { receive: '1', respond: true }), /invalid_activation/);
  assert.equal(f.runtime.activate({ ...scope, session: f.session - 1, turnId: 'old', receive: true, respond: true }), 'ignored');
  for (const turnId of ['one', 'two', 'three']) { f.arm(turnId); f.runtime.revoke(scope); }
  assert.equal(f.arm('two'), 'duplicate');
  assert.equal(f.arm('one'), 'armed'); // Explicit new authorization after bounded replay window.
});

test('draining is serialized across users even when several utterances finish together', async (t) => {
  const f = await fixture(t), blocker = gate();
  f.fake.stt.gate = blocker;
  f.utterance('first'); f.utterance('second', { ...scope, userId: '8' });
  await tick();
  assert.equal(f.fake.sttInputs.length, 1);
  blocker.release(); await f.runtime.idle();
  assert.equal(f.fake.sttInputs.length, 2);
  assert.equal(f.fake.plays.length, 2);
});

for (const invalid of ['detached', 'throwing-fill-object']) {
  test('invalid TTS ' + invalid + ' cannot poison idle/queue or prevent the next turn', async (t) => {
    const f = await fixture(t, { limits: { ...limits, maxQueue: 1 } });
    const original = f.fake.tts.synthesize;
    f.fake.tts.synthesize = async () => {
      if (invalid === 'throwing-fill-object') return { get fill() { throw new Error('private cleanup exception'); } };
      const pcm = new Int16Array(960).fill(1234);
      structuredClone(pcm.buffer, { transfer: [pcm.buffer] });
      return pcm;
    };
    assert.equal(f.utterance('invalid-' + invalid), 'queued');
    await assert.doesNotReject(f.runtime.idle());
    assert.equal(f.runtime.status().pending, 0);
    assert.equal(f.runtime.status().phase, 'idle');
    assert.equal(f.fake.plays.length, 0);
    f.fake.tts.synthesize = original;
    assert.equal(f.utterance('recovered-' + invalid), 'queued');
    await f.runtime.idle();
    assert.equal(f.fake.plays.length, 1);
    await f.runtime.disconnect();
    assert.equal(f.runtime.status().pending, 0);
  });
}

test('accessible typed-array audio is zeroed even when adapter overrides fill', async (t) => {
  const f = await fixture(t);
  const pcm = new Int16Array(960).fill(1234);
  pcm.fill = () => { throw new Error('private overridden method'); };
  f.fake.tts.synthesize = async () => pcm;
  f.utterance('overridden-fill');
  await assert.doesNotReject(f.runtime.idle());
  assert.equal(pcm.every((value) => value === 0), true);
  assert.equal(f.runtime.status().pending, 0);
});

test('late detached TTS result is safely discarded without an unhandled rejection', async (t) => {
  const f = await fixture(t), blocker = gate();
  const pcm = new Int16Array(960).fill(1234);
  structuredClone(pcm.buffer, { transfer: [pcm.buffer] });
  f.fake.tts.synthesize = async () => { await blocker.promise; return pcm; };
  f.utterance('late-detached'); await tick();
  assert.equal(f.runtime.status().phase, 'tts');
  await f.runtime.disconnect(); blocker.release(); await tick();
  assert.equal(f.fake.plays.length, 0);
  assert.equal(f.runtime.status().pending, 0);
});

test('STT cannot poison cleanup by detaching its owned PCM input', async (t) => {
  const f = await fixture(t), original = f.fake.stt.transcribe;
  f.fake.stt.transcribe = async (pcm) => {
    structuredClone(pcm.buffer, { transfer: [pcm.buffer] });
    throw new Error('synthetic bad STT');
  };
  f.utterance('detached-input');
  await assert.doesNotReject(f.runtime.idle());
  assert.equal(f.runtime.status().pending, 0);
  f.fake.stt.transcribe = original;
  f.utterance('valid-input'); await f.runtime.idle();
  assert.equal(f.fake.plays.length, 1);
});
