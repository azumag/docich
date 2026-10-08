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
test('wake processing continues ambient capture and cancels an earlier reply with at most one replacement',async()=>{
  const calls=[], events=[]; let release;
  const first=new Promise(r=>{release=r;});
  const s=new WakeSession({emit:e=>events.push(e),reply:async(text,context)=>{
    calls.push({text,context}); if(calls.length===1) await first;
  }});
  s.observe('背景の話',{userId:'1'}); await tick(); assert.equal(calls.length,0);
  s.observe('同志、最初の質問',{userId:'1'}); await tick(); assert.equal(calls.length,1);
  s.observe('別の人の追加情報',{userId:'2'}); assert.equal(calls[0].context.signal.aborted,false);
  s.observe('同志、次の質問',{userId:'2'});
  s.observe('同志、最後の質問',{userId:'3'});
  assert.equal(calls[0].context.signal.aborted,true);
  release(); await tick(); await tick();
  assert.equal(calls.length,2); assert.equal(calls[1].text,'同志、最後の質問');
  assert.ok(calls[1].context.recentContext.some(e=>e.text==='別の人の追加情報'));
  assert.ok(events.every(e=>Object.keys(e).length===1));
  s.stop(); s.observe('同志、停止後',{userId:'1'}); await tick(); assert.equal(calls.length,2);
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
