# 半熟の手動予約だけを取り消す固定operator

`corner-rotation-operator.yml` の `check-cancel-hanjuku` / `cancel-hanjuku`
は、半熟の `retro_corner_manual.json` 予約が
`manual-request-needs-resume-or-recovery` で待機し、天気が次枠予約にある場合だけを扱う。
オーナー、保護されたmain、production確認、VMの配備SHA一致を既存operatorと同じ経路で確認する。
任意コマンド・対象ゲーム・ファイルパス・service入力は受け付けない。

取消前に独立レビューを完了し、正規main配備後の診断から
`corner_rotation.manual_pending_fingerprint` を取得して対象予約を確認する。
`cancel-hanjuku` の `expected_reservation` にその64桁値を渡す。
指紋が変わった予約には適用しない。`check-cancel-hanjuku` は状態を書かず、
判定結果は従来のVM非公開operation logに残る。

rotation・両retro owner・共有program・game-switchの既存lockを非待機で保持して再照合する。
要求に対応する終了receipt（failed / rolled_back、cleanup_pending=false）を必須とし、
canonicalのactive/candidate/previous/retiring、owner、program待機記録、
当該runtimeのディレクトリ・ゲーム/agent window・adapter sessionの残存を確認する。
receipt欠落、読めない状態、進行中receipt、半熟active、資源残存、lock競合は拒否する。
**ownerが見つからないことや古い時刻だけでは未実行と断定しない。**
receiptが欠落した場合は、別要求の終了receiptや古い固定ownerの資源解放を
今回の要求が終了した証拠に代用せず拒否する。
残存runtimeディレクトリは既存presenterのstopped記録（子process group解放済み）と
ゲーム/agent window・adapter session不在が揃う場合だけ解放済みとする。
不明な場合は履歴上の未実行を推測せず、取消を拒否する。

適用時はrotation ledgerのmanual_pendingだけを解除し、取消前の予約を非公開audit欄に残す。
天気のqueued_manual、履歴、cooldown、ユーザー休止、各owner/receipt/runtimeは保持する。
ゲーム停止、service再起動、復旧、即時tickは実行しない。
次の通常timerが天気の予約を処理するため、適用後も自然dispatchと実行結果を別に検証する。

今回の要求のreceiptがない場合は取消できない。
その場合は予約を保持し、実行・資源所有を証明する診断を先に揃える。

正規read-only diagnosticsの `corner_rotation.manual_pending_receipt` は、
固定半熟予約のUUIDに対応するreceiptだけを読み、有無・読取可否・固定status/operation・
要求/対象/終了result一致・cleanup_pending・予約後の更新かをenum/booleanで返す。
終了result一致とcleanup_pending=falseの場合だけ既存operatorと同じruntime検証と
資源解放確認を行い、`runtime_identity_valid` / `runtime_resources_released` を返す。
識別子、runtimeパス、receipt本文、tmux出力、例外本文は公開しない。
`observed=false`・項目欠落は未観測、`observed=true, present=false` は今回の読取時点の欠落であり、
過去の未実行を意味しない。読取不能・不正identity・probe失敗は資源解放をunknown/nullにする。
`runtime_resources_released=true` もlockを保持しないスナップショットなので取消許可ではない。
canonical・owner・program待機記録・予約指紋を含む全条件は固定operatorがlock内で再確認する。
診断の準備はレビュー済mainを正規配備した後に `vm-operations` の
`operation=diagnostics, target=production, ref=main` で行う。
receipt欠落のwaiting予約には既存 `recover-failed` のrecovery_required前提がなく、
`recover-runtime` も現在の半熟ownerとcanonical identity一致が必要なため、証拠なしに代用しない。

