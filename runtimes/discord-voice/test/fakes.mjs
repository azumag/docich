// Synthetic fixtures only. No import of production text Bot or external providers.
export function gate() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release };
}
export function wait(gate, signal) {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => reject(new Error('synthetic_cancel'));
    signal.addEventListener('abort', abort, { once: true });
    gate.promise.then(() => { signal.removeEventListener('abort', abort); resolve(); });
  });
}
export function adapters() {
  const sttInputs = [], requests = [], plays = [], history = new Map();
  const transport = {
    kind: 'fake', stops: 0, playGate: null,
    async connect() { return { botUserId: '999' }; },
    async play(pcm, { signal }) {
      plays.push({ samples: pcm.length, signal });
      if (this.playGate) await wait(this.playGate, signal);
    },
    stop() { this.stops++; },
    disconnect() {},
  };
  const stt = {
    kind: 'fake', gate: null,
    async transcribe(pcm, { scope, signal }) {
      sttInputs.push(pcm); // Test-only references verify runtime scrubbing, never write files.
      if (this.gate) await wait(this.gate, signal);
      return '合成された固定発話';
    },
  };
  const conversation = {
    kind: 'fake', gate: null,
    async reply({ scope, transcript }, { signal }) {
      const key = JSON.stringify([scope.guildId, scope.channelId, scope.userId]);
      requests.push({ scope, context: [...(history.get(key) ?? [])] });
      if (this.gate) await wait(this.gate, signal);
      signal.throwIfAborted();
      history.set(key, [...(history.get(key) ?? []), transcript]);
      return '合成された固定応答';
    },
  };
  const tts = {
    kind: 'fake', gate: null,
    async synthesize(_reply, { signal }) {
      if (this.gate) await wait(this.gate, signal);
      return new Int16Array(960).fill(1000);
    },
  };
  return { transport, stt, conversation, tts, sttInputs, requests, plays, history };
}
