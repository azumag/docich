import fs from 'fs';


export const DIRECT_BROADCAST_STATE_ROUTE = '/__soren_overlay/broadcast/state';
export const DIRECT_BROADCAST_VERSION = 1;
const DEFAULT_PAPER_IMPROVE_STATE_FILE = '/home/ubuntu/docich/run-soren-live/trading/paper_improve_status.json';
const PAPER_IMPROVE_TERMINAL_VISIBLE_SEC = 120;
const DEFAULT_AB_STATE_FILE = 'tmp/state/ab_state.json';
const DEFAULT_AB_GAMES_FILE = 'tmp/state/ab_games.jsonl';
const DEFAULT_ROLLING_SCORES_FILE = 'tmp/state/rolling_scores.json';
const AB_COMPARISON_KEEP = 100;
const MIN_MATURE_GAMES = 12;
const RANK_LCB_Z = 1.28;
const RANK_WEIGHT_P50 = 0.55;
const RANK_WEIGHT_P25 = 0.30;
const RANK_WEIGHT_LCB = 0.15;


function readUtf8(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return '';
  }
}


function readJson(file) {
  try {
    const value = JSON.parse(readUtf8(file));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}


function directStreamSystemMetrics(file) {
  const metrics = readJson(file).system_metrics;
  if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) return null;
  const percentage = (value) => Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
  const integer = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const history = Array.isArray(metrics.history) ? metrics.history.slice(-36).map((point) => ({
    ts: integer(point?.ts),
    cpuPercent: percentage(point?.cpu_percent),
    memoryPercent: percentage(point?.memory_percent),
    diskPercent: percentage(point?.disk_percent),
  })) : [];
  return {
    sampledAt: integer(metrics.sampled_at),
    cpuPercent: percentage(metrics.cpu_percent),
    memoryPercent: percentage(metrics.memory_percent),
    memoryUsedBytes: integer(metrics.memory_used_bytes),
    memoryTotalBytes: integer(metrics.memory_total_bytes),
    diskPercent: percentage(metrics.disk_percent),
    diskUsedBytes: integer(metrics.disk_used_bytes),
    diskTotalBytes: integer(metrics.disk_total_bytes),
    diskFreeBytes: integer(metrics.disk_free_bytes),
    history,
  };
}


function sourceUpdatedAt(file) {
  try {
    return Math.floor(fs.statSync(file).mtimeMs / 1000);
  } catch {
    return 0;
  }
}


