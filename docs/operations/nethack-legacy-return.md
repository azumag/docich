# NetHack旧形式のfailed rotation再照合

旧NetHack stateで`previous_game`と復元元leaseの永続的な記録が欠落し、
その後に別の通常switchでSorenへ復帰した場合だけを扱う、owner承認済みの
legacy証拠契約。通常のsource lease fencingやR1 replayとは別の経路である。

正規の`nethack-corner recover-failed-rotation`から選ばれる。対象はschema 1で
`previous_game`キーがなく、`finish_reason=terminal`のautomatic failed owner。
manual corner、in-place rollback、保存済みR1/cleanup intent、source provenanceを
持つ新形式のreceiptへは適用しない。任意のrequestやtargetを引数に取らない。

## 必須の証拠

rotation → NetHack tick → NetHack owner → shared canonicalの既存lock順序を守り、
同じdispatched NetHack予約を確認する。次を全て読み直して照合する。

1. ownerの元runtimeにある、元復帰要求R0と一致する`ended`境界記録。
2. R0の不変rolled-back receiptが示すreplace-modeの復元世代。
3. その世代に唯一存在するruntimeの、別成功要求Rと一致する`ended`境界記録。
4. RのSoren succeeded receiptと、現在のSorenのgame/runtime/generation/lease。

設定されたpersistent runからplayerを特定できることを前提に、boundaryのplayer、
schema、runtime、generation、request、時刻を検証する。
receiptとcanonicalは既存schema検証に加え、厳密な整数型や完全なresult一致を検証する。
現在のcanonicalはready、driver/candidate/previousなし、retiring空、cleanup完了で
なければならない。unknown/active manual owner、時刻逆行、証拠の矛盾も拒否する。

これは過去のsource leaseが確認できたという主張ではない。
この限定legacy契約では2つのrequest付き終了境界を歴史的sourceの代替証拠とする。
現在のSorenの完全なleaseとsnapshotは必須であり、将来の通常経路には広げない。

## 保存と再実行

証拠JSONは設定されたstate root以下の各directory/fileでsymlinkを拒否し、regular fileに限る。
JSONは64 KiB以下、duplicate keyを拒否し、復元runtime探索は8192 entries以下。
内容の変更は、owner・ledger・2 receipt・2 boundary・manual ownerのdigestと
canonical全体のdigest/revisionで検知する。固定の拒否理由だけを返す。

shared canonical lockを保持したまま、`legacy_return_reconciliation`のprepared記録を
正規state writerで保存し、もう一度証拠を確認してcornerをinterrupted/committedにする。
途中失敗後は同じ証拠、reservation、snapshot、prepared timestampだけを再使用する。
証拠が変わればfailedのまま拒否し、新しいrequestや代替leaseを作らない。
committed後・rotation ledger確定前の再実行は同じ証拠を再確認して成功を返す。
rotation側のNetHack recovery guardも同じcommitted証拠を再確認し、shared canonical
lockをledger保存まで保持する。この間の通常coordinator変更は許可されず、
両段階の間にsnapshotが変わればpendingはlatchedのまま拒否する。

元receipt、save、runtime、pause、run履歴、元の開始/完了時刻は変更しない。
TTY観測、coordinator recover/switch、stop、kill、restart、R1 replayは呼ばない。

## 自動rotationへの影響

既存の`corner-rotation recover`は同じinterrupted予約を履歴へcommitし、pendingを
空にしてreadyへ戻す。このcommit中に新しいゲームを起動しない。automaticの
完了履歴は再照合時刻で追加され、NetHackのcooldownを維持する。

**既存gatewayの`recover-corner-rotation`操作は最後にrotationサービスを再起動する。**
queue設定では次tickで別の適格コーナーが始まり、通常の境界待ちを経てSorenから
切り替わる可能性がある。pause/history/cooldownや通常の切替条件は維持する。
コード・テスト・PRの承認だけでは本番解除・サービス再起動を行わない。
運用実行は最新状態の確認と、その実行範囲のowner承認を別途必要とする。
