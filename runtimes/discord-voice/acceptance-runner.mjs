import { EventEmitter } from 'node:events';
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { pathToFileURL } from 'node:url';

import { summarizeAcceptanceLines } from './acceptance-summary.mjs';
import { runLiveVoice } from './live.mjs';

function parseArgs(args) {
  const options = {
    log: null,
    minMinutes: 0,
    requireInterrupt: false,
    requireReconnect: false,
    stopAfterMinutes: null,
  };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === '--log') {
      options.log = args[++index] ?? null;
    } else if (arg === '--min-minutes') {
      options.minMinutes = Number(args[++index]);
    } else if (arg === '--stop-after-minutes') {
      options.stopAfterMinutes = Number(args[++index]);
    } else if (arg === '--require-interrupt') {
      options.requireInterrupt = true;
    } else if (arg === '--require-reconnect') {
      options.requireReconnect = true;
    } else {
      throw new Error('invalid_argument');
    }
  }

  if (
    typeof options.log !== 'string' ||
    !options.log ||
    !Number.isFinite(options.minMinutes) ||
    options.minMinutes < 0 ||
    options.minMinutes > 24 * 60 ||
    (options.stopAfterMinutes !== null &&
      (!Number.isFinite(options.stopAfterMinutes) ||
        options.stopAfterMinutes <= 0 ||
        options.stopAfterMinutes > 24 * 60))
  ) {
    throw new Error('invalid_argument');
  }

  return options;
}

function jsonLine(record) {
  return JSON.stringify(record);
}

export async function runAcceptance(
  options,
  {
    env = process.env,
    runVoice = runLiveVoice,
    now = () => new Date(),
    signalSource = process,
  } = {},
) {
  mkdirSync(dirname(options.log), { recursive: true });
  writeFileSync(options.log, '', { encoding: 'utf8', mode: 0o600 });

  const signalTarget = new EventEmitter();
  let timer = null;
  let stopping = false;

  const stop = () => {
    if (stopping) return;
    stopping = true;
    signalTarget.emit('SIGINT');
  };

  const armStopTimer = () => {
    if (options.stopAfterMinutes === null || timer !== null) return;
    timer = setTimeout(stop, options.stopAfterMinutes * 60_000);
    timer.unref?.();
  };

  const emitRecord = (record) => {
    const raw = jsonLine(record);
    appendFileSync(
      options.log,
      `${now().toISOString()}\t${raw}\n`,
      { encoding: 'utf8', mode: 0o600 },
    );
    process.stdout.write(raw + '\n');
    if (record?.event === 'voice_connected') armStopTimer();
  };

  const onSigint = () => stop();
  const onSigterm = () => stop();
  signalSource.once('SIGINT', onSigint);
  signalSource.once('SIGTERM', onSigterm);

  let runtimeCode = 2;
  try {
    try {
      runtimeCode = await runVoice(env, {
        signalTarget,
        emit: emitRecord,
        emitError: (code) => emitRecord({ event: 'live_voice_error', code }),
      });
    } catch (error) {
      const code =
        typeof error?.code === 'string' &&
        /^[a-z0-9_]{1,64}$/.test(error.code)
          ? error.code
          : 'live_runtime_failed';
      emitRecord({ event: 'live_voice_error', code });
      runtimeCode = 2;
    }
  } finally {
    if (timer) clearTimeout(timer);
    signalSource.off('SIGINT', onSigint);
    signalSource.off('SIGTERM', onSigterm);
  }

  const input = readFileSync(options.log, 'utf8');
  const summary = summarizeAcceptanceLines(input.split(/\r?\n/), options);
  const final = Object.freeze({
    ...summary,
    runtimeCode,
    passed: runtimeCode === 0 && summary.passed,
  });

  process.stdout.write(jsonLine({
    event: 'voice_acceptance_summary',
    passed: final.passed,
    runtimeCode,
    oneTurn: final.oneTurn,
    interruptOk: final.interruptOk,
    reconnectOk: final.reconnectOk,
    durationOk: final.durationOk,
    privacyOk: final.privacyOk,
    failureCount: final.failureCount,
    malformed: final.malformed,
    durationSeconds: Math.round(final.durationSeconds),
    completedChains: final.completedChains,
    counts: final.counts,
  }) + '\n');

  return final.passed ? 0 : 1;
}

export async function main(args = process.argv.slice(2)) {
  try {
    const options = parseArgs(args);
    return await runAcceptance(options);
  } catch {
    process.stderr.write(JSON.stringify({
      event: 'voice_acceptance_runner_error',
      code: 'invalid_acceptance_config',
    }) + '\n');
    return 2;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main();
}
