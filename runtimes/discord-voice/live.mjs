import { getCiphers } from 'node:crypto';
import { once } from 'node:events';
import { Readable } from 'node:stream';
import { pathToFileURL } from 'node:url';

import {
  AudioPlayerStatus,
  StreamType,
  VoiceConnectionStatus,
  createAudioPlayer,
  createAudioResource,
  entersState,
  joinVoiceChannel,
} from '@discordjs/voice';
import {
  ChannelType,
  Client,
  Events,
  GatewayIntentBits,
  PermissionFlagsBits,
} from 'discord.js';

import { CloudflareConversationClient } from './cloudflare-conversation.mjs';
import { CloudflareWhisperSTT } from './cloudflare-stt.mjs';
import { attachLiveSttReceiver } from './live-receive.mjs';
import { createLivePlayback } from './live-playback.mjs';
import { createLiveVoicevoxTTS } from './live-voicevox.mjs';
import { PCM } from './runtime.mjs';
import { LiveVoiceError, loadLiveVoiceConfig, makeStereoTestTone } from './live-support.mjs';

const emit = (record) => process.stdout.write(JSON.stringify(record) + '\n');
const emitError = (code) =>
  process.stderr.write(JSON.stringify({ event: 'live_voice_error', code }) + '\n');

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function timeoutAfter(ms, code) {
  return new Promise((_, reject) => {
    const timer = setTimeout(() => reject(new LiveVoiceError(code)), ms);
    timer.unref?.();
  });
}

async function waitClientReady(client) {
  const ready = once(client, Events.ClientReady);
  try {
    await Promise.race([ready, timeoutAfter(30_000, 'discord_ready_timeout')]);
  } catch {
    throw new LiveVoiceError('discord_ready_timeout');
  }
}

function buildTestResource() {
  const pcm = makeStereoTestTone();
  return createAudioResource(Readable.from([pcm]), { inputType: StreamType.Raw });
}

export async function checkLiveDependencies() {
  if (!getCiphers().includes('aes-256-gcm')) {
    throw new LiveVoiceError('missing_aes_256_gcm');
  }
  const resource = buildTestResource();
  if (!resource?.playStream) throw new LiveVoiceError('opus_unavailable');
  emit({ event: 'live_voice_check_ok', daveCapable: true, opus: true, rawPcm: true });
}

async function resolveVoiceChannel(client, config, isStopping = () => false) {
  let guild;
  try {
    guild = await client.guilds.fetch(config.guildId);
  } catch {
    throw new LiveVoiceError('guild_unavailable');
  }
  if (isStopping()) return null;

  let channel;
  try {
    channel = await guild.channels.fetch(config.channelId);
  } catch {
    throw new LiveVoiceError('channel_unavailable');
  }
  if (isStopping()) return null;

  if (!channel || channel.type !== ChannelType.GuildVoice) {
    throw new LiveVoiceError('channel_not_voice');
  }

  let member = guild.members.me;
  if (!member) {
    try {
      member = await guild.members.fetchMe();
    } catch {
      throw new LiveVoiceError('bot_member_unavailable');
    }
    if (isStopping()) return null;
  }

  const permissions = channel.permissionsFor(member);
  if (
    !permissions?.has(PermissionFlagsBits.Connect) ||
    !permissions.has(PermissionFlagsBits.Speak)
  ) {
    throw new LiveVoiceError('missing_voice_permissions');
  }

  return { guild, channel };
}

async function playTestTone(connection, isStopping = () => false) {
  if (isStopping()) return;

  const player = createAudioPlayer();
  const subscription = connection.subscribe(player);
  if (!subscription) throw new LiveVoiceError('voice_subscription_failed');
  if (isStopping()) {
    player.stop(true);
    return;
  }

  player.play(buildTestResource());
  try {
    await entersState(player, AudioPlayerStatus.Playing, 5_000);
    if (isStopping()) {
      player.stop(true);
      return;
    }
    await entersState(player, AudioPlayerStatus.Idle, 5_000);
  } catch {
    player.stop(true);
    throw new LiveVoiceError('test_tone_failed');
  }
}

