# 配信コメントbatchのdirect Web research

配信コメントのJEV evidence scopeが `web` のみの場合に、OpenCode researchを起動せず、共通Web Search + WebBroker receiptだけで根拠を集める段階導入。

## Gate

以下をすべて満たす場合だけdirect batch経路を使う。

- `DOCICH_REPLY_WEB_DIRECT_BATCH_ENABLED=1`
- `DOCICH_REPLY_WEB_DIRECT_ENABLED=1`
- `DOCICH_REPLY_WEB_SEARCH_ENABLED=1`
- `DOCICH_REPLY_RESEARCH_ENABLED=1`
- `DOCICH_ALLOW_REAL_AI=1`
- JEV aggregate scopeが `web`

`DOCICH_REPLY_WEB_SEARCH_BACKEND=cloudflare` を明示すれば既存Cloudflare Web Search adapterを使う。provider/account/gateway/tokenの選択、credential handling、検索childの環境分離は既存 `search_public()` の契約を変更しない。

## 安全境界

- Search結果はURL候補の認可にだけ使い、snippet/title/descriptionを根拠にしない。
- 本文は既存 `WebBroker` で取得し、receiptのtext hashを再確認する。
- `verify_quotes()` に `question` indexを付けて渡し、全コメントに `web` 根拠が揃った場合だけbatchをreadyにする。
- direct経路を選んだ後の検索・fetch失敗からOpenCodeへfallbackしない。
- `code` / `web_and_code` はこのsliceでは従来research経路を維持する。
- 共通brokerの4 fetch上限を広げないため、direct対象はresearchが必要なコメント最大4件。5件以上はnetwork 0でfail closedする。
- queryは各256文字まで。既存 `project_research_batch()` のprivate-input検査を通す。

## Rollout

コードmergeだけでは本番有効化しない。productionではまず既存Cloudflare Web Search設定が利用可能であることを確認し、固定ops経路で上記flagを明示してcanaryする。新credential作成、課金設定変更、worker/restart、実searchは別受入とする。

次のsliceでRADIO active consumerも同じ共通Web素材経路へ接続する。