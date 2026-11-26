import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  DEFAULT_SPEEDRUN_TIMER_FILE,
  SPEEDRUN_TIMER_MAX_SPLITS,
  SPEEDRUN_TIMER_STALE_MS,
  SPEEDRUN_TIMER_TTL_MS,
  buildDirectBroadcastOverlayState,
  speedrunTimerState,
} from '../overlays/direct_broadcast_overlay_state.mjs';

// Shapes copied from src/docich/hanjuku_speedrun/rta/timer.py (new_timer /
// start_attempt / confirm_split / finish / dnf), then frozen at a fixed epoch.
const NOW = 1_800_000_000_000;
const RUNTIME = 'g1300-abcdef12';

function timer(overrides = {}) {
  return {
    schema: 1,
    game: 'hanjuku-speedrun',
    runtime_id: RUNTIME,
    generation: 1300,
    attempt_id: 'a0007-g1300-abcdef12',
    attempt_no: 7,
    status: 'running',
    started_at_ms: NOW - 60_000,
    ended_at_ms: null,
    start_source: 'name_screen',
    name_seen_at_ms: NOW - 60_000,
    current_index: 2,
    splits: [
      {index: 0, end_index: 0, segment_ms: 10_000, cumulative_ms: 10_000,
       verdict: 'gold', delta_ms: -1022, source: 'chapter_end_save', merged: false},
      {index: 1, end_index: 1, segment_ms: 20_000, cumulative_ms: 30_000,
       verdict: 'blue', delta_ms: 15_210, source: 'chapter_end_save', merged: false},
    ],
    comparison: {
      pb_total_ms: 120_000,
      pb_segments_ms: [10_000, 20_000, 30_000, 10_000, 10_000, 10_000,
        10_000, 10_000, 10_000, 10_000, 10_000, 10_000],
      pb_cumulative_ms: [10_000, 30_000, 60_000, 70_000, 80_000, 90_000,
        100_000, 110_000, 120_000, 130_000, 140_000, 150_000],
      gold_segments_ms: [10_000, 20_000, null, null, null, null,
        null, null, null, null, null, null],
      sum_of_best_ms: null,
    },
    heartbeat_at_ms: NOW - 1000,
    pb_eligible: true,
    bot_version: 'b1',
    chart_edition: 'speedrun',
    ...overrides,
  };
}

function gameSwitch(overrides = {}) {
  return {
    schema_version: 1,
    revision: 4,
    phase: 'ready',
    active: {
      game: 'hanjuku-speedrun',
      adapter: 'retroarch',
      runtime_id: RUNTIME,
      generation: 1300,
    },
    ...overrides,
  };
}

function withFiles(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'docich-speedrun-timer-'));
  const timerFile = path.join(dir, 'timer.json');
  const gameSwitchFile = path.join(dir, 'game_switch.json');
  const write = (file, value) => {
    fs.writeFileSync(file, typeof value === 'string' ? value : JSON.stringify(value));
  };
  try {
    return run({dir, timerFile, gameSwitchFile, write});
  } finally {
    fs.rmSync(dir, {recursive: true, force: true});
  }
}

function stateOf(timerFile, gameSwitchFile, now = NOW) {
  return speedrunTimerState(timerFile, gameSwitchFile, now);
}

const PUBLIC_KEYS = [
  'attempt_no', 'status', 'started_at_ms', 'ended_at_ms', 'current_index',
  'splits', 'comparison', 'heartbeat_at_ms', 'pb_eligible', 'total_verdict',
  'total_ms', 'stale', 'nowMs',
];

test('default timer path follows the docich root and TTL/stale constants are exported', () => {
  assert.match(DEFAULT_SPEEDRUN_TIMER_FILE,
    /\/run-soren-live\/speedrun\/hanjuku-speedrun\/timer\.json$/);
  assert.equal(SPEEDRUN_TIMER_STALE_MS, 30_000);
  assert.equal(SPEEDRUN_TIMER_TTL_MS, 10 * 60_000);
  assert.equal(SPEEDRUN_TIMER_MAX_SPLITS, 12);
});

