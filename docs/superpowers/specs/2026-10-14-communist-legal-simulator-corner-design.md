# 共産主義法律シミュレーター — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production.
**Source issue:** azumag/docich#697 (共産主義法律シミュレーター)

## Goal

Define the planning and input contract for a stream corner that simulates and deep-dives communist legal systems: statutes, institutions, disputes, and their historical background.

## Non-goals

- No implementation of candidate retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- The fixed fixture is synthetic and used only for contract acceptance.

## Input contract

Each selected topic becomes one structured record containing title, topicId, eventDate, geography, actors, summaries, whatHappened, partyClaims, institutionalLegalPosition, historicalBackground, geoEconomicSecurityConditions, keyDisputes, opposingViewpoints, japanImpact, indicatorsAndEvents, labelsPolicy, segmentLabels, limitations, uncertainty, figureOrDiagram, youtubeIndependentDraft, primarySources, sourcePriority, citationKeepUrl, url.

## Non-production acceptance conditions

- fixed fixture parses as the contract above;
- primary source URLs and the event URL are present and HTTPS;
- source priority favors primary documents and institutions;
- synthetic fixture is not presented as a real event;
- verified material hashes and provenance follow the adapter contract.
