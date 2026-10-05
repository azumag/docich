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

async function resolveVoiceChannel(client, config) {
  let guild;
  try {
    guild = await client.guilds.fetch(config.guildId);
  } catch {
    throw new LiveVoiceError('guild_unavailable');
  }

  let channel;
  try {
    channel = await guild.channels.fetch(config.channelId);
  } catch {
    throw new LiveVoiceError('channel_unavailable');
  }

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

async function playTestTone(connection) {
  const player = createAudioPlayer();
  const subscription = connection.subscribe(player);
  if (!subscription) throw new LiveVoiceError('voice_subscription_failed');

  player.play(buildTestResource());
  try {
    await entersState(player, AudioPlayerStatus.Playing, 5_000);
    await entersState(player, AudioPlayerStatus.Idle, 5_000);
  } catch {
    player.stop(true);
    throw new LiveVoiceError('test_tone_failed');
  }
}

export async function runLiveVoice(env = process.env) {
  const config = loadLiveVoiceConfig(env);
  const client = new Client({
    intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
  });

  let connection = null;
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

  const requestStop = () => stopResolve?.({ kind: 'signal' });
  const failRuntime = (code) => {
    if (!stopping) fatalResolve?.({ kind: 'fatal', code });
  };

  const onClientError = () => emit({ event: 'discord_gateway_error' });
  client.on(Events.Error, onClientError);
  process.once('SIGINT', requestStop);
  process.once('SIGTERM', requestStop);

  try {
    const ready = waitClientReady(client);
    // If login itself fails, the pre-registered Ready waiter must not become
    // an unhandled timeout later. It is still awaited on the successful path.
    ready.catch(() => {});
    try {
      await client.login(config.token);
    } catch {
      throw new LiveVoiceError('discord_login_failed');
    }
    await ready;

    const { guild, channel } = await resolveVoiceChannel(client, config);

    connection = joinVoiceChannel({
      guildId: guild.id,
      channelId: channel.id,
      adapterCreator: guild.voiceAdapterCreator,
      selfDeaf: true,
      selfMute: false,
    });

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
          emit({ event: 'voice_recovering' });
          return;
        } catch {
          // Fall through to a bounded explicit rejoin of the configured channel.
        }

        for (let attempt = 1; attempt <= 3 && !stopping; attempt += 1) {
          emit({ event: 'voice_rejoin_attempt', attempt });
          const accepted = connection.rejoin({
            channelId: config.channelId,
            selfDeaf: true,
            selfMute: false,
          });
          if (accepted) {
            try {
              await entersState(connection, VoiceConnectionStatus.Ready, 15_000);
              emit({ event: 'voice_rejoined' });
              return;
            } catch {
              // Bounded retry below.
            }
          }
          await delay(attempt * 1_000);
        }

        if (!stopping) failRuntime('voice_reconnect_failed');
      })().finally(() => {
        recovering = false;
      });
    });

    try {
      await entersState(connection, VoiceConnectionStatus.Ready, 30_000);
    } catch {
      throw new LiveVoiceError('voice_connect_timeout');
    }

    emit({ event: 'voice_connected', selfDeaf: true, daveCapable: true });

    if (config.playTestTone) {
      await playTestTone(connection);
      emit({ event: 'voice_test_tone_completed' });
    }

    const result = await Promise.race([stopSignal, fatalSignal]);
    if (result?.kind === 'fatal') {
      emitError(result.code);
      return 3;
    }
    return 0;
  } finally {
    stopping = true;
    process.off('SIGINT', requestStop);
    process.off('SIGTERM', requestStop);
    client.off(Events.Error, onClientError);

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
