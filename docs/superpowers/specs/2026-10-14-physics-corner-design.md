# 物理学コーナー — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1714 (新コーナー: 物理学を身近な現象から深掘りする)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract; #1706 (研究速報, separate scope; #1714 は個別論文紹介ではなく物理概念の理解を主眼にする); #1707 (エンジニアリング「構造と原理」), #1710 (建築・建築工学), #1711 (電気・電子), #1712 (情報工学) と重なる題材でも、本 Issue では物理法則そのものの説明力を主眼にする。

## Goal

Define the planning and input contract for a stream corner that explains physics — mechanics, electromagnetism, heat, fluids, quantum, relativity, statistical mechanics, and cosmology — entering from an everyday phenomenon or a familiar technology. For each theme the corner states a question, records the intuition the audience starts from, names the phenomenon that is actually observed, states the physical law that governs it, gives the simplest formula or model that carries the explanation, and shows where intuition breaks.

The corner does not stop at "here is the equation". Every formula must answer what it explains, in what range it holds, and what its symbols and units are, together with one concrete number and one diagram. An equation is an explanation tool, not the topic.

The same material should also support an independently shaped script for YouTube extraction.

## Non-goals (this slice)

- No implementation of theme candidate management, retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real measurement, observation record, textbook value, or law is verified by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.
- No automatic progression, overlay display, public posting, or production startup.

## Theme candidate policy

Themes are collected first and filtered for explanatory power: an everyday phenomenon or technology through which a physical law becomes visible. Prefer topics where intuition and observation disagree, or where one law explains several unrelated appearances:

- why water boils at a lower temperature on a mountain, and why a pressure cooker works;
- why a spoon in a stirred cup collects at the centre of the vortex;
- why a magnifying glass can focus sunlight but a window pane cannot;
- why a rainbow is a circle of a fixed angle, and why it appears behind you;
- why a refrigerator can pump heat from cold to hot, and what that costs in work;
- why a sliding book stops, and what Newton's first law actually asserts;
- why a star's colour and a filament lamp's colour both follow the same curve;
- why a microwave can superheat water that then erupts;
- why a heavier and a lighter object fall together in vacuum but not in air;
- why a compass needle and an MRI magnet obey the same spin rule;
- why a GPS clock runs slower and must be corrected;
- why shaking a jar of mixed nuts sorts large ones to the top.

A valid theme must support at least one question worth asking, one intuition to record, one observable phenomenon, one named physical law, one simple formula or model with a stated validity range, one experimental or technological application, and one point where intuition fails. Trivia that ends at "physics is interesting" or in an unsourced awe fact is excluded.

## Information-source priority

Primary public and institutional sources are required; a general trivia article alone is not sufficient. Priority order:

1. public research institutions and national measurement institutes (NIST, NMIJ, PTB, national standards bodies);
2. public scientific agencies and observatories (NASA, JAXA, ESA, NOAA, national institutes);
3. measurement standards and reference data (SI definition, CODATA, standard reference data);
4. peer-reviewed academic papers;
5. physics textbooks;
6. university lecture and laboratory material;
7. technical manuals and measurement reports.

Web-only trivia articles may be used for orientation, but they cannot carry a claim that requires a primary source. Any quoted constant, unit, or measurement value must be traceable to a primary or reference-data source before it is presented as fact.

## Input contract: one structured physics theme

Each selected theme becomes one structured record containing:

- title and short topic identifier;
- the physics field and subfield of the day (mechanics / electromagnetism / heat / fluids / quantum / relativity / statistical mechanics / cosmology);
- the question of the day;
- the intuitive prediction, recorded before the law is stated;
- the phenomenon that is actually observed;
- the physical law or laws, by name;
- the simplest formula or model that carries the explanation;
- the experimental and technological application;
- the point where intuition fails;
- a 30-second summary and a one-line summary;
- requirements and constraints;
- a figure and formula handling note;
- per-formula contract entries (what it explains, symbol definitions, units, validity range, numeric example, plain-language restatement, diagram reference);
- an explicit fact / analysis / forecast label for each generated segment;
- limitations and unverified points;
- a figure or diagram plan;
- citation-kept source URLs;
- an independently usable YouTube-shaped script outline.

The output display also retains the question, intuition, phenomenon, law, formula/model, application, intuition-failure point, diagram, labels, and source URLs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from research institutions, standards and reference-data bodies, universities, publishers, or DOI pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. Code study (JEV) should be used only when code study is genuinely required. Search failure must not implicitly fall back to OpenCode.

## Required structured section order

For each introduced theme:

1. the question of the day;
2. the intuitive prediction;
3. the phenomenon that is actually observed;
4. the physical law;
5. the simplest formula or model;
6. the experimental and technological application;
7. the point where intuition fails.

## Figure and formula handling contract

Formulas are explanation objects, not decoration. Each formula must carry, or explicitly state the absence of:

- what the formula explains, in one sentence;
- a definition for every symbol, with no unexplained symbol on screen;
- the unit of every quantity;
- the validity range and the assumptions behind it;
- one worked numeric example with units;
- a plain-language restatement for someone who will not read the equation;
- a reference to the diagram that carries the same explanation.

Diagrams carry the geometry the formula assumes: pressure versus temperature axes, force arrows, a light path, a field line, a scale. A diagram must state its scope, its simplification, and that it is a schematic rather than a measurement. Overlay layers must not overwrite cited URLs, symbol definitions, or the validity range. A synthetic figure must not assert a real measurement, license, or usage permission.

An equation must never be shown alone: no formula without what it explains, its range, and one concrete number. Specialized terms must not be read verbatim; the diagram or the plain restatement should carry them.

## Fact / analysis / forecast labels

Generated segments must carry one of:

- `fact`: a primary or multiply corroborated statement about the physics;
- `analysis`: an interpretive explanation derived from the law and the evidence;
- `forecast`: a conditional prediction with stated assumptions.

The three labels must not be mixed within one segment; uncertainty must accompany any forecast.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed physics fixture parses as the contract above;
   - primary source URLs are present and HTTPS, and the source priority favors public research institutions and standards bodies;
   - the seven narrative sections (question, intuition, observation, law, formula/model, application, intuition failure) are explicit and non-empty;
   - every formula entry states what it explains, symbols, units, validity range, a numeric example, a plain restatement, and a diagram reference;
   - no formula is presented as the goal of the corner;
   - the synthetic fixture is not presented as a real measurement or a verified physical value;
   - verified material hashes and provenance follow the adapter contract;
   - uncertainty and limitations are explicit.

2. overlay and progression acceptance (later):
   - overlay displays the question, intuition, phenomenon, law, formula/model with symbols and units, application, intuition-failure point, figure, labels, and citation URLs;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/physics/fixed_physics_example.json`, with the corner-shaped copy at `tests/fixtures/physics_corner/fixed_physics_example.json`. Its contract is exercised by `tests/test_physics_contract.py` and `tests/test_physics_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial theme selection, implementation, model billing, deployment, public posting, overlay, and progression are also out of scope for this slice. Reviewer decides whether to proceed to overlay/progression implementation.
