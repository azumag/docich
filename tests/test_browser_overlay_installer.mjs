import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { overlayInstaller, BROWSER_OVERLAY_INSTALLER_CONTRACT_VERSION } from '../overlays/browser_overlay_installer.mjs';

function fixture({nested=false, loading=false, fetchImpl=async()=>({ok:false})}={}) {
  const frames=[];
  const callbacks=new Map();
  const intervals=[];
  const window={};
  window.top=nested?{}:window;
  const document={
    readyState:loading?'loading':'complete',
    body:{appendChild(node){frames.push(node);}},
    getElementById(id){return frames.find(n=>n.id===id)||null;},
    createElement(name){
      assert.equal(name,'iframe');
      return {style:{},dataset:{},setAttribute(k,v){this[k]=v;},
        addEventListener(event,fn){this.handlers??={};this.handlers[event]=fn;}};
    },
    addEventListener(event,callback){callbacks.set(event,callback);},
  };
  const logs=[];
  const globals={window,document,fetch:fetchImpl,console:{warn(...args){logs.push(args);}},
    setInterval(fn,ms){intervals.push({fn,ms});return intervals.length;}};
  const run=(surfaces)=>vm.runInNewContext('('+overlayInstaller.toString()+')(surfaces)',{...globals,surfaces});
  return {frames,callbacks,intervals,logs,run};
}

function surface(key,more={}){
  return {key,route:'/overlay/'+key,elementId:'overlay-'+key,title:key,
    pollMs:180,style:{left:'0',top:'0',zIndex:'2147483630'},...more};
}

const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};

test('versioned installer is a self-contained browser callback',()=>{
  assert.equal(BROWSER_OVERLAY_INSTALLER_CONTRACT_VERSION,1);
  assert.equal(overlayInstaller.toString().startsWith('function overlayInstaller(surfaces)'), true);
  assert.equal(overlayInstaller.toString().includes('import '),false);
});

test('nested frame cannot inject, DOMContentLoaded and repeated install are idempotent',()=>{
  const nested=fixture({nested:true});nested.run([surface('sidebar')]);assert.equal(nested.frames.length,0);
  const f=fixture({loading:true});const payload=[surface('twica',{srcUrl:'http://127.0.0.1:18080/overlay',style:{transform:'translateX(calc(100vw / 3))'}})];
  f.run(payload);assert.equal(f.frames.length,0);
  f.callbacks.get('DOMContentLoaded')();assert.equal(f.frames.length,1);
  assert.equal(f.frames[0].src,'http://127.0.0.1:18080/overlay');
  assert.equal(f.frames[0].style.transform,'translateX(calc(100vw / 3))');
  assert.equal(f.frames[0].dataset.sorenExternalOverlay,'1');
  assert.equal(f.intervals.length,0);
  f.run(payload);assert.equal(f.frames.length,1);
});

test('internal rails use isolated two-frame swapping without self-refresh and never cover game-gap',async()=>{
  let body='<meta http-equiv="refresh" content="2"><p>FIRST</p>';
  let requests=0;
  const f=fixture({fetchImpl:async()=>{requests++;return {ok:true,text:async()=>body};}});
  f.run([surface('sidebar',{region:'sidebar'}),surface('gap',{region:'game-gap'})]);
  assert.equal(f.frames.length,4);
  assert.equal(f.intervals.length,2);
  assert.equal(f.intervals[0].ms,180);
  assert.equal(f.frames[2].style.display,'none');
  assert.equal(f.frames[3].style.display,'none');
  await settle();
  assert.match(f.frames[1].srcdoc,/FIRST/);
  assert.doesNotMatch(f.frames[1].srcdoc,/http-equiv=/i);
  assert.match(f.frames[3].srcdoc,/FIRST/);
  f.frames[1].handlers.load();f.frames[3].handlers.load();
  assert.equal(f.frames[1].style.visibility,'visible');
  assert.equal(f.frames[0].style.visibility,'hidden');
  assert.equal(f.frames[2].style.display,'none');
  assert.equal(f.frames[3].style.display,'none'); // game-gap remains non-occluding
  assert.equal(f.frames[0].srcdoc.includes('background:transparent'),true);
  await f.intervals[0].fn();
  assert.equal(f.frames[0].srcdoc.includes('FIRST'),false); // unchanged => no swap
  body='<script>setTimeout(()=>location.reload(),1000)</script><p>SECOND</p>';
  await f.intervals[0].fn();
  assert.match(f.frames[0].srcdoc,/SECOND/);
  assert.doesNotMatch(f.frames[0].srcdoc,/location.reload/);
  f.frames[0].handlers.load();
  assert.equal(f.frames[0].style.visibility,'visible');
  assert.equal(f.frames[1].style.visibility,'hidden');
  assert.equal(requests>=4,true);
});

test('failed fetch is nonfatal and retried on the next poll',async()=>{
  let attempts=0;
  const f=fixture({fetchImpl:async()=>{
    attempts++;if(attempts===1)throw Error('transient');
    return {ok:true,text:async()=>'<p>BACK</p>'};
  }});
  f.run([surface('sidebar')]);await settle();
  assert.equal(f.frames[1].srcdoc,undefined);
  await f.intervals[0].fn();
  assert.match(f.frames[1].srcdoc,/BACK/);
  assert.equal(attempts,2);
});
