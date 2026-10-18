/** Self-contained HTML pages served by the arena worker.
 *
 * All pages follow the docich broadcast HTML standard: lang="ja",
 * translate="no" and the notranslate meta at the top. Client script blocks are
 * written as template literals that contain no backticks and no "${" of their
 * own, so each page stays a single reviewable literal. No external assets:
 * every page must render without third-party CDNs.
 */

const HEAD =
  '<!doctype html><html lang="ja" translate="no"><meta charset="utf-8">' +
  '<meta name="google" content="notranslate"><meta name="viewport" content="width=device-width,initial-scale=1">';

const BASE_CSS = [
  '*{box-sizing:border-box}html,body{margin:0;padding:0;background:#0d1117;color:#e8edf2;',
  'font-family:"Noto Sans CJK JP","Yu Gothic",system-ui,sans-serif}',
  'a{color:#7fb4c9}.hidden{display:none!important}',
  '.board{display:grid;grid-template-columns:repeat(8,1fr);gap:2px;background:#1b5e20;',
  'border:6px solid #123d16;border-radius:8px;touch-action:manipulation}',
  '.cell{position:relative;aspect-ratio:1/1;background:#2e7d32;border-radius:3px;min-width:0}',
  '.cell .disc{position:absolute;inset:8%;border-radius:50%}',
  '.disc.black{background:#101418;box-shadow:inset 0 0 0 2px #2c3e50}',
  '.disc.white{background:#f4f6f8;box-shadow:inset 0 0 0 2px #b9c2cc}',
  '.cell.last::after{content:"";position:absolute;inset:0;border:2px solid #ffd67a;border-radius:3px}',
].join('');

/** Shared client helpers. Rendering validates every server field before it
 * reaches innerHTML: the board must match ^[.BW]{64}$ and coordinates must be
 * a1..h8, so a broken server cannot inject markup. */
const BOARD_JS = `
function isBoardText(t){return typeof t==="string"&&t.length===64&&/^[.BW]{64}$/.test(t);}
function isValidCoord(c){return typeof c==="string"&&/^[a-h][1-8]$/.test(c);}
function coordToIndex(c){return (c.charCodeAt(1)-49)*8+(c.charCodeAt(0)-97);}
function indexToCoord(i){return String.fromCharCode(97+(i%8))+String(Math.floor(i/8)+1);}
function renderBoard(el, state){
  if(!state||!isBoardText(state.board)){return;}
  var last=state.lastMove&&isValidCoord(state.lastMove.coord)?coordToIndex(state.lastMove.coord):-1;
  var rows=[];
  for(var i=0;i<64;i++){
    var ch=state.board.charAt(i);
    var cls="cell"+(i===last?" last":"");
    var disc=ch==="B"?'<span class="disc black"></span>':ch==="W"?'<span class="disc white"></span>':"";
    rows.push('<div class="'+cls+'" data-i="'+i+'">'+disc+'</div>');
  }
  el.innerHTML=rows.join("");
}
`;

export function landingPage() {
  return [
    HEAD,
    '<title>docich 参加型アリーナ</title>',
    '<style>', BASE_CSS,
    'main{max-width:640px;margin:48px auto;padding:0 20px}',
    'h1{font-size:28px;letter-spacing:.04em}p.lead{color:#aeb8c4;line-height:1.7}',
    '.games{list-style:none;padding:0;margin:24px 0}',
    '.games li{border:1px solid #26313c;border-radius:10px;padding:14px 18px;margin-bottom:10px;',
    'display:flex;justify-content:space-between;align-items:center;background:#11181f}',
    '.games .soon{color:#7d8b98;font-size:13px}',
    '.play{background:#2563eb;color:#fff;text-decoration:none;padding:8px 18px;border-radius:8px;font-weight:700}',
    'footer{color:#66727e;font-size:12px;margin-top:32px;line-height:1.6}',
    '</style>',
    '<main>',
    '<h1>docich 参加型アリーナ</h1>',
    '<p class="lead">配信で公開されたURLから、AI（docich側）と直接対戦できる参加型ゲームです。対戦のようすは配信画面にも映ります。</p>',
    '<ul class="games">',
    '<li><span>オセロ（AIと対戦）</span><a class="play" href="/othello">対戦する</a></li>',
    '<li><span>テトリス</span><span class="soon">準備中</span></li>',
    '<li><span>ぷよぷよ</span><span class="soon">準備中</span></li>',
    '<li><span>衝立将棋</span><span class="soon">準備中</span></li>',
    '<li><span>FPS</span><span class="soon">準備中</span></li>',
    '</ul>',
    '<footer>対戦URLは本人に教わった相手にだけ意味があります。ルーム一覧は公開していません。</footer>',
    '</main>',
  ].join('');
}

