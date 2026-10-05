import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';
import { OfflineVoiceApp, OfflineAppError } from './app.mjs';

const COMMANDS = Object.freeze({
  join: ['guildId', 'channelId'], leave: [], activate: ['userId', 'turnId', 'receive', 'respond'],
  receive: ['userId', 'frames'], end: ['userId'], cancel: ['userId'], idle: [], status: [],
});
export async function command(app, input) {
  if (!input || typeof input !== 'object' || Array.isArray(input) || typeof input.command !== 'string' ||
    !Object.hasOwn(COMMANDS, input.command) || Object.keys(input).some((key) => key !== 'command' && !COMMANDS[input.command].includes(key))) {
    throw new OfflineAppError('invalid_command');
  }
  if (input.command === 'join') await app.join(input);
  else if (input.command === 'leave') await app.leave();
  else if (input.command === 'activate') return { result: app.activate(input), status: app.status() };
  else if (input.command === 'receive') return { result: app.receiveSynthetic(input), status: app.status() };
  else if (input.command === 'end') return { result: app.endSynthetic(input), status: app.status() };
  else if (input.command === 'cancel') app.cancel(input);
  else if (input.command === 'idle') await app.idle();
  return { status: app.status() };
}
export async function main(args = process.argv.slice(2)) {
  if (args.length < 1 || args[0] !== '--offline' || args.length > 2 || (args[1] && args[1] !== '--demo')) {
    process.stderr.write('{"event":"startup_rejected","code":"live_disabled_or_unconfigured"}\n');
    return 2;
  }
  let app, reader, stopping = false;
  const emit = (record) => process.stdout.write(JSON.stringify(record) + '\n');
  const stop = () => { stopping = true; reader?.close(); void app?.leave().catch(() => {}); };
  try {
    const persona = readFileSync(new URL('../../src/docich/comment/prompts/comment_persona_main.md', import.meta.url), 'utf8').trim();
    app = new OfflineVoiceApp({ persona, log: emit });
    process.on('SIGINT', stop); process.on('SIGTERM', stop);
    const run = async (input) => {
      if (stopping) return;
      try { emit({ event: 'command_completed', command: input?.command, ...await command(app, input) }); }
      catch { emit({ event: 'command_rejected', code: 'invalid_or_cancelled_command' }); }
    };
    if (args[1] === '--demo') {
      for (let i = 0; i < 2; i++) {
        await run({ command: 'join', guildId: '1', channelId: '2' });
        await run({ command: 'activate', userId: '3', turnId: 'fixture-' + i, receive: true, respond: true });
        await run({ command: 'receive', userId: '3', frames: 6 });
        await run({ command: 'end', userId: '3' }); await run({ command: 'idle' });
        await run({ command: 'leave' });
      }
    } else {
      reader = createInterface({ input: process.stdin, crlfDelay: Infinity });
      for await (const line of reader) {
        if (stopping) break;
        try {
          if (Buffer.byteLength(line) > 4096) throw new OfflineAppError('invalid_command');
          await run(JSON.parse(line));
        } catch { emit({ event: 'command_rejected', code: 'invalid_command' }); }
      }
    }
    return 0;
  } catch {
    process.stderr.write('{"event":"startup_rejected","code":"offline_startup_failed"}\n'); return 2;
  } finally {
    reader?.close(); await app?.leave(); process.off('SIGINT', stop); process.off('SIGTERM', stop);
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) process.exitCode = await main();
