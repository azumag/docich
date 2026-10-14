# 情報工学・コンピュータの仕組みコーナー — 設計 (企画)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1712 (新コーナー: 情報工学・コンピュータの仕組みを深掘りする)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract; #1707 (エンジニアリング／工学紹介「構造と原理」, separate scope; information engineering/computer mechanisms runs independently); #1711 (電子工学, separate scope).

## Goal

Define the planning and input contract for a stream corner that explains not only what a computer system does, but what happens inside it: OS, CPU, network, database, cryptography, distributed systems, compilers, and AI inference. For each theme, the corner connects the problem, the naive method, the information-engineering mechanism, the concrete implementation, the failure mode, and the performance/security/complexity tradeoff.

The corner does not end at "this is impressive"; it always explains what happens internally and why this design was chosen.

## Non-goals (this slice)

- No implementation of theme candidate management, retrieval, ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real hardware, kernel, protocol, database engine, or performance value is verified or measured by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.
- No automatic progression, overlay display, public posting, or production startup.

## Theme candidate policy

Themes are collected first and filtered for explanatory power: a mechanism where the internal behavior becomes visible. Prefer topics that make a constraint, tradeoff, or failure mode visible:

- CPU cache and cache coherence;
- virtual memory and page tables;
- TCP/IP and retransmission;
- DNS and negative caching;
- database transactions and isolation levels;
- queues and backpressure;
- idempotency;
- distributed locks and consensus;
- cryptography and digital signatures;
- compilers and JIT;
- GPU and parallelism;
- LLM inference and KV cache.

A valid theme must support at least one concrete problem, one naive method, one information-engineering mechanism, and one failure mode or tradeoff. Trivia that ends at "this system is fast" is excluded.

## Information-source priority

Primary technical and specification sources are required; a general trivia article alone is not sufficient. Priority order:

1. standards and specifications (RFC, IEEE, ISO, vendor architecture manuals);
2. CPU/OS/database/network vendor technical material (manuals, kernel docs, engine internals docs);
3. official project documentation and source code (Linux kernel, PostgreSQL, etc.);
4. public institutions and registries;
5. universities and research institutions;
6. peer-reviewed academic papers;
7. textbooks and technical manuals.

Web-only trivia articles may be used for orientation, but they cannot carry a claim that requires a primary source.

## Input contract: one structured mechanism theme

Each selected theme becomes one structured record containing:

- title and short topic identifier;
- the mechanism or theme of the day;
- problem context and workload;
- a 30-second summary and a one-line summary;
- requirements and constraints;
- naive approach;
- information-engineering mechanism and principles;
- implementation example;
- failure modes;
- performance/security/complexity tradeoffs;
- limitations and unverified points;
- a figure or diagram plan;
- citation-kept source URLs;
- an independently usable YouTube-shaped script outline.

The output display also retains the theme, requirements/constraints, naive approach, mechanism, implementation, failure modes, tradeoffs, diagram, and source URLs.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from standards bodies, vendors, official documentation, universities, publishers, or DOI pages first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. Code study (JEV) should be used only when code study is genuinely required. Search failure must not implicitly fall back to OpenCode.

Public real-world code examples (e.g. docich's own I/O or concurrency code) may be used only when their public scope is confirmed; non-public operational information must be separated.

## Required structured section order

For each introduced theme:

1. today's problem;
2. naive method;
3. information-engineering mechanism;
4. implementation example;
5. what fails if it goes wrong;
6. performance / safety / complexity tradeoff.

Complex formulas are used only when necessary; diagrams carry the explanation.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed information engineering fixture parses as the contract above;
   - primary source URLs are present and HTTPS;
   - source priority favors primary technical and specification sources;
   - problem, naive method, mechanism, implementation, failure, and tradeoff sections are explicit;
   - the synthetic fixture is not presented as a real system or measurement;
   - verified material hashes and provenance follow the adapter contract;
   - uncertainty and safety handling are explicit.

2. overlay and progression acceptance (later):
   - overlay displays theme, requirements/constraints, naive method, mechanism, implementation, failure modes, tradeoffs, diagram, and citation URLs;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives at `tests/fixtures/information_engineering_corner/fixed_information_engineering_example.json`. Its contract is exercised by `tests/test_information_engineering_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial theme selection, implementation, model billing, deployment, public posting, overlay, and progression are also out of scope for this slice. Reviewer decides whether to proceed to overlay/progression implementation.
