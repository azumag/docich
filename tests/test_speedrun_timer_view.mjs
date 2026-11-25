import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

// CI installs no Chromium, so the display rules that decide colors and text
// are evaluated straight from the overlay HTML.  The shared browser test
// (tests/test_speedrun_timer_overlay.py) covers the DOM wiring when a browser
// is available; this file is the part that always runs.
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const HTML_FILE = path.join(ROOT, 'overlays', 'direct_broadcast_overlay.html');
const BEGIN = '/* speedrun-timer-pure:begin */';
const END = '/* speedrun-timer-pure:end */';

function loadPureBlock() {
  const html = fs.readFileSync(HTML_FILE, 'utf8');
  const begin = html.indexOf(BEGIN);
  const end = html.indexOf(END);
  assert.ok(begin >= 0, `missing ${BEGIN} in overlays/direct_broadcast_overlay.html`);
  assert.ok(end > begin, `missing ${END} after the begin marker`);
  const block = html.slice(begin + BEGIN.length, end).trim();
  assert.ok(block.length > 200, 'the speedrun pure block is empty');
  const code = block.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  assert.doesNotMatch(code, /\bdocument\b|\bwindow\b/,
    'the speedrun pure block must stay DOM-free');
  const wrapper = [
    "'use strict';",
    '(() => {',
    block,
    'return {srtClock, srtSplit, srtHms, srtDelta, srtElapsed, srtRunningBase,',
    '  speedrunTotalClass, speedrunCellModel};',
    '})()',
  ].join('\n');
  return vm.runInContext(wrapper, vm.createContext({}));
}

const pure = loadPureBlock();

// Objects created inside the vm have that context's prototypes; hand back
// plain host objects so deepStrictEqual compares values, not realms.
function cell(...args) {
  const model = pure.speedrunCellModel(...args);
  return {time: model.time, delta: model.delta, classes: Array.from(model.classes)};
}

const PB_CUMULATIVE = [10_000, 30_000, 60_000, 70_000, 80_000, 90_000,
  100_000, 110_000, 120_000, 130_000, 140_000, 150_000];
const PB_SEGMENTS = [10_000, 20_000, 30_000, 10_000, 10_000, 10_000,
  10_000, 10_000, 10_000, 10_000, 10_000, 10_000];

function timer(overrides = {}) {
  return {
    status: 'running',
    started_at_ms: 1_000_000,
    ended_at_ms: null,
    current_index: 2,
    splits: [],
    comparison: {
      pb_total_ms: 150_000,
      pb_segments_ms: [...PB_SEGMENTS],
      pb_cumulative_ms: [...PB_CUMULATIVE],
      gold_segments_ms: [10_000, 20_000, null, null, null, null,
        null, null, null, null, null, null],
      sum_of_best_ms: null,
    },
    heartbeat_at_ms: 1_000_000,
    pb_eligible: true,
    total_verdict: null,
    total_ms: null,
    stale: false,
    ...overrides,
  };
}

test('the pure block is extractable and time formats survive long runs', () => {
  assert.equal(pure.srtClock(0), '0:00:00.000');
  assert.equal(pure.srtClock(3_723_456), '1:02:03.456');
  assert.equal(pure.srtClock(360_000_000), '100:00:00.000');
  assert.equal(pure.srtClock(360_123_456), '100:02:03.456');
  assert.equal(pure.srtSplit(61_000), '1:01.000');
  assert.equal(pure.srtSplit(3_723_456), '1:02:03.456');
  assert.equal(pure.srtHms(61_000), '0:01:01');
  assert.equal(pure.srtHms(3_661_000), '1:01:01');
  assert.equal(pure.srtHms(360_000_000), '100:00:00');
  assert.equal(pure.srtDelta(-1_022), '−0:01.022');
  assert.equal(pure.srtDelta(15_210), '+0:15.210');
  assert.equal(pure.srtDelta(null), '—');
  assert.equal(pure.srtDelta(undefined), '—');
});

test('a running total is red when either checkpoint is slower than PB', () => {
  // Confirmed cumulative 9s is ahead of the PB's 10s, and the elapsed 50s is
  // ahead of the PB cumulative at the running index: blue.
  const ahead = timer({
    current_index: 2,
    splits: [{index: 0, end_index: 0, segment_ms: 9_000, cumulative_ms: 9_000,
      verdict: 'gold', delta_ms: -1_000}],
  });
  assert.equal(pure.speedrunTotalClass(ahead, 50_000), 'is-blue');

  // The confirmed cumulative is still ahead, but the live elapsed passed the
  // PB checkpoint: the slower checkpoint wins, so red.
  assert.equal(pure.speedrunTotalClass(ahead, 61_000), 'is-red');

  // A confirmed cumulative behind the PB is red even while elapsed is fine.
  const behind = timer({
    current_index: 2,
    splits: [{index: 0, end_index: 0, segment_ms: 11_000, cumulative_ms: 11_000,
      verdict: 'red', delta_ms: 1_000}],
  });
  assert.equal(pure.speedrunTotalClass(behind, 50_000), 'is-red');

  // Elapsed alone is comparable (no splits yet).
  assert.equal(pure.speedrunTotalClass(timer({splits: []}), 50_000), 'is-blue');
  assert.equal(pure.speedrunTotalClass(timer({splits: []}), 61_000), 'is-red');
});

