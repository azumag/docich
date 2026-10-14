# 現在から遡る世界史コーナー — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1719 (新コーナー: 現在から遡る世界史)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract.

## Goal

Define the planning and input contract for a stream corner that answers why the present world is shaped as it is. Instead of memorizing dates, it starts from current institutions, borders, industries, and conflicts, traces backward, and explains previous history, turning points, multiple regional viewpoints, and impacts still visible today.

## Non-goals (this slice)

- No implementation of theme candidate management, retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real historical person, event, border, institution, statistic, or map is verified or measured by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.
- No automatic progression, overlay display, or public posting.

## Theme candidate policy

Themes are collected first and filtered by whether present-day questions can be explained through backward tracing. Prefer topics where the current form of an institution, border, industry, or conflict becomes visible when tracing history:

- why a present industrial core or supply chain formed where it did;
- why a current national or maritime border has its present shape;
- why a currency, standard, or institution became dominant;
- why a present conflict or alliance structure exists;
- colonial rule, empire, and its continuing institutional effects;
- cold-war, imperial, or trade structures that survive today;
- institutions whose origin is obscured by later narrative.

A valid theme must support at least a verifiable present-day condition, a primary or public historical source, multiple regional or force viewpoints, and a causal chain back to a turning point. Trivia that ends at "this happened on this date" is excluded. Uncertain, preprint, or incomplete status must be stated explicitly.

## Information-source priority

Primary and public historical sources are required; an orientation article alone is not sufficient. Priority order:

1. primary statements, treaties, statutes, court records, archives, official documents;
2. public institutions and official statistics;
3. academic, archival, or research materials with documented method and provenance;
4. established media outlets used for orientation or corroboration;
5. fact-check or independent verification sources.

For medicine-adjacent, health, social, or safety claims, research design, sample size, correlation/causation boundaries, and review status must be retained as caveats.

## Input contract: one structured world-history theme

Each selected theme becomes one structured record containing:

- title and short theme identifier;
- current question;
- time scope or period under examination;
- region or scope;
- map focus and why the map matters;
- 30-second summary and one-line summary;
- direct prehistory;
- deeper causes;
- turning points;
- multiple regional or force perspectives;
- lasting impact;
- explicit timeline entries;
- map handling metadata (scope, scale, projection, vintage, license, usage conditions);
- an explicit fact / analysis / forecast label for each generated segment;
- limitations and uncertainty;
- a figure or overlay plan;
- citation-kept source URLs;
- an independently usable YouTube-shaped script outline.

The output display also retains the question, prehistory, causes, turning points, perspectives, impact, timeline, labels, and source URLs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from primary documents, archives, public institutions, publishers, or DOI/arXiv pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. JEV code investigation should be used only when code study is genuinely required. Search failure must not implicitly fall back to OpenCode.

## Required structured section order

For each introduced theme:

1. current question;
2. direct prehistory;
3. deeper causes;
4. turning points;
5. multiple region or force perspectives;
6. lasting impact.

Specialized historical, legal, geographic, or institutional terms must not be read verbatim; diagrams, maps, or plain explanations should carry them when needed.

## Map and timeline handling contract

Maps and timelines are explanatory objects, not decoration. A map must carry, or explicitly state the absence of:

- geographic scope and center;
- scale and resolution;
- projection or coordinate reference;
- vintage or measurement date;
- layers and their source;
- license, attribution, and usage conditions;
- uncertainty, simplification, or editorial caveat.

Timelines must carry, or explicitly state the absence of:

- event date or period;
- event;
- involved regions or forces;
- consequence or remaining impact;
- uncertainty when chronology is disputed.

Overlay layers should be separated from the base map: borders, industrial cores, trade routes, and conflict zones are additive overlays. Overlay display must not overwrite cited URLs, labels, or limitations.

## Fact / analysis / forecast labels

Generated segments must carry one of:

- `fact`: a primary or multiply corroborated historical statement;
- `analysis`: an interpretive assessment derived from evidence;
- `forecast`: a conditional prediction with stated assumptions.

The three labels must not be mixed within one segment; uncertainty must accompany any forecast.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed world-history fixture parses as the contract above;
   - primary source URLs and the theme URL are present and HTTPS;
   - source priority favors primary and public historical sources;
   - current question, direct prehistory, deeper causes, turning points, multiple regional perspectives, lasting impact, and timeline are explicit;
   - map handling metadata (scope, scale, projection, vintage, license) is explicit;
   - the synthetic fixture is not presented as a real event or verified history;
   - verified material hashes and provenance follow the adapter contract;
   - failure, uncertainty, and caveat handling are explicit.

2. overlay and progression acceptance (later):
   - overlay displays question, map, timeline, perspective, impact, labels, and citation URLs;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/world_history_corner/fixed_world_history_example.json`. Its contract is exercised by `tests/test_world_history_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial theme selection, implementation, model billing, deployment, public posting, and overlay/progression are also out of scope for this slice. Reviewer decides whether to proceed to overlay/progression implementation.
