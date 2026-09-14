// Presentation-only multi-timeframe strip for the PAPER dashboard (Issue #348).
// Shows the daily / 1-hour / 15-minute / 1-minute chart of the focus market
// with a simple Bollinger band, read from the read-only local endpoint. It
// never posts and never influences a trading decision.
(() => {
  "use strict";

  const POLL_MS = 10000;
  const PAD = { l: 2, r: 2, t: 3, b: 2 };
  const timeframes = new Map();

  function finite(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function fmtPct(value) {
    const n = finite(value);
    if (n === null) return "--";
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(2)}%`;
  }

  function trendClass(trend) {
    if (trend === "上昇") return "pos";
    if (trend === "下降") return "neg";
    return "muted";
  }

  function ensureStrip() {
    let strip = document.getElementById("tfstrip");
    if (strip) return strip;
    const wrap = document.getElementById("chartwrap");
    if (!wrap) return null;
    strip = document.createElement("div");
    strip.id = "tfstrip";
    const note = document.getElementById("chartnote");
    if (note) wrap.insertBefore(strip, note);
    else wrap.appendChild(strip);
    return strip;
  }

  function cellFor(strip, timeframe, label) {
    let cell = timeframes.get(timeframe);
    if (cell && cell.root && cell.root.isConnected) return cell;
    const root = document.createElement("div");
    root.className = "tf-cell";
    const head = document.createElement("div");
    head.className = "tf-head";
    const name = document.createElement("span");
    name.className = "tf-name";
    name.textContent = label;
    const change = document.createElement("span");
    change.className = "tf-change";
    head.appendChild(name);
    head.appendChild(change);
    const canvas = document.createElement("canvas");
    canvas.className = "tf-canvas";
    root.appendChild(head);
    root.appendChild(canvas);
    strip.appendChild(root);
    cell = { root, change, canvas, head };
    timeframes.set(timeframe, cell);
    return cell;
  }

  function drawMini(canvas, view) {
    const bars = Array.isArray(view.bars) ? view.bars : [];
    const closes = bars.map((bar) => finite(bar.c)).filter((value) => value !== null);
    const dpr = window.devicePixelRatio || 1;
    const W = 122;
    const H = 30;
    if (canvas.width !== W * dpr || canvas.height !== H * dpr) {
      canvas.width = W * dpr;
      canvas.height = H * dpr;
    }
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    if (closes.length < 2) {
      g.fillStyle = "#6f86a8";
      g.font = "10px monospace";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText("取得待ち", W / 2, H / 2);
      return;
    }
    const upper = finite(view.bb_upper);
    const lower = finite(view.bb_lower);
    let lo = Math.min(...closes);
    let hi = Math.max(...closes);
    if (upper !== null) hi = Math.max(hi, upper);
    if (lower !== null) lo = Math.min(lo, lower);
    if (!(hi > lo)) {
      hi = lo + 1;
      lo = Math.max(0, lo - 1);
    }
    const span = hi - lo;
    const plotW = W - PAD.l - PAD.r;
    const plotH = H - PAD.t - PAD.b;
    const x = (i) => PAD.l + (plotW * i) / (closes.length - 1);
    const y = (value) => PAD.t + plotH - ((value - lo) / span) * plotH;

    if (upper !== null && lower !== null && upper > lower) {
      g.beginPath();
      g.moveTo(x(0), y(upper));
      for (let i = 1; i < closes.length; i++) g.lineTo(x(i), y(upper));
      for (let i = closes.length - 1; i >= 0; i--) g.lineTo(x(i), y(lower));
      g.closePath();
      g.fillStyle = "rgba(56,189,248,0.12)";
      g.fill();
      g.strokeStyle = "rgba(56,189,248,0.35)";
      g.lineWidth = 1;
      g.beginPath();
      g.moveTo(x(0), y(upper));
      g.lineTo(x(closes.length - 1), y(upper));
      g.moveTo(x(0), y(lower));
      g.lineTo(x(closes.length - 1), y(lower));
      g.stroke();
    }

    g.beginPath();
    for (let i = 0; i < closes.length; i++) {
      if (i === 0) g.moveTo(x(i), y(closes[i]));
      else g.lineTo(x(i), y(closes[i]));
    }
    g.strokeStyle = view.trend === "下降" ? "#fb7185" : "#34d399";
    g.lineWidth = 1.4;
    g.stroke();

    const lastX = x(closes.length - 1);
    const lastY = y(closes[closes.length - 1]);
    g.beginPath();
    g.arc(lastX, lastY, 2.4, 0, Math.PI * 2);
    g.fillStyle = "#e6edf7";
    g.fill();
  }

  function renderStrip(data) {
    if (!data || data.schema_version !== 1) return;
    const strip = ensureStrip();
    if (!strip) return;
    const views = Array.isArray(data.timeframes) ? data.timeframes : [];
    strip.classList.toggle("muted-note", data.available !== true);
    if (!views.length) {
      strip.textContent = data.available === true ? "" : "時間足チャート取得待ち";
      return;
    }
    for (const view of views) {
      if (!view || !view.timeframe) continue;
      const cell = cellFor(strip, String(view.timeframe), String(view.label || view.timeframe));
      const available = view.available === true;
      cell.change.textContent = available ? `${view.trend || ""} ${fmtPct(view.range_change_pct)}` : "取得待ち";
      cell.change.className = `tf-change ${available ? trendClass(view.trend) : "muted"}`;
      cell.root.title = available
        ? `${view.label}: ${view.bar_count || 0}本 ${view.bb_phrase || ""}` +
          (view.stale ? "（更新遅延）" : "")
        : `${view.label}: 公開データを取得できませんでした`;
      if (available) drawMini(cell.canvas, view);
    }
  }

  let inFlight = false;

  async function pollTimeframes() {
    if (inFlight) return;
    inFlight = true;
    try {
      const res = await fetch("/api/trading/timeframes", { cache: "no-store" });
      if (res.ok) renderStrip(await res.json());
    } catch (e) {
      /* keep the last frame; the endpoint is local and read-only */
    } finally {
      inFlight = false;
    }
  }

  pollTimeframes();
  setInterval(pollTimeframes, POLL_MS);
})();
