# RADIOのJEV判定と共通Web素材

Issue #1767、旧PR #1795のplannerを、main統合済み #1828 の
`web_material.py` collectorへ移植する小さなslice。
旧 #1793 の `radio/contracts.py` / `radio/material.py` は再導入しない。
既存COMMENT/PAPER/Web Search backend/direct providerは変更しない。

## preparation API

`docich.radio.research.plan_and_collect(topic, queries, env=...)`:

1. `DOCICH_RADIO_RESEARCH_ROUTING_ENABLED=1` と `DOCICH_ALLOW_REAL_AI=1` がある場合だけ、
   topicだけを単一JEV primary routeへ渡す。未設定はdisabled。
2. 固定 `api_only/web` と有限confidence .80以上だけを受理する。
   timeout/low confidence/unknown/provider failureはholdし、api_onlyへ変換しない。
3. api_onlyはWeb collection 0。webはcallerが用意したquery文字列だけを、
   既存 `collect_verified_web_material` へ渡す。JEVはquery/URL/provider/modelを選べない。
4. 共通collectorのreceipt由来 `VerifiedWebBundle` だけを受理する。
   型、canonical URL、64hex hash、UTF-8 excerpt上限とexcerpt hash、query indexesを確認。
   検索snippet/title/descriptionや旧DTOを本文根拠として受理しない。
5. `materials`を返して終了する。台本生成、TTS、配信queue、OpenCode、runtime操作は開始しない。

分類と取得の共通deadlineは最大45秒。JEVは最大1.5秒/1回、残り時間をcollectorへ渡す。
JEV開始前の残予算がtransportの最低50msを下回る場合は、呼出さずtimeoutへ終端する。
queryは共通collectorと同じ最大3件/各256文字、取得attempt最大4、素材最大4、
各excerptは最大8KiB。重複queryを正規化し、素材の `query_indexes` は正規化後の0-based index。
不足queryまたはcollector partialは `status=partial` として、検証済み素材を保持する。
`ok`は利用可能な資料があることを表し、topicの完全な解決・事実の真偽を保証しない。

`material_collector` / `transport` / `clock`の注入はtrusted caller/test用。
WebBroker receiptの取得・full-text hash再検証は共通collectorが所有し、
plannerのDTO検査だけを実取得の証明とはしない。

## 利用条件と今回の非対象

Cloudflare Web Search adapterは既にmainにあり、backendを明示した場合だけ使う。
既存Cloudflare account/gatewayとGateway creditsまたはstored BYOK、
Workers AI Read + AI Gateway Readが必要。今回は認証・課金・本番設定を変更しない。

現在の公式表はCeramic $0.25、Exa $7、Linkup $5 / 1,000 searches。
provider表はExa ZDR=No、Ceramic/Linkup=Yesで、launch changelogの全provider ZDR記載と
食い違う。ExaのZDRを保証しない。provider ZDRとGatewayのlog保存は別であり、
公開queryのみに制限する。Gateway creditsの購入には5% feeもあり、追加markupなしを
全費用0とは扱わない。`byokAlias`を明示すると未設定時は400でcredit fallbackしない。
省略時はdefault provider key、そのkeyがなければGateway creditsが使われる。

根拠: [公式schema](https://developers.cloudflare.com/web-search/how-to-use/)、
[provider料金/retention](https://developers.cloudflare.com/web-search/providers/)、
[Gateway logging](https://developers.cloudflare.com/ai-gateway/observability/logging/)、
[Gateway billing](https://developers.cloudflare.com/ai-gateway/features/unified-billing/)。

本sliceは既存RADIO実行へのactive cutoverを行わない。後続consumerがcaller-owned queryと
検証済み素材を既存bounded direct generationへ接続する必要がある。
JMA/weather、価格feed、GitHub/code、VM/game/OBS stateは専用一次API/runtime/code経路を保持。
実search/JEV/生成/API canary、credential作成・権限変更、production flag/restartは別の受入作業。

## Runtime変更checklist

新service/worker/queue/persistent stateは追加しないため、runtime registry/worker health/queue
登録を変更しない。plannerはraw topic/query/生成本文/credentialをlogへ出さず、固定statusと
confidence/verified DTOだけを返す。既存diagnosticsを本番受入の証明として扱わない。
合成回帰はzero-call disabled/hold、単一route、取得hash/provenance、partial、deadline、
malformed DTO拒否とsearch失敗からCLI/生成へ昇格しない経路を検査する。
