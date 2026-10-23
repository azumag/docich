# Research-paper primary-source adapter

Issue #1706 の一次情報取得slice。URL-only discovery (#2690) で選ばれた候補を、structured direct draft (#2689) が受け取れる trusted metadata + `VerifiedWebBundle` に変換する。

## 今回の対応範囲

- 新着候補向けの modern arXiv `/abs/YYMM.NNNNN`（version suffix可）のみ対応する。
- `DOCICH_RESEARCH_PAPER_PRIMARY_ENABLED=1` の明示opt-in。未設定時は fetch 0。
- discovery が保持した `query_indexes` / `queries_used` をそのまま verified material provenance に引き継ぐ。
- 選択URLを既存 `WebBroker` に authorize してHTML本文を取得する。
- WebBroker receipt の URL / text hash / size を再検証する。
- arXiv本文から title / authors / Subjects / v1公開日を決定論的に抽出し、review status は `preprint` とする。
- abstract が本文に存在しないページはmetadataだけで採用せず fail closed する。
- 同じreceiptから `VerifiedWebMaterial` を作り、`research_paper_draft.generate_record()` の入力契約へ渡せる。

## 境界

- Search snippet / title / description をmetadataや根拠へ昇格しない。
- PDFを取得しない。`WebBroker` の `text/plain` / `text/html` MIME、DNS、TLS、redirect、SSRF境界は変更しない。
- 独自Web検索、OpenCode research prepass、別provider fallbackを追加しない。
- arXiv API、OpenReview API、出版社APIの新しい外部API契約はこのsliceでは追加しない。
- production schedule / credential / model / 配信開始設定は変更しない。
- 実HTTP、実Web Search、実model callをCIで行わない。テストはsynthetic receiptとinjected broker/generatorを使う。

## 次段階

OpenReview / publisher / DOI landing pageはsourceごとのmetadata仕様と査読状態の扱いが異なるため、別adapterとして追加する。その後、候補選定→一次情報取得→direct draft→overlayの非本番orchestrationを接続する。
