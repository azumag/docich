import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import test from 'node:test';

import {
  attachLiveSttReceiver,
  stereoPcm16ToMono,
} from './live-receive.mjs';

const TARGET = '323456789012345678';
const OTHER = '423456789012345678';

function stereoChunk(value, frames = 960) {
  const bytes = Buffer.alloc(frames * 4);
  for (let frame = 0; frame < frames; frame += 1) {
    bytes.writeInt16LE(value, frame * 4);
    bytes.writeInt16LE(value, frame * 4 + 2);
  }
  return bytes;
}

function fakeConnection(packetFactory) {
  const speaking = new EventEmitter();
  const subscriptions = [];
  return {
    subscriptions,
    connection: {
      receiver: {
        speaking,
        subscribe(userId, options) {
          subscriptions.push({ userId, options });
          return Readable.from(packetFactory(userId));
        },
      },
    },
  };
}

const decoderFactory = () => ({
  decode(packet) {
    return packet;
  },
  close() {},
});

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.fail('timed out waiting for live receive fixture');
}

test('stereo PCM16 is downmixed to mono without changing sample rate', () => {
  const bytes = Buffer.alloc(8);
  bytes.writeInt16LE(1000, 0);
  bytes.writeInt16LE(-1000, 2);
  bytes.writeInt16LE(2000, 4);
  bytes.writeInt16LE(1000, 6);
  const mono = stereoPcm16ToMono(bytes);
  assert.deepEqual([...mono], [0, 1500]);
});

test('only the configured speaker is subscribed and normal logs never contain transcript', async () => {
  const events = [];
  const seen = [];
  const fixture = fakeConnection((userId) =>
    userId === TARGET
      ? Array.from({ length: 6 }, () => stereoChunk(2000))
      : [stereoChunk(2000)],
  );
  const stt = {
    async transcribe(pcm, { signal }) {
      signal.throwIfAborted();
      seen.push(new Int16Array(pcm));
      return '秘密の文字起こし本文';
    },
  };

  const receiver = attachLiveSttReceiver({
    connection: fixture.connection,
    targetUserId: TARGET,
    stt,
    emit: (event) => events.push(event),
    createDecoder: decoderFactory,
  });

  fixture.connection.receiver.speaking.emit('start', OTHER);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(fixture.subscriptions.length, 0);

  fixture.connection.receiver.speaking.emit('start', TARGET);
  await waitFor(() => events.some((event) => event.event === 'stt_completed'));

  assert.equal(fixture.subscriptions.length, 1);
  assert.equal(fixture.subscriptions[0].userId, TARGET);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].length, 960 * 6);
  assert.ok(events.some((event) => event.event === 'utterance_started'));
  assert.ok(events.some((event) => event.event === 'utterance_finished'));
  assert.ok(events.some((event) => event.event === 'stt_started'));
  assert.ok(events.some((event) => event.event === 'stt_completed'));
  assert.equal(
    JSON.stringify(events).includes('秘密の文字起こし本文'),
    false,
  );

  receiver.stop();
});

test('short noise is discarded before STT', async () => {
  const events = [];
  let sttCalls = 0;
  const fixture = fakeConnection(() => [stereoChunk(2000)]);
  const receiver = attachLiveSttReceiver({
    connection: fixture.connection,
    targetUserId: TARGET,
    stt: {
      async transcribe() {
        sttCalls += 1;
        return 'should not happen';
      },
    },
    emit: (event) => events.push(event),
    createDecoder: decoderFactory,
  });

  fixture.connection.receiver.speaking.emit('start', TARGET);
  await waitFor(() => events.some((event) => event.event === 'utterance_short'));

  assert.equal(sttCalls, 0);
  assert.equal(events.some((event) => event.event === 'stt_started'), false);
  receiver.stop();
});

test('explicit transcript debug is opt-in', async () => {
  const events = [];
  const fixture = fakeConnection(() =>
    Array.from({ length: 6 }, () => stereoChunk(2000)),
  );
  const receiver = attachLiveSttReceiver({
    connection: fixture.connection,
    targetUserId: TARGET,
    stt: {
      async transcribe() {
        return '明示デバッグだけに出る本文';
      },
    },
    emit: (event) => events.push(event),
    debugTranscript: true,
    createDecoder: decoderFactory,
  });

  fixture.connection.receiver.speaking.emit('start', TARGET);
  await waitFor(() =>
    events.some((event) => event.event === 'stt_debug_transcript'),
  );

  const debug = events.find((event) => event.event === 'stt_debug_transcript');
  assert.equal(debug.transcript, '明示デバッグだけに出る本文');
  receiver.stop();
});

