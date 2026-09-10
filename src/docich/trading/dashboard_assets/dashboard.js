"use strict";
// PAPER dashboard client (Issue #198). Read-only: it only GETs
// /api/trading/dashboard and draws. No posting, no trading.
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
  return v.toLocaleString("ja-JP", { maximumFractionDigits: abs >= 100 ? 0 : 6 });
}

function drawChart(chart, generatedAt) {
  const c = $("chart");
  const dpr = window.devicePixelRatio || 1;
  const W = 612, H = 252;
  if (c.width !== W * dpr || c.height !== H * dpr) {
    c.width = W * dpr; c.height = H * dpr;
  }
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);

  const series = (chart && Array.isArray(chart.closes)) ? chart.closes.filter((v) => isFinite(v)) : [];
  const plotW = W - PAD.l - PAD.r, plotH = H - PAD.t - PAD.b;

  // frame + grid
  g.strokeStyle = "#22335a"; g.lineWidth = 1;
  g.strokeRect(PAD.l, PAD.t, plotW, plotH);
  g.font = "11px monospace"; g.textBaseline = "middle";
  if (series.length < 2) {
    g.fillStyle = "#6f86a8"; g.textAlign = "center";
    g.fillText("終値チャート取得待ち（保有数値のみ）", PAD.l + plotW / 2, PAD.t + plotH / 2);
    return;
  }
  let lo = Math.min(...series), hi = Math.max(...series);
  if (hi <= lo) { hi = lo + 1; lo = lo - 1; }
  const span = hi - lo;
  const x = (i) => PAD.l + (plotW * i) / (series.length - 1);
  const y = (v) => PAD.t + plotH - ((v - lo) / span) * plotH;

  // horizontal grid + y labels
  g.textAlign = "right"; g.textBaseline = "middle";
  for (let r = 0; r <= 4; r++) {
    const value = hi - (span * r) / 4;
    const yy = PAD.t + (plotH * r) / 4;
    g.strokeStyle = r === 4 ? "#22335a" : "#182642";
    g.beginPath(); g.moveTo(PAD.l, yy); g.lineTo(PAD.l + plotW, yy); g.stroke();
    g.fillStyle = "#7f96ba"; g.fillText(fmtNum(value), PAD.l - 6, yy);
  }

  // area fill
  const grad = g.createLinearGradient(0, PAD.t, 0, PAD.t + plotH);
  grad.addColorStop(0, "rgba(56,189,248,0.38)");
  grad.addColorStop(1, "rgba(56,189,248,0.02)");
  g.beginPath();
  g.moveTo(x(0), y(series[0]));
  for (let i = 1; i < series.length; i++) g.lineTo(x(i), y(series[i]));
  g.lineTo(x(series.length - 1), PAD.t + plotH);
  g.lineTo(x(0), PAD.t + plotH);
  g.closePath(); g.fillStyle = grad; g.fill();

  // line
  g.beginPath();
  g.moveTo(x(0), y(series[0]));
  for (let i = 1; i < series.length; i++) g.lineTo(x(i), y(series[i]));
  g.strokeStyle = "#38bdf8"; g.lineWidth = 2; g.stroke();

  // last value marker
  const lx = x(series.length - 1), ly = y(series[series.length - 1]);
  g.beginPath(); g.arc(lx, ly, 3.5, 0, Math.PI * 2);
  g.fillStyle = "#e6edf7"; g.fill();
  g.textAlign = "left"; g.fillStyle = "#e6edf7";
  g.fillText(fmtNum(series[series.length - 1]), Math.min(lx + 6, W - 70), ly - 10);

  // x labels: bar count + first/last bar hint (closes only)
  g.textAlign = "center"; g.fillStyle = "#7f96ba";
  g.fillText(`${series.length}本`, PAD.l + plotW / 2, H - 8);
}

function render(data) {
  if (!data || data.schema_version !== 1) return;
  const h = data.header || {}, p = data.portfolio || {}, ch = data.chart || {}, dec = data.decision || {};
  $("title").textContent = h.title || "PAPER 暗号資産コーナー";
  $("clock").textContent = jst(h.generated_at || (Date.now() / 1000)) + " JST";
  $("state").textContent = "状態:" + (h.worker_state || "unknown") + " #" + (h.snapshot_seq ?? "?");
  const age = h.data_age_sec;
  $("remaining").textContent = (age === null || age === undefined) ? "" : `データ齢 ${age}s`;
  $("remaining").className = (age !== null && age > 180) ? "neg" : "";

  $("capital").textContent = fmtNum(Number(p.capital_jpy)) + " 円";
  $("deployed").textContent = fmtNum(Number(p.deployed_jpy)) + " 円";
  $("positions").textContent = (p.positions || []).length + " 銘柄";
  $("fresh").textContent = `${p.fresh_markets ?? 0}/${p.total_markets ?? 0}`;

  $("focus").textContent = "注目: " + (ch.symbol || "（観測待ち）");
  $("range").textContent = ch.count ? `5分足${ch.count}本 約${Math.round((ch.count * 5) / 60 * 10) / 10}時間` : "";
  $("decision").innerHTML = `<div>候補 ${dec.candidate_count ?? 0} 件</div>` +
    ((dec.reasons || []).length
      ? (dec.reasons || []).map((r) => `<div class="muted">・${r.label}</div>`).join("")
      : `<div class="muted">条件未達・見送り</div>`);
  $("skipped").innerHTML = (dec.skipped || []).length
    ? (dec.skipped || []).map((r) => `<div class="muted">${r.label}</div>`).join("")
    : `<div class="muted">見送り理由なし</div>`;
  $("holdings").innerHTML = (p.positions || []).length
    ? (p.positions || []).slice(0, 4).map((x) => `<div>${x.symbol} <span class="muted">${x.amount}</span></div>`).join("")
    : `<div class="muted">なし（未保有は正常）</div>`;

  const fills = data.fills || [];
  $("fills").innerHTML = fills.length
    ? fills.map((f) => `<div>${f.symbol} ${f.side} ${f.amount}@${f.price} <span class="muted">${f.quote}</span></div>`).join("")
    : `<div class="muted">なし（未取引は正常）</div>`;
  $("disclaimer").textContent = data.disclaimer || "";

  drawChart(ch, h.generated_at);
}

async function poll() {
  try {
    const res = await fetch("/api/trading/dashboard", { cache: "no-store" });
    if (res.ok) render(await res.json());
  } catch (e) { /* keep the last frame; the server is local and read-only */ }
}

poll();
setInterval(poll, 2000);
window.addEventListener("resize", () => render.lastData && render(render.lastData));
