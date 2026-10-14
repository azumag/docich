# 国際情勢・政治ニュース深掘りコーナー — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1716 (新コーナー: 国際情勢・政治ニュースを背景から深掘りする)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract.

## Goal

Define the planning and input contract for a stream corner that deep-dives one political or international news event from its background: not only what happened today, but history, institutions, geography, economy, security, and international law, in order to answer why it is happening now.

The corner separates fact, analysis, and forecast, collates primary and multiple reliable sources, and treats opposing major viewpoints fairly. It prioritizes themes with political or institutional significance over sensational news consumption.

## Non-goals (this slice)

- No implementation of candidate retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real event, statement, or statistic is verified or measured by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.
- No automatic progression, overlay display, or public posting.

## Candidate selection policy

Candidates are collected first and filtered by political and institutional significance. Prefer topics that make a background cause visible:

- territorial or maritime disputes;
- elections and government formation;
- sanctions, trade restrictions, and supply-chain shifts;
- alliance, treaty, and security-framework changes;
- humanitarian and refugee crises and their institutional responses;
- energy, food, and critical-mineral security;
- international court, arbitration, and legal dispute;
- domestic constitutional or legal reform with international impact.

A valid candidate must support at least a verifiable fact, a primary source, and a real disagreement or uncertainty. Sensationalism or single-viewpoint framing is excluded. Preprint or unverified status must be stated explicitly.

## Information-source priority

Primary information is required; an orientation article alone is not sufficient. Priority order:

1. primary statements, official documents, treaties, statutes, court records;
2. public institutions and official statistics;
3. established wire or media outlets (multiple, cited);
4. academic, think-tank, or research materials;
5. fact-check or independent verification sources.

For medicine-adjacent, health, social, or safety claims, research design, sample size, correlation/causation boundaries, and review status must be retained as caveats.

## Input contract: one structured international-politics event

Each selected event becomes one structured record containing:

- title and short topic identifier;
- event date, geography, and actors;
- 30-second summary and one-line summary;
- what happened;
- what each party claims;
- institutional and international-law position;
- historical background;
- geographic, economic, and security conditions;
- key disputes and opposing viewpoints;
- impact on Japan;
- indicators and events to watch;
- an explicit fact / analysis / forecast label for each generated segment;
- limitations and uncertainty;
- an independently usable YouTube-shaped script outline;
- citation-kept source URLs;
- a figure or overlay plan.

The output display also retains the event summary, claims, institutional position, background, key disputes, impact, indicators, labels, and source URLs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from primary statements, official documents, public institutions, publishers, or DOI/arXiv pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. JEV code investigation should be used only when code study is genuinely required. Search failure must not implicitly fall back to OpenCode.

## Required structured section order

For each introduced event:

1. what happened;
2. what each party claims;
3. institutional and international-law position;
4. historical background;
5. geographic, economic, and military conditions;
6. key disputes;
7. impact on Japan;
8. indicators and events to watch.

Specialized legal or institutional terms must not be read verbatim; diagrams or plain explanations should carry them when needed.

## Fact / analysis / forecast labels

Generated segments must carry one of:

- `fact`: a primary or multiply corroborated event;
- `analysis`: an interpretive assessment derived from evidence;
- `forecast`: a conditional prediction with stated assumptions.

The three labels must not be mixed within one segment; uncertainty must accompany any forecast.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed international-politics fixture parses as the contract above;
   - primary source URLs and the event URL are present and HTTPS;
   - source priority favors primary documents and institutions;
   - synthetic fixture is not presented as a real event;
   - verified material hashes and provenance follow the adapter contract;
   - failure, uncertainty, and safety handling (for health/social/safety claims) are explicit.

2. overlay and progression acceptance (later):
   - overlay event summary, claims, position, disputes, and indicators display;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/international_politics_corner/fixed_international_politics_example.json`. Its contract is exercised by `tests/test_international_politics_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial event selection, implementation, model billing, deployment, public posting, and overlay/progression are also out of scope for this slice. Reviewer decides whether to proceed to overlay/progression implementation.