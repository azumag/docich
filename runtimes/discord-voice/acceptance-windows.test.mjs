import assert from 'node:assert/strict';
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

test('Windows acceptance bootstrap provisions bridge secret without command-line value or persistence', () => {
  assert.match(
    source,
    /node_modules\/\.bin\/wrangler\.cmd/,
  );
  assert.match(
    source,
    /\$generatedSecret\s*\|\s*& \$wrangler secret put DISCORD_VOICE_INTERNAL_TOKEN --name docich-discord-chat/,
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

test('Windows acceptance bootstrap uses pinned Worker dependencies before secret mutation', () => {
  const install = source.indexOf(
    'npm install --no-package-lock --no-audit --no-fund',
  );
  const secret = source.indexOf(
    '$generatedSecret | & $wrangler secret put DISCORD_VOICE_INTERNAL_TOKEN',
  );
  assert.ok(install >= 0);
  assert.ok(secret > install);
});