test('missing files, another game, and an inactive switch return null', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    assert.equal(stateOf(timerFile, gameSwitchFile), null);
    write(gameSwitchFile, gameSwitch());
    assert.equal(stateOf(timerFile, gameSwitchFile), null);
    write(timerFile, timer());
    assert.equal(stateOf(timerFile, gameSwitchFile)?.status, 'running');
    write(gameSwitchFile, gameSwitch({active: {game: 'hanjuku-hero', runtime_id: RUNTIME}}));
    assert.equal(stateOf(timerFile, gameSwitchFile), null);
    write(gameSwitchFile, gameSwitch({active: null}));
    assert.equal(stateOf(timerFile, gameSwitchFile), null);
  });
});

test('corrupt JSON, a non-object, wrong schema/game/status, and bad indexes are dropped', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    write(gameSwitchFile, gameSwitch());
    for (const bad of ['{broken', '[]', 'null', JSON.stringify(timer({schema: 2})),
      JSON.stringify(timer({game: 'hanjuku-hero'})), JSON.stringify(timer({status: 'paused'})),
      JSON.stringify(timer({current_index: -1})), JSON.stringify(timer({current_index: 13})),
      JSON.stringify(timer({current_index: 12})), JSON.stringify(timer({current_index: '2'})),
      JSON.stringify(timer({started_at_ms: -1})),
      JSON.stringify(timer({started_at_ms: Number.MAX_SAFE_INTEGER + 1}))]) {
      write(timerFile, bad);
      assert.equal(stateOf(timerFile, gameSwitchFile), null, bad.slice(0, 40));
    }
  });
});

test('the timer runtime must match the active runtime', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    write(timerFile, timer());
    write(gameSwitchFile, gameSwitch());
    assert.equal(stateOf(timerFile, gameSwitchFile)?.status, 'running');
    write(gameSwitchFile, gameSwitch({active: {game: 'hanjuku-speedrun', runtime_id: 'g1301-00000000'}}));
    assert.equal(stateOf(timerFile, gameSwitchFile), null);
    // No usable runtime in the switch file: nothing can match, so nothing shows.
    write(gameSwitchFile, {schema_version: 1, phase: 'ready', active: {game: 'hanjuku-speedrun'}});
    assert.equal(stateOf(timerFile, gameSwitchFile), null);
    // A legacy plain-string active game has no runtime id either.
    write(gameSwitchFile, {schema_version: 1, phase: 'ready', active: 'hanjuku-speedrun'});
    assert.equal(stateOf(timerFile, gameSwitchFile), null);
    write(gameSwitchFile, {schema_version: 1, phase: 'ready', active: {game: 'hanjuku-speedrun',
      runtime_id: 7}});
    assert.equal(stateOf(timerFile, gameSwitchFile), null);
    write(gameSwitchFile, gameSwitch());
    write(timerFile, timer({runtime_id: 'g1301-00000000'}));
    assert.equal(stateOf(timerFile, gameSwitchFile), null);
  });
});

test('pending, running, finished, and dnf keep their documented values', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    write(gameSwitchFile, gameSwitch());
    write(timerFile, timer({
      status: 'pending', attempt_no: null, started_at_ms: null, current_index: 0, splits: [],
    }));
    const pending = stateOf(timerFile, gameSwitchFile);
    assert.equal(pending.status, 'pending');
    assert.equal(pending.started_at_ms, null);
    assert.equal(pending.ended_at_ms, null);
    assert.deepEqual(pending.splits, []);
    assert.equal(pending.stale, false);

    write(timerFile, timer());
    const running = stateOf(timerFile, gameSwitchFile);
    assert.equal(running.status, 'running');
    assert.equal(running.attempt_no, 7);
    assert.equal(running.current_index, 2);
    assert.equal(running.stale, false);

    const endedAt = NOW - 1000;
    write(timerFile, timer({
      status: 'finished', ended_at_ms: endedAt, current_index: 12, pb_eligible: true,
      total_verdict: 'gold', pb_updated: true,
    }));
    const finished = stateOf(timerFile, gameSwitchFile, endedAt + 5000);
    assert.equal(finished.status, 'finished');
    assert.equal(finished.ended_at_ms, endedAt);
    assert.equal(finished.current_index, 12);

    write(timerFile, timer({
      status: 'dnf', ended_at_ms: endedAt, current_index: 3, pb_eligible: false,
      dnf_reason: 'stalled',
    }));
    assert.equal(stateOf(timerFile, gameSwitchFile, endedAt + 5000)?.status, 'dnf');
  });
});

