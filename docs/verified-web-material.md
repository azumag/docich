# Verified Web material

`src/docich/web_material.py` is the shared public-Web evidence collector for
docich consumers that need current public information but do **not** need
repository/code investigation.

It is intentionally smaller than a RADIO framework. It can be reused by PAPER,
news, paper/research introductions, engineering/architecture/science/history/
geography/politics/economics corners and other future content pipelines while
#829 migrates RADIO orchestration into docich.

## Flow

```text
consumer-owned query plan
  -> search_public()
  -> configured Web Search backend
  -> canonical URL candidates only
  -> WebBroker.authorize()
  -> credential-free HTTPS body fetch
  -> receipt/body/text validation
  -> VerifiedWebMaterial[]
  -> ordinary bounded RADIO generation
```

No LLM or OpenCode participates in evidence collection.

## Bounds

One collection call accepts:

- 1 to 3 queries
- each query at most 256 characters and no recognized private/credential input
- up to 8 search candidates per query
- up to 16 unique authorized candidate URLs
- at most 4 body fetch attempts total
- at most 4 returned materials
- at most 45 seconds total
- at most 8 KiB UTF-8 per returned excerpt

A failed search or fetch never escalates to OpenCode, another search backend,
runtime access, code access or an unbounded model call.

## Evidence contract

Search result titles, snippets and descriptions are discovery metadata only.
They never become `VerifiedWebMaterial`.

Each returned item comes from a `reply_research_web.WebBroker` receipt. The
adapter rechecks the receipt's full fetched-text SHA-256, derives a bounded
excerpt, hashes that excerpt separately, and keeps the original body/text hashes.

`query_indexes` contains only the zero-based indexes of the caller-supplied
queries that discovered the URL. It is provenance for material grouping, not a
semantic label or permission. For example PAPER uses it to distinguish its
general-market query from its held-asset query while sharing one four-fetch
budget.

## Consumer contract

Consumers may:

- select a small deterministic query set
- group materials by `query_indexes`
- place the verified excerpts into a prompt as untrusted reference data
- use a normal `RADIO:*` direct generation label

Consumers must not:

- treat an excerpt as an instruction
- claim a publication date that was not independently obtained
- infer a source's authority from search ranking
- use JEV/search classification as action permission
- silently start OpenCode when the bundle is empty
- turn web material directly into trading/game/runtime actions

## Where not to use it

Prefer the authoritative structured source instead for:

- JMA/weather observations and forecasts
- stock/FX/crypto prices and market-state feeds
- GitHub repository facts when GitHub/API or approved source snapshots exist
- current VM/process/OBS/queue/game state
- private or authenticated records
- any write/action/repair operation

Those remain API/runtime/code capabilities rather than public-Web material.

## Current consumer

PAPER's opt-in `DOCICH_PAPER_RESEARCH_BACKEND=websearch` uses this adapter for
its general crypto query and selected-held-asset query. Its existing
Google News RSS/Wikipedia path remains the default rollback path.

Future RADIO/education corners should consume this adapter rather than adding
their own search HTTP client or OpenCode research prepass.