test('stop aborts an in-flight STT and detaches speaking listener', async () => {
  const events = [];
  let aborted = false;
  const fixture = fakeConnection(() =>
    Array.from({ length: 6 }, () => stereoChunk(2000)),
  );
  const stt = {
    transcribe(_pcm, { signal }) {
      return new Promise((resolve, reject) => {
        const onAbort = () => {
          aborted = true;
          reject(new Error('fixture aborted'));
        };
        if (signal.aborted) onAbort();
        else signal.addEventListener('abort', onAbort, { once: true });
      });
    },
  };
  const receiver = attachLiveSttReceiver({
    connection: fixture.connection,
    targetUserId: TARGET,
    stt,
    emit: (event) => events.push(event),
    createDecoder: decoderFactory,
  });

  fixture.connection.receiver.speaking.emit('start', TARGET);
  await waitFor(() => events.some((event) => event.event === 'stt_started'));
  receiver.stop();
  await waitFor(() => aborted);

  const before = fixture.subscriptions.length;
  fixture.connection.receiver.speaking.emit('start', TARGET);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(fixture.subscriptions.length, before);
});


test('subscription failure is contained as a fixed receive failure event', async () => {
  const events = [];
  const speaking = new EventEmitter();
  const receiver = attachLiveSttReceiver({
    connection: {
      receiver: {
        speaking,
        subscribe() {
          throw new Error('private Discord receive failure');
        },
      },
    },
    targetUserId: TARGET,
    stt: {
      async transcribe() {
        return 'unused';
      },
    },
    emit: (event) => events.push(event),
    createDecoder: decoderFactory,
  });

  speaking.emit('start', TARGET);
  await waitFor(() => events.some((event) => event.event === 'voice_receive_failed'));

  assert.deepEqual(events, [{ event: 'voice_receive_failed' }]);
  receiver.stop();
});


test('utterance above the 10 second bound is dropped before STT', async () => {
  const events = [];
  let sttCalls = 0;
  const fixture = fakeConnection(() =>
    Array.from({ length: 501 }, () => stereoChunk(2000)),
  );
  const receiver = attachLiveSttReceiver({
    connection: fixture.connection,
    targetUserId: TARGET,
    stt: {
      async transcribe() {
        sttCalls += 1;
        return 'unused';
      },
    },
    emit: (event) => events.push(event),
    createDecoder: decoderFactory,
  });

  fixture.connection.receiver.speaking.emit('start', TARGET);
  await waitFor(() =>
    events.some((event) => event.event === 'utterance_too_long'),
  );

  assert.equal(sttCalls, 0);
  assert.equal(events.some((event) => event.event === 'stt_started'), false);
  receiver.stop();
});


test('a new utterance is captured while the previous STT call is still running', async () => {
  const events = [];
  const fixture = fakeConnection(() =>
    Array.from({ length: 6 }, () => stereoChunk(2000)),
  );
  let releaseFirst;
  const firstGate = new Promise((resolve) => {
    releaseFirst = resolve;
  });
  let sttCalls = 0;
  const receiver = attachLiveSttReceiver({
    connection: fixture.connection,
    targetUserId: TARGET,
    stt: {
      async transcribe(_pcm, { signal }) {
        signal.throwIfAborted();
        sttCalls += 1;
        if (sttCalls === 1) await firstGate;
        signal.throwIfAborted();
        return `fixture-${sttCalls}`;
      },
    },
    emit: (event) => events.push(event),
    createDecoder: decoderFactory,
  });

  fixture.connection.receiver.speaking.emit('start', TARGET);
  await waitFor(() =>
    events.filter((event) => event.event === 'stt_started').length === 1,
  );

  fixture.connection.receiver.speaking.emit('start', TARGET);
  await waitFor(() => fixture.subscriptions.length === 2);
  await waitFor(() =>
    events.filter((event) => event.event === 'utterance_finished').length === 2,
  );
  assert.equal(sttCalls, 1);

  releaseFirst();
  await waitFor(() =>
    events.filter((event) => event.event === 'stt_completed').length === 2,
  );
  assert.equal(sttCalls, 2);
  receiver.stop();
});
