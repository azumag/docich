# English Learning from Real Materials Corner — Design (Planning)

**Date:** 2026-10-14
**Status:** Planning / non-production. Implementation, external API contracts, production billing, deployment, and production startup are intentionally out of scope for this document.
**Source issue:** azumag/docich#1717 (新コーナー: 実際のニュース・技術資料から学ぶ英語)
**Related:** #1767 / #1828 verified Web material adapter; common corner input contract.

## Goal

Define the planning and input contract for a stream corner that teaches English from real, published English materials: news, GitHub repositories and release notes, academic papers, games, and technical documentation. Instead of reviewing school English, the corner picks up concrete expressions from authentic public English and explains their meaning, nuance, grammar/usage, and differences from similar expressions.

The same material must also support an independently shaped script for YouTube extraction ("英語: 実例から学ぶ").

## Non-goals (this slice)

- No implementation of topic retrieval, candidate ranking, overlay text, runtime progression, external model billing, deployment, or production startup.
- No real English example is presented as verified or measured by this fixture; the fixed fixture is synthetic and is used only for contract acceptance.
- No change to the VERIFIED web material adapter bounds (1-3 queries, <= 4 materials, <= 45 s, <= 8 KiB excerpt).
- No adoption of a consumer-specific HTTP client or OpenCode research prepass.

## Material selection policy

Candidates are collected first and filtered for learning value. Prefer authentic public English that contains:

- a clear, quotable English sentence or short paragraph;
- at least one reusable key phrase;
- a meaning that benefits from nuance or usage notes;
- a technical, news, or daily-life context that adds interest;
- source text that is publicly available and can be cited with a URL.

A valid candidate must support at least one key phrase, one meaning note, one grammar/usage note, one nuance note, and one difference-from-similar note. Trivia that ends at "this is an English sentence" is excluded.

Short necessary excerpts are retained together with their sources. Long verbatim transfer is avoided.

## Information-source priority

Primary authentic English sources are required; a generic English-lesson article alone is not sufficient. Priority order:

1. official technical documentation / release notes / commit messages;
2. primary newsrooms and official statements;
3. academic papers and their abstracts;
4. game or project dialogue and in-game text from published works;
5. reputable explanatory articles used for orientation only.

Web-only lesson articles may be used for orientation, but they cannot carry a claim that requires a primary source.

## Input contract: one structured real example

Each selected example becomes one structured record containing:

- title and short example identifier;
- source type, source title, source URL, and published date;
- the English type / rotation category (technical English, news English, daily expression);
- today's real example (short quote);
- a 30-second summary;
- a one-line summary;
- a brief explanation of the source material's content;
- key phrases;
- key expressions;
- meaning;
- grammar / usage;
- nuance;
- similar expressions and their differences;
- an alternative natural example;
- citation-kept source URLs;
- limitations and unverified points;
- an overlay display plan;
- an independently usable YouTube-shaped script outline.

The output display also retains the source badge, subject, key phrase, meaning, grammar/usage, nuance, similar expressions, alternative example, and source citation.

## Verified material integration

Collection should reuse the shared verified Web material adapter rather than adding a consumer-specific HTTP client or an OpenCode research prepass. Discovery may use Web Search, but body and metadata should come from official documentation, project pages, publisher pages, or reputable primary sources first. The WebBroker MIME/SSRF boundaries must not be relaxed to read PDFs.

Script generation after evidence collection should use ordinary `RADIO:*` direct generation. Research prepass is not added.

## Required structured section order

For each introduced example:

1. today's real example;
2. key phrases;
3. meaning;
4. grammar / usage;
5. nuance;
6. difference from similar expressions;
7. another natural example;
8. a brief explanation of the source material.

Complex linguistic detail is used only when necessary; concrete examples carry the explanation.

## Short quote and citation range

Only short necessary excerpts are quoted. Each quoted excerpt must be accompanied by its source URL. Long verbatim transfer is prohibited. Citation URLs must be preserved end to end, including through overlay display and script generation.

## Non-production acceptance conditions

Acceptance is split into two phases:

1. input-contract and structured-draft acceptance:
   - a fixed English fixture parses as the contract above;
   - primary source URLs and the example URL are present and HTTPS;
   - source priority favors primary technical or primary documents;
   - short quotes are present and within the short-quote range;
   - key phrases, meaning, grammar/usage, nuance, and limitations are explicit;
   - the synthetic fixture is not presented as a real example;
   - verified Web material block is adapter-shaped (VerifiedWebBundle.wire()); non-production synthetic fixtures use well-formed placeholder body_sha256/text_sha256 values, while excerpt_sha256 is the real hash of the short excerpt.

2. overlay and progression acceptance (later):
   - overlay displays the source badge, subject, key phrase, meaning, grammar/usage, nuance, similar expressions, alternative example, and citation;
   - automatic progression from start to finish;
   - citation URL persistence;
   - independent YouTube-shaped script generation.

The fixed fixture lives under `tests/fixtures/english_corner/fixed_english_example.json`. Its contract is exercised by `tests/test_english_corner_contract.py`.

## Scope boundary for the next implementation step

The next implementation unit may start from this input contract, but implementation, external API contract, new model billing, deployment, and production startup remain separate work units and are not performed here. Initial material selection, implementation, model billing, deployment, and YouTube posting are also out of scope for this slice.
