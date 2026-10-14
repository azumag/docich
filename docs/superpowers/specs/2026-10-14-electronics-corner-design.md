# 電気・電子工学コーナー — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1711 (新コーナー: 電気・電子工学を深掘りする)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract; #1707 (エンジニアリング／工学紹介「構造と原理」, separate scope; electrical/electronics runs independently).

## Goal

Define the planning and input contract for a stream corner that explains how electricity is generated, converted, controlled, and turned into information or mechanical motion. The corner treats electrical/electronic-specific circuits, power, and signals as its focus, from power supplies and transformers to motors, inverters, semiconductors, sensors, analog/digital circuits, wireless/antennas, batteries, and power conversion.

The corner does not end at \"this is impressive\"; it always explains why this circuit or device was chosen, what it costs in loss, noise, and heat, and how it fails and is protected.

The same material should also support an independently shaped script for YouTube extraction (\"電気・電子工学：回路と原理\").

## Non-goals (this slice)

- No implementation of theme candidate management, retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real electrical device, standard, datasheet value, or measurement is verified or measured by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.
- No automatic progression, overlay display, public posting, or production startup.

## Theme candidate policy

Themes are collected first and filtered for explanatory power: a device or circuit where the design choice becomes visible. Prefer topics that make a constraint, tradeoff, or protection device visible:

- how an inverter drives a motor with controlled torque;
- power conversion efficiency vs thermal limits;
- semiconductor switching and gate drive;
- sensor signal conditioning and noise immunity;
- wireless link budget and antenna tradeoffs;
- battery management and protection;
- power distribution and fault isolation;
- analog/digital interface boundaries.

A valid theme must support at least one concrete circuit or device goal, one input/output relationship, one electrical principle, one circuit/device structure, one loss/noise/heat consideration, one failure mode and protection, and one design tradeoff. Trivia that ends at \"this is impressive\" is excluded.

## Information-source priority

Primary technical and regulatory sources are required; a general trivia article alone is not sufficient. Priority order:

1. component datasheets and manufacturer technical material;
2. standards and regulations (IEC, JIS, safety standards);
3. public institutions and certification bodies;
4. universities and research institutions (power electronics, control, RF research);
5. peer-reviewed academic papers;
6. engineering and electronics textbooks and technical manuals.

Web-only trivia articles may be used for orientation, but they cannot carry a claim that requires a primary source.

## Input contract: one structured electrical theme

Each selected theme becomes one structured record containing:

- title and short topic identifier;
- the electrical/electronic theme of the day;
- system and operating environment;
- a 30-second summary and a one-line summary;
- requirements and constraints;
- input and output;
- electrical principles;
- circuit/device structure;
- physical quantities;
- loss, noise, and heat;
- failure modes and protection;
- weak points;
- safety design;
- tradeoffs;
- limitations and unverified points;
- a figure or diagram plan;
- citation-kept source URLs;
- an independently usable YouTube-shaped script outline.

The output display also retains the theme, requirements/constraints, input/output, principles, structure, losses/noise/heat, failure modes/protection, and source URLs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from datasheets, manufacturers, standards bodies, public institutions, universities, publishers, or DOI pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. Code study (JEV) should be used only when code study is genuinely required. Search failure must not implicitly fall back to OpenCode.

## Required structured section order

For each introduced theme:

1. today's electrical/electronic theme;
2. requirements and constraints;
3. input and output;
4. electrical principles;
5. circuit/device structure;
6. loss, noise, and heat;
7. failure modes and protection;
8. why this design (rationale and tradeoffs).

Complex formulas are used only when necessary; diagrams carry the explanation.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed electronics fixture parses as the contract above;
   - primary source URLs are present and HTTPS;
   - source priority favors primary technical and regulatory sources;
   - requirements, input/output, principles, structure, losses/noise/heat, and failure modes/protection sections are explicit;
   - rationale and tradeoffs are explicit;
   - the synthetic fixture is not presented as a real device or measurement;
   - verified material hashes and provenance follow the adapter contract;
   - uncertainty and safety handling are explicit.

2. overlay and progression acceptance (later):
   - overlay displays theme, requirements/constraints, input/output, principles, structure, losses/noise/heat, failure modes/protection, rationale, diagram, and citation URLs;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/electronics_corner/fixed_electronics_example.json`. Its contract is exercised by `tests/test_electronics_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial theme selection, implementation, model billing, deployment, public posting, overlay, and progression are also out of scope for this slice. Reviewer decides whether to proceed to overlay/progression implementation.
