from pathlib import Path

path = Path("src/docich/webui.py")
text = path.read_text(encoding="utf-8")
start = 'const WC_WORKERS=[["prediction_worker","予想 prediction_worker"],["improve_daemon","改善 improve_daemon"]];\n'
end = 'const STREAM_SETTING_KEYS=["SOREN_DIRECT_STREAM_SIZE"'
if text.count(start) != 1:
    raise SystemExit(f"worker control start marker count={text.count(start)}")
if text.count(end) != 1:
    raise SystemExit(f"worker control end marker count={text.count(end)}")
left, rest = text.split(start, 1)
_old_block, right = rest.split(end, 1)
new_block = r'''const WC_WORKERS=[["prediction_worker","予想 prediction_worker"],["improve_daemon","改善 improve_daemon"]];
let predictionWorkerStatusCache=null;
let predictionWorkerStatusFetchedAt=0;
let predictionWorkerStatusInFlight=null;
const PREDICTION_WORKER_STATUS_TTL_MS=60000;
function wcEl(worker,suffix){ return document.getElementById(`wc-${worker}-${suffix}`); }
// prediction-worker-display:start
function predictionWorkerRemainingText(seconds){
  let n=Number(seconds||0);
  if(!Number.isFinite(n)||n<=0) return "0秒";
  n=Math.max(0,Math.floor(n));
  const h=Math.floor(n/3600), m=Math.floor((n%3600)/60), s=n%60;
  if(h) return `${h}時間${m?m+"分":""}${s?s+"秒":""}`;
  if(m) return `${m}分${s?s+"秒":""}`;
  return `${s}秒`;
}
function predictionWorkerDisplay(row,prediction){
  row=row||{};
  if(row.paused) return {text:"停止中 (paused)",className:"badge warn",detail:""};
  if(!row.alive) return {text:"停止中",className:"badge",detail:""};
  if(!prediction) return {text:"稼働中",className:"badge ok",detail:"予想状態を取得中"};
  if(!prediction.enabled) return {text:"稼働中・機能OFF",className:"badge warn",detail:"TWITCH_PREDICTIONS_ENABLED が無効"};
  if(!prediction.configured) return {text:"稼働中・設定不備",className:"badge bad",detail:"Twitch Predictions の設定を確認してください"};
  const retry=((prediction.retry||{}).create)||null;
  const httpCode=String((retry&&retry.http_code)||prediction.http_code||"");
  const errorText=String(prediction.error||"").toLowerCase();
  const authBad=httpCode==="401"||httpCode==="403"||/(?:^|\D)(?:401|403)(?:\D|$)|unauthorized|forbidden|invalid[_ -]?token|oauth/.test(errorText);
  if(authBad){
    const detail=httpCode?`Twitch API 認証エラー (HTTP ${httpCode})`:"Twitch API 認証エラー";
    return {text:"稼働中・認証不良",className:"badge bad",detail};
  }
  if(retry&&retry.active){
    const remaining=predictionWorkerRemainingText(retry.remaining);
    const code=httpCode?` / HTTP ${httpCode}`:"";
    return {text:"稼働中・create retry中",className:"badge warn",detail:`再試行まで ${remaining}${code}`};
  }
  const remote=Array.isArray(prediction.remote)?prediction.remote:[];
  const remoteActive=remote.some(p=>["ACTIVE","LOCKED"].includes(String((p||{}).status||"").toUpperCase()));
  if(remoteActive||prediction.local){
    const remoteState=remote.find(p=>["ACTIVE","LOCKED"].includes(String((p||{}).status||"").toUpperCase()));
    const detail=remoteState?`Twitch ${String(remoteState.status).toUpperCase()}`:"ローカル予想状態あり";
    return {text:"稼働中・予想実施中",className:"badge ok",detail};
  }
  const rawCount=((prediction.accumulated||{}).count);
  const count=Number.isFinite(Number(rawCount))?Math.max(0,Math.floor(Number(rawCount))):0;
  if(count>0) return {text:"稼働中・次サイクル待ち",className:"badge warn",detail:`蓄積 ${count}ゲーム / 次の改善サイクル開始で自動作成`};
  return {text:"稼働中・予想開始待ち",className:"badge warn",detail:"サイクル先頭 / worker の次tickで自動作成"};
}
// prediction-worker-display:end
function renderWorkersControl(workers,predictionStatus=predictionWorkerStatusCache){
  const wrap=document.getElementById("wc-rows");
  if(!wrap) return;
  if(!wrap.dataset.built){
    wrap.innerHTML=WC_WORKERS.map(([w,label])=>`
      <div class="row" style="align-items:center;margin-bottom:10px">
        <div style="min-width:260px"><b>${esc(label)}</b><div class="mono" id="wc-${w}-pid" style="font-size:11px;color:var(--muted)">pid=-</div><div id="wc-${w}-detail" style="font-size:11px;color:var(--muted);margin-top:2px"></div></div>
        <div><span class="badge" id="wc-${w}-badge">-</span></div>
        <div style="display:flex;gap:6px;margin-left:auto">
          <button class="btn primary" id="wc-${w}-start">開始</button>
          <button class="btn danger" id="wc-${w}-stop">停止</button>
        </div>
      </div>`).join("");
    wrap.dataset.built="1";
    for(const [w] of WC_WORKERS){
      const sb=wcEl(w,"start"), tb=wcEl(w,"stop");
      if(sb) sb.onclick=()=>workerControl(w,"start");
      if(tb) tb.onclick=()=>workerControl(w,"stop");
    }
  }
  for(const [w] of WC_WORKERS){
    const row=(workers||[]).find(x=>x.worker===w)||{};
    const badge=wcEl(w,"badge"), pidEl=wcEl(w,"pid"), detailEl=wcEl(w,"detail");
    if(w==="prediction_worker"){
      const display=predictionWorkerDisplay(row,predictionStatus);
      if(badge){ badge.textContent=display.text; badge.className=display.className; }
      if(detailEl){ detailEl.textContent=display.detail||""; detailEl.title=display.detail||""; }
    }else{
      if(badge){
        badge.textContent=row.paused?"停止中 (paused)":(row.alive?"稼働中":"停止中");
        badge.className=row.paused?"badge warn":(row.alive?"badge ok":"badge");
      }
      if(detailEl) detailEl.textContent="";
    }
    if(pidEl) pidEl.textContent=row.pid?`pid=${row.pid}`:"pid=-";
    const sb=wcEl(w,"start"), tb=wcEl(w,"stop");
    if(sb) sb.disabled=(!!row.alive&&!row.paused)||READ_ONLY;
    if(tb) tb.disabled=(!!row.paused)||READ_ONLY;
  }
}
async function loadPredictionWorkerStatus(force=false){
  const now=Date.now();
  if(!force&&predictionWorkerStatusCache&&(now-predictionWorkerStatusFetchedAt)<PREDICTION_WORKER_STATUS_TTL_MS) return predictionWorkerStatusCache;
  if(predictionWorkerStatusInFlight) return predictionWorkerStatusInFlight;
  predictionWorkerStatusInFlight=api("/api/predictions")
    .then(data=>{
      predictionWorkerStatusCache=data;
      predictionWorkerStatusFetchedAt=Date.now();
      return data;
    })
    .catch(e=>{
      console.warn("loadPredictionWorkerStatus",e);
      return predictionWorkerStatusCache;
    })
    .finally(()=>{ predictionWorkerStatusInFlight=null; });
  return predictionWorkerStatusInFlight;
}
async function loadWorkersControl(forcePrediction=false){
  try{
    const data=await api("/api/workers");
    renderWorkersControl(data.workers,predictionWorkerStatusCache);
    const prediction=await loadPredictionWorkerStatus(forcePrediction);
    if(prediction) renderWorkersControl(data.workers,prediction);
  }catch(e){ console.warn("loadWorkersControl",e); }
}
async function workerControl(worker,action){
  if(READ_ONLY){ toast("read-only"); return; }
  let confirmMsg;
  if(action==="stop"){
    confirmMsg=worker==="improve_daemon"
      ?"改善ワーカーを停止しますか？実行中の改善ジョブも停止します。"
      :"予想ワーカーを停止しますか？Twitch 予想の自動作成・解決が止まります。";
  }else{
    confirmMsg=(worker==="improve_daemon"?"改善ワーカー":"予想ワーカー")+"を開始しますか？";
  }
  if(!confirm(confirmMsg)) return;
  const msg=$("#wc-msg"); if(msg) msg.textContent="処理中...";
  try{
    const res=await api("/api/workers",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({worker,action,confirm:true})});
    if(msg){
      if(action==="start"&&!res.ok&&res.hint) msg.textContent=res.hint;
      else if(res.job_continues_in_background) msg.textContent="停止できません。改善ジョブがまだ稼働しています。";
      else if(action==="stop"&&res.stopped===false) msg.textContent="マーカーを作成しましたが、プロセスの退出を確認できていません。";
      else if(action==="stop") msg.textContent="停止しました (supervisor による再起動は抑止されます)。";
      else msg.textContent="開始しました。";
    }
    toast(`${worker} ${action}`);
    await loadWorkersControl(worker==="prediction_worker"&&action==="start");
  }catch(e){ if(msg) msg.textContent=String(e); toast(String(e),5000); }
}
'''
text = left + new_block + end + right
old_prediction_loader = '  try{ const data=await api("/api/predictions"); renderPredictionState(data); }'
new_prediction_loader = '  try{ const data=await api("/api/predictions"); predictionWorkerStatusCache=data; predictionWorkerStatusFetchedAt=Date.now(); renderPredictionState(data); }'
if text.count(old_prediction_loader) != 1:
    raise SystemExit(f"prediction loader marker count={text.count(old_prediction_loader)}")
text = text.replace(old_prediction_loader, new_prediction_loader, 1)
path.write_text(text, encoding="utf-8")
