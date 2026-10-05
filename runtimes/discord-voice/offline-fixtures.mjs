/** Fixed synthetic providers. No external requests, input audio, files or logging. */
export function syntheticWav() {
  const bytes = new Uint8Array(44 + 960 * 2), view = new DataView(bytes.buffer);
  const tag = (p, value) => bytes.set(new TextEncoder().encode(value), p);
  tag(0, 'RIFF'); view.setUint32(4, bytes.length - 8, true); tag(8, 'WAVE');
  tag(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, 48000, true); view.setUint32(28, 96000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); tag(36, 'data'); view.setUint32(40, 1920, true);
  for (let p = 44; p < bytes.length; p += 2) view.setInt16(p, 1200, true);
  return bytes;
}
export function offlineFixtures() {
  return {
    kind: 'fake',
    async connect(_channel, { signal }) { signal.throwIfAborted(); return { botUserId: '999' }; },
    async transcribe(_pcm, { signal }) { signal.throwIfAborted(); return '合成された固定発話'; },
    async model(_model, _input) { return { choices: [{ message: { content: '合成fixtureの応答です。' }, finish_reason: 'stop' }] }; },
    async request({ url, signal }) {
      signal.throwIfAborted();
      const body = url.includes('/audio_query?') ? new TextEncoder().encode(JSON.stringify({
        accent_phrases: [], speedScale: 1, pitchScale: 0,
      })) : syntheticWav();
      return { status: 200, body };
    },
    async play(_pcm, { signal }) { signal.throwIfAborted(); },
    stop() {}, disconnect() {},
  };
}