test('a running timer is stale after 30s without a heartbeat; terminal timers are not', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    write(gameSwitchFile, gameSwitch());
    write(timerFile, timer({heartbeat_at_ms: NOW - SPEEDRUN_TIMER_STALE_MS}));
    assert.equal(stateOf(timerFile, gameSwitchFile, NOW).stale, false);
    assert.equal(stateOf(timerFile, gameSwitchFile, NOW + 1).stale, true);
    write(timerFile, timer({heartbeat_at_ms: null}));
    assert.equal(stateOf(timerFile, gameSwitchFile, NOW).stale, true);
    write(timerFile, timer({status: 'finished', ended_at_ms: NOW - 1000, current_index: 12}));
    assert.equal(stateOf(timerFile, gameSwitchFile, NOW).stale, false);
  });
});

test('finished and dnf timers disappear once their TTL has passed', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    write(gameSwitchFile, gameSwitch());
    const endedAt = NOW - 1000;
    for (const status of ['finished', 'dnf']) {
      write(timerFile, timer({status, ended_at_ms: endedAt, current_index: status === 'finished' ? 12 : 4}));
      assert.equal(stateOf(timerFile, gameSwitchFile, endedAt + SPEEDRUN_TIMER_TTL_MS)?.status, status);
      assert.equal(stateOf(timerFile, gameSwitchFile, endedAt + SPEEDRUN_TIMER_TTL_MS + 1), null);
    }
    // A terminal timer without ended_at_ms is incomplete, not a live card.
    write(timerFile, timer({status: 'finished', ended_at_ms: null, current_index: 12}));
    assert.equal(stateOf(timerFile, gameSwitchFile, NOW), null);
  });
});

test('total_verdict only passes the finish() allow-list and total_ms is a time', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    write(gameSwitchFile, gameSwitch());
    const terminal = {status: 'finished', ended_at_ms: NOW - 1000, current_index: 12};
    write(timerFile, timer({...terminal, total_verdict: 'gold', total_ms: 120_000}));
    let value = stateOf(timerFile, gameSwitchFile, NOW);
    assert.equal(value.total_verdict, 'gold');
    assert.equal(value.total_ms, 120_000);
    const cases = [
      ['blue', 120_000, null, 120_000],
      ['white', -1, 'white', null],
      ['', 1.5, null, null],
      [null, Number.MAX_SAFE_INTEGER + 1, null, null],
      ['red', '120000', 'red', null],
    ];
    for (const [verdict, total, expectedVerdict, expectedTotal] of cases) {
      write(timerFile, timer({...terminal, total_verdict: verdict, total_ms: total}));
      value = stateOf(timerFile, gameSwitchFile, NOW);
      assert.equal(value.total_verdict, expectedVerdict);
      assert.equal(value.total_ms, expectedTotal);
    }
    // A running timer normally has neither field yet.
    write(timerFile, timer());
    value = stateOf(timerFile, gameSwitchFile, NOW);
    assert.equal(value.total_verdict, null);
    assert.equal(value.total_ms, null);
  });
});


test('splits are bounded to 12 and every numeric field is validated', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    write(gameSwitchFile, gameSwitch());
    const splits = [
      {index: 3, end_index: 2, segment_ms: 1, cumulative_ms: 1, verdict: 'gold'},
      {index: 4, end_index: 4, segment_ms: -1, cumulative_ms: 1, verdict: 'gold'},
      {index: 5, end_index: 5, segment_ms: 1, cumulative_ms: Number.MAX_SAFE_INTEGER + 1, verdict: 'gold'},
      {index: '7', end_index: 7, segment_ms: 1, cumulative_ms: 1, verdict: 'gold'},
      null,
    ];
    for (let index = 0; index < 13; index += 1) {
      splits.push({index, end_index: index, segment_ms: 1000 + index, cumulative_ms: 1000 * (index + 1),
        verdict: ['gold', 'blue', 'red', 'white'][index % 4], delta_ms: index === 0 ? -500 : index});
    }
    write(timerFile, timer({splits, current_index: 11}));
    const value = stateOf(timerFile, gameSwitchFile);
    assert.equal(value.splits.length, 12);
    assert.deepEqual(value.splits.map((split) => split.index), Array.from({length: 12}, (_, index) => index));
    for (const split of value.splits) {
      assert.ok(Number.isSafeInteger(split.segment_ms) && split.segment_ms >= 0);
      assert.ok(Number.isSafeInteger(split.cumulative_ms) && split.cumulative_ms >= 0);
      assert.ok(['gold', 'blue', 'red', 'white'].includes(split.verdict));
      assert.ok(split.delta_ms === null || Number.isSafeInteger(split.delta_ms));
    }
    assert.equal(value.splits[0].delta_ms, -500);

    write(timerFile, timer({
      splits: [{index: 0, end_index: 0, segment_ms: 1, cumulative_ms: 1,
        verdict: 'purple', delta_ms: 1.5}],
      current_index: 1,
    }));
    const sanitized = stateOf(timerFile, gameSwitchFile);
    assert.equal(sanitized.splits[0].verdict, 'white');
    assert.equal(sanitized.splits[0].delta_ms, null);
  });
});

