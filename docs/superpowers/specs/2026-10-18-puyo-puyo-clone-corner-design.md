# ぷよぷよクローン — 設計 (企画)

**Date:** 2026-10-18
**Status:** Planning / non-production.
**Source issue:** azumag/docich#698 (ぷよぷよクローン)

## Goal

Define the planning and input contract for a stream corner that presents an
original falling-block "connect 4+" puzzle clone in the style of Puyo Puyo
(ぷよぷよクローン): rules, piece pairs, controls, chain mechanics, scoring,
win/loss, the AI opponent, the spectator view, and viewer (同志) participation.

This corner is the single-game sibling of the participatory multi-game corner
(azumag/docich#699). It fixes only the input contract so overlay, progression,
match runtime, and production startup can be designed separately.

## Non-goals

- No game runtime, board engine, rendering, input adapter, or AI brain.
- No matchmaking, networking, viewer chat integration, or Twitch/VM I/O.
- No overlay wiring, progression frames, external model billing, deployment,
  or production startup.
- The fixed fixtures are synthetic and are used only for contract acceptance.
- No claim that this corner is the original Puyo Puyo, nor a derivative that
  reproduces Sega's original assets. 「ぷよぷよ」 is a trademark of its owner;
  this clone is an independent original implementation of the same genre.

## Input contract

Each corner run becomes one structured record (`game` container) containing:

`title`, `gameId`, `genre`, `referenceWork`, `originalIpNote`, `boardSize`,
`colors`, `piecePair`, `rotationModes`, `controls`, `gravityMode`,
`connectionRule`, `chainMechanics`, `scoring`, `winCondition`, `lossCondition`,
`matchType`, `matchFormat`, `players`, `playerCount`, `realTime`, `turnBased`,
`multiplayer`, `aiOpponent`, `difficulty`, `comradeMatch`, `spectatorView`,
`chatCommands`, `roundStructure`, `thirtySecondSummary`, `oneLineSummary`,
`labelsPolicy`, `segmentLabels`, `limitations`, `uncertainty`,
`figureOrDiagram`, `youtubeIndependentDraft`, `primarySources`,
`sourcePriority`, `citationKeepUrl`, `url`.

The fixture also carries `verifiedWebMaterial` (citation provenance) and a
`nonProductionAcceptance` block.

## Slug variants

The operator may name this corner with any of the following slugs. Every
variant ships an equivalent fixture and contract test so the corner is
recognizable regardless of the name used:

- `puyo-puyo-clone` (canonical, from the issue title)
- `puyo-puyo`
- `puyo-clone`

## Non-production acceptance conditions

- fixed fixture parses as the contract above;
- `isSyntheticFixture` is `true` and the fixture is never presented as real;
- primary source URLs and the corner URL are present and HTTPS;
- source priority favors primary/original references and institutions;
- the fixture reports an originalIP note and never claims the original work;
- fact/analysis/forecast labels and multiple perspectives are explicit;
- honest-material hashes follow the adapter contract;
- overlay and progression are split out after the input contract;
- partial completion is allowed and reviewer approval is required.

## Offline core (PR-2)

`src/docich/puyo_corner.py` implements the offline mechanics behind this
contract, following the precedent set by the TRPG corner offline core
(Issue #684): pure functions, no live I/O.

`load_state`/`new_state` fall back to a built-in DEFAULT_RULESET when a saved
state has no ruleset, so legacy or empty states keep working. `record_match`
only records terminal states (END/FAILED/TIMEOUT).

Ruleset validation is fail-closed:

* supported board only (6x12 + 1 hidden spawn row as declared above);
* 2-4 distinct colors declared in the ruleset, never widened;
* gravitySettle / chainReevaluation / simultaneousClear must be true;
* scoring descriptors must not be None (exact tables stay in the module);
* saved states carry a ruleset digest that must match the live module.

The falling pair is axis + satellite, spawn on the first visible row.

## Board mechanics

Every chain pass settles the board per column, clears every group of 4+
orthogonally connected same-color puyo at once (simultaneousClear), and the
settle→clear loop re-evaluates until no clearable group remains; chains
1, 2, 3, ... each add the bounded chain bonus, and an emptied board adds the
all-clear bonus. MAX_ACTIONS, MAX_PAIRS_PER_ROUND and MAX_CHAIN_STEPS bound
any tick, so runaway loops fail closed with an explicit budget code. A locked
pair with no room for its refill ends the round: the surviving side wins and
both sides topping out is a draw.

## Match runtime + AI brain + overlay draft (follow-up slice 1, PR-3)

Agreed in the owner-approved thread (2026-10-18): PR #2485 stays the input
contract; follow-ups implement overlay / progression / match runtime / AI
brain / ネット対戦 / 本番起動. This slice adds, still fully offline:

* `plan_actions(state, side)` — bounded offline brain. Enumerates at most
  ``MAX_PLANS_PER_DECISION`` scripts (<= 3 rotations x 6 target columns x
  hard drop), plays every candidate through the real engine on a copy, and
  picks the highest evaluation (engine chain/all-clear score minus bounded
  height / hole / bumpiness penalties). Deterministic; never mutates state.
* `play_match(state)` — offline self-play driver. Applies the brain's script
  for each side, then ticks. The engine budgets remain authoritative: the
  action/pair budgets end a runaway round as a bounded TIMEOUT (the same way
  the live corner's fixed display window would end it), never an infinite
  loop. A top-out ends the round with a winner; reset clears the board.
* `overlay_draft(state)` — bounded pure draft of the overlay sections
  (phase, both boards, scores, chains, winner). The 960x540 synthetic view /
  TTS / GameSwitch wiring consumes this shape in a later, explicitly enabled
  step.

No chat input, no adapter, no catalog change and no VM touched in this slice.

## Adapter + catalog 登記 (owner-approved PR-3, 既定 disabled / manual_only)

Owner approval (2026-10-18 thread): PR-3 = `puyo` adapter + catalog row,
default disabled. Merging PR #2485 happens after PR-3 is done. The slice
follows the read-only synthetic-view precedent (`doushi_corner.py` /
`doushi_view.py` / `adapters/doushi_program.py`) minus audio and narration:

* `src/docich/puyo_view.py` — loopback-only HTTP view (port 8807,
  ``VIEW_NAME = "puyo-view"``) showing one already-played offline match
  summary at the fixed 960x540 frame. It never fetches and never starts a
  match itself: the program adapter plays one bounded self-play through
  ``puyo_corner`` before the switch commits and hands over a validated
  transient payload file; a missing or invalid payload keeps the process
  from serving at all (fail-closed, no stale board).
* `src/docich/adapters/puyo_program.py` — GameSwitch program-view adapter
  (`make_puyo_view_adapter`, registered for `game == "puyo-view"`). The
  preflight plays exactly one bounded offline match (fresh 64-bit seed,
  engine budget `MAX_STEPS` bound) and writes `puyo_match.json` into the
  transient runtime directory only.
* `src/docich/puyo_program.py` — `PuyoProgramManager` bounds the slot: fixed
  duration (default 2, max 14 minutes), full runtime identity persistence,
  stop/interrupt/restore receipts identical to the doushi lifecycle. No
  audio, no narration, no fetch during display.
* `corner_adapters.PuyoCornerAdapter` + `ADAPTERS["puyo"]` — identity
  contract: `game` must be `puyo-view` and `live_eligible` false, otherwise
  `CornerExecutionError`.
* `corner_catalog` validation for `adapter == "puyo"`: id must be
  `puyo-corner`, game `puyo-view`, `manual_only` must stay true,
  `audio_enabled`/`fetch_on_start` rejected, `live_eligible` false,
  duration 1–14 when present. Nothing is widened or defaulted on.
* Production catalog row (config/docich.soren-live.toml):
  `{ id = "puyo-corner", adapter = "puyo", game = "puyo-view",
  enabled = false, manual_only = true }` — inert until the owner flips
  `enabled = true`, and even then manual-queue-only until a later approved
  step drops `manual_only`.
* WebUI: the row is visible in manual diagnostics and can be queued/stopped
  (`("puyo", "ぷよぷよクローン")` label) once enabled.

While `enabled = false` the corner can never be selected, not even manually
(`disabled-or-paused`); enabling is an explicit owner action on the VM.

## 明示的に後回し (not in PR-3)

チャット参加 (`!puyo join`), progression/成績記録, ネット対戦, TTS,
自動ローテーション組入 (`enabled = true` / `manual_only = false`),
本番起動 — each stays a separate explicitly approved slice.
