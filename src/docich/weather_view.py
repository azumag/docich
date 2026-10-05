"""Read-only, loopback-only nationwide JMA weather presentation.

No live stream, queue, worker, model, warning generator or scheduler is touched.
Every response validates the captured raw bulletin again. Browser content has a
separate monotonic expiry so a dead server cannot leave a stale forecast on air.
"""
from __future__ import annotations

import argparse
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import time
import uuid

from .weather import (
    MAX_BUNDLE_BYTES, WeatherError, build_bundle, decode, narration, project,
    write_json,
)
from .weather_map_data import CITY_LABELS, CITY_POINTS, POLYGONS, VIEWBOX

DEFAULT_PORT = 8803
CUE_MAX_BYTES = 64 * 1024

_MAP_DATA = json.dumps({"polygons": POLYGONS, "points": CITY_POINTS, "labels": CITY_LABELS, "viewbox": VIEWBOX}, separators=(",", ":")).replace("<", "\\u003c")

HTML = r'''<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>docich 全国の天気予報</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#07111d;color:#edf5fa;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
button{font:inherit;color:inherit;cursor:pointer}#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:15px 19px 12px;background:radial-gradient(ellipse at 38% 40%,#15304a 0,#0b1b2a 57%,#08131f 100%);overflow:hidden}
header{height:43px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #355268;padding-bottom:7px}h1{font-size:25px;letter-spacing:.08em;margin:0;font-weight:750}.eyebrow{font-size:9px;letter-spacing:.24em;color:#81c6d7;margin-right:9px}.headline{display:flex;align-items:baseline}.headline small{font-size:11px;color:#a2b8c6;margin-left:12px;letter-spacing:.04em}.head-right{display:flex;align-items:center;gap:8px}.date{font-size:15px;font-weight:650;color:#e7f4f7}.tag{border:1px solid #3e6879;background:#102b39;color:#92d8de;padding:5px 9px;border-radius:99px;font-size:10px;letter-spacing:.04em}
#status-line{height:23px;display:flex;align-items:center;justify-content:space-between;color:#9eb5c4;font-size:10px;letter-spacing:.03em}.status-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#68d2b1;box-shadow:0 0 9px #68d2b188;margin-right:6px}.sync-note{color:#d5b982}
#content{height:calc(100% - 76px);display:grid;grid-template-columns:minmax(0,1.64fr) minmax(310px,1fr);gap:12px}
.panel{border:1px solid #294456;border-radius:10px;background:linear-gradient(145deg,#102335e8,#0b1b2ad9);box-shadow:0 12px 30px #02070c44;min-width:0;overflow:hidden}.map-panel{position:relative;display:flex;flex-direction:column}.panel-head{height:34px;flex:none;padding:0 12px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1c394c;color:#c5d9e4}.section-name{font-size:10px;letter-spacing:.16em}.map-help{font-size:9px;color:#7896a8}.map-wrap{position:relative;flex:1;display:flex;justify-content:center;align-items:center;min-height:0}.map-svg{height:100%;width:auto;max-width:100%;aspect-ratio:1220/960;overflow:visible}.ocean-grid{fill:none;stroke:#1d4053;stroke-width:1;stroke-dasharray:2 12;opacity:.44;vector-effect:non-scaling-stroke}.graticule{fill:none;stroke:#24475a;stroke-width:1;opacity:.48;vector-effect:non-scaling-stroke}.land{fill:#23475a;stroke:#76a8b1;stroke-width:1.35;stroke-linejoin:round;vector-effect:non-scaling-stroke}.land-shadow{fill:none;stroke:#4fa5b1;stroke-width:4;opacity:.12;vector-effect:non-scaling-stroke}.marker{cursor:pointer;outline:none}.marker .halo{fill:#43d5c1;opacity:.14}.marker .point{fill:#b1f4e4;stroke:#071521;stroke-width:3;vector-effect:non-scaling-stroke}.marker .label-bg{fill:#0a1b2a;stroke:#477083;stroke-width:1;opacity:.95;vector-effect:non-scaling-stroke}.marker text{font-size:22px;font-weight:700;fill:#e8f5fa;paint-order:stroke;stroke:#08141f;stroke-width:3px;stroke-linejoin:round}.marker.selected .halo{fill:#ffcd69;opacity:.25}.marker.selected .point{fill:#ffe08e}.marker.selected .label-bg{fill:#624a20;stroke:#ffcf6d}.marker:focus .point{stroke:#fff;stroke-width:5}.map-compass{position:absolute;left:11px;bottom:9px;font-size:9px;color:#64889b;letter-spacing:.13em}.map-scale{position:absolute;right:11px;bottom:9px;color:#64889b;font-size:9px}
.info-panel{padding:10px 11px;display:flex;flex-direction:column;gap:6px}.detail-top{display:flex;justify-content:space-between;align-items:center;min-height:18px}.detail-kicker{font-size:9px;color:#7fa4b5;letter-spacing:.14em}.region-name{font-size:10px;color:#a5c3ce}.weather-summary{height:72px;display:grid;grid-template-columns:58px minmax(0,1fr) auto;align-items:center;border-bottom:1px solid #274355;padding-bottom:6px}.weather-symbol{font-size:43px;text-align:center;line-height:1;color:#ffd178;text-shadow:0 0 18px #ffc65b44}.city-block{min-width:0}.city-name{font-size:25px;line-height:1.15;font-weight:760;letter-spacing:.04em}.weather-text{font-size:12px;line-height:1.45;color:#c7dbe4;max-height:35px;overflow:hidden;overflow-wrap:anywhere}.temperature{font-size:16px;font-weight:700;white-space:nowrap;text-align:right}.high{color:#ffb994}.low{color:#95d5f1}.temp-caption{font-size:9px;text-align:right;color:#7593a3;margin-top:3px}.issued{font-size:9px;color:#7794a4;margin-top:3px;white-space:nowrap}.pop-title{display:flex;justify-content:space-between;color:#9bb5c2;font-size:9px;margin-top:1px}.pop-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}.pop{background:#10283a;border:1px solid #234458;border-radius:5px;padding:5px 5px 4px;min-width:0}.pop-time{font-size:8px;color:#8ba7b4;white-space:nowrap}.pop-value{font-size:13px;font-weight:700;margin-top:1px}.pop-track{height:3px;background:#244454;border-radius:3px;margin-top:4px;overflow:hidden}.pop-fill{height:100%;background:linear-gradient(90deg,#58c8d0,#84e0b4);border-radius:3px}.selectors{border-top:1px solid #233f50;padding-top:5px}.selector-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}.selector-head span{font-size:8px;color:#7896a6;letter-spacing:.13em}.region-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px}.region-btn,.city-btn,.control-btn{border:1px solid #315166;border-radius:5px;background:#10283a;color:#afc7d2;transition:background .16s,border-color .16s,color .16s}.region-btn{font-size:8px;padding:4px 1px;white-space:nowrap}.region-btn.active,.city-btn.active{background:#174553;border-color:#47c6bf;color:#eafffb}.city-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px;margin-top:5px}.city-btn{height:23px;padding:2px 0;font-size:9px;white-space:nowrap}.control-row{display:flex;gap:5px;align-items:center;margin-top:auto;padding-top:3px}.control-btn{height:25px;padding:0 8px;font-size:9px}.control-btn.primary{background:#135667;border-color:#2f98a2;color:#ecffff}.control-btn.tour[aria-pressed="true"]{background:#5a4722;border-color:#c5a456;color:#fff2c9}.tour-state{font-size:8px;color:#7895a4;margin-left:auto;text-align:right}button:hover{border-color:#7dc6ce!important;color:#f5ffff!important}button:focus-visible{outline:2px solid #f5d480;outline-offset:1px}
#unavailable{position:absolute;inset:0;z-index:5;display:flex;align-items:center;justify-content:center;background:#07111de8;color:#f0d9ad;text-align:center;font-size:21px;line-height:1.7;letter-spacing:.06em;padding:40px}#unavailable[hidden]{display:none}
#footer{position:absolute;left:20px;right:20px;bottom:5px;display:flex;justify-content:space-between;gap:8px;color:#6f8d9e;font-size:8px;line-height:12px}#footer a{color:#8db1c1;text-decoration:none}#footer .warning{color:#91a3ac;text-align:right}
@media(max-aspect-ratio:5/4){html,body{height:auto;min-height:100%;overflow:auto}#stage{position:relative;width:100%;height:auto;min-height:100vh;padding:13px 12px 10px;overflow:visible}header{height:auto;min-height:45px;gap:8px}.eyebrow{font-size:7px;margin-right:5px}.headline small{display:none}h1{font-size:21px}.head-right{gap:4px;flex-wrap:wrap;justify-content:flex-end}.tag{font-size:8px;padding:4px 6px}.date{font-size:11px}#status-line{font-size:9px}#content{height:auto;grid-template-columns:minmax(0,1fr);gap:9px}.map-panel{min-height:min(88vw,390px)}.map-svg{height:min(72vw,345px);max-width:100%}.info-panel{min-height:395px;padding:10px}.region-btn{font-size:9px;padding:6px 2px}.city-btn{height:28px;font-size:10px}.control-btn{height:30px;font-size:10px}.weather-summary{height:78px}.weather-text{font-size:13px}#footer{position:relative;left:auto;right:auto;bottom:auto;display:grid;margin:10px 3px 0;font-size:9px;line-height:14px}#footer .warning{text-align:left}#unavailable{font-size:16px}}
</style>
<div id="stage">
<header><div class="headline"><span class="eyebrow">WEATHER / JAPAN</span><h1>全国の天気予報</h1><small>代表11地点</small></div><div class="head-right"><span class="tag">表示試作</span><span class="tag">音声同期</span><div id="date" class="date">気象庁発表</div></div></header>
<div id="status-line"><span><i class="status-dot"></i><span id="state">予報を確認中</span></span><span class="sync-note">時刻：日本時間 ／ 読み上げ cue：再生完了同期</span></div>
<main id="content">
<section class="panel map-panel" aria-label="日本地図">
<div class="panel-head"><span class="section-name">JAPAN / FORECAST MAP</span><span class="map-help">都市を選ぶと地図が拡大します</span></div>
<div class="map-wrap"><svg id="map" class="map-svg" viewBox="0 0 1220 960" role="img" aria-label="Natural Earthの日本地図。北海道、本州、四国、九州、沖縄と予報地点"></svg><span class="map-compass">N ↑　NATURAL EARTH / PUBLIC DOMAIN</span><span class="map-scale">表示点：予報地点</span></div>
<div id="unavailable" role="status">気象庁の予報を確認しています</div>
</section>
<section class="panel info-panel" aria-label="地点別の予報">
<div class="detail-top"><span class="detail-kicker">FORECAST FOCUS</span><span id="region-name" class="region-name">全国</span></div>
<div class="weather-summary"><div id="weather-symbol" class="weather-symbol" aria-hidden="true">—</div><div class="city-block"><div id="city-name" class="city-name">全国</div><div id="weather-text" class="weather-text">地点を選択してください</div><div id="issued" class="issued">気象庁発表の予報</div></div><div><div class="temperature"><span id="high" class="high">—</span><span style="color:#7695a5"> / </span><span id="low" class="low">—</span></div><div class="temp-caption">最高 / 最低 °C</div></div></div>
<div class="pop-title"><span>降水確率</span><span>6時間ごと</span></div><div id="pop-grid" class="pop-grid" aria-label="6時間ごとの降水確率"></div>
<div class="selectors"><div class="selector-head"><span>REGION / 地方</span><span>選択でクローズアップ</span></div><div id="region-grid" class="region-grid"></div><div class="selector-head" style="margin-top:6px"><span>CITY / 都市</span><span>地図マーカーからも選択可</span></div><div id="city-grid" class="city-grid"></div></div>
<div class="control-row"><button id="national" class="control-btn">全国表示</button><button id="previous" class="control-btn" aria-label="前の地点">←</button><button id="next" class="control-btn" aria-label="次の地点">→</button><button id="tour" class="control-btn tour" aria-pressed="false">順送りズーム</button><span id="tour-state" class="tour-state">停止中</span></div>
</section></main>
<div id="footer"><span>出典：<a href="https://www.jma.go.jp/bosai/forecast/">気象庁ホームページ</a> ／ 気象庁の発表をもとにdocichが編集</span><span class="warning">地図：Natural Earth 1:10m・Public Domain ／ 予報の加工・独自補間なし</span></div>
</div>
<script>
'use strict';
const MAP_DATA=__MAP_DATA__;
const EXPECTED=['札幌','仙台','東京','新潟','名古屋','大阪','広島','高松','福岡','鹿児島','那覇'];
const PLACES=[['sapporo','札幌','北海道',0],['sendai','仙台','東北',1],['tokyo','東京','関東',2],['niigata','新潟','北陸',3],['nagoya','名古屋','東海',4],['osaka','大阪','近畿',5],['hiroshima','広島','中国',6],['takamatsu','高松','四国',7],['fukuoka','福岡','九州',8],['kagoshima','鹿児島','九州',9],['naha','那覇','沖縄',10]];
const REGIONS=[['北海道',0],['東北',1],['関東',2],['北陸',3],['東海',4],['近畿',5],['中国',6],['四国',7],['九州',8],['沖縄',10]];
const map=document.getElementById('map'), unavailable=document.getElementById('unavailable');
const BROADCAST=false;
let until=0,view=null,selected=null,pollVersion=0,cuePollVersion=0,cueBusy=false,tourTimer=0,tourIndex=0,focusScale=2.3;
function fit(){const el=document.getElementById('stage');if(matchMedia('(max-aspect-ratio:5/4)').matches){el.style.position='relative';el.style.transform='none';el.style.left='auto';el.style.top='auto';return;}const s=Math.min(innerWidth/960,innerHeight/540);el.style.position='absolute';el.style.transform=`scale(${s})`;el.style.left=`${(innerWidth-960*s)/2}px`;el.style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',()=>{fit();if(view)render();});fit();
function svg(tag,attrs={}){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,String(value));return n;}
const MAP_WIDTH=MAP_DATA.viewbox[0],MAP_HEIGHT=MAP_DATA.viewbox[1];
map.setAttribute('viewBox',`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`);
const defs=svg('defs'),gradient=svg('linearGradient',{id:'sea',x1:'0',x2:'0',y1:'0',y2:'1'});gradient.append(svg('stop',{offset:'0','stop-color':'#0c2638'}),svg('stop',{offset:'1','stop-color':'#0a1b2b'}));defs.append(gradient);map.append(defs,svg('rect',{x:0,y:0,width:MAP_WIDTH,height:MAP_HEIGHT,fill:'url(#sea)'}));
for(let x=120;x<MAP_WIDTH;x+=100)map.append(svg('path',{d:`M${x} 0V${MAP_HEIGHT}`,class:'ocean-grid'}));
for(let y=80;y<MAP_HEIGHT;y+=100)map.append(svg('path',{d:`M0 ${y}H${MAP_WIDTH}`,class:'ocean-grid'}));
const geometry=svg('g',{id:'geometry'});for(const d of MAP_DATA.polygons){geometry.append(svg('path',{d,class:'land-shadow'}),svg('path',{d,class:'land'}));}map.append(geometry);
const markers=svg('g',{id:'markers'});map.append(markers);const markerNodes=[];
function setStatus(txt){document.getElementById('state').textContent=txt;}
function temperature(v){return v===null?'—':`${v}°`;}
function safeDate(value){return typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value);}
function validForecast(data){
 if(!data||data.ok!==true||!safeDate(data.date)||!Array.isArray(data.cities)||data.cities.length!==EXPECTED.length)return false;
 if(!Number.isFinite(data.expires_at)||!Number.isFinite(data.server_now))return false;
 return data.cities.every((c,i)=>c&&c.city===EXPECTED[i]&&typeof c.weather==='string'&&c.weather.length>0&&c.weather.length<=240&&!/[<>\u0000-\u001f]/.test(c.weather)&&typeof c.issued_at==='string'&&Number.isFinite(Date.parse(c.issued_at))&&[c.high_c,c.low_c].every(v=>v===null||(typeof v==='number'&&Number.isFinite(v)&&v>=-60&&v<=60))&&Array.isArray(c.pops)&&c.pops.length===4&&c.pops.every((p,j)=>p&&p.start===[0,6,12,18][j]&&p.end===p.start+6&&(p.percent===null||(typeof p.percent==='number'&&Number.isFinite(p.percent)&&p.percent>=0&&p.percent<=100))));
 }
function iconFor(text){if(/雪|みぞれ/.test(text))return '❄';if(/雨/.test(text))return '☂';if(/曇|くもり/.test(text))return '☁';if(/晴/.test(text))return '☀';return '—';}
function labelOffset(id){const o=MAP_DATA.labels[id]||[12,0];return o;}
for(const [id,name,region,index] of PLACES){
 const point=MAP_DATA.points[id],offset=labelOffset(id),g=svg('g',{class:'marker',tabindex:'0',role:'button','data-index':index,'aria-label':`${name}の予報を表示`});
 const halo=svg('circle',{class:'halo',cx:point[0],cy:point[1],r:18});const dot=svg('circle',{class:'point',cx:point[0],cy:point[1],r:7});const labelBg=svg('rect',{class:'label-bg',x:point[0]+offset[0]-6,y:point[1]+offset[1]-17,width:name.length*22+22,height:29,rx:8});const text=svg('text',{x:point[0]+offset[0]+3,y:point[1]+offset[1]+4});text.textContent=name;
 g.append(halo,dot,labelBg,text);g.addEventListener('click',()=>choose(index,2.3));g.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(index,2.3);}});markers.append(g);markerNodes.push(g);
}
function cityFor(index){return view?.cities[index]||null;}
function clearTour(){if(tourTimer){clearInterval(tourTimer);tourTimer=0;}document.getElementById('tour').setAttribute('aria-pressed','false');document.getElementById('tour').textContent='順送りズーム';document.getElementById('tour-state').textContent='停止中';}
function hide(message='最新の予報を確認できないため休止中'){clearTour();until=0;view=null;selected=null;focusScale=2.3;unavailable.hidden=false;unavailable.textContent=message;setStatus('予報休止');document.getElementById('date').textContent='気象庁発表';document.getElementById('weather-symbol').textContent='—';document.getElementById('city-name').textContent='—';document.getElementById('region-name').textContent='予報なし';document.getElementById('weather-text').textContent='';document.getElementById('issued').textContent='';document.getElementById('high').textContent='—';document.getElementById('low').textContent='—';document.getElementById('pop-grid').replaceChildren();document.getElementById('city-grid').replaceChildren();for(const node of markerNodes){node.classList.remove('selected');node.querySelector('text').textContent=PLACES[Number(node.dataset.index)][1];}updateMap();}
function renderPop(city){const grid=document.getElementById('pop-grid');grid.replaceChildren();for(const p of city.pops){const box=document.createElement('div');box.className='pop';const when=document.createElement('div');when.className='pop-time';when.textContent=`${String(p.start).padStart(2,'0')}–${String(p.end).padStart(2,'0')}時`;const value=document.createElement('div');value.className='pop-value';value.textContent=p.percent===null?'—':`${p.percent}%`;const track=document.createElement('div');track.className='pop-track';const fill=document.createElement('div');fill.className='pop-fill';fill.style.width=`${p.percent===null?0:p.percent}%`;track.append(fill);box.append(when,value,track);grid.append(box);}}
function renderCities(){const grid=document.getElementById('city-grid');grid.replaceChildren();for(const [id,name,region,index] of PLACES){const b=document.createElement('button');b.type='button';b.className='city-btn'+(selected===index?' active':'');b.textContent=name;b.setAttribute('aria-pressed',String(selected===index));b.addEventListener('click',()=>choose(index,2.3));grid.append(b);}}
function renderRegions(){const grid=document.getElementById('region-grid');grid.replaceChildren();for(const [name,index] of REGIONS){const b=document.createElement('button');b.type='button';b.className='region-btn'+(selected!==null&&PLACES[selected][2]===name?' active':'');b.textContent=name;b.setAttribute('aria-pressed',String(selected!==null&&PLACES[selected][2]===name));b.addEventListener('click',()=>choose(index,1.55));grid.append(b);}}
function updateMap(){const c=selected===null?null:cityFor(selected);if(!c){map.setAttribute('viewBox',`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`);}else{const id=PLACES[selected][0],p=MAP_DATA.points[id],w=MAP_WIDTH/focusScale,h=MAP_HEIGHT/focusScale,x=Math.max(0,Math.min(MAP_WIDTH-w,p[0]-w/2)),y=Math.max(0,Math.min(MAP_HEIGHT-h,p[1]-h/2));map.setAttribute('viewBox',`${x} ${y} ${w} ${h}`);}for(const node of markerNodes){const i=Number(node.dataset.index),city=cityFor(i),name=PLACES[i][1],label=city?`${name} ${city.high_c===null?'—':city.high_c+'°'}`:name;node.querySelector('text').textContent=selected===null?label:name;node.classList.toggle('selected',selected===i);node.setAttribute('aria-pressed',String(selected===i));}}
function choose(index,scale=2.3){if(!view||!Number.isInteger(index)||index<0||index>=view.cities.length)return;clearTour();selected=index;focusScale=scale;render();}
function renderFocus(){const c=cityFor(selected);if(!c){document.getElementById('city-name').textContent='全国';document.getElementById('region-name').textContent='全国';document.getElementById('weather-symbol').textContent='◎';document.getElementById('weather-text').textContent='地点を選択すると予報を拡大表示';document.getElementById('issued').textContent='代表11地点 ／ 気象庁発表';document.getElementById('high').textContent='—';document.getElementById('low').textContent='—';document.getElementById('pop-grid').replaceChildren();return;}
 document.getElementById('city-name').textContent=c.city;document.getElementById('region-name').textContent=`${PLACES[selected][2]}地方`;document.getElementById('weather-symbol').textContent=iconFor(c.weather);const w=document.getElementById('weather-text');w.textContent=c.weather;document.getElementById('issued').textContent=`気象庁 ${c.issued_at.slice(5,10).replace('-','/')} ${c.issued_at.slice(11,16)} 発表`;document.getElementById('high').textContent=temperature(c.high_c);document.getElementById('low').textContent=temperature(c.low_c);renderPop(c);}
function national(){if(!view)return;clearTour();selected=null;render();}
function advance(delta){if(!view)return;const index=selected===null?(delta>0?0:view.cities.length-1):(selected+delta+view.cities.length)%view.cities.length;choose(index,2.3);}
function toggleTour(){if(!view)return;if(performance.now()>=until){hide();return;}if(tourTimer){clearTour();return;}tourIndex=selected===null?0:(selected+1)%view.cities.length;choose(tourIndex,2.3);if(!view)return;document.getElementById('tour').setAttribute('aria-pressed','true');document.getElementById('tour').textContent='順送り停止';document.getElementById('tour-state').textContent='7秒ごとに地点移動';tourTimer=setInterval(()=>{if(!view||performance.now()>=until){hide();return;}tourIndex=(tourIndex+1)%view.cities.length;selected=tourIndex;render();},7000);}
function cueSelection(itemIndex){return itemIndex>=1&&itemIndex<=11?itemIndex-1:null;}
function applyBroadcastCue(data){
 if(!BROADCAST||!view||!data||data.ok!==true||!Number.isInteger(data.item_index)||data.item_index<0||data.item_index>12)return;
 const target=cueSelection(data.item_index);
 if(selected!==target){selected=target;focusScale=2.3;render();}
 const label=target===null?(data.item_index===0?'全国予報を読み上げ中':'まとめを読み上げ中'):`${PLACES[target][1]}を読み上げ中`;
 setStatus(data.status==='completed'?'音声解説が完了しました':label);
 document.getElementById('tour-state').textContent=data.status==='completed'?'音声解説完了':'音声完了に合わせて地点移動';
}
async function pollCue(){
 if(!BROADCAST||!view||cueBusy||performance.now()>=until)return;
 cueBusy=true;const version=++cuePollVersion;
 try{const response=await fetch('/api/weather-cue',{cache:'no-store',signal:AbortSignal.timeout(700)});if(response.status===503)return;if(!response.ok)throw Error('cue-unavailable');const data=await response.json();if(version!==cuePollVersion)return;applyBroadcastCue(data);}catch(_){}finally{cueBusy=false;}
}
function startBroadcastSync(){clearTour();document.getElementById('tour').setAttribute('aria-pressed','true');document.getElementById('tour').textContent='音声同期中';document.getElementById('tour-state').textContent='音声完了に合わせて地点移動';pollCue();}
function render(){if(!view||performance.now()>=until){hide();return;}document.getElementById('date').textContent=view.date.replaceAll('-',' / ');setStatus('気象庁の発表を表示中');unavailable.hidden=true;renderFocus();renderCities();renderRegions();updateMap();const w=document.getElementById('weather-text');if(w.scrollHeight>w.clientHeight+1||w.scrollWidth>w.clientWidth+1)hide('予報文が画面内に収まらないため休止中');}
function hideUntilReady(){hide('気象庁の予報を確認しています');}
async function poll(){const version=++pollVersion,requested=performance.now();try{const response=await fetch('/api/weather',{cache:'no-store',signal:AbortSignal.timeout(2000)});if(!response.ok)throw Error('unavailable');const data=await response.json();if(version!==pollVersion)return;if(!validForecast(data))throw Error('invalid');const remaining=(data.expires_at-data.server_now)*1000-(performance.now()-requested);if(!Number.isFinite(remaining)||remaining<=0)throw Error('expired');const first=!view;view=data;until=performance.now()+Math.min(remaining,5000);render();if(first&&view&&BROADCAST)startBroadcastSync();}catch(_){if(version===pollVersion)hide();}}
for(const [id,fn] of [['national',national],['previous',()=>advance(-1)],['next',()=>advance(1)],['tour',toggleTour]])document.getElementById(id).addEventListener('click',fn);
setInterval(()=>{if(until&&performance.now()>=until)hide();},100);setInterval(()=>{if(BROADCAST&&view)pollCue();},250);
addEventListener('pageshow',()=>{hideUntilReady();poll();});addEventListener('visibilitychange',()=>{hideUntilReady();if(!document.hidden)poll();});setInterval(poll,2000);hideUntilReady();poll();
</script></html>
'''

