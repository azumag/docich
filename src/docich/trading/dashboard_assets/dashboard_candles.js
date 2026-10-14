// Presentation-only 10-second candlesticks built from the existing public live feed.
// Trading decisions continue to use the worker's reviewed 5-minute bars.
(() => {
  const originalDrawChart = window.drawChart;
  if (typeof originalDrawChart !== "function") return;

  const BUCKET_SECONDS = 10;
  const MAX_CANDLES = 90;
  const PAD2 = { l: 58, r: 10, t: 10, b: 22 };
  const streams = new Map();

  function finitePositive(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
  }

  function remember(state, key) {
    if (state.seen.has(key)) return false;
    state.seen.add(key);
    state.seenOrder.push(key);
    while (state.seenOrder.length > 1200) {
      state.seen.delete(state.seenOrder.shift());
    }
    return true;
  }

  function addSample(state, timestamp, price, identity) {
    const ts = Number(timestamp);
    const px = finitePositive(price);
    if (!Number.isFinite(ts) || ts <= 0 || px === null || !remember(state, identity)) return;
    const bucket = Math.floor(ts / BUCKET_SECONDS) * BUCKET_SECONDS;
    let candle = state.candles.get(bucket);
    if (!candle) {
      candle = { time: bucket, open: px, high: px, low: px, close: px, firstTs: ts, lastTs: ts };
      state.candles.set(bucket, candle);
    } else {
      candle.high = Math.max(candle.high, px);
      candle.low = Math.min(candle.low, px);
      if (ts < candle.firstTs) {
        candle.firstTs = ts;
        candle.open = px;
      }
      if (ts >= candle.lastTs) {
        candle.lastTs = ts;
        candle.close = px;
      }
    }
    const ordered = [...state.candles.keys()].sort((a, b) => a - b);
    while (ordered.length > MAX_CANDLES) {
      state.candles.delete(ordered.shift());
    }
  }

  function ingest(live) {
    if (!live || live.available !== true || !live.symbol) return null;
    const symbol = String(live.symbol);
    let state = streams.get(symbol);
    if (!state) {
      state = { candles: new Map(), seen: new Set(), seenOrder: [] };
      streams.set(symbol, state);
    }
    const trades = Array.isArray(live.trades) ? [...live.trades] : [];
    trades.sort((a, b) => Number(a.timestamp || 0) - Number(b.timestamp || 0));
    for (const trade of trades) {
      const identity = trade.id
        ? `trade:${trade.id}`
        : `trade:${trade.timestamp}:${trade.price}:${trade.amount}:${trade.side}`;
      addSample(state, trade.timestamp, trade.price, identity);
    }
    const ticker = live.ticker || {};
    const tickerTs = Number(ticker.as_of || live.fetched_at);
    const tickerLast = finitePositive(ticker.last);
    if (tickerLast !== null && Number.isFinite(tickerTs)) {
      addSample(state, tickerTs, tickerLast, `ticker:${tickerTs}:${tickerLast}`);
    }
    return [...state.candles.values()].sort((a, b) => a.time - b.time).slice(-MAX_CANDLES);
  }

  function drawCandles(chart, live) {
    if (!chart || !live || live.available !== true || !live.symbol) return false;
    const candles = ingest(live);
    if (!candles || candles.length < 1) return false;

    const canvas = document.getElementById("chart");
    const dpr = window.devicePixelRatio || 1;
    const W = 524, H = 330;
    if (canvas.width !== W * dpr || canvas.height !== H * dpr) {
      canvas.width = W * dpr;
      canvas.height = H * dpr;
    }
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);

    const plotW = W - PAD2.l - PAD2.r;
    const plotH = H - PAD2.t - PAD2.b;
    let lo = Math.min(...candles.map((c) => c.low));
    let hi = Math.max(...candles.map((c) => c.high));
    if (!(hi > lo)) { hi = lo + 1; lo = Math.max(0, lo - 1); }
    const padding = Math.max((hi - lo) * 0.04, Number.EPSILON);
    lo -= padding;
    hi += padding;
    const span = hi - lo;
    const y = (value) => PAD2.t + plotH - ((value - lo) / span) * plotH;
    const step = plotW / candles.length;
    const bodyWidth = Math.max(2, Math.min(8, step * 0.62));

    g.strokeStyle = "#22335a";
    g.lineWidth = 1;
    g.strokeRect(PAD2.l, PAD2.t, plotW, plotH);
    g.font = "11px monospace";
    g.textAlign = "right";
    g.textBaseline = "middle";
    for (let row = 0; row <= 4; row++) {
      const value = hi - (span * row) / 4;
      const yy = PAD2.t + (plotH * row) / 4;
      g.strokeStyle = row === 4 ? "#22335a" : "#182642";
      g.beginPath();
      g.moveTo(PAD2.l, yy);
      g.lineTo(PAD2.l + plotW, yy);
      g.stroke();
      g.fillStyle = "#7f96ba";
      g.fillText(fmtNum(value), PAD2.l - 6, yy);
    }

    candles.forEach((candle, index) => {
      const center = PAD2.l + step * (index + 0.5);
      const rising = candle.close >= candle.open;
      const color = rising ? "#34d399" : "#fb7185";
      const openY = y(candle.open);
      const closeY = y(candle.close);
      const highY = y(candle.high);
      const lowY = y(candle.low);
      g.strokeStyle = color;
      g.fillStyle = color;
      g.lineWidth = 1;
      g.beginPath();
      g.moveTo(center, highY);
      g.lineTo(center, lowY);
      g.stroke();
      const top = Math.min(openY, closeY);
      const height = Math.max(1.5, Math.abs(closeY - openY));
      g.fillRect(center - bodyWidth / 2, top, bodyWidth, height);
    });

    const latest = candles[candles.length - 1];
    g.fillStyle = "#e6edf7";
    g.textAlign = "left";
    g.fillText(`${fmtNum(latest.close)} LIVE`, Math.max(PAD2.l + 4, W - 118), y(latest.close) - 10);
    g.fillStyle = "#7f96ba";
    g.textAlign = "center";
    g.fillText(`${BUCKET_SECONDS}秒足 ${candles.length}本`, PAD2.l + plotW / 2, H - 8);

    const preferred = String(live.preferred_symbol || chart.symbol || "");
    const display = String(live.symbol || preferred);
    const fallback = live.display_fallback === true && preferred && display !== preferred;
    const focus = document.getElementById("focus");
    if (focus) {
      focus.textContent = fallback
        ? `注目: ${display}（${preferred}無風のため一時切替）`
        : `注目: ${display || "（観測待ち）"}`;
    }
    const range = document.getElementById("range");
    if (range) range.textContent = `${BUCKET_SECONDS}秒足${candles.length}本`;
    const note = document.getElementById("chartnote");
    if (note) note.textContent = fallback
      ? `${preferred}の約定・価格変化が${Number(live.focus_inactive_sec || 0)}秒止まったため、活発なBTC/JPYへ一時切替中。元銘柄が動けば自動で戻ります。売買判断には影響しません。`
      : `bitbank公開約定・現在値から生成した${BUCKET_SECONDS}秒足ローソク（表示専用・約2秒更新）。売買判断は従来の5分足です。`;
    return true;
  }

  window.drawChart = function(chart, live) {
    if (!drawCandles(chart, live)) originalDrawChart(chart, live);
  };
})();
