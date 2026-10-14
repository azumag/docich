# メリケンの経済学講座 — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1720 (新コーナー: メリケンの経済学講座)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract.

## Goal

Define the planning and input contract for a stream corner that explains economic news and familiar questions through microeconomics, macroeconomics, international economics, labor, industrial organization, and public economics.

Meriken serves as the character who foregrounds a market, profit, competition, and growth perspective. The program must still present opposing arguments, market failure, externalities, inequality, and institutional constraints alongside it, and must not treat any single viewpoint as fact.

## Non-goals (this slice)

- No implementation of theme candidate management, retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real statistic, statement, policy, market price, or event is verified or measured by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.
- No automatic progression, overlay display, or public posting.

## Theme candidate policy

Themes are collected first and filtered by whether a real-world economic question can be explained with structure and evidence. Prefer topics where market mechanisms, incentives, prices, and institutions are visible:

- why inflation occurs;
- why raising interest rates cools the economy;
- who bears the burden of tariffs;
- minimum wage;
- monopoly and competition;
- exchange rates;
- fiscal policy;
- trade;
- labor markets;
- public goods and taxation.

A valid theme must support at least a verifiable question, a primary or public statistical source, a model, and multiple perspectives including counterarguments. Topics that assert one ideology as fact are excluded. Uncertain, unreviewed, or provisional status must be stated explicitly.

## Information-source priority

Primary and public economic sources are required; an orientation article alone is not sufficient. Priority order:

1. primary statements, official documents, statutes, treaties, official statistics;
2. public institutions and official statistics;
3. established wire or media outlets (multiple, cited);
4. academic, research, or policy materials with documented method and provenance;
5. fact-check or independent verification sources.

For claims touching health, social policy, safety, labor, or distribution, research design, sample size, correlation/causation boundaries, and uncertainty must be retained as caveats.

## Input contract: one structured Meriken economics theme

Each selected theme becomes one structured record containing:

- title and short topic identifier;
- today's question;
- Meriken's view;
- economic model;
- data and empirics;
- counterarguments and conditions;
- distributional impact (who gains and who bears costs);
- application to current news or familiar questions;
- 30-second summary and one-line summary;
- an explicit fact / analysis / forecast label for each generated segment;
- limitations and uncertainty;
- a figure or overlay plan;
- citation-kept source URLs;
- an independently usable YouTube-shaped script outline;
- verified Web material references.

The output display also retains the question, Meriken's view, model, data, opposing views, distributional impact, news application, labels, and source URLs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from primary documents, public institutions, publishers, or DOI pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. JEV code investigation should be used only when code study is genuinely required. Search failure must not implicitly fall back to OpenCode.

## Required structured section order

For each introduced theme:

1. today's question;
2. Meriken's view;
3. economic model;
4. data and empirics;
5. counterarguments and conditions;
6. who gains and who bears costs;
7. application to current news.

Economic, statistical, legal, or institutional terms must not be read verbatim; diagrams, plain explanations, or overlays should carry them when needed.

## Fact / analysis / forecast labels

Generated segments must carry one of:

- `fact`: a primary or multiply corroborated statement;
- `analysis`: an interpretive assessment derived from evidence;
- `forecast`: a conditional prediction with stated assumptions.

The three labels must not be mixed within one segment; uncertainty must accompany any forecast.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed Meriken economics fixture parses as the contract above;
   - primary source URLs and the theme URL are present and HTTPS;
   - source priority favors primary documents, institutions, and public statistics;
   - today's question, Meriken's view, model, data, counterarguments, distributional impact, and news application are explicit;
   - the synthetic fixture is not presented as real economics data or verified analysis;
   - verified material hashes and provenance follow the adapter contract;
   - failure, uncertainty, and caveat handling are explicit.

2. overlay and progression acceptance (later):
   - overlay displays question, Meriken's view, model, data, opposing views, distribution, labels, and citation URLs;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/meriken_economics_corner/fixed_meriken_economics_example.json`. Its contract is exercised by `tests/test_meriken_economics_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial theme selection, implementation, model billing, deployment, public posting, and overlay/progression are also out of scope for this slice. Reviewer decides whether to proceed to overlay/progression implementation.