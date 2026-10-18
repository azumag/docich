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
