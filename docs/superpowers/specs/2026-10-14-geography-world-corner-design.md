# 地図から見る地理・世界コーナー — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1718 (新コーナー: 地図から見る地理・世界)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract.

## Goal

Define the planning and input contract for a stream corner that explains the characteristics of cities, nations, industries, and politics from maps, terrain, climate, population, resources, and transport routes. The map is the center of the explanation; geography becomes a lens that connects terrain and climate to economic activity, political boundaries, strategic value, and the present meaning of a place.

Rather than treating geography as static information, the corner connects past and present: why a city grew where it did, why a strait or canal matters, why mountain ranges change climate, why an ice-free port matters, why Taiwan, Singapore, the American Midwest, and other places have distinctive strategic or economic importance.

## Non-goals (this slice)

- No implementation of theme candidate management, retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real geography, demographic statistic, resource figure, or historical claim is verified or measured by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.
- No automatic progression, overlay display, public posting, or production startup.

## Theme candidate policy

Themes are collected first and filtered for explanatory power and map-centered clarity. Prefer topics where the map makes a cause visible:

- why an important city or port developed where it did;
- straits, canals, and choke points (Suez, Panama, and similar);
- ice-free ports and seasonal constraints;
- islands and maritime strategic position;
- mountain ranges and their climate effects;
- agricultural cores and food production regions;
- resource distribution and industrial concentration;
- transport corridors, rail, river, and ocean routes;
- population density, migration, and urbanization;
- borders, buffer zones, and geopolitical frontiers;
- current meaning of a geographic fact for economy, politics, and daily life.

A valid theme must support at least a verifiable geographic fact, a primary or public source, and a map-centered explanation. Trivia that ends at "this place exists" is excluded. Preprint, unofficial, or uncertain status must be stated explicitly.

## Information-source priority

Primary geographic and public sources are required; an orientation article alone is not sufficient. Priority order:

1. official geographic surveys, topographic and bathymetric maps, national institutes;
2. public institutions and official statistics (population, trade, resources, transport);
3. remote-sensing, satellite, climate, and environmental datasets with documented provenance;
4. academic, institutional, or expert geographic analysis;
5. reputable explanatory articles used for orientation only.

Web-only summaries may be used for orientation, but they cannot carry a claim that requires a primary source.

## Input contract: one structured geography theme

Each selected theme becomes one structured record containing:

- title and short theme identifier;
- place, region, or geographic scope;
- map focus (center, scale, projection, viewpoint, layers, and why this view matters);
- a 30-second summary and a one-line summary;
- terrain and climate;
- population;
- resources;
- transport routes;
- industry;
- history and politics impact;
- current meaning;
- an explicit map-handling note (coordinate reference, projection, scale, date, license/usage conditions);
- an explicit fact / analysis / forecast label for each generated segment;
- limitations and uncertainty;
- a figure or overlay plan;
- citation-kept source URLs;
- an independently usable YouTube-shaped script outline.

The output display also retains the map, theme, terrain/climate, population, resources, transport, current meaning, labels, and source URLs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from official geographic surveys, public institutions, publishers, or DOI/arXiv pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. JEV code investigation should be used only when code study is genuinely required. Search failure must not implicitly fall back to OpenCode.

## Required structured section order

For each introduced theme:

1. map;
2. terrain and climate;
3. population;
4. resources;
5. transport;
6. industry;
7. history and politics impact;
8. current meaning.

Specialized geographic, legal, or strategic terms must not be read verbatim; diagrams or plain explanations should carry them when needed.

## Map handling contract

Maps are explanatory objects, not decoration. A map must carry, or explicitly state the absence of:

- geographic scope and center (latitude/longitude or named center);
- scale and resolution;
- projection or coordinate reference;
- vintage or measurement date;
- data layers and their source;
- license, attribution, and usage conditions;
- uncertainty, simplification, or editorial caveat.

Overlay layers should be separated from the base map: terrain, population density, resources, transport routes, and political boundaries are additive overlays. Overlay display must not overwrite cited URLs, labels, or limitations.

## Fact / analysis / forecast labels

Generated segments must carry one of:

- `fact`: a primary or multiply corroborated geographic statement;
- `analysis`: an interpretive assessment derived from evidence;
- `forecast`: a conditional prediction with stated assumptions.

The three labels must not be mixed within one segment; uncertainty must accompany any forecast.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed geography fixture parses as the contract above;
   - primary source URLs and the map/source URL are present and HTTPS;
   - source priority favors official geographic and public sources;
   - map handling metadata (scope, scale, projection, vintage, license) is explicit;
   - terrain, climate, population, resources, transport, and industry sections are explicit;
   - history/politics impact and current meaning are explicit;
   - the synthetic fixture is not presented as a real place or measurement;
   - verified material hashes and provenance follow the adapter contract;
   - failure, uncertainty, and safety handling are explicit.

2. overlay and progression acceptance (later):
   - overlay displays map, theme, terrain/climate, population, resources, transport, industry, history/politics impact, current meaning, labels, and citation URLs;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/geography_world_corner/fixed_geography_example.json`. Its contract is exercised by `tests/test_geography_world_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial theme selection, implementation, model billing, deployment, public posting, and overlay/progression are also out of scope for this slice. Reviewer decides whether to proceed to overlay/progression implementation.
