import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { LocalWhisperSTT, loadLocalSttConfig } from './local-stt.mjs';
import { createStt, loadSttConfig } from './stt-provider.mjs';
import { PCM } from './runtime.mjs';

test('local selection needs no Cloudflare credentials and rejects remote or credentialed URLs',()=>{
  assert.equal(loadSttConfig({DOCICH_DISCORD_VOICE_STT_PROVIDER:'local'}).provider,'local');
  assert.ok(createStt({DOCICH_DISCORD_VOICE_STT_PROVIDER:'local'}) instanceof LocalWhisperSTT);
  for(const url of ['https://example.com/transcribe','http://localhost/transcribe','http://user:pass@127.0.0.1/transcribe','http://127.0.0.1/transcribe?token=x']) {
    assert.throws(()=>loadLocalSttConfig({DOCICH_DISCORD_VOICE_LOCAL_STT_URL:url}));
  }
  assert.throws(()=>loadSttConfig({DOCICH_DISCORD_VOICE_STT_PROVIDER:'typo'}));
});

test('local adapter sends a WAV to loopback without credentials, preserving PCM',async()=>{
  let received; let auth;
  const server=createServer(async(req,res)=>{
    const chunks=[];for await(const c of req) chunks.push(c);
    received=Buffer.concat(chunks);auth=req.headers.authorization;
    assert.equal(req.url,'/transcribe');assert.equal(req.headers['content-type'],'audio/wav');
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({text:' 同志、こんにちは。 '}));
  });
  server.listen(0,'127.0.0.1');await once(server,'listening');
  try {
    const pcm=new Int16Array([123,-321,456]);
    const stt=new LocalWhisperSTT({env:{DOCICH_DISCORD_VOICE_LOCAL_STT_URL:`http://127.0.0.1:${server.address().port}/transcribe`}});
    assert.equal(await stt.transcribe(pcm,{format:PCM,signal:AbortSignal.timeout(2000)}),'同志、こんにちは。');
    assert.equal(auth,undefined);assert.equal(received.subarray(0,4).toString(),'RIFF');
    assert.equal(received.readInt16LE(44),123);assert.equal(received.readInt16LE(46),-321);
    assert.deepEqual([...pcm],[123,-321,456]);
  } finally {server.closeAllConnections();await new Promise(r=>server.close(r));}
});

test('local errors, oversized responses and cancellation remain sanitized',async()=>{
  for(const response of [new Response('private exception details',{status:503}),new Response(JSON.stringify({text:'x'.repeat(2001)})),new Response('x'.repeat(17000))]) {
    const stt=new LocalWhisperSTT({env:{},request:async()=>response});
    await assert.rejects(()=>stt.transcribe(new Int16Array(960),{signal:AbortSignal.timeout(1000)}),{message:'local_stt_failed'});
  }
  const c=new AbortController();c.abort();let calls=0;
  const stt=new LocalWhisperSTT({env:{},request:async()=>{calls++;}});
  await assert.rejects(()=>stt.transcribe(new Int16Array(960),{signal:c.signal}),{message:'stt_cancelled'});
  assert.equal(calls,0);
});