function decodeHtmlEntities(value) {
  return String(value ?? '')
    .replace(/&#x([0-9a-f]+);/gi, (_, raw) => String.fromCodePoint(Number.parseInt(raw, 16)))
    .replace(/&#([0-9]+);/g, (_, raw) => String.fromCodePoint(Number.parseInt(raw, 10)))
    .replace(/&quot;/g, '"')
    .replace(/&#x27;|&#39;|&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}


export function extractLegacyOverlayText(documentHtml) {
  const match = String(documentHtml ?? '').match(/<pre\b[^>]*>([\s\S]*?)<\/pre>/i);
  if (!match) return '';
  return decodeHtmlEntities(
    match[1]
      .replace(/<br\s*\/?\s*>/gi, '\n')
      .replace(/<[^>]*>/g, ''),
  ).replace(/\r\n?/g, '\n').trimEnd();
}


function parseLegacySpanStyle(style) {
  let color = '';
  let bold = false;
  let opacity = '';
  for (const part of String(style ?? '').split(';')) {
    const decl = part.trim();
    if (!decl) continue;
    let match = /^color:\s*(#[0-9a-fA-F]{3,8})$/.exec(decl);
    if (match) {
      color = match[1].toLowerCase();
      continue;
    }
    match = /^font-weight:\s*700$/i.exec(decl);
    if (match) {
      bold = true;
      continue;
    }
    match = /^opacity:\s*((?:0|1)(?:\.[0-9]+)?|\.[0-9]+)$/.exec(decl);
    if (match) {
      opacity = match[1];
      continue;
    }
    return null;
  }
  return { c: color, b: bold, o: opacity };
}


// show_status_g 系オーバーレイの <pre> 内色付けを、許可リスト付きの
// テキストセグメント配列へ変換する。未知タグ・未知スタイルは装飾なし
// 平文として残し、マークアップは一切クライアントへ渡さない。
export function extractLegacyOverlayLineSegments(documentHtml) {
  const match = String(documentHtml ?? '').match(/<pre\b[^>]*>([\s\S]*?)<\/pre>/i);
  if (!match) return [];
  return match[1]
    .replace(/<br\s*\/?\s*>/gi, '\n')
    .replace(/\r\n?/g, '\n')
    .trimEnd()
    .split('\n')
    .map((rawLine) => {
      const segments = [];
      let current = null;
      let style = null;
      let last = 0;
      const tagRe = /<[^>]*>/g;
      let tag;
      const pushText = (piece) => {
        const text = decodeHtmlEntities(piece);
        if (!text) return;
        if (!current || current.c !== (style?.c || '') || current.b !== Boolean(style?.b) || current.o !== (style?.o || '')) {
          current = {
            t: text,
            ...(style?.c ? { c: style.c } : {}),
            ...(style?.b ? { b: 1 } : {}),
            ...(style?.o ? { o: style.o } : {}),
          };
          segments.push(current);
        } else {
          current.t += text;
        }
      };
      while ((tag = tagRe.exec(rawLine)) !== null) {
        pushText(rawLine.slice(last, tag.index));
        last = tag.index + tag[0].length;
        const closeMatch = /^<\/span\s*>$/i.exec(tag[0]);
        if (closeMatch) {
          style = null;
          current = null;
          continue;
        }
        const openMatch = /^<span\s+style="([^"]*)"\s*>$/i.exec(tag[0]);
        if (openMatch) {
          const parsed = parseLegacySpanStyle(openMatch[1]);
          style = parsed && (parsed.c || parsed.b || parsed.o) ? parsed : null;
          current = null;
          continue;
        }
        style = null;
        current = null;
      }
      pushText(rawLine.slice(last));
      return segments;
    });
}


function parseJsonConstant(documentHtml, name, fallback) {
  const document = String(documentHtml ?? '');
  const marker = new RegExp(`(?:^|\\n)\\s*const\\s+${name}\\s*=\\s*`, 'm').exec(document);
  if (!marker) return fallback;
  const start = marker.index + marker[0].length;
  let inString = false;
  let escaped = false;
  let depth = 0;
  let end = -1;
  for (let index = start; index < document.length; index += 1) {
    const char = document[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') inString = true;
    else if (char === '{' || char === '[') depth += 1;
    else if (char === '}' || char === ']') depth = Math.max(0, depth - 1);
    else if (char === ';' && depth === 0) {
      end = index;
      break;
    }
  }
  if (end < start) return fallback;
  try {
    return JSON.parse(document.slice(start, end));
  } catch {
    return fallback;
  }
}


function parseVisibleSeconds(documentHtml) {
  const match = String(documentHtml ?? '').match(/^\s*const\s+VISIBLE_SEC\s*=\s*(\d+)\s*;\s*$/m);
  if (!match) return 18;
  const value = Number(match[1]);
  return Number.isSafeInteger(value) && value >= 1 && value <= 3600 ? value : 18;
}


export function parseLegacyEventOverlayDocument(documentHtml) {
  const eventsRaw = parseJsonConstant(documentHtml, 'EVENTS', []);
  const workRaw = parseJsonConstant(documentHtml, 'WORK', {});
  const generatorsRaw = parseJsonConstant(documentHtml, 'GEN', []);
  return {
    events: Array.isArray(eventsRaw)
      ? eventsRaw.filter((item) => item && typeof item === 'object' && !Array.isArray(item)).slice(-18)
      : [],
    work: workRaw && typeof workRaw === 'object' && !Array.isArray(workRaw) ? workRaw : {},
    generators: Array.isArray(generatorsRaw)
      ? generatorsRaw.filter((item) => item && typeof item === 'object' && !Array.isArray(item)).slice(-4)
      : [],
    visibleSec: parseVisibleSeconds(documentHtml),
  };
}


function feed(label, file, { withSegments = false } = {}) {
  const documentHtml = readUtf8(file);
  const text = extractLegacyOverlayText(documentHtml);
  return {
    label,
    text,
    ...(withSegments ? { segments: extractLegacyOverlayLineSegments(documentHtml) } : {}),
    available: text.length > 0,
    updatedAt: sourceUpdatedAt(file),
    lineCount: text ? text.split('\n').length : 0,
  };
}


function quantile(values, p) {
  const xs = [...values].map(Number).filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return 0;
  if (xs.length === 1) return xs[0];
  const pos = (xs.length - 1) * p;
  const lo = Math.floor(pos);
  const hi = Math.min(lo + 1, xs.length - 1);
  const frac = pos - lo;
  return xs[lo] * (1 - frac) + xs[hi] * frac;
}


function strategyMetrics(values) {
  const xs = values.map(Number).filter(Number.isFinite);
  if (!xs.length) return null;
  const n = xs.length;
  const mean = xs.reduce((a, b) => a + b, 0) / n;
  const p25 = quantile(xs, 0.25);
  const p50 = quantile(xs, 0.5);
  const variance = n > 1 ? xs.reduce((acc, x) => acc + ((x - mean) ** 2), 0) / n : 0;
  const std = Math.sqrt(variance);
  const lcb = mean - RANK_LCB_Z * (std / Math.sqrt(n));
  const comp = RANK_WEIGHT_P50 * p50 + RANK_WEIGHT_P25 * p25 + RANK_WEIGHT_LCB * lcb;
  return { n, comp, p50, p25, lcb };
}


function loadAbExperiment(stateFile, gamesFile) {
  const state = readJson(stateFile);
  const aHash = String(state.a_hash || '');
  const bHash = String(state.b_hash || '');
  if (!aHash || !bHash) return null;
  const primary = String(state.primary || 'score').toLowerCase() === 'eval' ? 'eval' : 'score';
  const rows = [];
  const seen = new Set();
  for (const raw of readUtf8(gamesFile).split('\n')) {
    if (!raw.trim()) continue;
    let row;
    try { row = JSON.parse(raw); } catch { continue; }
    if (!row || typeof row !== 'object' || row.tainted) continue;
    const idx = Number(row.idx);
    if (!Number.isInteger(idx) || seen.has(idx)) continue;
    seen.add(idx);
    const arm = String(row.arm || '');
    if (arm !== 'A' && arm !== 'B') continue;
    const value = Number(row[primary]);
    if (!Number.isFinite(value)) continue;
    rows.push({ idx, arm, value });
  }
  const values = { A: [], B: [] };
  for (const row of rows) values[row.arm].push(row.value);
  const total = { A: values.A.length, B: values.B.length };
  const window = {
    A: values.A.slice(-AB_COMPARISON_KEEP),
    B: values.B.slice(-AB_COMPARISON_KEEP),
  };
  return {
    aHash,
    bHash,
    primary,
    total,
    window,
    metrics: { A: strategyMetrics(window.A), B: strategyMetrics(window.B) },
  };
}


function restorableStrategy(hash) {
  if (!hash) return false;
  return fs.existsSync(`strategy_versions/by_hash/${hash}.py`)
    || fs.existsSync(`strategy_versions_archive/by_hash/${hash}.py`);
}


function abRanks(experiment, rollingFile) {
  const entries = [];
  const skip = new Set([experiment.aHash, experiment.bHash]);
  const rolling = readJson(rollingFile);
  for (const [hash, data] of Object.entries(rolling)) {
    if (skip.has(hash) || !data || typeof data !== 'object') continue;
    const metrics = strategyMetrics(Array.isArray(data.scores) ? data.scores : []);
    if (!metrics || metrics.n < MIN_MATURE_GAMES || !restorableStrategy(hash)) continue;
    entries.push({ hash, ...metrics });
  }
  for (const arm of ['A', 'B']) {
    const metrics = experiment.metrics[arm];
    const hash = arm === 'A' ? experiment.aHash : experiment.bHash;
    if (metrics) entries.push({ hash, arm, ...metrics });
  }
  entries.sort((a, b) => (b.comp - a.comp) || (b.p50 - a.p50) || (b.p25 - a.p25) || (b.n - a.n));
  const out = {};
  entries.forEach((entry, index) => {
    if (entry.arm) out[entry.arm] = index + 1;
  });
  return out;
}


function augmentAbStrategyComparison(statusFeed, experiment, rollingFile) {
  if (!experiment || !statusFeed?.text) return statusFeed;
  const lines = String(statusFeed.text).split('\n');
  const segments = Array.isArray(statusFeed.segments) ? statusFeed.segments : [];
  const start = lines.findIndex((line) => line.trim().startsWith('Strategy Comparison'));
  if (start < 0) return statusFeed;
  let end = start + 1;
  while (end < lines.length && lines[end].trim()) end += 1;

  const a8 = experiment.aHash.slice(0, 8);
  const b8 = experiment.bHash.slice(0, 8);
  const keptLines = [];
  const keptSegments = [];
  for (let i = start; i < end; i += 1) {
    const line = lines[i];
    // Existing rolling/current rows for the two experimental hashes are based
    // on a different (normally 20-game) window.  Suppress those duplicates so
    // the viewer sees one authoritative A/B row per arm from ab_games.jsonl.
    if ((line.includes(a8) || line.includes(b8)) && /\b\d+\/\d+\b/.test(line)) continue;
    keptLines.push(line);
    if (segments.length) keptSegments.push(segments[i] || [{ t: line }]);
  }

  const ranks = abRanks(experiment, rollingFile);
  const armLines = [];
  for (const arm of ['A', 'B']) {
    const metrics = experiment.metrics[arm];
    if (!metrics) continue;
    const hash = (arm === 'A' ? experiment.aHash : experiment.bHash).slice(0, 8);
    const rank = ranks[arm] || 0;
    const total = experiment.total[arm];
    const maturity = metrics.n < MIN_MATURE_GAMES ? '*' : '';
    armLines.push(
      ` A/B: ${arm} rk${rank || '?'}${maturity} ${hash} n=${metrics.n}/${total}`
      + ` c${Math.round(metrics.comp)} m${Math.round(metrics.p50)} q${Math.round(metrics.p25)}`,
    );
  }
  const insertAt = Math.min(2, keptLines.length);
  keptLines.splice(insertAt, 0, ...armLines);
  if (segments.length) {
    keptSegments.splice(insertAt, 0, ...armLines.map((line) => [{ t: line }]));
  }

  const newLines = [...lines.slice(0, start), ...keptLines, ...lines.slice(end)];
  let newSegments;
  if (segments.length) {
    newSegments = [
      ...segments.slice(0, start),
      ...keptSegments,
      ...segments.slice(end),
    ];
  }
  return {
    ...statusFeed,
    text: newLines.join('\n'),
    ...(newSegments ? { segments: newSegments } : {}),
    lineCount: newLines.length,
  };
}


function cleanStatusLine(line) {
  return String(line || '').replace(/[│┌┐└┘─━]/g, '').trim();
}


function deriveAbTopOverride(statsText) {
  const stats = String(statsText || '').split('\n');
  const pick = (pattern) => stats.find((line) => pattern.test(String(line)));
  const head = pick(/SOREN\//);
  const recent = pick(/Recent30:/);
  const strategy = pick(/Strategy:/);
  const abLine = pick(/^.*A\/B: A [0-9a-f?]+ vs B /i);
  const remaining = pick(/(?:adopt-look in \d+g|no adopt-look left)/);
  if (!head || !recent || !abLine || !remaining) return null;

  const clean = cleanStatusLine(remaining);
  const look = clean.match(/\bk(\d+)\/(\d+)\b/);
  const adopt = clean.match(/adopt-look in (\d+)g/);
  const max = clean.match(/max (\d+)g/);
  const arm = cleanStatusLine(strategy).match(/\[([AB])\]/);
  let left = 'A/B 残り:';
  if (adopt && max) left += ` あと${adopt[1]}〜${max[1]}試合`;
  else if (adopt) left += ` あと${adopt[1]}試合`;
  else if (max) left += ` 最長${max[1]}試合`;
  else return null;
  if (arm) left += ` 現在${arm[1]}`;
  if (look) left += ` k${look[1]}/${look[2]}`;
  return {
    enabled: true,
    lines: [head, abLine, left, recent].map(cleanStatusLine).filter(Boolean).slice(0, 4),
    updatedAt: 0,
    derived: 'ab-status',
  };
}


const ANSI_RE = /\x1b\[[0-9;?]*[A-Za-z]/g;


function readLogTail(file, limit = 24) {
  const lines = readUtf8(file)
    .split('\n')
    .map((line) => line.replace(ANSI_RE, '').replace(/\r$/, ''))
    .filter((line) => line.length > 0)
    .slice(-limit);
  return lines;
}


function wildcardPhaseActive(state) {
  const phase = String(state?.phase || '').toLowerCase();
  return ['generating', 'running'].includes(phase);
}


function topOverrideState(file) {
  try {
    const raw = readUtf8(file);
    if (!raw.trim()) return null;
    const data = JSON.parse(raw);
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
    const updatedAt = Number(data.updatedAt ?? data.updated_at) || 0;
    if (data.enabled === false) return { enabled: false, lines: [], updatedAt };
    if (data.enabled === true && Array.isArray(data.lines)) {
      const lines = data.lines.map((s) => String(s).trim()).filter(Boolean).slice(0, 4);
      if (!lines.length) return null;
      return { enabled: true, lines, updatedAt };
    }
    return null;
  } catch {
    return null;
  }
}


function paperImproveFeed(file, nowSec) {
  const state = readJson(file);
  const status = String(state.status || '').toLowerCase();
  const updatedAt = Number(state.updated_at) || sourceUpdatedAt(file);
  const running = status === 'running' || status === 'queued';
  const terminal = ['improved', 'failed', 'skipped', 'dry-run'].includes(status);
  const recentTerminal = terminal && updatedAt > 0 && Math.max(0, nowSec - updatedAt) <= PAPER_IMPROVE_TERMINAL_VISIBLE_SEC;
  if (!running && !recentTerminal) return null;
  const progress = Math.max(0, Math.min(100, Number(state.progress) || 0));
  const phase = String(state.phase || '');
  const detail = String(state.detail || '');
  const lines = [];
  if (status === 'failed') lines.push(`FAILED PAPER IMPROVE ${progress}%`);
  else if (terminal) lines.push(`✓ PAPER IMPROVE ${progress}%`);
  else lines.push(`● PAPER IMPROVE ${progress}%`);
  if (phase) lines.push(`phase: ${phase}`);
  if (detail) lines.push(`detail: ${detail}`);
  return {
    active: true,
    status: `paper:${status || 'running'}`,
    phase,
    detail,
    progress,
    pid: 0,
    startedAt: Number(state.started_at) || 0,
    updatedAt,
    logUpdatedAt: updatedAt,
    logLines: lines,
    lineCount: lines.length,
    available: true,
    sourceUpdatedAt: sourceUpdatedAt(file),
    source: 'paper',
  };
}


function improveFeed(stateFile, logFile, wildcardStateFile, paperStateFile, nowSec) {
  const state = readJson(stateFile);
  const wildcard = readJson(wildcardStateFile);
  const status = String(state.status || 'idle').toLowerCase();
  const sorenActive = status === 'running' && !wildcardPhaseActive(wildcard);
  const logLines = readLogTail(logFile, 24);
  if (!sorenActive && !wildcardPhaseActive(wildcard)) {
    const paper = paperImproveFeed(paperStateFile, nowSec);
    if (paper) return paper;
  }
  return {
    active: sorenActive,
    status,
    phase: String(state.phase || ''),
    detail: String(state.detail || ''),
    progress: Number(state.progress) || 0,
    pid: Number(state.pid) || 0,
    startedAt: Number(state.started_at) || 0,
    updatedAt: Number(state.updated_at) || 0,
    logUpdatedAt: sourceUpdatedAt(logFile),
    logLines,
    lineCount: logLines.length,
    available: sorenActive || logLines.length > 0,
    sourceUpdatedAt: sourceUpdatedAt(stateFile),
    source: 'soren',
  };
}


export function buildDirectBroadcastOverlayState(config, nowMs = Date.now()) {
  const sources = config?.sources || {};
  const eventDocument = readUtf8(sources.eventHtmlFile);
  const notifications = parseLegacyEventOverlayDocument(eventDocument);
  const nowSec = Math.floor(Number(nowMs) / 1000);
  const paperImproveStateFile = String(
    sources.paperImproveStateFile
      || process.env.PAPER_IMPROVE_STATE_FILE
      || DEFAULT_PAPER_IMPROVE_STATE_FILE,
  );
  const abStateFile = String(sources.abStateFile || process.env.AB_STATE_FILE || DEFAULT_AB_STATE_FILE);
  const abGamesFile = String(sources.abGamesFile || process.env.AB_GAMES_FILE || DEFAULT_AB_GAMES_FILE);
  const rollingScoresFile = String(
    sources.rollingScoresFile || process.env.ROLLING_SCORES_FILE || DEFAULT_ROLLING_SCORES_FILE,
  );
  const experiment = loadAbExperiment(abStateFile, abGamesFile);
  const rawStatusG = feed('SHOW-STATUS-G', sources.statsHtmlFile, { withSegments: true });
  const showStatusG = augmentAbStrategyComparison(rawStatusG, experiment, rollingScoresFile);
  const explicitTopOverride = topOverrideState(sources.topOverrideFile);
  const derivedTopOverride = explicitTopOverride === null ? deriveAbTopOverride(showStatusG.text) : null;
  const topOverride = explicitTopOverride || derivedTopOverride;
  return {
    gameGapEnabled: config?.gameGapEnabled === true,
    version: DIRECT_BROADCAST_VERSION,
    updatedAt: nowSec,
    feeds: {
      showStatusG,
      showStatus: feed('SHOW-STATUS', sources.opsHtmlFile),
      systemMetrics: directStreamSystemMetrics(sources.directStreamStatusFile),
      improve: improveFeed(
        sources.improveStateFile,
        sources.improveLogFile,
        sources.wildcardStateFile,
        paperImproveStateFile,
        nowSec,
      ),
    },
    notifications: {
      ...notifications,
      sourceUpdatedAt: sourceUpdatedAt(sources.eventHtmlFile),
    },
    topOverride: topOverride ? {
      ...topOverride,
      sourceUpdatedAt: explicitTopOverride ? sourceUpdatedAt(sources.topOverrideFile) : sourceUpdatedAt(sources.statsHtmlFile),
    } : null,
  };
}
