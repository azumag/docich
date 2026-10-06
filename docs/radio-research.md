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

## Native script consumer（既定off）

`docich.radio.script.generate_script(topic, queries, agents=..., env=...)` は、
このplannerの結果を既存のnative direct dispatchへ渡す純core。
`DOCICH_RADIO_SCRIPT_DIRECT_ENABLED=1`、上記routing flag、`DOCICH_ALLOW_REAL_AI=1`
がすべて必要。未設定はclassifier/search/generationすべて0。
callerは具体的な登録済み `*-api:` agentsを明示する。chain最大8件、CLI/local/旧aliasや
未登録modelの混在はclassifier前に拒否する。provider/billing設定の検証とcredential処理は
既存direct adapterが所有し、このcoreは新しいAPI transportを作らない。

topicだけをJEVへ渡し、Webが必要ならcaller-owned queryから得たVerifiedWebMaterialだけを
JSONデータとして生成promptへ渡す。検索snippetは入れない。低confidence、unknown、
取得失敗はhold。partialは不足をprompt/resultに保持し、未取得部分を架空補充しない。
分類・取得・生成は同じ最大45秒の予算を使い、native queue/fallbackも残時間へ制限する。
残予算がなくなった、または遅れて返った生成結果はscriptとして返さない。
生成promptはmessagesへのJSON再エスケープと登録model/provider wrapperの予約分を数え、
既存named direct APIの32KiB request上限に収まらなければ生成前に`input_limit`へ終端する。
collectorの最大4×8KiB bundleが全量入るとは保証せず、hash/provenanceを壊す暗黙切詰めをしない。

出力は既存 `ON_AIR_SCRIPT_START` / `===SUMMARY===` parser契約へ通す。
parserはdocich gitlinkで固定されたSoren commit `793990939dbfd262491846be52a804841d7aa56c`
の `lib/radio_parser.py`（blob `f455459926b2d0bb87eb82cd1e7a791907bd5c96`）を純関数化したもの。
必須marker時に到達しない無marker枝は移植せず、旧parser実行から採取した16合成goldenで
body/summary/selected-newsと必須marker拒否を照合する。本文が空でSUMMARYだけが長い応答は
生成coreが拒否し、旧parserの短文救済を音声本文の生成根拠にしない。
生成coreは既存final-output/onair guardも使い、100字以上・日本語・終端約物・summaryを確認。
receipt/hashは素材の取得根拠、parser/guardは出力形式と衛生の検査であり、生成した全主張の
事実検証やnative RADIO全体の互換検証にはならない。

返すのはメモリ上のtyped script/materialsのみ。`docich radio`の既存参照実行、常駐worker、
persona/templateの正本、state/history、音声・字幕・queueへのdeliveryはこのsliceで切り替えない。
既存RADIOのactive consumer接続・実provider canary・本番有効化は未実施。

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
