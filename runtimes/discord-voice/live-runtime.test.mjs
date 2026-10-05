import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { VoiceConnectionStatus } from '@discordjs/voice';

import { runLiveVoice } from './live.mjs';

const env = () => ({
  DOCICH_DISCORD_VOICE_ENABLED: '1',
  DOCICH_DISCORD_TOKEN: 'x'.repeat(64),
  DOCICH_DISCORD_VOICE_GUILD_ID: '123456789012345678',
  DOCICH_DISCORD_VOICE_CHANNEL_ID: '223456789012345678',
  DOCICH_DISCORD_VOICE_TEST_TONE: '1',
});

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.fail('timed out waiting for staged live-runtime fixture');
}

function makeHarness(stage) {
  const trace = [];
  const gate = deferred();
  const signalTarget = new EventEmitter();
  let clientDestroyCount = 0;
  let connectionDestroyCount = 0;
  let connection = null;

  class FakeClient extends EventEmitter {
    async login() {
      trace.push('login_start');
      if (stage === 'login') await gate.promise;
      trace.push('login_done');
      return 'ok';
    }

    destroy() {
      clientDestroyCount += 1;
      trace.push('client_destroy');
    }
  }

  const client = new FakeClient();
  const guild = { id: '123456789012345678' };
  const channel = { id: '223456789012345678' };

  const runtimeOps = {
    signalTarget,
    createClient: () => client,
    waitClientReady: async () => {
      trace.push('ready_start');
      if (stage === 'ready') await gate.promise;
      trace.push('ready_done');
    },
    resolveVoiceChannel: async () => {
      trace.push('channel_start');
      if (stage === 'channel') await gate.promise;
      trace.push('channel_done');
      return { guild, channel };
    },
    joinVoice: () => {
      trace.push('join');
      connection = new EventEmitter();
      connection.state = { status: VoiceConnectionStatus.Ready };
      connection.rejoin = () => {
        trace.push('rejoin');
        return true;
      };
      connection.destroy = () => {
        connectionDestroyCount += 1;
        connection.state.status = VoiceConnectionStatus.Destroyed;
        trace.push('connection_destroy');
      };
      return connection;
    },
    waitVoiceReady: async () => {
      trace.push('voice_ready_start');
      if (stage === 'voice_ready') await gate.promise;
      trace.push('voice_ready_done');
    },
    playTestTone: async () => {
      trace.push('tone_start');
      if (stage === 'playback') await gate.promise;
      trace.push('tone_done');
    },
  };

  return {
    trace,
    gate,
    signalTarget,
    runtimeOps,
    getConnection: () => connection,
    getClientDestroyCount: () => clientDestroyCount,
    getConnectionDestroyCount: () => connectionDestroyCount,
  };
}

const cases = [
  {
    stage: 'login',
    pending: (trace) => trace.includes('login_start') && !trace.includes('login_done'),
    forbidden: ['channel_start', 'join', 'tone_start', 'rejoin'],
    joined: false,
  },
  {
    stage: 'ready',
    pending: (trace) =>
      trace.includes('login_done') &&
      trace.includes('ready_start') &&
      !trace.includes('ready_done'),
    forbidden: ['channel_start', 'join', 'tone_start', 'rejoin'],
    joined: false,
  },
  {
    stage: 'channel',
    pending: (trace) => trace.includes('channel_start') && !trace.includes('channel_done'),
    forbidden: ['join', 'tone_start', 'rejoin'],
    joined: false,
  },
  {
    stage: 'voice_ready',
    pending: (trace) =>
      trace.includes('voice_ready_start') && !trace.includes('voice_ready_done'),
    forbidden: ['tone_start', 'rejoin'],
    joined: true,
  },
  {
    stage: 'playback',
    pending: (trace) => trace.includes('tone_start') && !trace.includes('tone_done'),
    forbidden: ['rejoin'],
    joined: true,
  },
];

for (const scenario of cases) {
  test(`SIGINT during ${scenario.stage} stops startup without later join/playback/rejoin`, async () => {
    const harness = makeHarness(scenario.stage);
    const running = runLiveVoice(env(), harness.runtimeOps);

    await waitFor(() => scenario.pending(harness.trace));
    harness.signalTarget.emit('SIGINT');

    const connection = harness.getConnection();
    if (connection) {
      connection.emit(VoiceConnectionStatus.Disconnected);
    }

    harness.gate.resolve();
    const code = await running;
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(code, 0);
    assert.equal(harness.getClientDestroyCount(), 1);
    assert.equal(
      harness.getConnectionDestroyCount(),
      scenario.joined ? 1 : 0,
      `unexpected connection cleanup count: ${harness.trace.join(' -> ')}`,
    );

    for (const event of scenario.forbidden) {
      assert.equal(
        harness.trace.includes(event),
        false,
        `unexpected ${event} after stop: ${harness.trace.join(' -> ')}`,
      );
    }
  });
}


test('receive mode creates STT before login, attaches after voice ready, and stops once', async () => {
  const trace = [];
  const signalTarget = new EventEmitter();

  class FakeClient extends EventEmitter {
    async login() {
      trace.push('login');
      return 'ok';
    }
    destroy() {
      trace.push('client_destroy');
    }
  }

  const connection = new EventEmitter();
  connection.state = { status: VoiceConnectionStatus.Ready };
  connection.destroy = () => {
    connection.state.status = VoiceConnectionStatus.Destroyed;
    trace.push('connection_destroy');
  };
  connection.rejoin = () => true;

  const stt = { transcribe: async () => 'fixture' };
  let receiverStops = 0;
  const receiveEnv = {
    ...env(),
    DOCICH_DISCORD_VOICE_TEST_TONE: '0',
    DOCICH_DISCORD_VOICE_RECEIVE_ENABLED: '1',
    DOCICH_DISCORD_VOICE_RECEIVE_USER_ID: '323456789012345678',
  };

  const running = runLiveVoice(receiveEnv, {
    signalTarget,
    createStt: () => {
      trace.push('create_stt');
      return stt;
    },
    createClient: () => new FakeClient(),
    waitClientReady: async () => {
      trace.push('client_ready');
    },
    resolveVoiceChannel: async () => ({
      guild: { id: receiveEnv.DOCICH_DISCORD_VOICE_GUILD_ID },
      channel: { id: receiveEnv.DOCICH_DISCORD_VOICE_CHANNEL_ID },
    }),
    joinVoice: ({ config }) => {
      trace.push(config.receiveEnabled ? 'join_receive' : 'join_deaf');
      return connection;
    },
    waitVoiceReady: async () => {
      trace.push('voice_ready');
    },
    attachReceiver: ({ targetUserId, stt: receivedStt, debugTranscript }) => {
      assert.equal(targetUserId, receiveEnv.DOCICH_DISCORD_VOICE_RECEIVE_USER_ID);
      assert.equal(receivedStt, stt);
      assert.equal(debugTranscript, false);
      trace.push('receiver_attached');
      return {
        stop() {
          receiverStops += 1;
          trace.push('receiver_stopped');
        },
      };
    },
  });

  await waitFor(() => trace.includes('receiver_attached'));
  assert.ok(trace.indexOf('create_stt') < trace.indexOf('login'));
  assert.ok(trace.indexOf('voice_ready') < trace.indexOf('receiver_attached'));
  assert.ok(trace.includes('join_receive'));

  signalTarget.emit('SIGINT');
  assert.equal(await running, 0);
  assert.equal(receiverStops, 1);
  assert.ok(trace.indexOf('receiver_stopped') < trace.indexOf('connection_destroy'));
});
