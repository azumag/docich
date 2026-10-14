# 数学コーナー — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1715 (新コーナー: 問いから入る数学コーナー)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract; #1712 (情報工学), #1714 (物理学) と題材が重なっても、本 Issue では数学的な「なぜそうなるか」の説明力を主眼にする。

## Goal

Define the planning and input contract for a stream corner that explains mathematics — probability, combinatorics, graph theory, number theory, linear algebra, Fourier analysis, game theory, optimization, voting systems, cryptography, statistics and inference — entering from a real-world question or a puzzle rather than from definitions and theorems. For each theme the corner states a question, records the intuition the audience starts from, works a small example, states the mathematical model that carries the explanation, generalizes it, shows real-world applications, and ends with an intuitive summary.

The corner does not stop at "here is the formula". Every formula must answer what it explains, in what range it holds, and what its symbols and assumptions are, together with one concrete number and one diagram. A formula is an explanation tool, not the topic. The audience should first convince itself of the answer on a small case, and only then meet the general statement.

The same material should also support an independently shaped script for YouTube extraction.

## Non-goals (this slice)

- No implementation of theme candidate management, retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real measurement, observation record, textbook value, or law is verified by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.
- No automatic progression, overlay display, public posting, or production startup.

## Theme candidate policy

Themes are collected first and filtered for explanatory power: a real question or puzzle through which a mathematical idea becomes visible. Prefer topics where intuition and combinatorial reality disagree, or where one model explains several unrelated appearances:

- why 23 people already make a birthday match likely;
- how do you find the shortest path between two points;
- why election rules change the outcome;
- why prime numbers are used in cryptography;
- Bayesian inference;
- game theory;
- linear algebra and images / AI;
- Fourier transform;
- graph theory.

A valid theme must support at least one question worth asking, one intuition to record, one small worked example, one mathematical model, one generalization, one real-world application, and one intuitive summary that carries the mechanism. Trivia that ends at "math is beautiful" or in an unsourced awe fact is excluded.

## Information-source priority

Primary public and institutional sources are required; a general trivia article alone is not sufficient. Priority order:

1. public research institutions, national academies and mathematics societies (AMS, SIAM, EMS, national academies);
2. publicly funded research organizations and observatories;
3. peer-reviewed academic papers and preprints (arXiv);
4. university lecture and laboratory material;
5. mathematics textbooks;
6. technical manuals and measurement reports;
7. verified reference data where applicable.

Web-only trivia articles may be used for orientation, but they cannot carry a claim that requires a primary source. Any quoted constant, probability, or asymptotic value must be traceable to a primary or reference source before it is presented as fact.

## Input contract: one structured math theme

Each selected theme becomes one structured record containing:

- title and short topic identifier;
- the mathematics field and topic of the day (probability, combinatorics, graph theory, number theory, linear algebra, statistics, optimization, game theory, etc.);
- the question of the day;
- the intuitive prediction, recorded before the model is stated;
- the phenomenon, observation, or puzzle that is actually presented;
- a small example that the audience can follow by hand;
- the mathematical model or laws, by name;
- the simplest formula or model that carries the explanation;
- the generalization beyond the example;
- the experimental and technological application;
- the real-world application;
- a 30-second summary and a one-line summary;
- requirements and constraints;
- a figure and formula handling note;
- per-formula contract entries (what it explains, symbol definitions, units, validity range, numeric example, plain-language restatement, diagram reference);
- an explicit fact / analysis / forecast label for each generated segment;
- limitations and unverified points;
- a figure or diagram plan;
- citation-kept source URLs;
- an independently usable YouTube-shaped script outline.

The output display also retains the question, intuition, example, model, formula, generalization, application, diagram, labels, and source URLs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from research societies, universities, publishers, preprint servers, or DOI pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. Code study (JEV) should be used only when code study is genuinely required. Search failure must not implicitly fall back to OpenCode.

## Required structured section order

For each introduced theme:

1. the question of the day;
2. the intuitive prediction;
3. a small example;
4. the mathematical model;
5. the generalization;
6. the real-world application;
7. an intuitive summary.

## Figure and formula handling contract

Formulas are explanation objects, not decoration. Each formula must carry, or explicitly state the absence of:

- what the formula explains, in one sentence;
- a definition for every symbol, with no unexplained symbol on screen;
- the unit of every quantity (including dimensionless probabilities where applicable);
- the validity range and the assumptions behind it;
- one worked numeric example with units;
- a plain-language restatement for someone who will not read the equation;
- a reference to the diagram that carries the same explanation.

Diagrams carry the structure the formula assumes: a state space, a graph, an axis, a counting table. A diagram must state its scope, its simplification, and that it is a schematic rather than a measurement. Overlay layers must not overwrite cited URLs, symbol definitions, or the validity range. A synthetic figure must not assert a real measurement, license, or usage permission.

An equation must never be shown alone: no formula without what it explains, its range, and one concrete number. Specialized terms must not be read verbatim; the diagram or the plain restatement should carry them.

## Fact / analysis / forecast labels

Generated segments must carry one of:

- `fact`: a primary or multiply corroborated statement about the mathematics;
- `analysis`: an interpretive explanation derived from the model and the evidence;
- `forecast`: a conditional prediction with stated assumptions.

The three labels must not be mixed within one segment; uncertainty must accompany any forecast.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed math fixture parses as the contract above;
   - primary source URLs are present and HTTPS, and the source priority favors public research institutions and mathematics societies;
   - the seven narrative sections (question, intuition, small example, model, generalization, application, intuitive summary) are explicit and non-empty;
   - every formula entry states what it explains, symbols, units, validity range, a numeric example, a plain restatement, and a diagram reference;
   - no formula is presented as the goal of the corner;
   - the synthetic fixture is not presented as a real measurement or a verified mathematical value;
   - verified material hashes and provenance follow the adapter contract;
   - uncertainty and limitations are explicit.

2. overlay and progression acceptance (later):
   - overlay displays the question, intuition, example, model, formula with symbols and units, generalization, application, figure, labels, and citation URLs;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/math/fixed_math_example.json`, with the corner-shaped copy at `tests/fixtures/math_corner/fixed_math_example.json`. Its contract is exercised by `tests/test_math_contract.py` and `tests/test_math_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial theme selection, implementation, model billing, deployment, public posting, overlay, and progression are also out of scope for this slice. Reviewer decides whether to proceed to overlay/progression implementation.
