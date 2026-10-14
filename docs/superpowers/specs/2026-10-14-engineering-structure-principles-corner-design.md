# Engineering / "Structure and Principles" Corner — Design (Planning)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1707 (新コーナー: エンジニアリング／工学紹介「構造と原理」)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract.

## Goal

Define the planning and input contract for a stream corner that introduces a real machine, infrastructure system, or electronic device by explaining why its shape exists, how it works, how it breaks, and what has been designed around those failures. The corner is not trivia about finished products; it focuses on constraints, failure modes, tradeoffs, and the design decisions that follow from them.

The same material should also support an independently shaped script for YouTube extraction ("工学：構造と原理").

## Non-goals (this slice)

- No implementation of topic retrieval, candidate ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real engineering specimen is presented as verified or measured by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.

## Topic selection policy

Candidates are collected first and filtered by engineering significance. Prefer topics that make a constraint visible:

- why the Shinkansen nose has its shape;
- why a suspension bridge does not fall;
- what happens if an elevator wire breaks;
- why an aircraft wing does not break;
- how a data center is cooled;
- how an SSD stores data;
- what reactor control rods do;
- why a highway curve is banked;
- optical image stabilization in a smartphone;
- railway signaling and block systems;
- dam shape and water pressure;
- turbochargers;
- heat pumps;
- GPS;
- optical fiber.

A valid candidate must support at least one concrete design constraint, one failure mode, and one safety or redundancy decision. Trivia that ends at "this is impressive" is excluded.

## Information-source priority

Primary technical sources are required; a general trivia article alone is not sufficient. Priority order:

1. manufacturer technical material / drawings / specifications;
2. standards and public-institution material;
3. universities and research institutions;
4. peer-reviewed academic papers;
5. patents;
6. technical manuals and textbooks.

Web-only trivia articles may be used for orientation, but they cannot carry a claim that requires a primary source. For medicine-adjacent, health, social, or safety claims, research design, sample size, correlation/causation boundaries, and review status must be retained as caveats.

## Input contract: one structured engineering theme

Each selected theme becomes one structured record containing:

- title and short topic identifier;
- subject system and its operating environment;
- one 30-second summary;
- the problem to be solved (what is difficult);
- the minimum necessary principles (physics, electricity, materials, information);
- the actual structure, with a section or mode diagram and important physical quantities;
- input -> mechanism -> output;
- where it can break, weak points, and unexpected states;
- safety and redundancy design;
- tradeoffs and historical improvement;
- an independently usable YouTube-shaped script outline;
- citation-kept source URLs;
- limitations and unverified points;
- a figure or diagram plan.

The output display also retains the subject, section or mode diagram, input -> mechanism -> output, physical quantities, failure points, safety devices, and tradeoffs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from manufacturer, public-institution, standards, university, or DOI/publisher pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. JEV code investigation should be used only when code study is genuinely required.

## Required structured section order

For each introduced theme:

1. first look at the real object;
2. define the problem;
3. principle (minimum necessary);
4. structure (section / mode diagram);
5. how it breaks (failure, weakness, unexpected state);
6. safety and redundant design;
7. further interesting points (tradeoff, historical improvement).

Complex formulas are used only when necessary; diagrams carry the explanation.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed engineering fixture parses as the contract above;
   - primary source URLs and the theme URL are present and HTTPS;
   - source priority favors primary technical material;
   - synthetic fixture is not presented as a real specimen;
   - verified material hashes and provenance follow the adapter contract;
   - safety and failure handling are explicit.

2. overlay and progression acceptance (later):
   - overlay subject, diagram, quantities, failure points, and tradeoffs display;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/engineering_structure_principles_corner/fixed_engineering_example.json`. Its contract is exercised by `tests/test_engineering_structure_principles_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial theme selection, implementation, model billing, deployment, and YouTube posting are also out of scope for this slice.