test('comparison is sanitized and no private rta field leaks into the view', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    write(gameSwitchFile, gameSwitch());
    write(timerFile, timer({
      comparison: {
        pb_total_ms: -1,
        pb_segments_ms: [1000, null, '2000', Number.MAX_SAFE_INTEGER + 1],
        pb_cumulative_ms: 'broken',
        gold_segments_ms: [-5, 1000],
        sum_of_best_ms: 1.5,
      },
    }));
    const value = stateOf(timerFile, gameSwitchFile);
    assert.deepEqual(Object.keys(value).sort(), [...PUBLIC_KEYS].sort());
    assert.equal(value.comparison.pb_total_ms, null);
    assert.deepEqual(value.comparison.pb_segments_ms, [1000, null, null, null]);
    assert.deepEqual(value.comparison.pb_cumulative_ms, []);
    assert.deepEqual(value.comparison.gold_segments_ms, [null, 1000]);
    assert.equal(value.comparison.sum_of_best_ms, null);
    for (const privateKey of ['runtime_id', 'attempt_id', 'generation', 'bot_version',
      'chart_edition', 'start_source', 'name_seen_at_ms', 'dnf_reason',
      'pb_updated', 'merged']) {
      assert.equal(Object.hasOwn(value, privateKey), false, privateKey);
    }
  });
});

test('buildDirectBroadcastOverlayState adds the timer without changing existing keys', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    write(gameSwitchFile, gameSwitch());
    write(timerFile, timer());
    const built = buildDirectBroadcastOverlayState({
      sources: {speedrunTimerFile: timerFile, gameSwitchFile},
    }, NOW);
    assert.equal(built.version, 1);
    assert.ok(built.feeds);
    assert.ok(built.notifications);
    assert.equal(built.speedrunTimer.status, 'running');
    assert.deepEqual(Object.keys(built.speedrunTimer).sort(), [...PUBLIC_KEYS].sort());
  });
});

test('config sources and the documented environment variables override the paths', () => {
  withFiles(({timerFile, gameSwitchFile, write}) => {
    write(gameSwitchFile, gameSwitch());
    write(timerFile, timer());
    const previousTimer = process.env.DOCICH_SPEEDRUN_TIMER_FILE;
    const previousSwitch = process.env.DOCICH_GAME_SWITCH_FILE;
    try {
      process.env.DOCICH_SPEEDRUN_TIMER_FILE = timerFile;
      process.env.DOCICH_GAME_SWITCH_FILE = gameSwitchFile;
      const fromEnv = buildDirectBroadcastOverlayState({sources: {}}, NOW);
      assert.equal(fromEnv.speedrunTimer.status, 'running');
      const fromSources = buildDirectBroadcastOverlayState({
        sources: {speedrunTimerFile: timerFile, gameSwitchFile},
      }, NOW);
      assert.equal(fromSources.speedrunTimer.status, 'running');
    } finally {
      if (previousTimer === undefined) delete process.env.DOCICH_SPEEDRUN_TIMER_FILE;
      else process.env.DOCICH_SPEEDRUN_TIMER_FILE = previousTimer;
      if (previousSwitch === undefined) delete process.env.DOCICH_GAME_SWITCH_FILE;
      else process.env.DOCICH_GAME_SWITCH_FILE = previousSwitch;
    }
  });
});
