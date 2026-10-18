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

## Engine integration from PR #2491 (owner-approved merge, 2026-10-18)

The owner directed merging the closed PR #2491 engine branch
(`feat/698-puyo-puyo-clone-core`) into this PR so both implementations become
one module. Integrated features, all still offline:

* **Tsu-faithful scoring tables** — `_chain_score` is now
  `(chain power + color bonus + connected-group bonus) x perPuyo x blocks`
  with the published classic ("通") values: chain power 1連鎖=0, 2=8, 3=16,
  4=32, 5=64, 6=96 ... 19=512 (clamped); color bonus 1色=0, 2色=3, 3色=6,
  4色=12; connected-group bonus 4個=0, 5個=2, 6個=3 ... 11個以上=10 (the
  ruleset's `simultaneousBonus` descriptor). A lone 1-chain scores 0 points
  and sends no ojama, like the original. `perPuyo` stays ruleset-driven.
* **Ojama (garbage) economy** — attack = `(carry + lock score) // 70` with
  the remainder carried over; a 30-ojama reserve cap (overflow discarded);
  at most 30 dropped per lock, filled flat bottom row first, left to right;
  received ojama falls only after the lock's own chains resolve (it cannot
  be popped by that lock); ojama pop when directly adjacent to a cleared
  color cell (never ojama→ojama chains) and are never scored. Attacks move
  to the opponent's reserve once per tick (`_exchange_ojama`).
* **`greedy_policy(state, side)`** — the PR #2491 single-step control
  contract: the first action of `plan_actions`' best script. The bounded
  `plan_actions` brain stays the primary; both share one evaluation path.
* **Bounded match time limit** — `MATCH_MAX_TICKS = 15000`. `play_match`
  ends a match that reaches the tick limit (or the driver's step cap) by
  score comparison (higher total wins, tie -> draw) via `ROUND_OVER`, so an
  unattended match always finishes (試合完走). Budget exhaustion keeps the
  existing bounded TIMEOUT path.

All 48 offline contract tests (this PR's suite + the ported PR #2491 cases,
with the two superseded score assertions updated to the Tsu tables) pass
locally; adapter / catalog registration (既定 disabled / manual_only) is the
next slice and stays owner-gated.
