# Research-paper direct draft slice

Issue #1706 の非本番実装単位。既存 `verified Web material` と native direct LLM dispatch を使い、論文紹介コーナーの8章構造化レコードを作る。

## 入力

caller が所有する trusted metadata:

- title / authors / affiliations / field / publishedAt
- reviewStatus (`peer-reviewed` / `preprint` / `unknown`)
- DOI（任意）
- canonical HTTPS paper URL
- canonical HTTPS primarySources（最大8件）

加えて `VerifiedWebBundle` を受け取る。bundle は1〜3 query、最大4 materialの既存契約を維持する。material URL は trusted metadata の `primarySources` に完全一致するものだけを採用し、excerpt SHA-256 をconsumer側でも再確認する。

## 生成

`DOCICH_RESEARCH_PAPER_DIRECT_ENABLED=1` かつ `DOCICH_ALLOW_REAL_AI=1` のときだけ生成する。agent chain は explicit `openrouter-api` / `vercel-api` / `cloudflare-api` のdirect providerだけを許可し、OpenCode / CLI / local providerを混在させない。

label は `RADIO:paper-corner-draft`。モデルには検索・tool実行・URL選択を許可しない。verified excerptにない外部事実を補わず、review status、研究デザイン/データ強度、相関/因果の留保を保持するよう要求する。

モデル出力はJSON 1個だけで、次の固定field setを受理する。

1. `thirtySecondSummary`
2. `researchQuestion`
3. `priorProblem`
4. `novelty`
5. `experimentStrength`
6. `limitations`
7. `practicalUse`
8. `docichApplication`
9. `figureOrSimpleExplanation`
10. `applicationTargets`

unknown field、duplicate key、NaN/Infinity、空field、生成側が追加したURLは拒否する。citation URLはモデル出力ではなくtrusted metadataから決定的に保持する。

成功時は既存 `new_corner_runtime.render_overlay()` / `progress_frames()` / `validate_status()` が受理できる `corner=research-paper` recordを返す。

## このsliceで行わないこと

- 論文候補の外部取得・ranking
- arXiv/OpenReview/publisher APIの新規HTTP client
- PDF取得やWebBroker MIME境界の緩和
- production flagの有効化
- credentialの作成/変更
- 実provider canary
- overlay本番配線、音声再生、schedule登録

候補発見は次sliceで、既存Web Searchをdiscoveryに使いつつ、metadata/本文は一次情報sourceを優先する。検索snippetを論文事実へ昇格させない。
