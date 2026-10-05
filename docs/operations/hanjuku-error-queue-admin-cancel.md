# 固定2 error queueの管理上取消（正規入口のコード準備、本番未実行）

## 目的と承認の境界

`docich.hanjuku_queue_admin_cancel` は、scheduled/manualの固定2 program queueを
履歴保持で管理上 `error` → `cancelled` にする専用helperである。既存の通常取消、
管理予約解除、FIFO・owner判定のguardは変更しない。旧予約解除とは別の操作であり、
このhelper自身は旧予約を解除しない。

現行queueの `requested_at` / `wait_deadline_ts` / `status` はrequest ID、generation、
runtime帰属を証明しない。`error` は枠取得後の例外でも待機中のregistry異常でも発生する。
ownerがterminalでも、該当queueの全過去実行・残存資源を証明できない。

このためapplyには、次の両方が必須である。

1. checkで得た、VM内に保持された固定2記録と全承認contextの完全なfingerprint
2. 対象2記録の管理取消と、過去の実行・資源解放が未知のままであることを了承した
   所有者の別途明示承認。コードでは `acknowledge_unknown_resources is True` が必須

通常の開発・レビュー・コード実装の承認や、旧予約だけの解除承認をapplyへ流用しない。
`all_resources_released=null` / `cancellation_authority=false` はcheck、apply、監査journalの
すべてで維持する。`cancelled` は管理上の分類であり、安全な非実行・資源解放の認定ではない。
新しいdiagnostics投影を受入条件にしない。既存のqueue/status投影が `cancelled` を示しても、
要求やruntimeの帰属・全資源解放を認定する根拠にはしない。

canonical `corner-rotation-operator.yml` とauthorizerに固定2入口
`check-admin-cancel-retro-queues` / `admin-cancel-retro-queues` を追加する。
actor/repository ID、protected main、vm-operations environment、同concurrency group、
immutable SHA、production HEAD/clean preflightを保持する。新credential、gateway OPS、
SSH経路、role、任意command入力は追加しない。既存owner-only `exec` のstdout-withheld契約を
そのまま使い、dispatch・配備・実queue操作はこのコード準備に含めない。
helperを診断collectorから呼び出すことは禁止する。context指紋はVM内の私的recordだけに保存し、
workflow input/output/env、GitHub logs/issues/artifacts、Slackには出さない。

## 正規入口と非秘密run handle

checkは6既存lockを使う限定checkを実施し、そのtokenと完全なcontext digest mapが一致する
こと、続くbounded再読取で変化しないことを確認して、VM内の固定private plan directoryへ
exclusiveにcheck recordを保存する。この後半観測は無lockでありatomic snapshotではない。
初回guarded checkの指紋一致と、execute内のlock下再照合によって変化を安全に拒否する。
checkはqueue/予約を書き換えないが、私的control recordを作成するためread-onlyとは呼ばない。

本人が使うhandleはcheckの公開GitHub run IDとattemptを合わせた `runID-attempt` だけである。
handleやrecord内binding fieldsは認証ではなく、既存owner認可を代替しない。実際の権限境界は
canonical workflow authorizer、environment protection、pinned SSH forced gatewayである。
recordは固定actor/repository/workflow/
main SHA、固定2対象の全context指紋、15分の期限へ束縛し、同handleの上書きを拒否する。
record16KiB上限、全経路no-follow、実行uid/0600、directory0700、単一link、durable installを
要求する。既存lockを自動作成せず、record以外のcontrol-state作成もしない。

executeには別runと `plan_handle`、`unknown_resources=acknowledged`、`confirm=production` が必要。
期待指紋や予約IDをworkflow inputへ渡さない。SSH直前のremote main再照合とVM内SHA/clean
preflight後、既存record inodeを実flockし、期限・binding・pathname inode・全contextを確認する。
元2 error記録以外へ変われば拒否する。planを一回限りclaimedへdurableに保存してから、
unknownを了承した対象限定queue helperを呼ぶ。claimed/完了/失敗handleは再利用できない。
旧fdを保持した競合callerはpathname inode不一致で拒否され、新inodeのcallerはclaimedで拒否される。

claim後の中断、transport不明、queue部分取消、claim/journal破損は安全停止として扱う。
同入口でpartial resumeや自動retryをしない。既存helperの承認済みsame-journal resume能力は保持するが、
その利用には別の明示管理判断が必要である。固定入口は既存queue journalを再利用しない。
複数checkが同じ元記録に対して用意されても、先の取消後に別planを実行すればcontext変更で拒否する。

GitHubに返すのは一致したwithheld gateway envelopeからの一般成否と操作種別だけである。
owner-only起動はGitHub logs/artifactsのprivate閲覧を保証しないため、暗号化artifactや新しい鍵、
任意log読取endpointを追加しない。実guard拒否理由・指紋・source stateは公開結果へ移さない。

