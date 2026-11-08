import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {directOverlaySurfaceVisible, DIRECT_OVERLAY_VISIBILITY_CONTRACT_VERSION} from '../overlays/direct_overlay_visibility.mjs';

test('docich-owned visibility contract is versioned and keeps always-visible rails',()=>{
  assert.equal(DIRECT_OVERLAY_VISIBILITY_CONTRACT_VERSION, 1);
  for(const input of [null,undefined,{visibility:'always'}]){
    assert.equal(directOverlaySurfaceVisible(input),true);
  }
  // Legacy behavior: a surface object with an absent visibility flag is hidden.
  assert.equal(directOverlaySurfaceVisible({visibility:undefined}),false);
  assert.equal(directOverlaySurfaceVisible({visibility:'unknown'}),false);
});

test('wildcard, dashboard and improve preserve fail-open and exclusion priority',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'docich-visibility-'));
  const wildcard=path.join(root,'wildcard.json'),improve=path.join(root,'improve.json');
  const dashboard={visibility:'dashboard',suppressStateFile:wildcard};
  const wildcardItem={visibility:'wildcard',visibilityStateFile:wildcard};
  const improveItem={visibility:'improve',suppressStateFile:wildcard,visibilityStateFile:improve};
  try {
    assert.equal(directOverlaySurfaceVisible(dashboard),true);
    assert.equal(directOverlaySurfaceVisible(wildcardItem),false);
    assert.equal(directOverlaySurfaceVisible(improveItem),false);
    fs.writeFileSync(improve,JSON.stringify({status:'RUNNING'}));
    fs.writeFileSync(wildcard,JSON.stringify({phase:'idle'}));
    assert.equal(directOverlaySurfaceVisible(dashboard),true);
    assert.equal(directOverlaySurfaceVisible(wildcardItem),false);
    assert.equal(directOverlaySurfaceVisible(improveItem),true);
    for(const phase of ['generating','RUNNING']){
      fs.writeFileSync(wildcard,JSON.stringify({phase}));
      assert.equal(directOverlaySurfaceVisible(dashboard),false);
      assert.equal(directOverlaySurfaceVisible(wildcardItem),true);
      assert.equal(directOverlaySurfaceVisible(improveItem),false);
    }
    fs.writeFileSync(wildcard,'{broken');
    assert.equal(directOverlaySurfaceVisible(wildcardItem),false);
    assert.equal(directOverlaySurfaceVisible(dashboard),true);
    fs.writeFileSync(wildcard,'[]');
    assert.equal(directOverlaySurfaceVisible(wildcardItem),false);
    fs.writeFileSync(wildcard,JSON.stringify({phase:'idle'}));
    fs.writeFileSync(improve,JSON.stringify({status:'stopped'}));
    assert.equal(directOverlaySurfaceVisible(improveItem),false);
    fs.writeFileSync(improve,'{broken');
    assert.equal(directOverlaySurfaceVisible(improveItem),false);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});