export function playerPage(roomId) {
  const initial = roomId ? `"${roomId}"` : "null";
  return [
    HEAD,
    '<title>オセロ対戦 — docich 参加型アリーナ</title>',
    '<style>', BASE_CSS,
    'main{max-width:560px;margin:18px auto;padding:0 14px}',
    'h1{font-size:22px;margin:8px 0}h1 small{font-size:13px;color:#7d8b98;font-weight:400}',
    '.status{min-height:24px;font-size:15px;color:#ffd67a;margin:6px 0}',
    '.notice{font-size:13px;color:#aeb8c4;margin:6px 0;min-height:16px}',
    '.spectator-escape{display:none;margin:6px 0}',
    '.spectator-escape.on{display:block}',
    '.meta{color:#aeb8c4;font-size:13px;margin:6px 0 12px;line-height:1.7}',
    '.meta b{color:#e8edf2}',
    'button{background:#2563eb;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:15px;font-weight:700;cursor:pointer}',
    'button.ghost{background:#1c2833;color:#cfd8e0}',
    '.setup{border:1px solid #26313c;border-radius:10px;padding:18px;background:#11181f;margin-top:10px}',
    '.setup label{display:block;font-size:13px;color:#aeb8c4;margin:10px 0 4px}',
    '.setup select,.setup input{width:100%;padding:10px;border-radius:8px;border:1px solid #26313c;background:#0d1117;color:#e8edf2;font-size:15px}',
    '.actions{display:flex;gap:10px;margin-top:12px}',
    '.result{border:1px solid #4a5a3f;border-radius:10px;padding:12px 16px;background:rgba(120,170,110,.12);margin-top:12px}',
    '.result .big{font-size:22px;font-weight:800}',
    '.url{font-size:12px;color:#66727e;word-break:break-all;margin-top:12px}',
    '</style>',
    '<main>',
    '<h1>オセロ <small>あなた=黒（先手）／ AI=白</small></h1>',
    '<div class="status" id="status">接続しています…</div>',
    '<div class="notice" id="notice"></div>',
    '<div class="spectator-escape" id="escape">自分の対戦を新しく作る場合は<a href="/othello">こちら</a>から。</div>',
    '<div class="meta" id="meta"></div>',
    '<div class="board" id="board"></div>',
    '<div class="result hidden" id="result"></div>',
    '<div class="actions">',
    '<button id="resign" class="ghost hidden">投了</button>',
    '<button id="rematch" class="hidden">再戦する</button>',
    '</div>',
    '<div class="setup" id="setup">',
    '<label for="name">表示名（任意・20文字まで）</label>',
    '<input id="name" maxlength="20" placeholder="名無しの同志">',
    '<label for="level">AIの強さ</label>',
    '<select id="level"><option value="easy">かんたん</option>',
    '<option value="normal" selected>ふつう</option>',
    '<option value="hard">つよい</option></select>',
    '<div class="actions"><button id="create">AIと対戦を始める</button></div>',
    '</div>',
    '<div class="url" id="roomurl"></div>',
    '</main>',
    '<script>', BOARD_JS,
    `
var ROOM_ID=${initial};
var seated=false;var state=null;var ws=null;var pollTimer=null;var retryTimer=null;var myTurn=false;
function byId(id){return document.getElementById(id);}
function setStatus(t){byId("status").textContent=t;}
function setNotice(t){byId("notice").textContent=t||"";}
function render(){
  if(!state){return;}
  renderBoard(byId("board"),state);
  myTurn=seated&&state.status==="playing"&&state.turn==="black";
  if(state.status==="waiting"){setStatus("対戦相手を募集中です。下のフォームから着席してください。");}
  else if(state.status==="playing"){setStatus(myTurn?"あなたの手番です。":"AIの考え中…");}
  else if(state.status==="ended"&&state.result){
    var r=state.result;
    var t=r.winner==="black"?"あなたの勝ち":r.winner==="white"?"AIの勝ち":"引き分け";
    var reason=r.reason==="resign"?"投了":"得点";
    setStatus("終局（"+t+"）");
    var el=byId("result");el.classList.remove("hidden");
    el.innerHTML='<div class="big">'+t+'</div><div>黒 '+r.black+' — 白 '+r.white+'（'+reason+'）</div>';
  }
  var sc=state.score||{black:0,white:0};
  var seatLine="黒: "+(state.seats&&state.seats.black?state.seats.black.name||"対戦者":"（募集中）")+
    " ／ 白: "+(state.seats&&state.seats.white?state.seats.white.name:"AI");
  byId("meta").innerHTML="黒 <b>"+sc.black+"</b> ／ 白 <b>"+sc.white+"</b>　"+
    state.gameNumber+"局目　成績 あなた<b>"+state.wins.human+"</b> AI<b>"+state.wins.ai+"</b> 分<b>"+state.wins.draw+"</b>"+
    "<br>"+seatLine;
  byId("resign").classList.toggle("hidden",!(seated&&state.status==="playing"));
  byId("rematch").classList.toggle("hidden",!(seated&&state.status==="ended"));
}
function applyState(next,asSeat){
  if(asSeat){seated=true;setNotice("");}
  if(!next){return;}
  state=next;render();
}
function send(obj){if(ws&&ws.readyState===1){ws.send(JSON.stringify(obj));}}
function connect(){
  clearTimeout(retryTimer);
  ws=new WebSocket((location.protocol==="https:"?"wss://":"ws://")+location.host+"/api/othello/rooms/"+ROOM_ID+"/ws");
  ws.onopen=function(){stopPoll();send({type:"hello",name:byId("name").value});};
  ws.onmessage=function(ev){
    var msg;try{msg=JSON.parse(ev.data);}catch(e){return;}
    if(msg.type==="welcome"){applyState(msg.state,false);}
    else if(msg.type==="joined"){applyState(msg.state,true);}
    else if(msg.type==="state"||msg.type==="game_end"){applyState(msg.state,false);}
    else if(msg.type==="error"&&msg.code==="seat_taken"){setNotice("すでに対戦者がいます。観戦モードで表示しています。");byId("escape").classList.add("on");}
  };
  ws.onclose=function(){startPoll();retryTimer=setTimeout(connect,2000);};
  ws.onerror=function(){try{ws.close();}catch(e){}};
}
function stopPoll(){clearInterval(pollTimer);pollTimer=null;}
function startPoll(){
  if(pollTimer){return;}
  pollTimer=setInterval(async function(){
    try{
      var res=await fetch("/api/othello/rooms/"+ROOM_ID,{cache:"no-store"});
      if(res.status===404){setStatus("この対戦は終了・削除されました。");stopPoll();return;}
      var data=await res.json();
      if(data.ok){applyState(data.snapshot,false);}
    }catch(e){}
  },2000);
}
byId("board").addEventListener("click",function(ev){
  var cell=ev.target.closest(".cell");
  if(!cell||!myTurn||!ws){return;}
  send({type:"move",coord:indexToCoord(parseInt(cell.getAttribute("data-i"),10))});
});
byId("create").addEventListener("click",async function(){
  var level=byId("level").value;
  try{
    var res=await fetch("/api/othello/rooms",{method:"POST",
      headers:{"content-type":"application/json"},body:JSON.stringify({level:level})});
    if(!res.ok){setStatus("対戦を作成できませんでした。しばらくしてから再度お試しください。");return;}
    var data=await res.json();
    history.replaceState(null,"","/othello/"+data.roomId);
    ROOM_ID=data.roomId;showRoomUrl();connect();
  }catch(e){setStatus("対戦を作成できませんでした。");}
});
byId("resign").addEventListener("click",function(){if(seated){send({type:"resign"});}});
byId("rematch").addEventListener("click",function(){if(seated){send({type:"rematch"});}});
function showRoomUrl(){if(ROOM_ID){byId("roomurl").textContent="この対戦の参加URL: "+location.origin+"/othello/"+ROOM_ID;}}
if(ROOM_ID){byId("setup").classList.add("hidden");showRoomUrl();connect();}
else{setStatus("AIの強さを選んで対戦を始めてください。");}
    `,
    '</script>',
  ].join('');
}