HTML = HTML.replace("__MAP_DATA__", _MAP_DATA)


def read_view(path: Path, *, clock=time.time) -> dict:
    # Bound before decoding; an accidentally huge local file cannot exhaust RAM.
    with path.open('rb') as handle:
        raw = handle.read(MAX_BUNDLE_BYTES + 1)
    return project(decode(raw, limit=MAX_BUNDLE_BYTES), now=clock())



def read_presentation_cue(path: Path, *, runtime_id: str, generation, lease_id) -> dict:
    """Return only the runtime-bound ordinal needed to synchronize the map."""
    if type(generation) is not int or generation < 1 or not isinstance(lease_id, str):
        raise WeatherError("weather-cue-runtime-unavailable")
    try:
        with path.open("rb") as handle:
            raw = handle.read(CUE_MAX_BYTES + 1)
    except OSError as exc:
        raise WeatherError("weather-cue-unavailable") from exc
    if len(raw) > CUE_MAX_BYTES:
        raise WeatherError("weather-cue-invalid")
    try:
        state = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WeatherError("weather-cue-invalid") from exc
    expected = {
        "game": "weather-view", "runtime_id": runtime_id,
        "generation": generation, "lease_id": lease_id,
    }
    if (not isinstance(state, dict) or state.get("schema_version") != 1
            or state.get("status") != "active"
            or state.get("weather_runtime_identity") != expected):
        raise WeatherError("weather-cue-owner-mismatch")
    delivery = state.get("audio_delivery")
    if delivery is None:
        return {"ok": True, "status": "waiting", "item_index": 0}
    if not isinstance(delivery, dict):
        raise WeatherError("weather-cue-invalid")
    status = delivery.get("status")
    next_index = delivery.get("next_index")
    if status not in {"running", "stopping", "completed", "stopped", "failed"}:
        raise WeatherError("weather-cue-invalid")
    if type(next_index) is not int or not 0 <= next_index <= 13:
        raise WeatherError("weather-cue-invalid")
    if status in {"running", "stopping"} and next_index >= 13:
        raise WeatherError("weather-cue-invalid")
    if status == "completed" and next_index != 13:
        raise WeatherError("weather-cue-invalid")
    return {"ok": True, "status": status, "item_index": min(next_index, 12)}


