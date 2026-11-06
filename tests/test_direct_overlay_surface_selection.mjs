import assert from 'node:assert/strict';
import test from 'node:test';
import {DIRECT_OVERLAY_SURFACE_SELECTION_CONTRACT_VERSION,selectDirectOverlaySurfaces} from '../overlays/direct_overlay_surface_selection.mjs';

const broadcastKeys=['broadcastSidebar','broadcastTop','broadcastGameGap','broadcastBottom'];
const legacyKeys=['event','stats','ops','improve','wildcard','avsync'];
function fixture(stageEnabled=true,env={}){
  const make=(key,region)=>({key,region,style:{zIndex:key==='twica'?'2147483645':'2147483630'}});
  const broadcastSurfaces=broadcastKeys.map((key,i)=>make(key,['sidebar','top','game-gap','bottom'][i]));
  const definitions=legacyKeys.map(k=>make(k));
  const twicaSurface=make('twica');
  return {stage:{enabled:stageEnabled},env,broadcastSurfaces,definitions,twicaSurface};
}
function keys(f){return selectDirectOverlaySurfaces(f).surfaces.map(x=>x.key);}
test('contract is a pure module with version 1',()=>{
  assert.equal(DIRECT_OVERLAY_SURFACE_SELECTION_CONTRACT_VERSION,1);
  assert.deepEqual(keys(fixture()),[...broadcastKeys,'twica','wildcard','avsync']);
});
test('dashboard keeps game plane clear and always excludes event/stats/ops/improve',()=>{
  const f=fixture(true,{SOREN_DIRECT_EVENT_OVERLAY_ENABLED:'1',SOREN_DIRECT_IMPROVE_OVERLAY_ENABLED:'1'});
  const selected=selectDirectOverlaySurfaces(f);
  assert.deepEqual(selected.surfaces.map(x=>x.key),[...broadcastKeys,'twica','wildcard','avsync']);
  assert.equal(selected.broadcastEnabled,true);
  assert.equal(selected.surfaces[0],f.broadcastSurfaces[0]);
  assert.equal(selected.surfaces[4],f.twicaSurface);
});
test('fullscreen or broadcast opt-out preserves legacy surface order and null broadcast state',()=>{
  const expected=[...legacyKeys,'twica'];
  for(const f of [fixture(false),fixture(true,{SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED:'0'}),fixture(true,{SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED:'FALSE'})]){
    assert.deepEqual(keys(f),expected);
    assert.equal(selectDirectOverlaySurfaces(f).broadcastEnabled,false);
  }
});
test('per-surface opt-outs keep just the allowed rails, wildcard and TwiCa',()=>{
  const f=fixture(true,{SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED:'1',
    SOREN_DIRECT_TWICA_OVERLAY_ENABLED:'no',SOREN_DIRECT_WILDCARD_OVERLAY_ENABLED:'off',
    SOREN_DIRECT_AV_SYNC_OVERLAY_ENABLED:'0'});
  assert.deepEqual(keys(f),broadcastKeys);
  const none=fixture(true,{SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED:'false',
    SOREN_DIRECT_EVENT_OVERLAY_ENABLED:'0',SOREN_DIRECT_STATS_OVERLAY_ENABLED:'0',
    SOREN_DIRECT_OPS_OVERLAY_ENABLED:'0',SOREN_DIRECT_IMPROVE_OVERLAY_ENABLED:'0',
    SOREN_DIRECT_WILDCARD_OVERLAY_ENABLED:'0',SOREN_DIRECT_AV_SYNC_OVERLAY_ENABLED:'0',
    SOREN_DIRECT_TWICA_OVERLAY_ENABLED:'0'});
  assert.deepEqual(keys(none),[]);
  assert.equal(selectDirectOverlaySurfaces(none).broadcastEnabled,false);
});
test('selection is non-mutating across toggle changes',()=>{
  const f=fixture();const first=keys(f);
  f.env.SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED='0';
  assert.deepEqual(keys(f),legacyKeys.concat('twica'));
  assert.deepEqual(first,[...broadcastKeys,'twica','wildcard','avsync']);
  assert.equal(f.broadcastSurfaces.length,4);
  assert.equal(f.definitions.length,6);
});
