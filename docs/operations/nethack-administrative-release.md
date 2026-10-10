# NetHackの独立した管理解除

automatic NetHackのfailed予約について、現在の資源不在を独立に検査し、
別途承認された同じ予約だけを一度管理解除する固定操作。
通常のR1/source lease照合と旧形式のlegacy例外は変更しない。
歴史的なR1やleaseを捏造せず、結果と監査に`history_authority=false`を残す。

**実在する予約がこの操作の条件を満たすかは未確認である。**
合成fixtureで閉じた資源集合とtransactionを検証した開発変更であり、配備、
本番census、controllerの参加確認、実解除、rotation再開は別の承認と実測を要する。
共有worker/daemonの正の帰属契約が欠ける本番では、正常配信中でも拒否され得る。
その拒否を復旧完了とは扱わない。

## 現在資源の検査と限定したspawn fence

検査はLinuxのstate owner UIDについて、PID/birth、親、3つのownership tag、
実行ファイル、cwd、引数digest、boot IDとPID namespaceを有界に観測する。
2回の全contextが同一でなければ拒否する。読取不能、PID増減、namespace差、
部分tag、未知job/child、矛盾も拒否する。他UIDについて環境・引数は読まない。

許可するのは検査自身の実際の祖先、現在のcanonical Soren identityの完全な
3 tagを持つprocess、およびtmux自身への固定queryと現在PID/birth/executable/argvが
一致した通常のtmux server一つだけである。現在tagでも引数にNetHackがあるprocess、
別socketのtmux、他paneや共有serverの子を包括的に許可しない。
実行ファイル名やplain PIDfileを根拠に共有daemon/workerを許可しない。

固定のローカルDocker socketに対するrunning container一覧も必要である。
一覧が空でなければ拒否する。これは別UIDの孤立したcanary containerを、UID censusの
対象外という理由で見逃さないためである。任意Docker host/context、container名に
よる共有許可はない。Docker不在、接続拒否、不正/過大な応答も拒否する。

既知の旧NetHackの両window/sessionはstrictな不在確認を要する。
正常stopで消えるpresentation/tilesのPID・birth・membersや過去のimprove jobは
必須にしない。残るrecordは承認contextへ含めるが、現在資源の帰属証明には使わない。
過去の出生時刻の復元を解除条件にしない。

`locks/nethack-resource-fence.lock`は恒久的なNetHack専用lockである。
以下の入口がshared flockを持ち、管理解除はexclusive flockを持つ。
競合時は待機せず拒否する。producerはspawnだけでなく、次の表の範囲を保持する。

| 入口 | shared fenceの保持範囲 |
| --- | --- |
| NetHack coordinator `materialize_runtime` / `start_agent` | 正規のgame/agent生成 |
| tiles supervisor `run` | fallbackの再生成と最終cleanupを含む全寿命 |
| daily improvement / canary | 実験の生成から子のcleanup完了まで |
| 独立host container launcher | 固定production configのstateに対しcontainer cleanupまで |
| NetHack resolver `evaluate` / `improve_once` / `run_daemon` | activity marker作成から評価・cleanupまで。daemonは全寿命 |

他ゲーム、Sorenの共有worker、start-all/controllerは変更しない。
新コードがdiskにあることだけでは、既存の実行中controllerの参加を証明しない。
未知または未参加のproducerがある場合、条件成立を主張せず拒否を維持する。
本番実行前には現在の全NetHack起動入口がこの契約に参加していることが必要である。
参加確認や共有帰属の不足を、別のPID/command入力や許可名の追加で回避しない。

tmux teardownでは、snapshot時に他paneがゼロでもserverを動的な保護rootに含める。
後から作られた共有paneと子を保護し、停止対象pane自身とその子は回収対象に残す。
これは管理解除後に使われる既存teardownの共有基盤保護であり、管理解除helperは
teardownやsignalを呼ばない。

## 対象を固定する条件

- schema 1、`previous_game=sorengame`、`finish_reason=terminal`のautomatic failed owner。
- sourceなしのdispatched NetHack予約とautomatic reservation履歴。
  manual pending/queued manual/manual inboxは拒否する。
- 元の復帰要求R0のrolled-back receipt、replace-modeの復元世代、
  request付きの2つのNetHack終了境界、別の成功要求R。
  recovery/cleanup intent、新形式source付きreceipt、in-placeは対象外。
  R0の過去の`cleanup_pending=true`は現在の独立検査で扱い、元receiptを変更しない。
- Rの完全なSoren game/runtime/generation/leaseと現在のcanonical全体。
  ready、driver/candidate/previous/deadlineなし、retiring空、時刻整合を要する。
  成功したR自身のcleanup pendingは許可しない。
- 他corner owner、program registry、NetHack improvement/resolver marker、
  program queue全件、receipt FIFO全件のterminal状態。
  未知queue名、待機/稼働、不正JSON、link/device、上限超過は拒否する。