def handler_for(path: Path, runtime_id: str = "preview", *, generation=None,
                lease_id=None, cue_path: Path | None = None, clock=time.time):
    if not isinstance(runtime_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", runtime_id) is None:
        raise WeatherError("invalid-runtime-id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise WeatherError("invalid-generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise WeatherError("invalid-lease-id") from None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # no request paths, raw data or operational identities in logs

        def reply(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            port = self.server.server_port
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.reply(403, b"forbidden", "text/plain; charset=utf-8")
                return
            if self.path in {"/", "/broadcast"}:
                html = HTML.replace("const BROADCAST=false;", "const BROADCAST=true;") if self.path == "/broadcast" else HTML
                self.reply(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path == "/api/weather-cue":
                if cue_path is None:
                    data, status = {"ok": False, "reason": "cue-unavailable"}, 503
                else:
                    try:
                        data = read_presentation_cue(
                            cue_path, runtime_id=runtime_id, generation=generation,
                            lease_id=lease_id,
                        )
                        status = 200
                    except (OSError, WeatherError):
                        data, status = {"ok": False, "reason": "cue-unavailable"}, 503
                self.reply(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")
                return
            if self.path != "/api/weather":
                self.reply(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                data = read_view(path, clock=clock)
                data.update(ok=True, runtime_id=runtime_id, generation=generation,
                            lease_id=lease_id,
                            narration=narration(data))
                status = 200
            except (OSError, WeatherError):
                data, status = {"ok": False, "reason": "forecast-unavailable"}, 503
            self.reply(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

    return Handler


def serve(path: Path, *, port=DEFAULT_PORT, runtime_id="preview", generation=None,
          lease_id=None) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise WeatherError("invalid-port")
    # No --host switch: the listener can never bind publicly by configuration.
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(path, runtime_id, generation=generation, lease_id=lease_id,
                    cue_path=path.parent.parent / "weather_corner.json"),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def refresh_snapshot(state_dir: Path, *, day="auto") -> dict:
    """Publish only a fully validated bundle; share the CLI's single-flight lock."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "snapshot.json"
    with (state_dir / ".fetch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path.unlink(missing_ok=True)
        bundle = build_bundle(state_dir / "cache", day=day)
        # Validate before publication, including elapsed network time.
        project(bundle, now=time.time())
        write_json(path, bundle)
        return read_view(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="気象庁全国予報の取得・原稿・読み取り専用画面")
    parser.add_argument("--state-dir", type=Path, required=True, help="専用weather artifact/cacheディレクトリ")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="全11地点を検証し、成功時だけsnapshotを公開")
    fetch.add_argument("--day", choices=("auto", "today", "tomorrow"), default="auto")
    sub.add_parser("status", help="有効なsnapshotの状態のみを表示")
    sub.add_parser("narration", help="検証済みsnapshotの定型原稿をJSON出力（音声queueには送らない）")
    server = sub.add_parser("serve", help="外部通信しない読み取り専用画面")
    server.add_argument("--port", type=int, default=DEFAULT_PORT)
    server.add_argument("--runtime-id", default="preview")
    server.add_argument("--generation", type=int)
    server.add_argument("--lease-id")
    args = parser.parse_args(argv)
    path = args.state_dir / "snapshot.json"
    try:
        if args.command == "fetch":
            data = refresh_snapshot(args.state_dir, day=args.day)
            print(json.dumps({"ok": True, "date": data["date"], "cities": len(data["cities"]), "expires_at": data["expires_at"]}))
        elif args.command == "serve":
            serve(path, port=args.port, runtime_id=args.runtime_id,
                  generation=args.generation, lease_id=args.lease_id)
        else:
            data = read_view(path)
            print(json.dumps({"ok": True, "date": data["date"], "narration": narration(data)} if args.command == "narration" else {"ok": True, "date": data["date"], "expires_at": data["expires_at"]}, ensure_ascii=False))
        return 0
    except (WeatherError, OSError):
        print(json.dumps({"ok": False, "reason": "forecast-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
