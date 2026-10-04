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
要求に対応するreceiptがある場合は終了（failed / rolled_back、cleanup_pending=false）を必須とし、
canonicalのactive/candidate/previous/retiring、owner、program待機記録、
当該runtimeのディレクトリ・ゲーム/agent window・adapter sessionの残存を確認する。
読めない状態、進行中receipt、半熟active、資源残存、lock競合は拒否する。
**ownerが見つからないことや古い時刻だけでは未実行と断定しない。**
receiptが欠落した場合は既存receipt一覧に進行中の半熟要求がないことと、
固定半熟ownerの最後のruntime identity・recovery_required=false・資源解放を追加確認する。
残存runtimeディレクトリは既存presenterのstopped記録（子process group解放済み）と
ゲーム/agent window・adapter session不在が揃う場合だけ解放済みとする。
不明な場合は履歴上の未実行を推測せず、取消を拒否する。

適用時はrotation ledgerのmanual_pendingだけを解除し、取消前の予約を非公開audit欄に残す。
天気のqueued_manual、履歴、cooldown、ユーザー休止、各owner/receipt/runtimeは保持する。
ゲーム停止、service再起動、復旧、即時tickは実行しない。
次の通常timerが天気の予約を処理するため、適用後も自然dispatchと実行結果を別に検証する。

今回の要求のreceiptがなく、固定ownerからの資源解放確認もできない場合は取消できない。
その場合は予約を保持し、実行・資源所有を証明する診断を先に揃える。
