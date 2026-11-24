# 参加型 同志と対戦できるぷよぷよ、テトリス、オセロ、衝立将棋、FPS — 設計 (企画)

**Date:** 2026-10-18
**Status:** Planning / non-production.
**Source issue:** azumag/docich#699

## Goal

Define the planning and input contract for a stream corner where viewers (同志) play against each other in puyo puyo, tetris, othello, tsuitate shogi, and FPS.

## Non-goals

- No implementation of match runtime, networking, overlay, or production startup.
- Fixed fixtures are synthetic and used only for contract acceptance.

## Input contract

Each selected topic becomes one structured record (see tests/fixtures/*_corner).

## Non-production acceptance conditions

- fixed fixture parses as the contract above;
- primary source URLs and the event URL are present and HTTPS;
- synthetic fixture is not presented as a real event.