function defaultRuntimeOps() {
  return {
    createClient: () =>
      new Client({
        intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
      }),
    waitClientReady,
    resolveVoiceChannel,
    joinVoice: ({ guild, channel, config }) =>
      joinVoiceChannel({
        guildId: guild.id,
        channelId: channel.id,
        adapterCreator: guild.voiceAdapterCreator,
        selfDeaf: !config.receiveEnabled,
        selfMute: false,
      }),
    waitVoiceReady: (connection) =>
      entersState(connection, VoiceConnectionStatus.Ready, 30_000),
    createStt: (env) => new CloudflareWhisperSTT({ env }),
    createConversation: (env) => new CloudflareConversationClient({ env }),
    createTts: (env, config) =>
      createLiveVoicevoxTTS(env, {
        allowLoopback: config.voicevoxAllowLoopback,
      }),
    createPlayback: (connection) => createLivePlayback(connection),
    attachReceiver: (options) => attachLiveSttReceiver(options),
    playTestTone,
    signalTarget: process,
  };
}

export async function runLiveVoice(env = process.env, runtimeOps = {}) {
  const config = loadLiveVoiceConfig(env);
  const ops = { ...defaultRuntimeOps(), ...runtimeOps };
  const signalTarget = ops.signalTarget;
  const stt = config.receiveEnabled ? ops.createStt(env) : null;
  const conversation = config.conversationEnabled
    ? ops.createConversation(env)
    : null;
  const tts = config.ttsEnabled ? ops.createTts(env, config) : null;
  const client = ops.createClient();

  let connection = null;
  let liveReceiver = null;
  let livePlayback = null;
  let activeTurn = null;
  let stopping = false;
  let recovering = false;
  let stopResolve;
  let fatalResolve;
  const stopSignal = new Promise((resolve) => {
    stopResolve = resolve;
  });
  const fatalSignal = new Promise((resolve) => {
    fatalResolve = resolve;
  });
  const STOPPED = Symbol('stopped');

  const requestStop = () => {
    if (stopping) return;
    stopping = true;
    stopResolve?.({ kind: 'signal' });
  };
  const failRuntime = (code) => {
    if (!stopping) fatalResolve?.({ kind: 'fatal', code });
  };
  const awaitStage = async (stage) => {
    if (stopping) return STOPPED;
    const settled = Promise.resolve(stage).then(
      (value) => ({ kind: 'value', value }),
      (error) => ({ kind: 'error', error }),
    );
    const result = await Promise.race([settled, stopSignal]);
    if (result?.kind === 'signal') return STOPPED;
    if (result?.kind === 'error') throw result.error;
    return result.value;
  };
  const stoppedAfter = (value) => value === STOPPED || stopping;
  const runBoundedStage = async (parentSignal, timeoutMs, operation) => {
    const controller = new AbortController();
    const onParentAbort = () => controller.abort();
    parentSignal.addEventListener('abort', onParentAbort, { once: true });
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    timer.unref?.();
    try {
      parentSignal.throwIfAborted();
      return await operation(controller.signal);
    } finally {
      clearTimeout(timer);
      parentSignal.removeEventListener('abort', onParentAbort);
      controller.abort();
    }
  };

  const onClientError = () => emit({ event: 'discord_gateway_error' });
  client.on(Events.Error, onClientError);
  signalTarget.once('SIGINT', requestStop);
  signalTarget.once('SIGTERM', requestStop);

  try {
    const ready = ops.waitClientReady(client);
    // If login or shutdown wins first, the pre-registered Ready waiter may settle
    // later. Always observe it so a late timeout/rejection is not unhandled.
    Promise.resolve(ready).catch(() => {});

    let loginResult;
    try {
      loginResult = await awaitStage(client.login(config.token));
    } catch {
      if (stopping) return 0;
      throw new LiveVoiceError('discord_login_failed');
    }
    if (stoppedAfter(loginResult)) return 0;

    let readyResult;
    try {
      readyResult = await awaitStage(ready);
    } catch (error) {
      if (stopping) return 0;
      throw error;
    }
    if (stoppedAfter(readyResult)) return 0;

    let resolved;
    try {
      resolved = await awaitStage(
        ops.resolveVoiceChannel(client, config, () => stopping),
      );
    } catch (error) {
      if (stopping) return 0;
      throw error;
    }
    if (stoppedAfter(resolved) || !resolved) return 0;

    const { guild, channel } = resolved;
    if (stopping) return 0;
    connection = ops.joinVoice({ guild, channel, config });

    connection.on('error', () => emit({ event: 'voice_transport_error' }));

    connection.on(VoiceConnectionStatus.Disconnected, () => {
      if (stopping || recovering) return;
      recovering = true;
      void (async () => {
        emit({ event: 'voice_disconnected' });
        try {
          await Promise.race([
            entersState(connection, VoiceConnectionStatus.Signalling, 5_000),
            entersState(connection, VoiceConnectionStatus.Connecting, 5_000),
          ]);
          if (stopping) return;
          emit({ event: 'voice_recovering' });
          return;
        } catch {
          // Fall through to a bounded explicit rejoin of the configured channel.
        }

        for (let attempt = 1; attempt <= 3 && !stopping; attempt += 1) {
          emit({ event: 'voice_rejoin_attempt', attempt });
          const accepted = connection.rejoin({
            channelId: config.channelId,
            selfDeaf: !config.receiveEnabled,
            selfMute: false,
          });
          if (accepted) {
            try {
              await entersState(connection, VoiceConnectionStatus.Ready, 15_000);
              if (stopping) return;
              emit({ event: 'voice_rejoined' });
              return;
            } catch {
              // Bounded retry below.
            }
          }
          if (stopping) return;
          await delay(attempt * 1_000);
        }

        if (!stopping) failRuntime('voice_reconnect_failed');
      })().finally(() => {
        recovering = false;
      });
    });

    let voiceReadyResult;
    try {
      voiceReadyResult = await awaitStage(ops.waitVoiceReady(connection));
    } catch {
      if (stopping) return 0;
      throw new LiveVoiceError('voice_connect_timeout');
    }
    if (stoppedAfter(voiceReadyResult)) return 0;

    emit({
      event: 'voice_connected',
      selfDeaf: !config.receiveEnabled,
      daveCapable: true,
      receiveEnabled: config.receiveEnabled,
    });

    if (config.playTestTone) {
      let toneResult;
      try {
        toneResult = await awaitStage(
          ops.playTestTone(connection, () => stopping),
        );
      } catch (error) {
        if (stopping) return 0;
        throw error;
      }
      if (stoppedAfter(toneResult)) return 0;
      emit({ event: 'voice_test_tone_completed' });
    }

    if (config.ttsEnabled) {
      livePlayback = ops.createPlayback(connection);
      emit({ event: 'voice_tts_enabled' });
    }

    if (config.receiveEnabled) {
      const scope = Object.freeze({
        guildId: config.guildId,
        channelId: config.channelId,
        userId: config.receiveUserId,
      });

      const onTranscript = conversation
        ? async (transcript, { signal }) => {
            const turnController = new AbortController();
            const onParentAbort = () => turnController.abort();
            signal.addEventListener('abort', onParentAbort, { once: true });
            const turn = { controller: turnController, interrupted: false };
            activeTurn = turn;

            let generated;
            try {
              emit({ event: 'llm_started' });
              try {
                generated = await runBoundedStage(
                  turnController.signal,
                  10_000,
                  (stageSignal) =>
                    conversation.generate(transcript, {
                      ...scope,
                      signal: stageSignal,
                    }),
                );
                if (turnController.signal.aborted || stopping) {
                  emit({ event: 'llm_cancelled' });
                  return;
                }
                emit({ event: 'llm_completed' });
                if (config.replyDebug) {
                  emit({ event: 'llm_debug_reply', reply: generated.reply });
                }
              } catch {
                emit({
                  event: turnController.signal.aborted || stopping
                    ? 'llm_cancelled'
                    : 'llm_failed',
                });
                return;
              }

              if (!tts || !livePlayback) return;

              let pcm;
              let outputStage = 'tts';
              try {
                emit({ event: 'tts_started' });
                pcm = await runBoundedStage(
                  turnController.signal,
                  30_000,
                  (stageSignal) =>
                    tts.synthesize(generated.reply, {
                      format: PCM,
                      scope,
                      signal: stageSignal,
                    }),
                );
                if (turnController.signal.aborted || stopping) {
                  emit({ event: 'tts_cancelled' });
                  return;
                }
                emit({ event: 'tts_completed' });

                outputStage = 'playback';
                emit({ event: 'playback_started' });
                await runBoundedStage(
                  turnController.signal,
                  35_000,
                  (stageSignal) =>
                    livePlayback.play(pcm, { signal: stageSignal }),
                );
                if (turnController.signal.aborted || stopping) {
                  emit({
                    event: turn.interrupted
                      ? 'playback_interrupted'
                      : 'playback_cancelled',
                  });
                  return;
                }
                emit({ event: 'playback_completed' });
              } catch {
                const interrupted = turn.interrupted;
                const cancelled = turnController.signal.aborted || stopping;
                if (pcm) {
                  try {
                    Int16Array.prototype.fill.call(pcm, 0);
                  } catch {
                    // Returned PCM ownership cleanup is best-effort.
                  }
                }
                if (!generated) return;
                if (cancelled && interrupted && outputStage === 'playback') {
                  emit({ event: 'playback_interrupted' });
                } else if (cancelled && outputStage === 'tts') {
                  emit({ event: 'tts_cancelled' });
                } else if (cancelled) {
                  emit({ event: 'playback_cancelled' });
                } else {
                  emit({
                    event: outputStage === 'playback'
                      ? 'playback_failed'
                      : 'tts_failed',
                  });
                }
                return;
              } finally {
                if (pcm) {
                  try {
                    Int16Array.prototype.fill.call(pcm, 0);
                  } catch {
                    // Returned PCM ownership cleanup is best-effort.
                  }
                }
              }

              if (activeTurn === turn) activeTurn = null;

              try {
                emit({ event: 'memory_commit_started' });
                await runBoundedStage(
                  signal,
                  5_000,
                  (stageSignal) =>
                    conversation.commit(
                      {
                        turnId: generated.turnId,
                        transcript,
                        reply: generated.reply,
                      },
                      { ...scope, signal: stageSignal },
                    ),
                );
                emit({ event: 'memory_commit_completed' });
              } catch {
                emit({
                  event: signal.aborted || stopping
                    ? 'memory_commit_cancelled'
                    : 'memory_commit_failed',
                });
              }
            } finally {
              signal.removeEventListener('abort', onParentAbort);
              turnController.abort();
              if (activeTurn === turn) activeTurn = null;
            }
          }
        : null;

      const onTargetSpeechStart = config.ttsEnabled
        ? () => {
            if (activeTurn && !activeTurn.controller.signal.aborted) {
              activeTurn.interrupted = true;
              activeTurn.controller.abort();
              emit({ event: 'turn_interrupted' });
            }
          }
        : null;

      liveReceiver = ops.attachReceiver({
        connection,
        targetUserId: config.receiveUserId,
        stt,
        emit,
        debugTranscript: config.transcriptDebug,
        onTranscript,
        onTargetSpeechStart,
      });
      emit({ event: 'voice_receive_enabled' });
      if (conversation) emit({ event: 'voice_conversation_enabled' });
    }

    const result = await Promise.race([stopSignal, fatalSignal]);
    if (result?.kind === 'fatal') {
      emitError(result.code);
      return 3;
    }
    return 0;
  } finally {
    stopping = true;
    signalTarget.off('SIGINT', requestStop);
    signalTarget.off('SIGTERM', requestStop);
    client.off(Events.Error, onClientError);

    try {
      liveReceiver?.stop();
    } catch {
      // Fixed shutdown path.
    }
    if (activeTurn && !activeTurn.controller.signal.aborted) {
      activeTurn.controller.abort();
    }
    try {
      livePlayback?.close();
    } catch {
      // Fixed shutdown path.
    }

    if (connection && connection.state.status !== VoiceConnectionStatus.Destroyed) {
      try {
        connection.destroy();
      } catch {
        // Fixed shutdown path: no provider/internal error text is exposed.
      }
    }
    client.destroy();
    emit({ event: 'voice_stopped' });
  }
}

export async function main(args = process.argv.slice(2)) {
  try {
    if (args.length === 1 && args[0] === '--check') {
      await checkLiveDependencies();
      return 0;
    }
    if (args.length === 1 && args[0] === '--live') {
      return await runLiveVoice();
    }
    emitError('invalid_mode');
    return 2;
  } catch (error) {
    emitError(error instanceof LiveVoiceError ? error.code : 'live_runtime_failed');
    return 2;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main();
}
