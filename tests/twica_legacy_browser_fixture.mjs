// Real Chromium + local event streams; imported legacy guard is the production module.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.DOCICH_NODE_PLAYWRIGHT || 'playwright');
const {installTwicaLegacyOwner,readLegacyPolicy}=await import(pathToFileURL(path.join(process.env.DOCICH_SOREN_ROOT,'lib/twica_legacy_owner.mjs')));
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'twica-real-'));
const streams=new Set();let total=0,max=0;
const server=http.createServer((req,res)=>{
  if(requests.length<30) requests.push(req.url==='/events'?'events':req.url==='/host'?'host':'overlay');
  if(req.url==='/events'){
    res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store'});
    res.write(': ready\n\n');streams.add(res);total++;max=Math.max(max,streams.size);
    res.on('close',()=>streams.delete(res));return;
  }
  if(req.url==='/host'){res.writeHead(200,{'Content-Type':'text/html'});res.end('<!doctype html><title>Fixture host</title>');return;}
  res.writeHead(200,{'Content-Type':'text/html'});
  res.end(`<script>window.events=[];window.s=new EventSource('/events');s.onmessage=e=>events.push(Number(e.data));</script>`);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const url=`http://127.0.0.1:${server.address().port}/overlay/x`;
const browser=await chromium.launch({headless:true,executablePath:process.env.SOREN_CHROME_EXECUTABLE_PATH||undefined});
const guards=[]; const pageErrors=[]; const requests=[]; let phase='initial';
function observePage(p){
 p.on('pageerror',e=>pageErrors.push(e.name));
 const evaluate=p.evaluate.bind(p);
 p.evaluate=async (...args)=>{try{return await evaluate(...args);}catch(e){
   if(pageErrors.length<10)pageErrors.push(String(e.message).slice(0,400));throw e;
 }};
}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function wait(fn){
 for(let i=0;i<250;i++){if(await fn())return;await sleep(20);}
 const pages=await Promise.all(browser.contexts().flatMap(c=>c.pages()).map(async p=>{
   try{return await p.evaluate(()=>({body:!!document.body,frames:document.querySelectorAll('iframe').length,top:window.top===window}));}
   catch{return {unavailable:true};}
 }));
 throw new Error('fixture timeout '+JSON.stringify({phase,total,max,active:streams.size,
   policy:readLegacyPolicy(dir), guards:guards.map(g=>({managed:g.managed,subscribed:g.subscribed,stopped:g.stopped})),pages,pageErrors,requests}));
}
function policy(owner,legacy_role='game'){
 fs.writeFileSync(path.join(dir,'control.json'),JSON.stringify({schema:1,owner,legacy_role,generation:(owner==='common'?'c':owner==='none'?'b':'a').repeat(32)}),{mode:0o600});
}
try{
 policy('legacy');
 assert.equal(readLegacyPolicy(dir).owner,'legacy','fixture must be an accepted private policy');
 const game=await browser.newPage(), shared=await browser.newPage();
 observePage(game);observePage(shared);
 await game.goto(new URL('/host',url).href);await shared.goto(new URL('/host',url).href);
 const item={title:'TwiCa',srcUrl:url,style:{inset:'0',width:'100vw',height:'100vh'}};
 guards.push(await installTwicaLegacyOwner(game,item,{directory:dir,role:'game',intervalMs:20}));
 guards.push(await installTwicaLegacyOwner(shared,item,{directory:dir,role:'shared',intervalMs:20}));
 await wait(()=>streams.size===1);
 phase='legacy quiescence';policy('none');await wait(()=>streams.size===0);
 phase='common subscription';policy('common');
 const common=await browser.newPage();await common.goto(url);await wait(()=>streams.size===1);
 for(let id=1;id<=8;id++){
   phase='event '+id;
   await game.reload();await shared.reload();await sleep(30);
   for(const stream of streams)stream.write(`data: ${id}\n\n`);
   await wait(async()=>await common.evaluate('events.length')===id);
   assert.equal(streams.size,1);
 }
 const events=await common.evaluate('events');assert.deepEqual(events,[1,2,3,4,5,6,7,8]);
 assert.equal(total,2);assert.equal(max,1);
 await common.close();await wait(()=>streams.size===0);
 phase='rollback shared';policy('legacy','shared');await wait(()=>streams.size===1);
 assert.equal(total,3);assert.equal(max,1);
 console.log(JSON.stringify({passed:true,events:events.length,max_subscriptions:max,total_subscriptions:total}));
}finally{
 for(const guard of guards)guard.close();await browser.close();
 for(const stream of streams)stream.end();await new Promise(r=>server.close(r));
 fs.rmSync(dir,{recursive:true,force:true});
}