追加の `corner_rotation.manual_pending_evidence` は同じ固定予約のpositive記録だけを調べる。
読取元は固定EventLog、当該UUIDのreceipt、両retro owner、canonicalだけ。
開始時に対象予約のfingerprint・selected_at、確認終点、ログidentityと初期サイズSを固定し、
EventLogの先頭から `[0,S)` を64KiB×最大256ページ・16MiB・論理処理予算10秒で一度走査する。
Sがbyte/page上限を超える場合は読み始めず終了する。追記を追いかけず、開き直し・自動再試行はしない。
行サイズ64KiB、一致行256、runtime8件の上限を保持し、上限到達でログ走査を終了する。
`log.initial_size_bytes` / `required_pages` / `pages` / `bytes_read` / `eof_reached` / `end_reason` を返す。
EOF、size/page/line/match/generation上限、partial record、不正行、source変更、予約変更、予算切れを区別し、
`log.scan_complete` は保持されたファイルの当該snapshot全体を読めたことだけを表す。
ページ前後に固定ledgerの予約fingerprintとログのidentity・size・mtime/ctimeを再照合する。
置換・縮小・追記等の変更や予約の変更/読取不能、時間切れは終了し、資源判定をunknownへ戻す。
`checkpoint_status` は固定enumで再照合結果を示し、fingerprintの元データやログidentityは返さない。
一度検出したcheckpoint不一致・読取不能・予算切れは保持し、その呼出内でstableへ戻さず資源probeを止める。
ログ未完走やruntime保持上限では、未読行の矛盾を排除する独立証明がないため各世代の資源解放もunknownとする。
`eof_reached` はbyte境界への到達だけを表し、行評価・checkpoint検証を含む `scan_complete` と区別する。
10秒はmonotonic clockに基づく協調的な処理予算で、metadata後のread直前にも再検査する。
予算切れを検出した後は追加stat・資源probeを行わない。一度開始した同期regular-file I/Oは
O_NONBLOCKでも期限内の中断を保証しないため、wall clockのhard deadlineが10秒という保証ではない。
既存gatewayのcollector subprocess timeoutは別途60秒、workflow上限は20分。
通常のhung collectorは上位60秒timeoutで失敗扱いとなる。新しいprocess envelopeは導入しない。
対象時刻は固定selected_atから確認終点までだが、行の時刻順を仮定した早期終了はしない。
運用取得は独立レビュー・正規配布後の一回に限定し、上限や変更で未完了なら再実行・上限追加せず保守判断へ進む。
予約後の一致するrequested/accepted/queued/terminal記録と、そこから得たnumeric generationを返す。
runtime/lease/request識別子、ログ本文、argv、env、ファイルパス、例外本文は返さない。
同一generationの異なるruntimeやownerのlease矛盾は資源解放をunknownにする。
保持上限後の矛盾も判定に反映し、ownerのbot_runtime_idとidentityのruntime_id不一致・欠落もinvalid/unknownとする。
資源probeは検証されたruntimeだけに既存の読取専用チェックを使い、canonicalで追跡中なら解放扱いしない。
tmuxは固定formatのwindow/session一覧だけを最大16KiB・各300msか残り予算で読み、失敗はunknownにする。

EventLogはbest-effortで、receipt削除のtombstoneもないため、保持行の完走や一致行の欠落から
未dispatch・全歴史世代網羅・全資源帰属を証明しない。`request_generation_coverage=unknown` と
少なくとも1件の `resource_attribution_unknown` を残し、`all_resources_released=null`、
`cancellation_authority=false` を維持する。観測できた全runtimeの解放がtrueでもこの不足は消えない。
この投影は取消条件を追加・緩和せず、現在の終了receipt必須契約も変更しない。
完走しても証拠不足なら予約を保持し、行政的な解除は別承認scopeの判断とする。
対象予約だけの解除は直接配信を停止しないが、未帰属資源・遅延処理の不確実性と後続timerのdispatchが残る。
帰属不能資源を解消する別保守では現在ゲームや配信の停止が必要になり得るが、停止だけで欠落receiptや過去履歴は復元されない。

## 別承認による管理的解除

`check-admin-release-hanjuku` / `admin-release-hanjuku` は終了receiptに基づく取消とは別の管理操作。
固定診断でも要求の実行・全資源帰属を証明できず、対応receiptが欠落した予約について、
オーナーが「過去の実行・資源はunknownのまま、この対象予約だけ解除する」と明示承認した場合だけ使う。
通常の `cancel-hanjuku` のreceipt必須条件、診断の `cancellation_authority=false` は変更しない。

親の独立レビュー・CI・protected mainへの統合・正規配布後、同じcanonical operatorを使う。
両操作で `confirm=production` と、承認対象の `manual_pending_fingerprint` を
`expected_reservation` に指定する。指紋はUUID・selected_atを含む予約全体を束縛する。
まずcheckを実行し、成功した対象に対してreleaseを一度だけ実行する。結果はVM非公開operation logに残る。
任意パス・shell・ゲーム入力は受け付けない。既存gatewayのexec搬送は固定reviewed scriptのみ使用する。
workflowのstatus照合に加え、gateway deployment lock内の固定scriptでもVM HEADとtracked cleanを照合する。
VM helperの非ゼロexit・SHA不一致はworkflowも失敗とし、原記録を公開しない。

全writer lockを非待機で保持し、waiting理由・automatic pending不在・weather予約保持、
両retro ownerが不在または別要求のterminalであること、program待機/active owner、
安定canonicalで半熟active/candidate/previous/retiringがないこと、対象receiptの欠落を再確認する。
新receipt、予約指紋の変化、live/matching owner、lock競合、読取不能、書込前のsnapshot変化は拒否する。
checkはEventLog再走査・資源probe・state作成を行わない。

releaseはrotation ledgerをatomic置換し、対象 `manual_pending` だけを解除する。
待機理由を `manual-request-admin-released`、error_kindをnullにし、元の予約・時刻・管理解除理由と
historical coverage/資源帰属unknownを追記audit `manual_admin_releases` に保持する。
既存history・cooldown・weather/他予約・receipt・owner・canonical・runtime・ユーザー休止は変更しない。
receiptの捏造やfailed/successへの書換、cancel/recoveryの代用、tick・start・kill・service再起動は行わない。
二度目は同じ予約がないため拒否し、自動retryはしない。書込後のtimeout等で結果不明なら再実行せず診断する。

解除後は通常timerによるweatherの自然dispatch、対応receiptとowner終了、元ゲームへの復元を
別途read-only診断で確認する。ledger解除の成功だけでweather開始・終了・復元済みとはしない。
