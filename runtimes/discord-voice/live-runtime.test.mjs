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
  const conversation = {
    async generate(transcript, context) {
      assert.equal(transcript, 'fixture transcript');
      assert.equal(context.guildId, receiveEnv.DOCICH_DISCORD_VOICE_GUILD_ID);
      assert.equal(context.channelId, receiveEnv.DOCICH_DISCORD_VOICE_CHANNEL_ID);
      assert.equal(context.userId, receiveEnv.DOCICH_DISCORD_VOICE_RECEIVE_USER_ID);
      context.signal.throwIfAborted();
      trace.push('conversation_generate');
      return { turnId: 'fixture-turn', reply: 'fixture reply' };
    },
  };
  let receiverStops = 0;
  let transcriptHandler = null;
  const receiveEnv = {
    ...env(),
    DOCICH_DISCORD_VOICE_TEST_TONE: '0',
    DOCICH_DISCORD_VOICE_RECEIVE_ENABLED: '1',
    DOCICH_DISCORD_VOICE_RECEIVE_USER_ID: '323456789012345678',
    DOCICH_DISCORD_VOICE_CONVERSATION_ENABLED: '1',
  };

  const running = runLiveVoice(receiveEnv, {
    signalTarget,
    createStt: () => {
      trace.push('create_stt');
      return stt;
    },
    createConversation: () => {
      trace.push('create_conversation');
      return conversation;
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
    attachReceiver: ({
      targetUserId,
      stt: receivedStt,
      debugTranscript,
      onTranscript,
      onTargetSpeechStart,
    }) => {
      assert.equal(targetUserId, receiveEnv.DOCICH_DISCORD_VOICE_RECEIVE_USER_ID);
      assert.equal(receivedStt, stt);
      assert.equal(debugTranscript, false);
      assert.equal(typeof onTranscript, 'function');
      assert.equal(onTargetSpeechStart, null);
      transcriptHandler = onTranscript;
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
  assert.ok(trace.indexOf('create_conversation') < trace.indexOf('login'));
  assert.ok(trace.indexOf('voice_ready') < trace.indexOf('receiver_attached'));
  assert.ok(trace.includes('join_receive'));

  const transcriptController = new AbortController();
  await transcriptHandler('fixture transcript', { signal: transcriptController.signal });
  assert.ok(trace.includes('conversation_generate'));

  signalTarget.emit('SIGINT');
  assert.equal(await running, 0);
  assert.equal(receiverStops, 1);
  assert.ok(trace.indexOf('receiver_stopped') < trace.indexOf('connection_destroy'));
});


test('TTS mode commits memory only after successful playback', async () => {
  const trace = [];
  const signalTarget = new EventEmitter();
  let transcriptHandler = null;
  let pcmReference = null;

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

  const liveEnv = {
    ...env(),
    DOCICH_DISCORD_VOICE_TEST_TONE: '0',
    DOCICH_DISCORD_VOICE_RECEIVE_ENABLED: '1',
    DOCICH_DISCORD_VOICE_RECEIVE_USER_ID: '323456789012345678',
    DOCICH_DISCORD_VOICE_CONVERSATION_ENABLED: '1',
    DOCICH_DISCORD_VOICE_TTS_ENABLED: '1',
  };

  const running = runLiveVoice(liveEnv, {
    signalTarget,
    createStt: () => ({ transcribe: async () => 'fixture' }),
    createConversation: () => ({
      async generate(transcript, context) {
        trace.push('generate');
        assert.equal(transcript, 'fixture transcript');
        context.signal.throwIfAborted();
        return { turnId: 'turn-success', reply: '返答です' };
      },
      async commit(turn, context) {
        trace.push('commit');
        assert.deepEqual(turn, {
          turnId: 'turn-success',
          transcript: 'fixture transcript',
          reply: '返答です',
        });
        context.signal.throwIfAborted();
        return 'committed';
      },
    }),
    createTts: () => ({
      async synthesize(reply, context) {
        trace.push('tts');
        assert.equal(reply, '返答です');
        context.signal.throwIfAborted();
        pcmReference = new Int16Array(960).fill(1500);
        return pcmReference;
      },
    }),
    createPlayback: () => ({
      async play(pcm, { signal }) {
        trace.push('playback');
        assert.equal(pcm, pcmReference);
        signal.throwIfAborted();
      },
      close() {
        trace.push('playback_close');
      },
    }),
    createClient: () => new FakeClient(),
    waitClientReady: async () => {},
    resolveVoiceChannel: async () => ({
      guild: { id: liveEnv.DOCICH_DISCORD_VOICE_GUILD_ID },
      channel: { id: liveEnv.DOCICH_DISCORD_VOICE_CHANNEL_ID },
    }),
    joinVoice: () => connection,
    waitVoiceReady: async () => {},
    attachReceiver: ({ onTranscript, onTargetSpeechStart }) => {
      assert.equal(typeof onTranscript, 'function');
      assert.equal(typeof onTargetSpeechStart, 'function');
      transcriptHandler = onTranscript;
      trace.push('receiver_attached');
      return {
        stop() {
          trace.push('receiver_stopped');
        },
      };
    },
  });

  await waitFor(() => trace.includes('receiver_attached'));
  await transcriptHandler('fixture transcript', {
    signal: new AbortController().signal,
  });

  assert.ok(trace.indexOf('generate') < trace.indexOf('tts'));
  assert.ok(trace.indexOf('tts') < trace.indexOf('playback'));
  assert.ok(trace.indexOf('playback') < trace.indexOf('commit'));
  assert.ok(pcmReference.every((sample) => sample === 0));

  signalTarget.emit('SIGINT');
  assert.equal(await running, 0);
  assert.ok(trace.indexOf('playback_close') < trace.indexOf('connection_destroy'));
});

test('barge-in during playback cancels output and never commits memory', async () => {
  const trace = [];
  const signalTarget = new EventEmitter();
  let transcriptHandler = null;
  let speechHandler = null;
  let playbackStarted = false;
  let commitCalls = 0;

  class FakeClient extends EventEmitter {
    async login() {
      return 'ok';
    }
    destroy() {}
  }

  const connection = new EventEmitter();
  connection.state = { status: VoiceConnectionStatus.Ready };
  connection.destroy = () => {
    connection.state.status = VoiceConnectionStatus.Destroyed;
  };
  connection.rejoin = () => true;

  const liveEnv = {
    ...env(),
    DOCICH_DISCORD_VOICE_TEST_TONE: '0',
    DOCICH_DISCORD_VOICE_RECEIVE_ENABLED: '1',
    DOCICH_DISCORD_VOICE_RECEIVE_USER_ID: '323456789012345678',
    DOCICH_DISCORD_VOICE_CONVERSATION_ENABLED: '1',
    DOCICH_DISCORD_VOICE_TTS_ENABLED: '1',
  };

  const running = runLiveVoice(liveEnv, {
    signalTarget,
    createStt: () => ({ transcribe: async () => 'fixture' }),
    createConversation: () => ({
      async generate(_transcript, { signal }) {
        signal.throwIfAborted();
        return { turnId: 'turn-interrupt', reply: '長めの返答です' };
      },
      async commit() {
        commitCalls += 1;
        return 'committed';
      },
    }),
    createTts: () => ({
      async synthesize(_reply, { signal }) {
        signal.throwIfAborted();
        return new Int16Array(960).fill(1000);
      },
    }),
    createPlayback: () => ({
      play(_pcm, { signal }) {
        playbackStarted = true;
        trace.push('playback_started_fixture');
        return new Promise((resolve, reject) => {
          const onAbort = () => reject(new Error('fixture playback aborted'));
          if (signal.aborted) onAbort();
          else signal.addEventListener('abort', onAbort, { once: true });
        });
      },
      close() {},
    }),
    createClient: () => new FakeClient(),
    waitClientReady: async () => {},
    resolveVoiceChannel: async () => ({
      guild: { id: liveEnv.DOCICH_DISCORD_VOICE_GUILD_ID },
      channel: { id: liveEnv.DOCICH_DISCORD_VOICE_CHANNEL_ID },
    }),
    joinVoice: () => connection,
    waitVoiceReady: async () => {},
    attachReceiver: ({ onTranscript, onTargetSpeechStart }) => {
      transcriptHandler = onTranscript;
      speechHandler = onTargetSpeechStart;
      return { stop() {} };
    },
  });

  await waitFor(() => transcriptHandler !== null && speechHandler !== null);
  const turn = transcriptHandler('fixture transcript', {
    signal: new AbortController().signal,
  });
  await waitFor(() => playbackStarted);
  speechHandler();
  await turn;

  assert.equal(commitCalls, 0);

  signalTarget.emit('SIGINT');
  assert.equal(await running, 0);
});