test('a running total without a PB is white, and so is a solved verdict', () => {
  const noPb = timer({
    comparison: {pb_total_ms: null, pb_segments_ms: [], pb_cumulative_ms: [],
      gold_segments_ms: [], sum_of_best_ms: null},
    splits: [{index: 0, end_index: 0, segment_ms: 9_000, cumulative_ms: 9_000,
      verdict: 'gold', delta_ms: null}],
  });
  assert.equal(pure.speedrunTotalClass(noPb, 50_000), 'is-white');
  assert.equal(pure.speedrunTotalClass(timer({status: 'dnf'}), 50_000), 'is-white');
  assert.equal(pure.speedrunTotalClass(timer({status: 'pending'}), 0), 'is-white');
});

test('a finished total prefers the rta total_verdict and falls back without it', () => {
  const finished = (overrides = {}) => timer({
    status: 'finished', ended_at_ms: 1_120_000, current_index: 12, ...overrides,
  });
  // The recorded verdict wins over any elapsed/PB arithmetic.
  assert.equal(pure.speedrunTotalClass(
    finished({total_verdict: 'gold', total_ms: 120_000}), 130_000), 'is-gold');
  assert.equal(pure.speedrunTotalClass(
    finished({total_verdict: 'red', total_ms: 120_000}), 120_000), 'is-red');
  assert.equal(pure.speedrunTotalClass(
    finished({total_verdict: 'white', total_ms: 120_000}), 120_000), 'is-white');
  // An unknown verdict is not trusted; the old PB-total comparison is used.
  assert.equal(pure.speedrunTotalClass(
    finished({total_verdict: 'blue', comparison: {
      ...timer().comparison, pb_total_ms: 120_000}}), 120_000), 'is-gold');
  assert.equal(pure.speedrunTotalClass(
    finished({comparison: {...timer().comparison, pb_total_ms: 120_000}}), 120_000), 'is-gold');
  assert.equal(pure.speedrunTotalClass(
    finished({comparison: {...timer().comparison, pb_total_ms: 100_000}}), 120_000), 'is-red');
  // No PB, or not PB-eligible: not enough evidence for yellow or red.
  assert.equal(pure.speedrunTotalClass(
    finished({comparison: {pb_total_ms: null, pb_segments_ms: [], pb_cumulative_ms: [],
      gold_segments_ms: [], sum_of_best_ms: null}}), 120_000), 'is-white');
  assert.equal(pure.speedrunTotalClass(
    finished({pb_eligible: false, comparison: {...timer().comparison, pb_total_ms: 120_000}}),
    120_000), 'is-white');
});

test('cells keep confirmed verdicts, merge continuations, and grey the rest', () => {
  const merged = timer({
    status: 'running',
    current_index: 4,
    splits: [
      {index: 0, end_index: 0, segment_ms: 61_000, cumulative_ms: 61_000,
        verdict: 'gold', delta_ms: -1_022},
      {index: 1, end_index: 3, segment_ms: 90_000, cumulative_ms: 151_000,
        verdict: 'blue', delta_ms: 5_000},
    ],
  });
  assert.deepEqual(cell(merged, 0, 160_000),
    {time: '1:01.000', delta: '−0:01.022', classes: ['is-gold']});
  // The merged range starts in cell 2 and later cells only say 結合.
  assert.deepEqual(cell(merged, 1, 160_000),
    {time: '1:30.000', delta: '+0:05.000', classes: ['is-blue']});
  for (const index of [2, 3]) {
    assert.deepEqual(cell(merged, index, 160_000),
      {time: '', delta: '結合', classes: ['is-gray']});
  }
  // The running cell starts after the merged range (151s) and is white until
  // it passes the PB segment (10s).
  assert.deepEqual(cell(merged, 4, 160_000),
    {time: '0:09.000', delta: '', classes: ['is-running', 'is-white']});
  assert.deepEqual(cell(merged, 4, 165_000),
    {time: '0:14.000', delta: '', classes: ['is-running', 'is-red']});
  // Unreached cells show the PB segment in grey h:mm:ss; no PB means empty.
  assert.deepEqual(cell(merged, 5, 160_000),
    {time: 'PB 0:00:10', delta: '', classes: ['is-unreached']});
  const longPb = timer({
    current_index: 8,
    comparison: {...timer().comparison,
      pb_segments_ms: [null, null, null, null, null, null, null, 3_661_000]},
  });
  assert.deepEqual(cell(longPb, 7, 0),
    {time: 'PB 1:01:01', delta: '', classes: ['is-unreached']});
  const noPb = timer({
    current_index: 0,
    comparison: {pb_total_ms: null, pb_segments_ms: [], pb_cumulative_ms: [],
      gold_segments_ms: [], sum_of_best_ms: null},
  });
  assert.deepEqual(cell(noPb, 5, 0),
    {time: '', delta: '', classes: ['is-unreached']});
  // A confirmed split without a PB delta still renders the long dash.
  const noDelta = timer({
    current_index: 1,
    splits: [{index: 0, end_index: 0, segment_ms: 9_000, cumulative_ms: 9_000,
      verdict: 'white', delta_ms: null}],
  });
  assert.deepEqual(cell(noDelta, 0, 9_500),
    {time: '0:09.000', delta: '—', classes: ['is-white']});
});

test('a stale running timer freezes the elapsed and reports itself', () => {
  const stale = timer({
    status: 'running', started_at_ms: 1_000_000, heartbeat_at_ms: 1_030_000,
    current_index: 3, stale: true,
  });
  assert.equal(pure.srtElapsed(stale, 9_999_999), 30_000);
  assert.equal(pure.srtElapsed(timer({status: 'finished', ended_at_ms: 1_120_000}),
    9_999_999), 120_000);
  assert.equal(pure.srtElapsed(timer({status: 'pending'}), 9_999_999), 0);
});