- 既存の8 writer guardの安全なdev/inode/UID/mode。
  missing/unsafe/busy guardは作成や修復せず拒否する。

JSONは各64 KiB以下、receipt FIFOは1024件/合計4 MiB以下、process inventoryは
8192件以下、container一覧は256件/64 KiB以下とする。
checkはstate作成、lock取得、signal、TTY入力、service操作を行わない。
project bytecodeの書込みと既存cache読込みも無効化する。
import前にhelperのHEAD bytesとtransitive project importsのclean状態を確認し、
未配備/ソースdriftは新projectionだけを`code_unverified`で拒否する。
script/FD255の一致を、既に読み込まれたBash関数の証明に使わない。

## 承認、再検査と保存

fixed checkは条件が成立する場合だけfingerprint、code SHA、10分境界のexpiryを返す。
有効時間は次の境界までの残り時間で、最大10分。
owner、ledger、canonical、receipt/boundary、manual state、残存record、queue、
process identity/digest、boot/namespace、既存writer guardのinodeをfingerprintへ含める。
生引数、環境値、player、path、request/runtime値は公開projectionへ出さない。

releaseは次の既存guardを順に非blockingで取得する。
`corner-rotation.lock`、`corner-manual-queue.lock`、automatic/manualそれぞれの
`nethack-corner[-manual]-tick.lock`と`nethack-corner[-manual].lock`、
Sorenの`docich_program.lock`、`game-switch.lock`。
その内側でNetHack専用fenceをexclusiveに保持し、同じfingerprint/SHA/expiry、
2回の全context、commit直前の実clockの失効/逆行を確認する。
新しい専用fence fileだけは固定producer protocolとして作成可能で、削除・差替えない。

予約と監査の保存先は`corner_rotation.json`一つである。
atomic replacementで同時に、`nethack_admin_releases`監査、当該pendingの消費、
ready状態、completion履歴を保存する。
元owner、receipt、終了境界、canonical、save、pause、既存履歴は書き換えない。
監査はowner/context digest、exact reservation、承認情報、実解除時刻、元reason/error、
`history_authority=false`を保持する。
NetHack adapterは一致するcommitted監査と履歴があるautomatic ownerだけを
interruptedとして観測する。生ownerはfailedのままで、manual ownerには適用しない。
新run、owner変更、不正監査、同じ未消費pendingはこの観測例外を継承しない。

atomic保存中の例外は`persistence_unconfirmed`として成功扱いしない。
同fingerprintの再試行はcommit済みなら読取で`already-admin-released`を返す。
失効後の再試行は既存commitの確認に限り、新たなledger書込みを許可しない。
helperはゲーム開始、cleanup、restartやrotation tickを実行しない。
動いているtimerの次のtickには通常の既存選択規則が適用されるため、実解除の承認は
その影響を含む必要がある。

## 配備前の条件と正規操作（本番未実行）

1. 同じHEADの独立レビューと必須CIを確認し、protected mainからの正規配備を別途承認する。
   root-owned gateway、SSH認証、権限、ネットワーク設定は変更しない。
2. 配備済みコードと全NetHack producerのfence参加、現在Soren/共有基盤の保持対象、
   全UID censusとcontainer一覧の完全性を確認する。共有帰属/参加がunknownなら解除しない。
3. ownerのprotected-main固定workflowで`check-admin-release-nethack`を実行する。
   `confirm=production`。checkに予約/expiry inputを渡さず、既存read-only diagnosticsの
   `nethack_admin_check`固定projectionを読む。
4. refusedなら固定理由と不足契約を本人へ返す。旧NetHackが実際に残る場合は、
   現在の正の所有証明と保存/終了境界に基づく保全付き対象stopを別に設計・承認する。
   このPRは任意stop、共有worker/controller入替えや未登録process停止を実装しない。
5. eligibleなら対象、timerへの影響、fingerprint、expiry、deployed SHAを本人へ示し、
   実解除を別途承認する。同じSHA・未失効fingerprint・expiryで`admin-release-nethack`を実行する。
   固定private event inputをquoted captureし、既存owner-only execへ固定scriptを渡す。
6. 固定結果と実機状態を照合する。拒否/timeout/不明な保存結果を成功扱いせず、
   rotation再開と実測までを別々に報告する。

既存automatic failed ownerのstopはnoopであり、manual stopは別のmanual ownerが対象、
canonical recoverは追跡中のactive/candidate/retiringが対象である。
これらは未登録の旧NetHackに対する保存境界付きstopを代替しない。
共有資源の停止・controller入替えが必要なら、対象と配信への影響を本人へ提示してから
別の仕様へ進む。本番の個別値や生ログをPR/公開Actionsへ貼り付けない。
