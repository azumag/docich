"use strict";
// PAPER dashboard client (Issue #198). Read-only: it only GETs
// allowlisted local dashboard endpoints and draws. No posting, no trading.
const $ = (id) => document.getElementById(id);
const PAD = { l: 58, r: 10, t: 10, b: 22 };

function jst(epoch) {
  if (epoch === null || epoch === undefined || !isFinite(epoch)) return "--:--:--";
  const d = new Date(epoch * 1000);
  return d.toLocaleTimeString("ja-JP", { hour12: false, timeZone: "Asia/Tokyo" });
}
function fmtNum(v) {
  if (v === null || v === undefined || !isFinite(v)) return "-";
  const abs = Math.abs(v);
  return Number(v).toLocaleString("ja-JP", { maximumFractionDigits: abs >= 100 ? 0 : 6 });
}
function maybeNumber(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return isFinite(n) ? n : null;
}
function fmtMoney(v, signed = false) {
  const n = maybeNumber(v);
  if (n === null) return "-";
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${n.toLocaleString("ja-JP", { maximumFractionDigits: 0 })}円`;
}
function pnlClass(v) {
  const n = maybeNumber(v);
  if (n === null || n === 0) return "";
  return n > 0 ? "pos" : "neg";
}
function setPnl(id, value, fallback = "-") {
  const el = $(id);
  const n = maybeNumber(value);
  if (n === null) {
    el.textContent = fallback;
    el.className = "v muted";
    return;
  }
  el.textContent = fmtMoney(n, true);
  el.className = `v ${pnlClass(n)}`.trim();
}
function esc(v) {
  return String(v ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
function ageLabel(epoch) {
  const n = maybeNumber(epoch);
  if (n === null) return "";
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - n));
  return sec < 60 ? `${sec}s前` : `${Math.floor(sec / 60)}m前`;
}

function liveForChart(chart, live) {
  if (!live || live.available !== true || !chart || live.symbol !== chart.symbol) return null;
  return maybeNumber(live.ticker && live.ticker.last);
}

function drawChart(chart, live) {
  const c = $("chart");
  const dpr = window.devicePixelRatio || 1;
  const W = 524, H = 330;
  if (c.width !== W * dpr || c.height !== H * dpr) {
    c.width = W * dpr; c.height = H * dpr;
  }
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);

  const stored = (chart && Array.isArray(chart.closes)) ? chart.closes.filter((v) => isFinite(v)) : [];
  const liveLast = liveForChart(chart, live);
  const series = liveLast === null ? stored.slice() : [...stored, liveLast];
  const hasLive = liveLast !== null;
  const plotW = W - PAD.l - PAD.r, plotH = H - PAD.t - PAD.b;

  g.strokeStyle = "#22335a"; g.lineWidth = 1;
  g.strokeRect(PAD.l, PAD.t, plotW, plotH);
  g.font = "11px monospace"; g.textBaseline = "middle";
  if (series.length < 2) {
    g.fillStyle = "#6f86a8"; g.textAlign = "center";
    g.fillText("終値チャート取得待ち", PAD.l + plotW / 2, PAD.t + plotH / 2);
    return;
  }
  let lo = Math.min(...series), hi = Math.max(...series);
  if (hi <= lo) { hi = lo + 1; lo = lo - 1; }
  const span = hi - lo;
  const x = (i) => PAD.l + (plotW * i) / (series.length - 1);
  const y = (v) => PAD.t + plotH - ((v - lo) / span) * plotH;

  g.textAlign = "right"; g.textBaseline = "middle";
  for (let r = 0; r <= 4; r++) {
    const value = hi - (span * r) / 4;
    const yy = PAD.t + (plotH * r) / 4;
    g.strokeStyle = r === 4 ? "#22335a" : "#182642";
    g.beginPath(); g.moveTo(PAD.l, yy); g.lineTo(PAD.l + plotW, yy); g.stroke();
    g.fillStyle = "#7f96ba"; g.fillText(fmtNum(value), PAD.l - 6, yy);
  }

  const grad = g.createLinearGradient(0, PAD.t, 0, PAD.t + plotH);
  grad.addColorStop(0, "rgba(56,189,248,0.38)");
  grad.addColorStop(1, "rgba(56,189,248,0.02)");
  g.beginPath();
  g.moveTo(x(0), y(series[0]));
  for (let i = 1; i < series.length; i++) g.lineTo(x(i), y(series[i]));
  g.lineTo(x(series.length - 1), PAD.t + plotH);
  g.lineTo(x(0), PAD.t + plotH);
  g.closePath(); g.fillStyle = grad; g.fill();

  g.beginPath();
  g.moveTo(x(0), y(series[0]));
  for (let i = 1; i < series.length; i++) g.lineTo(x(i), y(series[i]));
  g.strokeStyle = "#38bdf8"; g.lineWidth = 2; g.stroke();

  const lx = x(series.length - 1), ly = y(series[series.length - 1]);
  g.beginPath(); g.arc(lx, ly, hasLive ? 4.5 : 3.5, 0, Math.PI * 2);
  g.fillStyle = hasLive ? "#67e8f9" : "#e6edf7"; g.fill();
  g.textAlign = "left"; g.fillStyle = hasLive ? "#67e8f9" : "#e6edf7";
  const latestLabel = hasLive ? `${fmtNum(series[series.length - 1])} LIVE` : fmtNum(series[series.length - 1]);
  g.fillText(latestLabel, Math.min(lx + 6, W - 100), ly - 10);

  g.textAlign = "center"; g.fillStyle = "#7f96ba";
  g.fillText(hasLive ? `${stored.length}本 + LIVE` : `${stored.length}本`, PAD.l + plotW / 2, H - 8);
}

function renderPaperFill(data) {
  const fills = data && Array.isArray(data.fills) ? data.fills : [];
  const f = fills[0];
  if (!f) {
    $("paperfill").textContent = "模擬約定なし";
    return;
  }
  const side = String(f.side).toLowerCase() === "sell" ? "売" : "買";
  const rp = maybeNumber(f.realized_pnl_jpy);
  const pnl = String(f.side).toLowerCase() === "sell" && rp !== null
    ? ` 損益${rp > 0 ? "+" : ""}${fmtMoney(rp)}` : "";
  $("paperfill").textContent = `${f.symbol} ${side} ${f.amount}@${f.price}${pnl}`;
}

function render(data) {
  if (!data || data.schema_version !== 1) return;
  render.lastData = data;
  const h = data.header || {}, p = data.portfolio || {}, perf = data.performance || {};
  const ch = data.chart || {}, dec = data.decision || {};
  $("title").textContent = h.title || "PAPER 暗号資産コーナー";
  $("clock").textContent = jst(h.generated_at || (Date.now() / 1000)) + " JST";
  $("state").textContent = "状態:" + (h.worker_state || "unknown") + " #" + (h.snapshot_seq ?? "?");
  const age = h.data_age_sec;
  $("remaining").textContent = (age === null || age === undefined) ? "" : `データ齢 ${age}s`;
  $("remaining").className = (age !== null && age > 180) ? "neg" : "";
  $("funding").textContent = `資金 ${fmtMoney(p.capital_jpy)} / 投入 ${fmtMoney(p.deployed_jpy)}`;

  $("equity").textContent = perf.equity_jpy === null || perf.equity_jpy === undefined
    ? "評価待ち" : fmtMoney(perf.equity_jpy);
  $("equity").className = perf.equity_jpy === null || perf.equity_jpy === undefined ? "v muted" : "v";
  setPnl("totalpnl", perf.cumulative_pnl_jpy, "価格不足");
  setPnl("todaypnl", perf.today_realized_pnl_jpy, "-");
  setPnl("unrealized", perf.unrealized_pnl_jpy, "価格不足");
  $("positions").textContent = `${p.position_count ?? 0} 銘柄`;
  $("fresh").textContent = `${p.fresh_markets ?? 0}/${p.total_markets ?? 0}`;

  $("focus").textContent = "注目: " + (ch.symbol || "（観測待ち）");
  const liveLast = liveForChart(ch, render.liveData);
  $("range").textContent = ch.count
    ? `5分足${ch.count}本${liveLast === null ? "" : " + LIVE"}`
    : (liveLast === null ? "" : "LIVE");
  $("decision").innerHTML = `<div>候補</div><div>${dec.candidate_count ?? 0}件</div>` +
    ((dec.reasons || []).length
      ? (dec.reasons || []).slice(0, 2).map((r) => `<div class="muted">主因</div><div>${esc(r.label)}</div>`).join("")
      : `<div class="muted">判断</div><div>条件未達・様子見</div>`);

  $("skipped").innerHTML = (dec.skipped || []).length
    ? (dec.skipped || []).slice(0, 6).map((r) => {
        const who = r.symbol ? `${r.symbol} ${r.side_label || "取引"}` : "取引";
        return `<div class="row"><span class="muted">${esc(who)}</span> ${esc(r.label)}</div>`;
      }).join("")
    : `<div class="muted">見送り理由なし</div>`;

  const positionCount = Number(p.position_count ?? 0);
  const shown = Number(p.displayed_position_count ?? (p.positions || []).length);
  $("holdingnote").textContent = positionCount > shown ? `（${positionCount}銘柄中 上位${shown}）` : "";
  $("holdings").innerHTML = (p.positions || []).length
    ? (p.positions || []).map((x) => {
        const value = x.market_value_jpy === null || x.market_value_jpy === undefined ? "評価待ち" : fmtMoney(x.market_value_jpy);
        const pnl = maybeNumber(x.unrealized_pnl_jpy);
        const pnlText = pnl === null ? "" : ` ${pnl > 0 ? "+" : ""}${fmtMoney(pnl)}`;
        return `<div class="holding"><span class="symbol">${esc(x.symbol)}</span><span class="holding-meta ${pnlClass(pnl)}">${esc(value + pnlText)}</span></div>`;
      }).join("")
    : `<div class="muted">なし（未保有は正常）</div>`;

  renderPaperFill(data);
  $("disclaimer").textContent = data.disclaimer || "";
  drawChart(ch, render.liveData);
}
render.lastData = null;
render.liveData = null;

function renderLive(data) {
  if (!data || data.schema_version !== 1) return;
  render.liveData = data;
  const available = data.available === true;
  const ticker = data.ticker || {};
  const last = maybeNumber(ticker.last);
  const bid = maybeNumber(ticker.bid);
  const ask = maybeNumber(ticker.ask);
  if (available && last !== null) {
    $("livequote").textContent = `現在 ${fmtNum(last)}  B ${fmtNum(bid)}  A ${fmtNum(ask)}`;
    $("livequote").className = "livequote";
  } else {
    $("livequote").textContent = "現在値 取得待ち";
    $("livequote").className = "livequote muted";
  }
  $("liveage").textContent = data.stale ? "更新遅延" : ageLabel(data.fetched_at);
  const trades = available && Array.isArray(data.trades) ? data.trades : [];
  $("tape").innerHTML = trades.length
    ? trades.slice(0, 5).map((t) => {
        const side = String(t.side).toLowerCase();
        const sideLabel = side === "buy" ? "買" : side === "sell" ? "売" : "-";
        const klass = side === "buy" ? "pos" : side === "sell" ? "neg" : "muted";
        return `<div class="row"><span>${esc(jst(t.timestamp))}</span><span class="${klass}">${sideLabel}</span>` +
          `<span class="price">${esc(fmtNum(t.price))}</span><span class="amount">${esc(fmtNum(t.amount))}</span></div>`;
      }).join("")
    : `<div class="muted">市場約定を取得待ち</div>`;
  $("chartnote").textContent = available
    ? "5分足保存値 + bitbank公開現在値（表示専用・約2秒更新）。売買判断周期は変更しません。"
    : "5分足の保存済み終値。PAPER台帳と公開価格から損益計算。";
  if (render.lastData) {
    const ch = render.lastData.chart || {};
    $("range").textContent = ch.count
      ? `5分足${ch.count}本${liveForChart(ch, data) === null ? "" : " + LIVE"}`
      : (liveForChart(ch, data) === null ? "" : "LIVE");
    drawChart(ch, data);
  }
}

async function poll() {
  try {
    const res = await fetch("/api/trading/dashboard", { cache: "no-store" });
    if (res.ok) render(await res.json());
  } catch (e) { /* keep the last frame; the server is local and read-only */ }
}

async function pollLive() {
  try {
    const res = await fetch("/api/trading/live", { cache: "no-store" });
    if (res.ok) renderLive(await res.json());
  } catch (e) { /* preserve last live frame */ }
}

poll();
pollLive();
setInterval(poll, 2000);
setInterval(pollLive, 1000);
window.addEventListener("resize", () => render.lastData && render(render.lastData));
