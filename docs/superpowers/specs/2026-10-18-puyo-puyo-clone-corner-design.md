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
