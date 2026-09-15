"""Self-contained browser UI for the read-only NetHack spectator."""

HTML_PAGE = r'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'">
<title>Docich NetHack Spectator</title>
<style>
:root{color-scheme:dark;--tile:30px;--panel:#15171c;--edge:#303640;--text:#edf0f5;--muted:#9ca6b7}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#090b0e;color:var(--text);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
#app{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:14px;height:100vh;padding:14px}
#stage{position:relative;overflow:hidden;border:1px solid var(--edge);border-radius:12px;background:radial-gradient(circle at 50% 48%,#161a20,#080a0d 68%);box-shadow:inset 0 0 40px #000}
#board{position:absolute;display:grid;grid-auto-rows:var(--tile)}
.tile{width:var(--tile);height:var(--tile);position:relative;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:16px;user-select:none}
.tile.floor::before{content:"";width:5px;height:5px;border-radius:50%;background:#51545a}.tile.corridor::before{content:"";width:11px;height:11px;border-radius:3px;background:#565b62}
.tile.wall{background:linear-gradient(135deg,#3d4651,#242a31);border:1px solid #596573}.tile.door{background:#72583b;border:2px solid #a88455}.tile.stairs{font-size:23px;color:#dfe8ff}.tile.trap{color:#ff796f;font-size:22px}.tile.water{background:#17354f;color:#77c9ff}
.tile.player::before{content:"";width:20px;height:20px;border-radius:50%;background:#f5d65c;box-shadow:0 0 12px #f5d65c99}.tile.player::after{content:"@";position:absolute;color:#25210f;font-size:13px}
.tile.monster{border-radius:40%;background:#6b3037;color:#ffe5e7;border:1px solid #a84b58}.tile.item{border-radius:50%;background:#243a50;border:1px solid #4d7699;color:#d5ecff;font-size:13px}.tile.gold{color:#ffda58;background:#493c17}.tile.food{color:#f1b36a}.tile.unknown{color:#9ba5b4}
#message{position:absolute;left:14px;right:14px;top:14px;min-height:44px;padding:11px 14px;border-radius:9px;background:#101318dd;border:1px solid #363d48;font-size:17px;line-height:1.3;z-index:5}
#terminal{display:none;position:absolute;inset:72px 18px 18px;white-space:pre;font-size:clamp(11px,1.42vw,20px);line-height:1.1;color:#d8dde6;background:#0a0c0fdd;padding:18px;border-radius:9px;border:1px solid var(--edge);overflow:hidden}
#side{display:flex;flex-direction:column;gap:12px;min-width:0}.card{background:var(--panel);border:1px solid var(--edge);border-radius:12px;padding:14px}.eyebrow{font-size:12px;color:#7f8b9d;letter-spacing:.15em}.title{font-size:22px;font-weight:900;margin-top:4px}.expedition{margin-top:9px;font-size:18px;color:#f0cf65}
.statusgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.metric{background:#0d1014;border:1px solid #262c34;border-radius:8px;padding:9px}.metric b{display:block;font-size:20px;margin-top:3px}.metric span{font-size:11px;color:var(--muted)}
.bar{height:8px;border-radius:8px;background:#242930;overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;width:0}.hpbar i{background:#c2555c}.pwbar i{background:#5579bf}.legend{display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;color:#bbc3cf}.legend span{padding:5px 7px;border-radius:6px;background:#0d1014}.footer{margin-top:auto;font-size:11px;color:#697485}.error{color:#ff8585}
</style>
</head>
<body>
<div id="app">
<section id="stage"><div id="message">NetHackを準備中…</div><div id="board"></div><pre id="terminal"></pre></section>
<aside id="side">
<div class="card"><div class="eyebrow">DOCICH / NETHACK</div><div class="title">AI、ダンジョンに潜る</div><div id="expedition" class="expedition">遠征情報を取得中</div></div>
<div class="card"><div class="eyebrow">STATUS</div><div class="statusgrid">
<div class="metric"><span>HP</span><b id="hp">–</b><div class="bar hpbar"><i id="hpfill"></i></div></div>
<div class="metric"><span>Pw</span><b id="pw">–</b><div class="bar pwbar"><i id="pwfill"></i></div></div>
<div class="metric"><span>階層</span><b id="dlvl">–</b></div><div class="metric"><span>AC</span><b id="ac">–</b></div>
<div class="metric"><span>経験</span><b id="exp">–</b></div><div class="metric"><span>Turn</span><b id="turn">–</b></div>
</div></div>
<div class="card"><div class="eyebrow">LEGEND</div><div class="legend"><span>● 冒険者</span><span>A–Z モンスター</span><span>! ポーション</span><span>? 巻物</span><span>) 武器</span><span>[ 防具</span><span>&gt;/&lt; 階段</span><span>$ ゴールド</span></div></div>
<div id="health" class="footer">spectator: connecting</div>
</aside></div>
<script>
const board=document.getElementById('board'),stage=document.getElementById('stage'),terminal=document.getElementById('terminal');
const itemChars=new Set(['!','?',')','[','/','=','*','"','(','%','$']);
function klass(ch){if(ch===' ')return 'empty';if(ch==='@')return 'player';if(ch==='.')return 'floor';if(ch==='#')return 'corridor';if(ch==='|'||ch==='-')return 'wall';if(ch==='+')return 'door';if(ch==='>'||ch==='<')return 'stairs';if(ch==='^')return 'trap';if(ch==='}'||ch==='{')return 'water';if(itemChars.has(ch))return ch==='$'?'item gold':(ch==='%'?'item food':'item');if(/[A-Za-z&;:]/.test(ch))return 'monster';return 'unknown'}
function label(ch,k){if(k==='stairs')return ch==='>'?'▼':'▲';if(k==='trap')return '△';if(k.includes('item')||k==='monster')return ch;if(k==='water')return '≈';if(k==='unknown')return ch;return ''}
function metric(id,value){document.getElementById(id).textContent=value===null||value===undefined||value===''?'–':String(value)}
function ratio(cur,max){if(typeof cur!=='number'||typeof max!=='number'||max<=0)return 0;return Math.max(0,Math.min(100,cur/max*100))}
function drawMap(s){terminal.style.display='none';board.style.display='grid';const rows=s.map||[];const h=rows.length,w=rows.reduce((n,r)=>Math.max(n,r.length),0)||80;board.style.gridTemplateColumns=`repeat(${w},var(--tile))`;board.replaceChildren();const frag=document.createDocumentFragment();for(let y=0;y<h;y++){const row=rows[y]||'';for(let x=0;x<w;x++){const ch=row[x]||' ';const k=klass(ch);const t=document.createElement('div');t.className=`tile ${k}`;t.dataset.glyph=ch;t.textContent=label(ch,k);frag.appendChild(t)}}board.appendChild(frag);requestAnimationFrame(()=>{const tile=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--tile'))||30;const p=s.player||{x:Math.floor(w/2),y:Math.floor(h/2)};board.style.left=`${stage.clientWidth/2-(p.x+.5)*tile}px`;board.style.top=`${stage.clientHeight/2-(p.y+.5)*tile+24}px`})}
function drawTerminal(s){board.style.display='none';terminal.style.display='block';terminal.textContent=(s.terminal||[]).join('\n')}
function render(payload){const s=payload.snapshot||{};document.getElementById('message').textContent=s.message||' ';if(s.mode==='map')drawMap(s);else drawTerminal(s);const st=s.status||{};metric('hp',st.hp==null?'–':`${st.hp}/${st.max_hp??'–'}`);metric('pw',st.pw==null?'–':`${st.pw}/${st.max_pw??'–'}`);metric('dlvl',st.dlvl);metric('ac',st.ac);metric('exp',st.exp_level);metric('turn',st.turn);document.getElementById('hpfill').style.width=`${ratio(st.hp,st.max_hp)}%`;document.getElementById('pwfill').style.width=`${ratio(st.pw,st.max_pw)}%`;const run=payload.run;if(run&&Number.isInteger(run.expedition))document.getElementById('expedition').textContent=`第${run.expedition}次遠征 · ${run.status||'active'}`;else document.getElementById('expedition').textContent='遠征情報なし';const h=document.getElementById('health');h.textContent=`spectator: live · ${new Date().toLocaleTimeString()}`;h.classList.remove('error')}
async function poll(){try{const r=await fetch('/api/snapshot',{cache:'no-store'});if(!r.ok)throw new Error(`HTTP ${r.status}`);render(await r.json())}catch(e){const h=document.getElementById('health');h.textContent=`spectator error: ${e.message}`;h.classList.add('error')}}
poll();setInterval(poll,350);window.addEventListener('resize',poll);
</script>
</body>
</html>'''