export function viewPage(roomId) {
  const initial = roomId ? `"${roomId}"` : '""';
  return [
    HEAD,
    '<title>オセロ観戦 — docich 参加型アリーナ</title>',
    '<style>',
    '*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0b1017;color:#f2f5f8;',
    'font-family:"Noto Sans CJK JP","Yu Gothic",system-ui,sans-serif}',
    '#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:16px 22px;display:flex;gap:22px;',
    'background:radial-gradient(ellipse at 25% 15%,#17293a 0,#0f1720 60%,#0a0f15 100%)}',
    '.left{width:480px;display:flex;flex-direction:column}',
    '.sub{font-size:11px;color:#7fb4c9;letter-spacing:.2em}',
    'h1{font-size:24px;margin:0 0 10px;letter-spacing:.04em}',
    '.right{flex:1;display:flex;flex-direction:column;gap:10px;padding-top:6px}',
    '.panel{border:1px solid #263a4c;border-radius:10px;background:rgba(19,33,49,.55);padding:10px 14px}',
    '.k{font-size:11px;color:#8ea6b6;letter-spacing:.08em}.v{font-size:20px;font-weight:700;margin-top:2px}',
    '.turn{font-size:22px;font-weight:800;color:#ffd67a;min-height:28px}',
    '.score{display:flex;gap:16px;font-size:26px;font-weight:800;align-items:center}',
    '.dash{color:#5a6a78}',
    '.dotb{display:inline-block;width:16px;height:16px;border-radius:50%;background:#101418;margin-right:8px;vertical-align:-2px}',
    '.dotw{display:inline-block;width:16px;height:16px;border-radius:50%;background:#f4f6f8;margin-right:8px;vertical-align:-2px}',
    '.join{font-size:14px;color:#9fd3dc;word-break:break-all;line-height:1.5}',
    '#unavailable{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#f0d9ad;font-size:20px;background:#0b1017ee}',
    '</style>',
    '<div id="stage">',
    '<div class="left"><div class="sub">DOCICH PARTICIPATORY ARENA</div><h1>参加型オセロ</h1>',
    '<div id="board" class="board"></div></div>',
    '<div class="right">',
    '<div class="panel"><div class="k">手番</div><div class="turn" id="turn">-</div></div>',
    '<div class="panel"><div class="k">得点</div><div class="score" id="score">-</div></div>',
    '<div class="panel"><div class="k">対戦</div><div class="v" id="seats" style="font-size:15px">-</div>',
    '<div class="k" style="margin-top:6px">連戦成績</div><div class="v" id="wins" style="font-size:15px">-</div></div>',
    '<div class="panel"><div class="k">参加URL</div><div class="join" id="join">-</div></div>',
    '</div>',
    '</div>',
    '<div id="unavailable" class="hidden">対戦情報を取得できません</div>',
    '<script>', BOARD_JS,
    `
var ROOM_ID=${initial};
function byId(id){return document.getElementById(id);}
function showUnavailable(text){
  var el=byId("unavailable");el.classList.remove("hidden");el.textContent=text;
}
if(!/^[0-9a-f]{16}$/.test(ROOM_ID)){showUnavailable("URLに room= がありません");}
else{setInterval(poll,1000);poll();}
async function poll(){
  try{
    var res=await fetch("/api/othello/rooms/"+ROOM_ID,{cache:"no-store"});
    if(!res.ok){showUnavailable(res.status===404?"対戦が見つかりません":"対戦情報を取得できません");return;}
    var data=await res.json();
    if(!data.ok){showUnavailable("対戦情報を取得できません");return;}
    byId("unavailable").classList.add("hidden");
    render(data.snapshot);
  }catch(e){showUnavailable("対戦情報を取得できません");}
}
function render(s){
  if(!s||!isBoardText(s.board)){return;}
  renderBoard(byId("board"),s);
  var turn=s.status==="waiting"?"対戦者募集中":s.status==="ended"?"終局":(s.turn==="black"?"黒の手番":"白（AI）の手番");
  byId("turn").textContent=turn;
  var sc=s.score||{black:0,white:0};
  byId("score").innerHTML='<span><span class="dotb"></span>'+sc.black+'</span>'+
    '<span class="dash">–</span><span><span class="dotw"></span>'+sc.white+'</span>';
  var b=s.seats&&s.seats.black?s.seats.black.name||"対戦者":"（募集中）";
  byId("seats").textContent="黒: "+b+" ／ 白: "+(s.seats&&s.seats.white?s.seats.white.name:"AI");
  byId("wins").textContent=s.gameNumber+"局目 ・ あなた"+s.wins.human+"勝 AI"+s.wins.ai+"勝 分"+s.wins.draw;
  byId("join").textContent=location.origin+"/othello/"+s.roomId;
}
    `,
    '</script>',
  ].join('');
}
