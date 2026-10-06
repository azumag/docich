import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('./acceptance-windows.ps1', import.meta.url),
  'utf8',
);

test('Windows acceptance bootstrap requires PowerShell 7 and no personal worker URL default', () => {
  assert.match(source, /^#requires -Version 7\.0/m);
  assert.match(source, /DOCICH_DISCORD_VOICE_WORKER_BASE_URL/);
  assert.doesNotMatch(source, /tsubasa-azumagakito\.workers\.dev/i);
  assert.match(source, /\[switch\]\$ProvisionBridgeSecret/);
});

test('Windows acceptance bootstrap uses Uri loopback detection including IPv6', () => {
  assert.match(source, /\$voicevoxUri\.IsLoopback/);
  assert.match(source, /"\[::\]"/);
});

test('Windows acceptance bootstrap keeps private debug output disabled', () => {
  assert.match(
    source,
    /DOCICH_DISCORD_VOICE_TRANSCRIPT_DEBUG\s*=\s*"0"/,
  );
  assert.match(
    source,
    /DOCICH_DISCORD_VOICE_REPLY_DEBUG\s*=\s*"0"/,
  );
});

test('Windows acceptance bootstrap bounds bridge secret propagation retries', () => {
  assert.match(source, /for \(\$attempt = 1; \$attempt -le 5; \$attempt\+\+\)/);
  assert.match(source, /Start-Sleep -Seconds 2/);
  assert.match(source, /Expected authenticated invalid-request 400/);
});

test('Windows acceptance bootstrap provisions bridge secret without command-line value or persistence', () => {
  assert.match(
    source,
    /node_modules\/\.bin\/cf\.cmd/,
  );
  assert.match(
    source,
    /\$generatedSecret\s*\|\s*& \$cfcli workers secrets update DISCORD_VOICE_INTERNAL_TOKEN --worker docich-discord-chat --type secret_text/,
  );
  assert.match(
    source,
    /DOCICH_DISCORD_VOICE_CHAT_TOKEN = \$null/,
  );

  assert.doesNotMatch(source, /\bsetx\b/i);
  assert.doesNotMatch(source, /Set-Content[^\n]*generatedSecret/i);
  assert.doesNotMatch(source, /Out-File[^\n]*generatedSecret/i);
  assert.doesNotMatch(source, /Write-(Host|Output)[^\n]*generatedSecret/i);
});

test('Windows acceptance bootstrap gives the non-TTY Cloudflare CLI an explicit secret type before stdin', () => {
  const invocation = source.match(
    /\$generatedSecret\s*\|\s*& \$cfcli (workers secrets update DISCORD_VOICE_INTERNAL_TOKEN --worker docich-discord-chat --type secret_text)/,
  );
  assert.ok(invocation, 'secret_text must be selected in the piped CLI invocation');

  const mockCli = String.raw`
const args = process.argv.slice(1);
async function main() {
  if (!process.stdin.isTTY && !args.includes('--type')) {
    process.stderr.write('--type is required before the non-TTY type prompt\n');
    process.exit(2);
  }
  if (args[args.indexOf('--type') + 1] !== 'secret_text') {
    process.stderr.write('unexpected secret type\n');
    process.exit(3);
  }
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  if (Buffer.concat(chunks).toString() !== 'dummy-secret-for-non-tty-contract-test') {
    process.stderr.write('stdin payload mismatch\n');
    process.exit(4);
  }
  process.stdout.write('secret text consumed\n');
}
main().catch(() => process.exit(5));
`;
  const result = spawnSync(
    process.execPath,
    ['-e', mockCli, ...invocation[1].split(/\s+/)],
    { input: 'dummy-secret-for-non-tty-contract-test', encoding: 'utf8' },
  );
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, 'secret text consumed\n');
  assert.doesNotMatch(result.stdout + result.stderr, /dummy-secret/);
  assert.doesNotMatch(invocation[1], /dummy-secret/);
});


test('Windows PowerShell native string pipeline exposes the Cloudflare trailing-CR risk', {
  skip: process.platform !== 'win32',
}, () => {
  const collector = String.raw`
const chunks = [];
process.stdin.on('data', (chunk) => chunks.push(chunk));
process.stdin.on('end', () => {
  const raw = Buffer.concat(chunks);
  const text = raw.toString('utf8');
  const cloudflareValue = text.endsWith('\n') ? text.slice(0, -1) : text;
  process.stdout.write(JSON.stringify({
    rawEndsWithCRLF: raw.subarray(-2).equals(Buffer.from('\r\n')),
    cloudflareKeepsCR: cloudflareValue.endsWith('\r'),
    cloudflareValueMatches: cloudflareValue === 'synthetic-pipeline-dummy',
    byteLength: raw.length,
  }));
});
`;
  const collectorBase64 = Buffer.from(collector).toString('base64');
  const ps = [
    "$dummy = 'synthetic-pipeline-dummy'",
    `$result = $dummy | node -e 'eval(Buffer.from("${collectorBase64}", "base64").toString())'`,
    'Write-Output $result',
  ].join('; ');
  const encoded = Buffer.from(ps, 'utf16le').toString('base64');
  const result = spawnSync('pwsh', ['-NoProfile', '-EncodedCommand', encoded], {
    encoding: 'utf8',
    windowsHide: true,
  });
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout.trim());
  assert.equal(report.rawEndsWithCRLF, true);
  assert.equal(report.cloudflareKeepsCR, true);
  assert.equal(report.cloudflareValueMatches, false);
  assert.equal(result.stdout.includes('synthetic-pipeline-dummy'), false);
});

test('Windows acceptance bootstrap uses pinned Worker dependencies before secret mutation', () => {
  const install = source.indexOf(
    'npm install --no-package-lock --no-audit --no-fund',
  );
  const secret = source.indexOf(
    '$generatedSecret | & $cfcli workers secrets update DISCORD_VOICE_INTERNAL_TOKEN',
  );
  assert.ok(install >= 0);
  assert.ok(secret > install);
});
