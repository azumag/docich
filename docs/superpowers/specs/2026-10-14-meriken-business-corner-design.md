# メリケンの商売・ビジネス研究所 — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1721 (新コーナー: メリケンの商売・ビジネス研究所)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract; #1720 (メリケンの経済学講座, separate scope).

## Goal

Define the planning and input contract for a stream corner that decomposes companies and services by their business model: not simply praising successful or famous firms, but breaking down where value is created, from whom money is received, and how cost, competition, and scale economics are designed.

The corner analyzes businesses through customer, value, pricing, cost, competitive advantage, unit economics, growth, and vulnerabilities. メリケン serves as the character who foregrounds a capitalist, profit, and competition perspective. The program must still present labor, regulation, consumers, externalities, and sustainability constraints alongside it, and must not treat any single viewpoint as fact.

## Non-goals (this slice)

- No implementation of candidate management, retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real company, statement, statistic, market price, or business result is verified or measured by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.
- No automatic progression, overlay display, or public posting.

## Candidate / business model selection policy

Themes are collected first and filtered by whether a company or service can be decomposed into a concrete business model with evidence. Prefer topics where value creation, revenue, cost, and competition are visible:

- why a membership fee (subscription / fee) model is used;
- why customers are made to self-assemble or self-serve;
- platform vs marketplace structure;
- convenience-store profit structure;
- airline pricing differences;
- SaaS monthly subscription;
- advertising model vs subscription;
- franchise;
- vertical vs horizontal integration;
- loss-leader and cross-subsidy.

A valid candidate must support at least a verifiable company or service question, a primary or official source (IR, official disclosure, etc.), a decomposable business model, and multiple perspectives including constraints and counterarguments. Topics that assert one ideology as fact are excluded. Uncertain, provisional, or unreviewed status must be stated explicitly.

Candidate management for companies and business models, overlap checks against existing persona / draft / progression processing, and the input contract are in scope for this slice; initial event selection, implementation, model billing, deployment, public posting, and overlay/progression are not.

## Information-source priority

Primary and official business sources are required; an orientation article alone is not sufficient. Priority order:

1. IR, official filings, annual reports, and primary corporate disclosures;
2. official documents, statutes, and public institutions;
3. established wire or media outlets (multiple, cited);
4. academic, research, or analyst materials with documented method and provenance;
5. fact-check or independent verification sources.

For claims touching labor, regulation, consumers, health, social policy, safety, or distribution, research design, sample size, correlation/causation boundaries, and uncertainty must be retained as caveats.

## Input contract: one structured business model record

Each selected company or service becomes one structured record containing:

- title and short topic identifier;
- the business question (why / how);
- Meriken's view (capitalism / profit / competition perspective);
- customer (who the customer is);
- offering (what is sold);
- revenue model (how it is charged);
- cost structure (main costs);
- competitive advantage / moat;
- unit economics;
- scaling dynamics (what changes when growing);
- vulnerabilities (weaknesses, regulation, competitors);
- counterarguments and constraints (labor, regulation, consumer, externality, sustainability);
- stakeholder and externality impact;
- application to current news or familiar companies;
- 30-second summary and one-line summary;
- an explicit fact / analysis / forecast label for each generated segment;
- limitations and uncertainty;
- a figure or overlay plan;
- citation-kept source URLs;
- verified Web material references;
- an independently usable YouTube-shaped script outline.

The output display also retains the business question, Meriken's view, customer, offering, revenue model, costs, moat, unit economics, vulnerabilities, labels, and source URLs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from IR, official disclosures, public institutions, publishers, or DOI pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. JEV code investigation should be used only when code study is genuinely required. Search failure must not implicitly fall back to OpenCode.

Primary structured API / official sources are preferred when available; search failures must not implicitly fall back to OpenCode.

## Required structured section order

For each introduced company or service:

1. customer (who the customer is);
2. offering (what is sold);
3. revenue model (how it is charged);
4. cost structure (main costs);
5. moat / competitive advantage;
6. unit economics;
7. scaling dynamics (what changes when growing);
8. vulnerabilities, regulation, and competitors.

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
   - a fixed business fixture parses as the contract above;
   - primary source URLs (IR / official disclosures) and the topic URL are present and HTTPS;
   - source priority favors IR, official disclosures, and public institutions;
   - customer, offering, revenue model, cost structure, moat, unit economics, scaling dynamics, and vulnerabilities are explicit;
   - Meriken's view and the counterarguments / constraints are explicit;
   - the synthetic fixture is not presented as real business data or verified analysis;
   - verified material hashes and provenance follow the adapter contract;
   - failure, uncertainty, and caveat handling are explicit.

2. overlay and progression acceptance (later):
   - overlay displays business question, Meriken's view, customer, offering, revenue model, cost structure, moat, unit economics, vulnerabilities, labels, and citation URLs;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/meriken_business_corner/fixed_meriken_business_example.json`. Its contract is exercised by `tests/test_meriken_business_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial company selection, implementation, model billing, deployment, public posting, and overlay/progression are also out of scope for this slice. Reviewer decides whether to proceed to overlay/progression implementation.
