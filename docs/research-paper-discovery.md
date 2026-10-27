# Research-paper URL discovery

Issue #1706 の候補発見slice。既存 Web Search capability を使うが、検索結果から保持するのは canonical HTTPS URL、source kind、query provenance、順位だけとする。

## 原則

- Search snippet / title / description は候補発見専用で、論文の事実・metadata・原稿素材へ昇格させない。
- primary-source候補hostだけを固定allowlistで残す。
- PDFやarchiveを候補にせず、arXivは `/abs/`、OpenReviewは public `/forum?id=...` landing pageだけを受理する。
- 最大3 query、各search結果は先頭8 URLまで、返却候補は最大12件、全体20秒。
- 検索失敗時にOpenCodeや別providerへ暗黙fallbackしない。
- rankingは検索本文の意味判断ではなく、複数queryから同じURLが見つかった回数、source categoryの固定priority、初出順だけで決定する。

## Gate

`DOCICH_RESEARCH_PAPER_DISCOVERY_ENABLED=1` と `DOCICH_REPLY_WEB_SEARCH_ENABLED=1` の両方が必要。未設定時はnetwork 0。

実際のsearch backendは共通 `search_public()` の明示設定に従う。このmoduleがprovider、credential、endpointを選ばない。

## 次段階

発見されたURLはまだ「候補」であり、paper recordではない。次のprimary-source adapterで、選ばれたURLに対してtitle/authors/date/review status/abstract等を一次情報から取得・検証し、その後に `VerifiedWebBundle` と `research_paper_draft` へ渡す。

このsliceでは external API contract、PDF取得、production schedule、credential、実search、課金、配信開始を行わない。
