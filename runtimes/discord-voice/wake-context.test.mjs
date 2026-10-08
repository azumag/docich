import assert from 'node:assert/strict';
import test from 'node:test';
import { WakeContext, WakeSession } from './wake-context.mjs';
const tick=()=>new Promise(r=>setImmediate(r));
test('ambient utterances are context only; only the current wake utterance requests a reply',()=>{
  let now=0; const c=new WakeContext({now:()=>now});
  assert.equal(c.observe('1','週末は京都へ行きます。'),null);
  assert.equal(c.observe('2','お寺を見たいです。'),null);
  const wake=c.observe('3','同志、どこがよさそうですか？');
  assert.equal(wake.transcript,'同志、どこがよさそうですか？');
  assert.deepEqual(wake.recentContext.map(e=>e.userId),['1','2']);
  assert.equal(c.observe('2','電車で行きます。'),null);
  now=30*60_000;
  assert.deepEqual(c.recent(),[]);
});
test('context is session isolated, bounded, and erased on clear',()=>{
  const a=new WakeContext(), b=new WakeContext();
  for(let i=0;i<400;i++) a.observe('1','あ'.repeat(199));
  assert.ok(a.recent().length<=12);
  assert.ok(a.recent().reduce((n,e)=>n+e.text.length,0)<=2000);
  assert.deepEqual(b.recent(),[]);
  a.clear(); assert.deepEqual(a.recent(),[]);
});
test('wake replies remain FIFO across speakers and keep each arrival context',async()=>{
  const calls=[],events=[];let release;
  const first=new Promise(r=>{release=r;});
  const session=new WakeSession({emit:e=>events.push(e),reply:async(text,context)=>{
    calls.push({text,context});if(calls.length===1)await first;
  }});
  session.observe('京都へ行く話',{userId:'1'});await tick();assert.equal(calls.length,0);
  session.observe('同志、Aの質問',{userId:'1'});await tick();assert.equal(calls.length,1);
  session.observe('背景の追加情報',{userId:'2'});
  session.observe('同志、Bの質問',{userId:'2'});
  session.observe('同志、Cの質問',{userId:'3'});await tick();
  assert.equal(calls[0].context.signal.aborted,false);assert.equal(calls.length,1);
  release();await tick();await tick();await tick();
  assert.deepEqual(calls.map(c=>c.text),['同志、Aの質問','同志、Bの質問','同志、Cの質問']);
  assert.deepEqual(calls.map(c=>c.context.userId),['1','2','3']);
  assert.ok(calls[1].context.recentContext.some(e=>e.text==='背景の追加情報'));
  assert.equal(calls[0].context.recentContext.some(e=>e.text==='同志、Bの質問'),false);
  assert.ok(events.every(e=>Object.keys(e).length===1));session.stop();
});

test('stopping before queued reply begins suppresses all output',async()=>{
  let calls=0; const s=new WakeSession({reply:()=>{calls++;}});
  s.observe('同志',{userId:'1'}); s.stop(); await tick(); assert.equal(calls,0);
});


test('spoken wake spelling variants are accepted only as a leading separated call',()=>{
  for(const text of ['同士、雨でも行ける場所は？','どうし、教えて。','ドウシ 教えて。','「どうし、こんにちは」','どうし']) {
    const c=new WakeContext(); const request=c.observe('1',text);
    assert.ok(request,text); assert.equal(request.transcript,text); c.clear();
  }
  for(const text of ['どうしようかな。','どうして雨なの？','友達同士で話します。','同士討ちを避けます。','同士の集まりです。','昔「どうし、教えて」と聞きました。']) {
    const c=new WakeContext();assert.equal(c.observe('1',text),null,text);c.clear();
  }
});


test('full reply queue preserves eight pending calls instead of replacing them',async()=>{
  let release;const held=new Promise(r=>{release=r;});const calls=[],events=[];
  const session=new WakeSession({emit:e=>events.push(e.event),reply:async text=>{calls.push(text);if(calls.length===1)await held;}});
  session.observe('同志、A',{userId:'1'});await tick();
  for(let n=0;n<9;n++)session.observe('同志、queued '+n,{userId:'2'});
  assert.equal(events.filter(e=>e==='voice_reply_queue_full').length,1);
  release();for(let i=0;i<12;i++)await tick();
  assert.deepEqual(calls,['同志、A',...Array.from({length:8},(_,n)=>'同志、queued '+n)]);session.stop();
});

test('queue skips departed speakers, continues after a failed turn, and stop clears remaining calls',async()=>{
  let release;const held=new Promise(r=>{release=r;});const calls=[],events=[],allowed=new Set(['1','2','3']);
  const session=new WakeSession({allowReply:id=>allowed.has(id),emit:e=>events.push(e.event),reply:async(text,ctx)=>{
    calls.push({text,signal:ctx.signal});if(calls.length===1)await held;if(text.includes('失敗'))throw Error('private fixture');
  }});
  session.observe('同志、最初',{userId:'1'});await tick();
  session.observe('同志、退出した人',{userId:'2'});session.observe('同志、失敗',{userId:'3'});session.observe('同志、成功',{userId:'1'});
  allowed.delete('2');release();for(let i=0;i<5;i++)await tick();
  assert.deepEqual(calls.map(c=>c.text),['同志、最初','同志、失敗','同志、成功']);
  assert.ok(events.includes('voice_reply_skipped'));assert.ok(events.includes('voice_wake_reply_failed'));session.stop();
  let releaseStop;const blocking=new Promise(r=>{releaseStop=r;});const stoppedCalls=[];
  const stopped=new WakeSession({reply:async(text,ctx)=>{stoppedCalls.push({text,signal:ctx.signal});await blocking;}});
  stopped.observe('同志、active',{userId:'1'});await tick();stopped.observe('同志、pending',{userId:'2'});stopped.stop();
  assert.equal(stoppedCalls[0].signal.aborted,true);releaseStop();await tick();await tick();assert.equal(stoppedCalls.length,1);
});
