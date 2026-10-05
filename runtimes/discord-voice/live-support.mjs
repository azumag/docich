export class LiveVoiceError extends Error {
  constructor(code) {
    super(code);
    this.name = 'LiveVoiceError';
    this.code = code;
  }
}

const fail = (code) => {
  throw new LiveVoiceError(code);
};

const validSnowflake = (value) =>
  typeof value === 'string' &&
  /^[1-9][0-9]{0,19}$/.test(value) &&
  BigInt(value) < 2n ** 64n;

const validToken = (value) =>
  typeof value === 'string' &&
  value.length >= 20 &&
  value.length <= 4096 &&
  !/\s/.test(value);

const boolFlag = (value, fallback = false) => {
  if (value === undefined || value === '') return fallback;
  if (value === '0') return false;
  if (value === '1') return true;
  fail('invalid_config');
};

export function loadLiveVoiceConfig(env = process.env) {
  if (env.DOCICH_DISCORD_VOICE_ENABLED !== '1') fail('live_disabled');

  const token = env.DOCICH_DISCORD_TOKEN;
  const guildId = env.DOCICH_DISCORD_VOICE_GUILD_ID;
  const channelId = env.DOCICH_DISCORD_VOICE_CHANNEL_ID;

  if (!validToken(token) || !validSnowflake(guildId) || !validSnowflake(channelId)) {
    fail('invalid_config');
  }

  return Object.freeze({
    token,
    guildId,
    channelId,
    playTestTone: boolFlag(env.DOCICH_DISCORD_VOICE_TEST_TONE, false),
  });
}

export function makeStereoTestTone({
  durationMs = 700,
  frequencyHz = 660,
  sampleRate = 48_000,
  amplitude = 0.12,
} = {}) {
  if (
    !Number.isInteger(durationMs) ||
    durationMs < 100 ||
    durationMs > 2_000 ||
    !Number.isFinite(frequencyHz) ||
    frequencyHz < 100 ||
    frequencyHz > 2_000 ||
    sampleRate !== 48_000 ||
    !Number.isFinite(amplitude) ||
    amplitude <= 0 ||
    amplitude > 0.25
  ) {
    fail('invalid_tone_config');
  }

  const frames = Math.round((sampleRate * durationMs) / 1_000);
  const output = Buffer.alloc(frames * 2 * Int16Array.BYTES_PER_ELEMENT);
  const fadeFrames = Math.max(1, Math.round(sampleRate * 0.015));

  for (let frame = 0; frame < frames; frame += 1) {
    const attack = Math.min(1, frame / fadeFrames);
    const release = Math.min(1, (frames - 1 - frame) / fadeFrames);
    const envelope = Math.max(0, Math.min(attack, release));
    const sample = Math.round(
      32_767 * amplitude * envelope * Math.sin((2 * Math.PI * frequencyHz * frame) / sampleRate),
    );
    const offset = frame * 4;
    output.writeInt16LE(sample, offset);
    output.writeInt16LE(sample, offset + 2);
  }

  return output;
}
