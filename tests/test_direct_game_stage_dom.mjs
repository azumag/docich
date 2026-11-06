import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {DIRECT_GAME_STAGE_DOM_CONTRACT_VERSION,applyDirectGameStageDom} from '../overlays/direct_game_stage_dom.mjs';

const STAGE = Object.freeze({
  enabled:true,elementId:'soren-direct-stream-stage',mode:'dashboard',
  outputWidth:1280,outputHeight:720,
  sidebarLeft:960,sidebarWidth:320,
  gameWidth:960,gameHeight:540,gameLeft:0,gameTop:90,
  topRailHeight:90,bottomRailTop:630,bottomRailHeight:90,
});

function fixture({canvas=true,container=true,old=true}={}){
  const children=[];
  let removed=0;
  const unityCanvas=canvas?{style:{},width:576,height:324}:null;
  const unityContainer=container?{style:{}}:null;
  const oldStage=old?{remove(){removed++;}}:null;
  const document={
    body:{style:{},appendChild(element){children.push(element);}},
    documentElement:{style:{}},
    querySelector(selector){
      if(selector==='#unity-container')return unityContainer;
      if(selector==='#unity-canvas')return unityCanvas;
      if(selector==='#unity-footer'||selector==='#unity-loading-bar')return controls[selector];
      return null;
    },
    getElementById(id){return id===STAGE.elementId?oldStage:null;},
    createElement(tag){
      assert.equal(tag,'div');
      return {tagName:tag,style:{},children:[],setAttribute(name,value){this[name]=value;},
        appendChild(child){this.children.push(child);}};
    },
  };
  const controls={'#unity-footer':{style:{}},'#unity-loading-bar':{style:{}}};
  const run=(options={})=>vm.runInNewContext(
    '('+applyDirectGameStageDom.toString()+')(payload)',
    {document,payload:{stage:{...STAGE},containerSelector:'#unity-container',
      canvasSelector:'#unity-canvas',drawBufferWidth:0,drawBufferHeight:0,...options}},
  );
  return {run,children,unityCanvas,unityContainer,controls,document,get removed(){return removed;}};
}

test('versioned stage DOM function is browser-serializable and changes no game at import',()=>{
  assert.equal(DIRECT_GAME_STAGE_DOM_CONTRACT_VERSION,1);
  assert.equal(applyDirectGameStageDom.toString().startsWith('function applyDirectGameStageDom('),true);
  assert.equal(applyDirectGameStageDom.toString().includes('import '),false);
});

test('1280x720 dashboard stage preserves game drawing buffer and creates three hidden backing rails',()=>{
  const f=fixture();
  const result=f.run();
  assert.equal(result.stageMode,'dashboard');
  assert.equal(result.gameWidth,960);
  assert.equal(result.gameTop,90);
  assert.equal(result.canvasWidth,576);
  assert.equal(result.canvasHeight,324);
  assert.equal(f.unityCanvas.width,576);assert.equal(f.unityCanvas.height,324);
  assert.equal(f.unityCanvas.style.width,'960px');assert.equal(f.unityCanvas.style.height,'540px');
  assert.equal(f.unityCanvas.style.imageRendering,'pixelated');
  assert.equal(f.unityContainer.style.position,'fixed');
  assert.equal(f.unityContainer.style.top,'90px');
  assert.equal(f.unityContainer.style.zIndex,'2147483621');
  assert.equal(f.document.body.style.overflow,'hidden');
  assert.equal(f.controls['#unity-footer'].style.display,'none');
  assert.equal(f.controls['#unity-loading-bar'].style.display,'none');
  assert.equal(f.removed,1);
  assert.equal(f.children.length,1);
  const stage=f.children[0];
  assert.equal(stage.id,STAGE.elementId);
  assert.equal(stage['aria-hidden'],'true');
  assert.equal(stage.style.zIndex,'2147483620');
  assert.equal(stage.style.pointerEvents,'none');
  assert.equal(stage.children.length,3);
  assert.equal(stage.children[0].style.left,'960px');
  assert.equal(stage.children[0].style.width,'320px');
  assert.equal(stage.children[1].style.height,'90px');
  assert.equal(stage.children[2].style.top,'630px');
  assert.equal(stage.children[2].style.height,'90px');
  assert.equal(stage.children.every(p=>p.style.opacity==='0'),true);
});

test('explicit drawing buffer overrides are applied without inferring them from CSS size',()=>{
  const f=fixture({old:false});
  const result=f.run({drawBufferWidth:640,drawBufferHeight:360});
  assert.equal(result.canvasWidth,640);
  assert.equal(result.canvasHeight,360);
  assert.equal(f.unityCanvas.style.width,'960px');
  assert.equal(f.removed,0);
});

test('missing game container/canvas fails before resizing or touching drawing buffer',()=>{
  for(const options of [{canvas:false},{container:false}]){
    const f=fixture(options);
    assert.throws(()=>f.run(),/game stage container\/canvas not found/);
    assert.equal(f.children.length,1);
    if(f.unityCanvas)assert.equal(f.unityCanvas.width,576);
  }
});