実施順序はreview/tests/CI/main、承認された正規配備、fixed check、対象とunknownの個別了承、
同handleのfixed execute、状態確認、旧予約の既存admin-release check/別途承認済み解除である。
queue取消から予約解除・recover・startへ連結しない。旧予約解除後の通常timerでweatherが開始
し得ることを実変更前に説明し、本番実測なしで復旧済みと報告しない。

## 対象と拒否条件

- Soren rootの `tmp/state/docich_program_queue/retro_corner.json` と
  `retro_corner_manual.json` の2件のみ。callerからpath・queue keyを受け取らない
- 2件とも `error`。有限・非負のrequested/deadline、時刻順、既存admin metadata不在を確認
- 半熟manual reservationが既存管理解除と同じ限定形で、weatherがqueuedのままであること
- 2 ownerが欠落またはterminalで、今回の予約とは異なること
- registryは固定9 owner候補との文字列完全一致だけで参照。未知path、欠落登録owner、
  active/failed等の非terminal owner、今回の予約との一致は拒否
- canonicalを既存validatorで検証し、idleまたはready、candidate/previous/retiringなし。
  activeを保持する場合は現在のSorenだけ。他ゲームactiveは拒否
- 今回の予約の対応receiptは欠落。新receiptが現れた場合は拒否
- 予約、2 queue、2 owner、registry、canonical、該当固定registry owner、対応receiptの
  全raw bytesをfingerprintへ含める。JSON true/1、int/float、追加フィールド、空白変更も別context
- corner-rotation、通常/手動共有retro tick guard、retro通常/manual、program、game-switchの既存6 lockを同じ順で
  nonblockingに取得する。現mainのManualRetroCornerManagerは通常managerと同じ
  `retro-corner-tick.lock` を共有するため、存在しないmanual tick lockを推測・作成しない。
  共通rotation/manual経路はrotation lock、legacy scheduled経路のprogram_slot前のqueue書込は
  shared tick guardで排他する。lock欠落・非regular・symlink・busyは拒否し、checkはlockを作らない
- 全経路descriptor-relative no-follow、regular file、queue64KiB、他record256KiB、
  journal512KiB上限。duplicate JSON key / 非finite値 / malformed objectは拒否
- write直前とwrite間に、全snapshotとlock inodeを再確認。変更は上書き・rollbackせず拒否

## 履歴と中断

2ファイルを同時に原子的に置換することはできない。最初に、承認fingerprintを名前とする
単一の私的journalへ、元2記録の完全なbytes、予定置換bytes、context指紋、承認済み未知状態を
保存する。base64は秘密化ではなくbytes保持のためである。新journalは0600、専用directoryは
0700、exclusive installとfsyncを行う。既存directory/journalにも実行uidの所有・0700/0600・
regular単一link（journal）を要求し、不適合なら拒否する。既存権限はchmodしない。再試行でもjournal本体・directory・親directoryを
queue write前に再fsyncし、過去のfsync失敗で残った可読ファイルを永続化済みと誤認しない。原記録・監査を削除・pruneしない。

各queueの既存fieldsは保持し、statusと固定admin metadataだけを加える。journalの存在・内容を
確認してから固定2 queueを順次atomic replaceし、各write後のbytesと最終contextを再確認する。
owner、registry、canonical、receipt、rotation履歴・予約、weather queue、pause、runtimeには書かない。
start/stop/kill、資源probe、履歴走査、DB変更は行わない。

journal作成後や1件目置換後に中断した場合、同じfingerprintを指定した再実行だけが再開候補と
なる。非queue contextが当初と完全一致し、各queueが元bytesまたはそのjournalの予定置換bytesと
完全一致する場合だけ、残る同一計画を完了できる。journalはimmutableで、全完了後の同一再試行も
追加writeをしない。別fingerprint、変更された予約/owner/queue、破損journalは拒否する。
ただしexclusive journal installのhard link成功後・自分のtemp unlink前にhard crashした場合は
journalに2 linkが残るため、単一link guardで安全に拒否する。この停止点ではqueueは未変更で、
両原bytesは保持されるが自動再開はできない。helperはalias探索・削除をせず、個別の管理判断が
必要となる。すべてのcrash点から自動再開できるとは保証しない。

途中で外部writerが状態を変更した場合は、既に保存された履歴と部分結果を保持して停止する。
通常writerとの排他は既存lock契約に依存する。lockに従わないwriterのread→rename間の極短い
競合をPOSIX renameでcompare-and-swapする保証はない。二重before/after照合やjournalを
atomic snapshot、完全な資源証明、全2件トランザクションとは説明しない。

完了しても旧予約は残る。旧予約解除には別承認済み管理操作のcheckを再実施し、その結果と
条件に従う。管理解除後の通常tickが次コーナーを発火し得る点も実変更前に説明する。

## 将来の帰属記録（別提案）

将来のqueue producerではrequest ID、generation/runtime identity、開始と終了/cleanup receiptの
正確な対応、再利用時の履歴世代を記録する案がある。ただし、この変更は実装しない。
将来schemaを追加しても、既存error2記録の欠落した帰属を後付けで推測・認定してはならない。
