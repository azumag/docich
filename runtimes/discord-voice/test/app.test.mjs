import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { OfflineVoiceApp } from '../app.mjs';
import { offlineFixtures, syntheticWav } from '../offline-fixtures.mjs';
import { command } from '../cli.mjs';

const channel = { guildId: '1', channelId: '2' };
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };

for (const reply of [{ content: '', finish_reason: 'stop' }, { content: 'partial', finish_reason: 'length' }]) {
  test(`leave prevents a model retry after a late ${reply.finish_reason} response`, async (t) => {
    const blocked = deferred();
    let calls = 0;
    const f = await fixture(t, { model: async () => {
      calls++;
      if (calls === 1) return blocked.promise;
      return { choices: [{ message: { content: 'retry result' }, finish_reason: 'stop' }] };
    } });
    f.utterance('cancelled');
    await tick();
    assert.equal(calls, 1);
    await f.app.leave();
    blocked.resolve({ choices: [{ message: { content: reply.content }, finish_reason: reply.finish_reason }] });
    await tick();
    assert.equal(calls, 1);
    assert.equal(f.app.status().joined, false);
    assert.equal(f.app.status().remembered, 0);
    assert.equal(f.http.length, 0);
    assert.equal(f.pcm.length, 0);
  });
}

async function fixture(t, overrides = {}, options = {}) {
  const events = [], inputs = [], http = [], pcm = [];
  const base = offlineFixtures();
  const fixtures = { ...base,
    model: async (...args) => { inputs.push(args[1]); return base.model(...args); },
    request: async (r) => { http.push(r); return base.request(r); },
    play: async (p, c) => { pcm.push(p); return base.play(p, c); }, ...overrides };
  const app = new OfflineVoiceApp({ persona: '合成人格fixture', fixtures, log: (record) => events.push(record), ...options });
  t.after(() => app.leave()); await app.join(channel);
  const arm = (turnId, userId = '3', flags = { receive: true, respond: true }) => app.activate({ userId, turnId, ...flags });
  const utterance = (turnId, userId = '3') => { arm(turnId, userId); app.receiveSynthetic({ userId }); return app.endSynthetic({ userId }); };
  return { app, arm, utterance, events, inputs, http, pcm };
}
test('joined app is default off; activation and explicit receive end run core -> VOICEVOX -> playback', async (t) => {
  const f = await fixture(t);
  assert.equal(f.app.receiveSynthetic({ userId: '3' }), 'disabled'); assert.equal(f.inputs.length, 0);
  assert.equal(f.arm('one'), 'armed'); assert.equal(f.app.receiveSynthetic({ userId: '3' }), 'capturing');
  assert.equal(f.inputs.length, 0); assert.equal(f.app.endSynthetic({ userId: '3' }), 'queued'); await f.app.idle();
  assert.equal(f.app.status().completed, 1); assert.equal(f.app.status().remembered, 1);
  assert.match(f.inputs[0].messages[0].content, /^合成人格fixture/); assert.equal(f.http.length, 2);
  assert.match(f.http[0].url, /^http:\/\/windows.example:50021\/audio_query/); assert.match(f.http[1].url, /\/synthesis\?speaker=3$/);
  assert.equal(f.pcm[0].every((n) => n === 0), true); assert.equal(f.app.receiveSynthetic({ userId: '3' }), 'disabled');
  assert.ok(f.events.every((record) => Object.keys(record).join() === 'event'));
});
test('session recall separates speakers; dedup and reconnect require fresh activation and erase memory', async (t) => {
  const f = await fixture(t); f.utterance('one'); await f.app.idle();
  assert.equal(f.arm('one'), 'duplicate'); f.utterance('two'); await f.app.idle();
  assert.equal(f.inputs[1].messages.length, 4); f.utterance('other', '4'); await f.app.idle();
  assert.equal(f.inputs[2].messages.length, 2); assert.equal(f.app.status().remembered, 3);
  await f.app.leave(); assert.equal(f.app.status().remembered, 0); assert.equal(f.app.status().pending, 0);
  await f.app.join(channel); assert.equal(f.app.receiveSynthetic({ userId: '3' }), 'disabled');
  f.utterance('one'); await f.app.idle(); assert.equal(f.inputs.at(-1).messages.length, 2);
});
test('capture-only, own bot and inactive scopes never reach the providers', async (t) => {
  const f = await fixture(t);
  assert.equal(f.arm('off', '3', {}), 'disabled'); assert.equal(f.arm('bot', '999'), 'ignored');
  f.arm('capture', '3', { receive: true }); f.app.receiveSynthetic({ userId: '3' });
  assert.equal(f.app.endSynthetic({ userId: '3' }), 'response_disabled');
  assert.equal(f.app.receiveSynthetic({ userId: '4' }), 'disabled'); await f.app.idle(); assert.equal(f.inputs.length, 0);
});
test('memory commits only after playback acknowledgement and cancel prevents recall', async (t) => {
  const wait = deferred(); const f = await fixture(t, { play: async (_pcm, { signal }) => {
    await Promise.race([wait.promise, new Promise((_, reject) => signal.addEventListener('abort', () => reject(new Error('fixture')), { once: true }))]);
  } });
  f.utterance('one'); await tick(); assert.equal(f.app.status().phase, 'playback'); assert.equal(f.app.status().remembered, 0);
  f.app.cancel({ userId: '3' }); await f.app.idle(); assert.equal(f.app.status().remembered, 0); assert.equal(f.app.status().pending, 0);
  wait.resolve(); f.utterance('two'); await f.app.idle(); assert.equal(f.inputs.at(-1).messages.length, 2);
  assert.equal(f.app.status().remembered, 1);
});
for (const stage of ['transcribe', 'model', 'request', 'play']) {
  test(`leave/cancel ${stage} clears state and ignores uncooperative late result`, async (t) => {
    const wait = deferred(); const f = await fixture(t, { [stage]: async () => wait.promise });
    f.utterance('blocked'); await tick(); await f.app.leave(); assert.equal(f.app.status().pending, 0); assert.equal(f.app.status().remembered, 0);
    let result;
    if (stage === 'transcribe') result = 'late fixture';
    if (stage === 'model') result = { choices: [{ message: { content: 'late fixture' } }] };
    if (stage === 'request') result = { status: 200, body: syntheticWav() };
    wait.resolve(result); await tick(); await f.app.join(channel);
    assert.equal(f.app.status().completed, 0); assert.equal(f.app.status().remembered, 0);
    if (stage === 'request') assert.ok(result.body.every((v) => v === 0));
  });
}
test('receive cancellation discards unfinished utterance; barge-in cancels active playback', async (t) => {
  const wait = deferred(); let plays = 0;
  const f = await fixture(t, { play: async (_pcm, { signal }) => {
    if (++plays === 1) await new Promise((_, reject) => signal.addEventListener('abort', () => reject(new Error('fixture')), { once: true }));
    else await wait.promise;
  } });
  f.arm('unfinished'); f.app.receiveSynthetic({ userId: '3' }); f.app.cancel({ userId: '3' });
  assert.equal(f.app.endSynthetic({ userId: '3' }), 'disabled');
  f.utterance('first'); await tick(); assert.equal(f.app.status().phase, 'playback');
  f.utterance('interrupt', '4'); wait.resolve(); await f.app.idle();
  assert.equal(f.app.status().remembered, 1); assert.equal(f.app.status().completed, 1);
  assert.ok(f.events.some((e) => e.event === 'playback_interrupted'));
});
test('reply policy rejects above 200 chars without clipping core output or invoking TTS', async (t) => {
  const f = await fixture(t, { model: async () => ({ choices: [{ message: { content: 'x'.repeat(901) } }] }) });
  f.utterance('large'); await f.app.idle(); assert.equal(f.http.length, 0); assert.equal(f.app.status().remembered, 0);
  assert.ok(f.events.some((e) => e.event === 'llm_failed'));
});
test('deadline cancels whole turn and leaves the app reusable; provider errors remain fixed', async (t) => {
  const wait = deferred(), events = []; const fixtures = offlineFixtures(); let calls = 0;
  fixtures.transcribe = async () => ++calls === 1 ? wait.promise : 'fixture';
  const f = await fixture(t, fixtures, { stageTimeoutMs: 10, log: (e) => events.push(e) });
  f.utterance('timeout'); await f.app.idle(); assert.equal(f.app.status().pending, 0);
  wait.resolve('EXAMPLE_PRIVATE_LATE'); await tick(); f.utterance('next'); await f.app.idle();
  assert.equal(f.app.status().remembered, 1); assert.ok(!JSON.stringify(events).includes('EXAMPLE_PRIVATE_LATE'));
});
test('join/leave race is bounded and repeated leave is idempotent', async (t) => {
  const wait = deferred(); let connects = 0;
  const app = new OfflineVoiceApp({ persona: 'fixture', fixtures: { ...offlineFixtures(), connect: async () => ++connects === 1 ? wait.promise : { botUserId: '999' } } });
  t.after(() => app.leave()); const joining = app.join(channel);
  const rejection = assert.rejects(joining, /session_failed/); await tick(); await app.leave(); await rejection;
  await app.join(channel); wait.resolve({ botUserId: '999' }); await tick(); assert.equal(app.status().joined, true);
  await app.leave(); await app.leave(); assert.equal(app.status().joined, false);
});
test('session memory remains bounded to 64 turns; leave permits a fresh session', async (t) => {
  const f = await fixture(t);
  for (let i = 0; i < 65; i++) { f.utterance('turn-' + i); await f.app.idle(); }
  assert.equal(f.app.status().remembered, 64); assert.equal(f.app.status().completed, 64); assert.equal(f.inputs.length, 64);
  await f.app.leave(); await f.app.join(channel); f.utterance('fresh'); await f.app.idle(); assert.equal(f.app.status().remembered, 1);
});
test('provider failure logs no private content and is not committed to session recall', async (t) => {
  let calls = 0;
  const f = await fixture(t, { request: async (r) => {
    if (++calls === 1) throw new Error('EXAMPLE_PRIVATE_PROVIDER_SECRET');
    return offlineFixtures().request(r);
  } });
  f.utterance('failed'); await f.app.idle(); assert.equal(f.app.status().remembered, 0);
  f.utterance('next'); await f.app.idle(); assert.equal(f.inputs.at(-1).messages.length, 2);
  assert.equal(f.app.status().remembered, 1); assert.ok(!JSON.stringify(f.events).includes('EXAMPLE_PRIVATE_PROVIDER_SECRET'));
});
test('commands reject unknown/private payload and configuration without calling providers', async (t) => {
  const f = await fixture(t);
  for (const value of [{ command: 'private' }, { command: 'receive', userId: '3', samples: [1] }, null, []]) await assert.rejects(command(f.app, value), /invalid_command/);
  await assert.rejects(f.app.join(channel), /session_busy/);
  assert.throws(() => new OfflineVoiceApp({ persona: 'fixture', fixtures: { ...offlineFixtures(), kind: 'live' } }), /invalid_config/);
});
function cli(args, input = '') {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [fileURLToPath(new URL('../cli.mjs', import.meta.url)), ...args], { stdio: ['pipe', 'pipe', 'pipe'] });
    let out = '', err = ''; child.stdout.on('data', (b) => { out += b; }); child.stderr.on('data', (b) => { err += b; });
    child.on('error', reject); child.on('exit', (code) => resolve({ code, out, err })); child.stdin.end(input);
  });
}
test('executable offline demo runs two full sessions; missing/live configuration never starts', async () => {
  const demo = await cli(['--offline', '--demo']); assert.equal(demo.code, 0);
  const events = demo.out.trim().split('\n').map(JSON.parse);
  assert.equal(events.filter((e) => e.event === 'playback_completed').length, 2);
  assert.equal(events.at(-1).status.joined, false); assert.equal(events.at(-1).status.remembered, 0);
  for (const args of [[], ['--live'], ['--offline', '--endpoint=EXAMPLE_PRIVATE']]) {
    const result = await cli(args); assert.equal(result.code, 2); assert.equal(result.out, ''); assert.ok(!result.err.includes('EXAMPLE_PRIVATE'));
  }
});
test('JSONL control input stays sanitized and EOF always leaves and clears memory', async () => {
  const lines = [JSON.stringify({ command: 'join', ...channel }), JSON.stringify({ command: 'private', text: 'EXAMPLE_PRIVATE_TEXT' }), JSON.stringify({ command: 'status' })].join('\n');
  const result = await cli(['--offline'], lines); assert.equal(result.code, 0);
  assert.ok(!result.out.includes('EXAMPLE_PRIVATE')); assert.match(result.out, /session_closed/);
});
test('SIGTERM during synthetic capture leaves once and does not restart', { timeout: 10000, skip: process.platform === 'win32' ? 'POSIX SIGTERM semantics' : false }, async (t) => {
  const child = spawn(process.execPath, [fileURLToPath(new URL('../cli.mjs', import.meta.url)), '--offline'], { stdio: ['pipe', 'pipe', 'pipe'] });
  t.after(() => { if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL'); });
  let output = '', signalled = false;
  const exited = new Promise((resolve, reject) => {
    child.on('error', reject); child.on('exit', (code) => resolve(code));
    child.stdout.on('data', (bytes) => {
      output += bytes;
      if (!signalled && output.includes('"command":"receive"')) { signalled = true; child.kill('SIGTERM'); }
    });
  });
  child.stdin.write([
    { command: 'join', ...channel }, { command: 'activate', userId: '3', turnId: 'signal', receive: true, respond: true },
    { command: 'receive', userId: '3' },
  ].map(JSON.stringify).join('\n') + '\n');
  assert.equal(await exited, 0); assert.equal(signalled, true);
  const events = output.trim().split('\n').map(JSON.parse);
  assert.equal(events.filter((e) => e.event === 'session_closed').length, 1);
  assert.equal(events.filter((e) => e.event === 'session_opened').length, 1);
});
