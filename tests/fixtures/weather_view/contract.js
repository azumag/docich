// DOM-free control-flow tests. Geometry/layout getters are mocked; this is not browser QA.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{
 let now=0,mobile=false,overflowHeight=false,overflowWidth=false,nextTimer=1;
 const timers=new Map(),events=new Map(),nodes=new Map();
 class Node {
  constructor(tag,id=''){this.tag=tag;this.id=id;this.children=[];this.attrs={};this.dataset={};this.style={};this.hidden=false;this.textContent='';this.listeners={};this.classes=new Set();this.classList={remove:c=>this.classes.delete(c),toggle:(c,on)=>on?this.classes.add(c):this.classes.delete(c)};}
  setAttribute(k,v){this.attrs[k]=String(v);if(k==='id')nodes.set(String(v),this);if(k==='data-index')this.dataset.index=String(v);}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];}
  querySelector(tag){return this.children.find(c=>c.tag===tag);}
  addEventListener(name,fn){this.listeners[name]=fn;}
  get clientHeight(){return 35;}
  get scrollHeight(){return this.id==='weather-text'&&(overflowHeight||this.textContent.length>100)?80:17;}
  get clientWidth(){return 160;}
  get scrollWidth(){return this.id==='weather-text'&&overflowWidth?220:80;}
 }
 const doc={hidden:false,getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node('div',id));return nodes.get(id);},createElement:tag=>new Node(tag),createElementNS:(_,tag)=>new Node(tag)};
 let fetchImpl=()=>Promise.reject(Error('synthetic no server'));
 const context=vm.createContext({document:doc,performance:{now:()=>now},innerWidth:960,innerHeight:540,matchMedia:()=>({matches:mobile}),addEventListener:(name,fn)=>events.set(name,fn),setInterval:(fn,ms)=>{const id=nextTimer++;timers.set(id,{fn,ms});return id;},clearInterval:id=>timers.delete(id),fetch:(...a)=>fetchImpl(...a),AbortSignal:{timeout:()=>({})}});
 const run=source=>vm.runInContext(source,context);
 vm.runInContext(input.script,context);
 await Promise.resolve();await Promise.resolve();
 const names=['札幌','仙台','東京','新潟','名古屋','大阪','広島','高松','福岡','鹿児島','那覇'];
 const fixture={ok:true,date:'2026-10-03',expires_at:15,server_now:10,cities:names.map(city=>({city,weather:'合成予報',issued_at:'2026-10-03T05:00:00+09:00',high_c:21,low_c:null,pops:[0,6,12,18].map(start=>({start,end:start+6,percent:start===0?0:null}))}))};
 function seed(longIndex=null){if(longIndex!==null)fixture.cities[longIndex].weather='合成長文'.repeat(40);context.fixture=fixture;run('view=fixture;until=5000;selected=null;render();');}
 function paused(){assert.equal(run('view===null'),true,'forecast still visible');assert.equal(run('until'),0);assert.equal(nodes.get('unavailable').hidden,false);assert.equal(nodes.get('weather-text').textContent,'');assert.equal(nodes.get('high').textContent,'—');assert.equal(nodes.get('pop-grid').children.length,0);assert.equal(run('tourTimer'),0);assert.equal(nodes.get('tour').attrs['aria-pressed'],'false');for(const m of nodes.get('markers').children)assert(!m.querySelector('text').textContent.includes('21°'));}
 function tourTick(){const t=[...timers.values()].find(t=>t.ms===7000);assert(t);t.fn();}
 const scenario=input.scenario;
 if(['choose','next','previous','region','marker','tour_start'].includes(scenario)){
  seed(1);
  if(scenario==='choose')run('choose(1)');
  if(scenario==='next'){run('choose(0);advance(1)');}
  if(scenario==='previous'){run('choose(2);advance(-1)');}
  if(scenario==='region')nodes.get('region-grid').children[1].listeners.click();
  if(scenario==='marker')nodes.get('markers').children[1].listeners.click();
  if(scenario==='tour_start'){run('choose(0);toggleTour()');}
  paused();
 }else if(scenario==='tour_tick'){
  seed(2);run('choose(0);toggleTour()');tourTick();paused();
 }else if(scenario==='national'){
  seed();run('choose(0)');overflowHeight=true;run('national()');paused();
 }else if(scenario==='width_overflow'){
  seed();overflowWidth=true;run('choose(0)');paused();
 }else if(scenario==='resize'){
  seed();run('choose(0)');mobile=true;overflowHeight=true;events.get('resize')();assert.equal(nodes.get('stage').style.transform,'none');paused();
 }else if(scenario==='expired_tour_stop'){
  seed();run('toggleTour()');now=5100;run('toggleTour()');paused();
 }else if(scenario.startsWith('expired_')){
  seed();now=5100;run({expired_choose:'choose(0)',expired_next:'advance(1)',expired_national:'national()',expired_tour:'toggleTour()'}[scenario]);paused();
 }else if(scenario==='all_locations'){
  seed();assert.equal(nodes.get('geometry').children.length,218);assert.equal(nodes.get('markers').children.length,11);
  for(let i=0;i<11;i++){run(`choose(${i})`);assert.equal(nodes.get('city-name').textContent,names[i]);assert.equal(nodes.get('high').textContent,'21°');assert.equal(nodes.get('low').textContent,'—');assert.equal(nodes.get('pop-grid').children[0].children[1].textContent,'0%');assert.equal(nodes.get('pop-grid').children[1].children[1].textContent,'—');const b=nodes.get('map').attrs.viewBox.split(' ').map(Number);assert(b[0]>=0&&b[1]>=0&&b[0]+b[2]<=1220+1e-9&&b[1]+b[3]<=960+1e-9);}
  run('choose(10);advance(1)');assert.equal(nodes.get('city-name').textContent,names[0]);run('choose(0);advance(-1)');assert.equal(nodes.get('city-name').textContent,names[10]);
  context.innerWidth=1920;context.innerHeight=1080;events.get('resize')();assert.equal(nodes.get('stage').style.transform,'scale(2)');
  run('national()');assert.equal(nodes.get('map').attrs.viewBox,'0 0 1220 960');assert.equal(nodes.get('city-name').textContent,'全国');
  run('choose(0);toggleTour()');tourTick();run('toggleTour()');assert(![...timers.values()].some(t=>t.ms===7000));assert.equal(nodes.get('tour').attrs['aria-pressed'],'false');
 }else if(scenario==='lease_expiry'){
  seed();now=5000;[...timers.values()].find(t=>t.ms===100).fn();paused();
 }else if(scenario==='server_failure'){
  seed();run('choose(0)');await run('poll()');paused();
 }else if(scenario==='incomplete'){
  seed();fixture.cities.pop();fetchImpl=async()=>({ok:true,json:async()=>fixture});await run('poll()');paused();
 }else if(scenario==='manual_stop'){
  seed();run('toggleTour();choose(7)');assert.equal(nodes.get('city-name').textContent,names[7]);assert.equal(run('tourTimer'),0);assert(![...timers.values()].some(t=>t.ms===7000));
 }else if(scenario==='old_failure'){
  seed();const pending=[];fetchImpl=()=>new Promise(resolve=>pending.push(resolve));const older=run('poll()'),newer=run('poll()');pending[1]({ok:true,json:async()=>fixture});await newer;pending[0]({ok:false});await older;assert.equal(run('view!==null'),true);assert.equal(nodes.get('unavailable').hidden,true);
 }else if(scenario==='old_poll'){
  seed();const pending=[];fetchImpl=()=>new Promise(resolve=>pending.push(resolve));const older=run('poll()'),newer=run('poll()');pending[1]({ok:false});await newer;pending[0]({ok:true,json:async()=>fixture});await older;paused();
 }else if(scenario==='broadcast_auto'){
  let cue={ok:true,status:'running',item_index:1};
  fetchImpl=async url=>url==='/api/weather-cue'
    ?({status:200,ok:true,json:async()=>cue})
    :({status:200,ok:true,json:async()=>fixture});
  await run('poll()');
  await new Promise(resolve=>setTimeout(resolve,0)); // Flush startBroadcastSync()'s unawaited initial cue poll.
  assert.equal(run('tourTimer'),0);assert.equal(nodes.get('tour').attrs['aria-pressed'],'true');
  assert([...timers.values()].some(t=>t.ms===250));
  assert.equal(run('selected'),0);assert.equal(nodes.get('city-name').textContent,names[0]);
  cue={ok:true,status:'running',item_index:2};await run('pollCue()');
  assert.equal(run('selected'),1);assert.equal(nodes.get('city-name').textContent,names[1]);
  await run('poll()');assert.equal(run('selected'),1); // Forecast polling cannot advance the focus.
 }else if(scenario.startsWith('broadcast_')){
  seed(scenario==='broadcast_overflow'?0:null);run('startBroadcastSync()');
  assert.equal(run('tourTimer'),0);assert(![...timers.values()].some(t=>t.ms===4000));
  if(scenario==='broadcast_expiry'){now=5100;run("applyBroadcastCue({ok:true,status:'running',item_index:1})");paused();}
  else if(scenario==='broadcast_overflow'){run("applyBroadcastCue({ok:true,status:'running',item_index:1})");paused();}
  else {
   run("applyBroadcastCue({ok:true,status:'running',item_index:0})");assert.equal(run('selected'),null);
   for(let item=1;item<=11;item++){run(`applyBroadcastCue({ok:true,status:'running',item_index:${item}})`);assert.equal(run('selected'),item-1);assert.equal(nodes.get('city-name').textContent,names[item-1]);}
   run("applyBroadcastCue({ok:true,status:'running',item_index:12})");assert.equal(run('selected'),null);
  }
 }else throw Error('unknown scenario');
 console.log(scenario+': passed (mocked layout, no browser)');
})().catch(error=>{console.error(error);process.exitCode=1;});
