# Cross-Disciplinary Research Paper Corner — Design (Planning)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1706 (新コーナー: 分野横断の論文紹介コーナー)
**Related:** #1767 / #1828 verified Web material adapter; paper-first-info contract.

## Goal

Define the planning and input contract for a stream corner that introduces one (or a few) recently published, cross-disciplinary research paper without limiting candidates to AI/CS. The corner must explain not only the abstract, but what was discovered, what is new, how trustworthy the evidence is, and how it relates to real life or docich projects.

## Non-goals (this slice)

- No implementation of candidate retrieval, ranking, overlay text, runtime completion, external model billing, deployment, or production startup.
- No real paper fixture is claimed as a publication; the fixed fixture is synthetic and is used only for contract acceptance.
- No extension of existing financial PAPER (#769) news-linkage, conflation of issue areas, or reopening approved paper-handling cards.
- No change to VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 sec, <= 8 KiB excerpt).

## Candidate selection policy

Candidates are selected after collection and must satisfy:

- recently published;
- surprising or non-obvious result;
- new method or new knowledge;
- explainable to general viewers;
- imaginable real-world application;
- bonus if applicable to docich, MoYoGarden, game AI, or HCI;
- lower priority for generic LLM promotion or advertising-like material;
- preprint status must be stated explicitly when a paper is not peer-reviewed.

Fields considered cross-disciplinary include AI/ML, CS, robotics, physics, space, biology, medicine, psychology, social science, urban/transport, materials, energy, game research, HCI, and mathematics. The selected field set is deliberately open.

## Information-source priority

Primary information is required; an introduction article alone is not sufficient. Priority order:

1. paper body;
2. arXiv or equivalent preprint;
3. publisher / society page;
4. author public materials;
5. supplement / code repository.

For medicine, health, and social science, the generated draft must retain research design, sample size, correlation/causation boundaries, and review status as caveats.

## Input contract: one structured paper record

Each selected paper becomes one structured record containing:

- title, authors, affiliations (as needed);
- field;
- publication date;
- review status (`peer-reviewed`, `preprint`, or `unknown`);
- DOI and paper URL;
- one-line summary;
- research question;
- prior problem;
- novelty;
- experiment/data strength;
- limitations and caveats;
- practical use;
- docich-project application;
- figure or simple explanation;
- citation-kept source URLs.

The output display also retains title/authors, field, publication date, review status, one-line summary, novelty, figure/table or simplified diagram, application target, and DOI/URL.

## Verified material integration

Published-material collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or OpenCode research prepass. Paper discovery may use Web Search, but paper body and metadata should come from arXiv/OpenReview/publisher/DOI HTML or APIs first. The adapter's MIME/SSRF boundaries of the WebBroker must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. JEV code investigation should be used only when code study is genuinely required.

## Required structured section order

For each introduced paper:

1. 30-second summary;
2. what the researchers investigated;
3. what was previously problematic;
4. what is new this time;
5. how strong the experiment/data are;
6. limitations and caveats;
7. "so what can be used?";
8. application to docich-related projects.

Specialized terms must not be read verbatim. Diagrams or simple examples should explain them when needed.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed paper fixture parses as the contract above;
   - DOI/URL and source URLs are present and HTTPS;
   - review status and limitations are explicit;
   - synthetic fixture is not presented as a real publication;
   - verified material hashes and provenance follow the adapter contract.

2. overlay and progression acceptance (later):
   - overlay title/key points display;
   - automatic progression from start to finish;
   - citation URL persistence;
   - caveat retention for medicine/health/social science.

The fixed fixture lives at `tests/fixtures/research_paper_corner/fixed_paper_example.json`. Its contract is exercised by `tests/test_research_paper_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Reviewer decides whether to proceed to overlay/progression implementation.
