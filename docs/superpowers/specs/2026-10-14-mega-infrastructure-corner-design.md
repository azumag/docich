# 世界の巨大インフラコーナー — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1713 (新コーナー: 世界の巨大インフラを深掘りする)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract; #1707 (エンジニアリング／工学紹介「構造と原理」, separate scope; #1713 は「社会システムとしての巨大インフラ」を主眼にし、個別構造物の構造原理は扱わない); #1710 (建築・建築工学), #1712 (情報工学・コンピュータの仕組み) と重なる題材でも、本 Issue では社会を支えるシステムとしての巨大インフラを主眼にする。

## Goal

Define the planning and input contract for a stream corner that explains dams, bridges, tunnels, railways, airports, harbours, water and sewage systems, power transmission grids, and data centres as one system rather than as a list of impressive megastructures. For each theme the corner connects what the infrastructure supports, demand, geography and site conditions, scale and capacity, structure and construction method, operation, bottlenecks, redundancy, failure and disaster behaviour, maintenance and renewal, and social/economic impact.

The corner does not end at "this is impressive"; it always explains why this capacity, route, and method were chosen, what the bottleneck is, and what happens when one part of the chain fails.

The same material should also support an independently shaped script for YouTube extraction.

## Non-goals (this slice)

- No implementation of theme candidate management, retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real infrastructure capacity, route length, throughput, cost, or disaster record is verified or measured by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.
- No automatic progression, overlay display, public posting, or production startup.

## Theme candidate policy

Themes are collected first and filtered for explanatory power: an infrastructure whose system behaviour becomes visible. Prefer topics that make a constraint, tradeoff, or failure mode visible:

- why a dam's location and reservoir volume are chosen;
- how a bridge's span follows geology, navigation clearance, and wind;
- tunnel boring versus cut-and-cover versus immersed tunnel;
- how a rail corridor's capacity is set by signalling, gradients, and terminals, not by track length;
- airport runway geometry, taxiway capacity, and slot constraints;
- harbour depth, berth, and hinterland connection;
- water purification and distribution head loss and pressure zones;
- sewage treatment capacity and combined-sewer overflow;
- transmission grid stability, voltage, and reserve margin;
- submarine cable and data-centre cooling, power, and latency;
- redundancy, single points of failure, and how a chain survives one outage;
- renewal and end-of-life of an infrastructure built decades ago.

A valid theme must support at least one concrete service it provides, one demand or capacity figure, one geographic/site condition, one structural or construction decision, one operational constraint, one bottleneck or single point of failure, and one failure/disaster or maintenance case. Trivia that ends at "this is the biggest" or "this is the longest" is excluded.

## Information-source priority

Primary public and operator sources are required; a general trivia article alone is not sufficient. Priority order:

1. operator and owner technical material (ministries, municipalities, port and rail authorities, utilities);
2. standards, design codes, and regulations (ISO, IEC, national codes);
3. public institutions and official statistics (capacity, traffic, demand, disaster records);
4. operator safety, inspection, and incident reports;
5. universities and research institutions;
6. peer-reviewed academic papers;
7. engineering textbooks and technical manuals.

Web-only trivia articles may be used for orientation, but they cannot carry a claim that requires a primary source.

## Input contract: one structured infrastructure theme

Each selected theme becomes one structured record containing:

- title and short topic identifier;
- the infrastructure theme of the day;
- what the infrastructure supports (service and users);
- demand and required capacity;
- geography and site conditions;
- scale and capacity (concrete figures with units);
- structure and construction method;
- operation;
- bottlenecks and single points of failure;
- redundancy;
- failure and disaster behaviour;
- maintenance and renewal;
- social and economic impact;
- a 30-second summary and a one-line summary;
- requirements and constraints;
- a map / schematic handling note (scope, scale, projection, vintage, layers, resolution, license, usage conditions);
- an explicit fact / analysis / forecast label for each generated segment;
- limitations and unverified points;
- a figure or diagram plan;
- citation-kept source URLs;
- an independently usable YouTube-shaped script outline.

The output display also retains the theme, what it supports, demand/capacity, geography/site, structure/method, operation, bottleneck, failure/disaster, maintenance/renewal, social/economic impact, diagram, and source URLs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from operators, standards bodies, public institutions, universities, publishers, or DOI pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. Code study (JEV) should be used only when code study is genuinely required. Search failure must not implicitly fall back to OpenCode.

## Required structured section order

For each introduced theme:

1. what this infrastructure supports;
2. demand and capacity;
3. geography and site conditions;
4. scale and capacity;
5. structure and construction method;
6. operation;
7. bottleneck and redundancy;
8. failure and disaster behaviour;
9. maintenance and renewal;
10. social and economic impact.

Complex formulas are used only when necessary; diagrams and schematic figures carry the explanation. Specialized terms must not be read verbatim; diagrams or plain explanations should carry them when needed.

## Map and schematic handling contract

Maps and schematic diagrams are explanatory objects, not decoration. Each figure must carry, or explicitly state the absence of:

- geographic scope and center (latitude/longitude or named center);
- scale and resolution;
- projection or coordinate reference;
- vintage or measurement date;
- data layers and their source;
- license, attribution, and usage conditions;
- uncertainty, simplification, or editorial caveat.

Overlay layers should be separated from the base map or schematic: catchment, route, network topology, capacity, and failure zones are additive overlays. Overlay display must not overwrite cited URLs, labels, or limitations. A synthetic figure must not assert a real map license or usage permission.

## Fact / analysis / forecast labels

Generated segments must carry one of:

- `fact`: a primary or multiply corroborated statement about the infrastructure;
- `analysis`: an interpretive assessment derived from evidence;
- `forecast`: a conditional prediction with stated assumptions.

The three labels must not be mixed within one segment; uncertainty must accompany any forecast.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed mega infrastructure fixture parses as the contract above;
   - primary source URLs and the figure source URL are present and HTTPS;
   - source priority favors operator, standards, and public-institution sources;
   - map/schematic handling metadata (scope, scale, projection, vintage, license) is explicit;
   - what it supports, demand, geography/site, scale/capacity, structure/method, operation, bottleneck/redundancy, failure/disaster, maintenance/renewal, and social/economic impact sections are explicit;
   - the synthetic fixture is not presented as a real infrastructure or measurement;
   - verified material hashes and provenance follow the adapter contract;
   - uncertainty and safety handling are explicit.

2. overlay and progression acceptance (later):
   - overlay displays theme, what it supports, demand/capacity, geography/site, structure/method, operation, bottleneck/redundancy, failure/disaster, maintenance/renewal, social/economic impact, figure, labels, and citation URLs;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/mega_infrastructure_corner/fixed_mega_infrastructure_example.json`. Its contract is exercised by `tests/test_mega_infrastructure_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial theme selection, implementation, model billing, deployment, public posting, overlay, and progression are also out of scope for this slice. Reviewer decides whether to proceed to overlay/progression implementation.
