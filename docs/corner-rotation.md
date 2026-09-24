# 全cornerの24時間rolling rotation

## 構造とcatalog

`config/docich.soren-live.toml` の `[corner_rotation].corners` がproduction登録の正本。
各レトロゲーム・PAPER・メリケンを同列の項目として登録する。レトロという親枠はない。
既存設定から移した9件には半熟英雄も有効登録する。ただし半熟英雄はVM限定ROMのため、
ROM/core/adapter適格性を通った項目だけを有効数Nに数える。ゲーム設定の無効化、実行ファイル不足、catalogの
`paused=true`、`state_dir/corners/<id>.paused` を反映し、Nを固定しない。

`adapter="game"`の項目には`target_matches`（整数1〜100）を任意指定できる。
省略時は従来の`retro_corner.target_matches`（既定3）を継承する。
productionの`nsnake`は1試合とし、開始以降のscorelog保存を確認して既存の終了・復帰へ進む。
時間上限、試合境界待ち、次slotの間隔とcooldownは維持する。試合数はNに加算しない。
Moon Buggyに未評価の改善候補がある場合は、次の枠をABBAの4試合（途中終了なら残り試合）にし、
各試合の開始時にbaseline/candidateの重みを固定する。scorelogには腕と重みSHA-256を残す。
4試合後、各腕2試合の平均スコアが高い方を採り、同点はbaselineを維持する。候補は
headless評価の点差だけでは棄却せず、比較が完了した改善ジョブでのみ昇格する。
試合数を扱わないPAPER/メリケン/専用NetHack adapterへの指定と不正値は設定エラーとする。

`corner_catalog.py` は登録検証、`corner_rotation.py` は選択・時刻・履歴・予約、
`corner_adapters.py` はcorner固有managerの構築と実行、
`CornerExecutionCoordinator` は共通program slotの排他を担当する。
実際の開始・境界待ち・停止・子プロセス解放・復帰は既存`GameSwitchCoordinator`へ委譲する。
スケジューラにgame/PAPER/メリケンの選択分岐や固定時刻優先はない。

PAPERは`PaperCornerManager`の逐次生成・発話完了待ちを維持する。catalogと実行stateの
`live_eligible=false`を固定し、PaperBroker、仮想約定、取引側の安全ゲートは変更しない。
ニュースや理論値から実注文へ接続する経路は追加しない。
メリケンは既存のliveness監視と声、NetHackは既存の保存境界を引き継ぐ。

JEVは自動有効化されていない手動のplayer-policy試験であり、従来から定期cornerではない。
`config/market-paper.toml`の株式/FXも現行設定では双方無効。これらの有効化は本変更に含めない。
新adapterが必要な項目を未対応名でcatalogに追加した場合は起動時に拒否する。

## dispatch policy（`schedule_mode`）

- `interval`（既定）: 目標間隔は常に86400/N秒。毎tickに実効Nを再評価する。N=0なら待機する。
  遅延やcornerの長さによってN回/日を保証しない。取りこぼしは1回にまとめ、過去slotを連発しない。
- `queue`: 間隔アンカーを使わず、共有スロットが空き次第、rolling cooldown外の候補を
  決定論的順位（seed・slot・corner IDのSHA-256）で連続発火する。`next_due_at`は
  「次に発火可能な時刻」の意味になり、候補が空のときだけ最も早いcooldown明けへ再アンカーする。
  発火は常に1本ずつで、重複・並行起動はしない（pending・共通program slotは両modeで同じ）。
- `cooldown_hours`（既定24）は同一cornerを再選択できるまでのrolling窓。小さいほど連続性が上がる。
  どのmodeでも予約・実開始・終了・手動使用をcooldownへ数える。
- queue modeの実効間隔は `max(cooldownが許す範囲, 直前cornerの所要時間 + 改善レーンの待ち)`。
  24h cooldownを維持する場合、eligible N件を消化した後は最も早いcooldown明けまで待機する。
- queue modeでは次のcornerは前の改善ジョブの終端を待たない（`resources_released`の
  改善待ちを外す）。改善ジョブは `locks/corner-improve-lane.lock` の共有レーンで直列化し、
  同時実行を1本に制限する。レーン待ちは1800秒で打ち切り、取得できない回は
  `status=skipped` / `reason_code=lane-busy` をdurable recordに残す。corner本体と
  次の発火は止めない。interval modeは従来どおり改善ジョブの終端を待つ。
- 改善ジョブが共有stateの上書きと競合しないよう、retroのspawnは確定済みの
  `started_at` / `ends_at` をepoch秒でjobへ明示する（`--started-at` / `--ends-at`）。
  次コーナーが先にstateを書き換えても、jobは自分のコーナー期間だけを評価する。

## 時間と重複排除（interval mode）

- 目標間隔は常に86400/N秒。毎tickに実効Nを再評価する。N=0なら待機する。
- 選択候補は有効cornerのうち、予約・実開始・終了・手動使用が直近24時間にないもの。
  終了時刻も使用に数える保守的な契約とし、長時間枠・遅延開始でcooldownが短くならない。
  24時間ちょうどで再び候補になる。したがって24/Nは目標であり、遅延やcornerの長さによって
  N回/日を保証しない。候補空は明示待機し、重複で穴埋めしない。
- 初回はdue。既存retro履歴があれば引き継ぐ。取りこぼしは1回にまとめ、過去slotを連発しない。
  次回は実際に受理/開始したslotから24/N。queuedの遅延開始が分かればanchorを更新する。
- cadence上はdueでも全eligible cornerがrolling cooldown中なら、最も早くcooldownが解ける時刻へ
  `next_due_at`を再アンカーする。過去のnominal時刻を保存し続けず、毎分tickはその時刻に到達した
  次の候補を一度だけ選ぶ。これはcooldownを短縮したり、同じcornerを重複起動したりするものではない。
- 永続seed、slot番号、corner IDのSHA-256順位で候補を選ぶ。登録順に依存せず、同じseedと
  clockで再現可能。seedは初回だけ生成し、再起動による再抽選をしない。
- 時刻逆行は以前の観測時刻へ戻るまで待つ。観測間隔が24時間を超えた場合は、長期停止と
  大幅時計変更を区別できないため24時間隔離する。clock/seedはテストへ注入できる。

## 永続化、キュー、所有権

`state_dir/corner_rotation.json` schema v1にseed、slot、適格性、履歴、next_due、
last_seen、pending、request UUID、結果をatomic writeする。
`locks/corner-rotation.lock`を実行/復帰まで保持し、timerの重複tickは即座に待機する。
選択をside effectより先に記録する。program境界やFIFO待ちではpendingを消さず、
同じrequest UUIDを既存game-switch receiptへ再投入する。

### latchとoperator復旧（`status=recovery_required`、#986）

予約後の実行が例外で終了すると、ledgerは `status=recovery_required` /
`reason=execution-or-state-unverified` / `error_kind=<固定enum>` とlatchされ、
`tick()` は冒頭で即returnする。例外本文はstateに書かない（provider出力やcredentialを
含み得るため）。latchは自動では解けないfail-closed契約で、次の自動開始も手動startも拒否する。

復旧は固定operation `recover-failed`（`ops/vm_actions/recover_corner_rotation.sh`）だけ:

1. `bin/docich --config ... corner-rotation recover` がlatchを解決する。
   - adapter観測に同じrequestの**terminal**があれば、それを完了としてcommitする
     （request identity・history・cooldownを保ち、**二重起動しない**）。
     自動予約は `pending`、手動予約は `manual_pending` を同じ規則で解決し、
     手動のcompletedだけ `manual-completion` の履歴行を足す。
   - そのrequestが**一度も起動していない**自動予約なら、ledgerは `waiting`/`execution-pending`
     のまま予約を保持して戻す。以降は通常の `tick()` がgame-switch phase・program slot・
     cooldown・所有権を検証してから同じrequestで実行する。原因が残っていれば再度latch
     され、`error_kind` が固定分類として残る。
   - 自分のrequestが**実行中／corner側が要復旧**（列挙順に依存しない。1件でもbusyなら拒否）、
     `pending` と `manual_pending` の同時存在、catalogから削除された予約、時計逆行は
     拒否してlatchのまま（exit非0、unitには触れない）。
   - **手動予約でterminal観測がまだない**（一度も起動していない）場合は、予約を消さずに
     `waiting` / `manual-request-needs-resume-or-recovery` へ戻す。以降の `tick()` は
     自動発火を止めて待ち、**同じ手動 start が同じrequestで再開**するか、当該cornerの
     手動 stop/recover と game-switch receipt が終了を証明するまで保持する
     （操作者のスロットを暗黙に捨てない。観測が有れば同じ規則でcommitする）。
   - 時計の逆行は**どの分支より先に**拒否する（復旧が `last_seen_at` を過去へ書き換えない）。
   - 成功時だけ `last_seen_at` を現在時刻へ進める（長期latch後の復旧で、次のtickが
     長期停止の隔離（24時間）へ落ちないようにするため。cooldownは履歴だけが決める。
     前方ジャンプを隔離で検知できるのはこの復旧操作を経由した場合だけ、という意図的な非対称）。
2. 成功した場合だけ `systemctl --user --no-block restart` でreviewed unitを起動し、
   直後のtickが同じrecorded gameを再試行する。**成否の契約は「試した」ではなく
   「ledgerから `recovery_required` が消えた」こと**で、残存時と、ledgerが存在して読めない
   場合はCLIが非0（exit 4）を返してunitを触れない（ロック競合・rotation無効時も同じ。
   ledgerが存在しなければlatchは無いので0）。結果JSONには `latch_resolved` を含め、
   ログを読む側はexit codeとこのフラグで判定する。

`tick()` 自身はlatchを自動解除しない（毎分のtimerが自動再試行ループを作るため）。
ledgerの手編集・`pending`の強制clear・state削除・seed再生成・時計調整は復旧手順ではない。
`error_kind` は**初回のlatch時**の固定分類として、次にlatchし直すまでledgerと診断に残る。
復旧操作が拒否された時に上書きしない（拒否理由より元原因の分類を残す）。
診断は `corners.corner_rotation` に `error_kind` と `pending_corner` / `pending_phase` /
`pending_age_sec` / `pending_owner` / `pending_owner_status` を固定投影し（予約が在る限り、
latch中かどうかを問わず出す）、latch自体を総合 `warn` としてruntime health alertへ届ける。
復旧後の `tick()` のcooldown再検証は `phase=="selected"` の予約だけで、
`dispatched` の復元はdispatch時の履歴行がcooldownを担保する。

起動済みcornerは無効化後も安全な終了・復帰を継続する。まだ起動していない予約が無効化
された場合は予約を保持して待機する。pending対象のcatalog削除は要復旧。
各managerのstateには`rotation_request_id`と`rotation_runtime_id`を記録する。
同名ゲームでもruntime世代が違えば復帰せず要復旧。終了境界不明、timeout、停止未確認、
canonicalの危険phase、failed/restoringの他ownerも次の開始を阻止する。例外はPAPERの
手動枠が復帰失敗を記録した後、stateに完了時刻があり、`recovery_required`でなく、canonicalが
`ready`で記録済みの元ゲームへ戻っていることを確認できる場合だけである。この場合はstateを
削除・上書きせず、観測上のみterminalとして扱う。canonicalが読めない、遷移中、元ゲームが
一致しない場合は従来どおりfail-closedで次の開始を阻止する。
PAPERの専用stateは`game=null`の旧手動記録も観測する。canonical欠落や`previous_game`キー欠落は
復帰の証拠としない。明示された`previous_game=null`だけは、実在するcanonicalのidle/activeなしを要求する。
この例外は現行catalogと削除済みPAPERの双方に適用し、retroなど他cornerのfailed判定は変えない。
共有program slotのowner記録にも同じ観測判定を使い、復帰済みPAPERのraw failedで再び待たせない。
対象は同じcanonical stateディレクトリ内のPAPER専用stateだけで、元stateは保存する。
完了時刻は有限非負数またはtimezone付きISOを要求し、`recovery_required`がある場合はboolean falseのみ許可する。
共通encoder/audio/通知/statusの停止・再起動経路は追加しない。

終了後の独立改善ジョブは既存の実行方式を維持し、次のcornerはlock解放とその実行以後の
終了記録を待つ。retroは`corner_improve_<game>.json`、PAPERは既存
`trading/paper_improve_status.json`。改善ジョブは開始時に`running`を書き、終了時に
terminal記録を書いてからlockを離す。`failed`は、corner完了以後の`started_at`と
`started_at`以後の有限な`completed_at`、解放済みlockを確認できる場合に限り、
この実行のterminalとして次の開始を許可する。記録は削除・上書きせず診断に残す。
SIGKILL後のrunning、終了記録欠落、corner完了より古い`started_at`、`completed_at`の欠落・逆行、
`recovery_required`、所有権不明を停止済み扱いにしない。安全なPID所有権証明なしにkillする代替経路は設けない。

手動startも共通lock/program slotを通し、同じrolling cooldownに**使用を記録する**
（以後の自動選択とcooldown集計は今までどおり）。ただし**手動start自体はrolling cooldownで
選択を拒否しない**（オーナー決定2026-09-23: 手動起動はcooldownを無視。one-off runnerで
テスト・operator起動を即座にできるため）。latch・自動pending・他ownerのprogram slotは
引き続き手動startを拒否する。
独立manual stateと既存のduration等は維持する。自動pendingがあれば手動startを拒否する。
manual予約も`manual_pending`にrequest UUIDとowner state名を保存する。途中で切れた場合は、
同じmanual startで同じrequestを再開するか、既存のmanual stop/recoverおよびgame-switch
receiptを照合して復旧するまで次の自動枠を待たせる。暗黙の別cornerへの置換やキュー削除はしない。

owner-only `corner-rotation recover` は、自動pendingのcorner stateが`failed`でも、同じ
request IDを持つterminal `rolled_back` receipt、対象/元gameとerror codeの一致、canonicalの
`ready`な元game、復帰generation以上のactive generation、candidate/previous/retiring不在、
cleanup未完了でないことを確認できた場合だけ、その失敗startを`interrupted`として記録する。
記録済み`completed_at`、pending request、cooldown履歴は保ち、同じcornerを二重起動しない。
corner資源解放も確認し、receipt欠落・cleanup不明・別runtime・解放待ちはラッチを維持する。
operatorはラッチ解消後にだけcorner rotation timerを再開する。

## 移行と互換入口

`corner_rotation.enabled=true`のとき、旧retro/PAPER/メリケン/専用NetHackのtickは
共通tickへ委譲する。旧profileはそのままlegacy動作を維持する。
productionのretroゲーム一覧はcatalogから導出し、二重のリストを編集しない。
canonical unitは`docich-corner-rotation.service/timer`で、serviceが
`corner-rotation tick`を実行する。移行期間は`docich-retro-corner.service/timer`が
canonical名へのrelative aliasになり、旧名と新名を独立timerとして二重enableしない。
移行は`ops/vm_actions/corner_rotation_timer_migration_epoch`をreview済みで追加した
deployだけが実行し、旧timerのstop/disable後でなければ切り替えない。旧serviceが
activeならkillせず中断する。review済み内容と一致しない旧unitは上書きしない。
rollbackはcanonical operator workflowの固定operation `rollback-timer`（またはowner-only `exec`）が
`ops/vm_actions/rollback_corner_rotation_timer.sh`を実行し、新timerを停止して
旧regular unitを復元する。state、lock、pause marker、game-switch receiptは
移行・rollbackで変更しない。`bin/docich`は全corner入口を既存trading Python環境で
実行可能にする。

初回移行はretroの選択履歴とnext_dueを取り込む。legacy active/starting/pendingがあれば
先に旧実行の終了・復旧を要求する。壊れたJSONや未知schemaは空stateとして再作成しない。
既存PAPER/メリケン/manual stateの実使用もcooldownに取り込む。
失敗stateの削除、seed再生成、時計を進めてcooldownを回避する操作は移行手順ではない。

## 診断とローカル検証の引継ぎ

新しい常駐worker/外部queueは追加しない。既存timerとgame-switch FIFOを使用する。
`docich-corner-rotation.timer`はdeploy時にreview済みunitを再配置してenableし、enable直後/boot後の
30秒tickと60秒間隔のmonotonic tickを持つ。移行前の`docich-retro-corner.timer`は
同じunitへのaliasとして解決される。read-only diagnosticsは`corners.corner_rotation`に
状態、待機理由、slot、next_due、last_seen、last_slot、interval、適格数、pending有無を固定投影し、
`corner_rotation_timer`に支配的なunit名（移行後は`docich-corner-rotation.timer`）と
active/enabled、旧名が正しいaliasかを示すbounded boolean `legacy_alias`だけを投影する。
seed、prompt、生成文、adapter例外は公開しない。
各cornerの固定投影には実行stateの`target_matches`（有効な整数1〜100のみ）も含める。

本変更の実装・テストは専用worktreeで行う。本番受入はPR/required CI/protected main/
canonical VM deploy後に、timer active/enabled、待機理由、game-switch/FIFO、実開始を別々に
確認する。timerの有効化は共通配信基盤を再起動せず、review済みのowner-only deploy hookだけが行う。

未実施の受入ゲート:

1. 本番相当Linuxでcatalog適格性とN、timerからtrading interpreterへの経路を確認する。
2. legacy状態と改善ジョブのterminal証明を確認して移行し、pending/FIFOの途中再起動を試す。
3. 各ゲームの試合終了/保存境界、旧AI/改善job/子プロセスの停止、共有PID維持を実測する。
4. PAPERの表示・仮想約定・発話終端、メリケンの入力/映像/声、全ゲームの960x540 contain表示を確認する。
5. 24時間以上の観測で実開始履歴・cooldown・休止/再有効化・時計異常・手動復旧を確認する。

ローカルのmockテスト成功は、これらruntime/E2Eや配信品質の成功を意味しない。

### 終了後改善ジョブの投入環境（#947）

終了後改善ジョブは tick の `KillMode=control-group` から逃がすため `systemd-run --user` の
transient unit として投入する。このクライアントは user manager の bus を `XDG_RUNTIME_DIR`
（または `DBUS_SESSION_BUS_ADDRESS`）から解決するが、timer 起動の user service は
`XDG_RUNTIME_DIR` を継承しないことがある。どの unit が tick を実行しても同じように動くよう、
spawn 側（`docich.procs.user_bus_env`）が `XDG_RUNTIME_DIR` 未設定時に `/run/user/<uid>` を
既定化し、bus socket が存在する場合だけ `DBUS_SESSION_BUS_ADDRESS` を補う。unit テンプレートの
`Environment=XDG_RUNTIME_DIR=%t` は二重の防御であり、これを唯一の根拠にしない。

投入失敗時は親 cgroup へフォールバックしない。`retro` / `paper` は systemd-run の rc と stderr を
bounded な durable record（`improve_job.error`）に残し、`corners.*.improve_job` として
read-only diagnostics に投影される。

### 2026-09-21 ローカル検証結果

- 基点: `origin/main = efb353de7944c6060f995f991997ecb744a155cb`（ローカル参照）。
  専用branch: `codex/corner-rotation-unification`。共有checkoutは編集していない。
- 最終focused/回帰: `python3 -m pytest -q -p no:cacheprovider` に
  `test_corner_rotation{,_execution}.py`、retro/PAPER/manual/restore/watchdog、
  Soren91、NetHack、corner_boundary/improve、game_switch/security、coordinator、
  program_view、diagnostics、hanjuku registrationを指定。
  **535 passed / 80 subtests passed、15.99秒、終了コード0**。タイムアウトなし。
- 先行した広い関連回帰: 952 passed / 102 subtests passed、40.71秒。
  診断projectionを含む追加検証: 65 passed、6.65秒。
- 全体pytestは`--maxfail=5`で終了コード1（204.59秒、786 passed、3 skipped、
  249 subtests passed）。失敗は今回未変更のLinux/実行環境依存領域:
  `test_moomoo_opend_service.py`の2件はmacOSの`stat`に`-c`がないため、
  `test_restart_radio_worker.py`の1件はLinux `/proc/<pid>/cmdline`前提、
  `test_captions.py`の2件はsandboxでUnix socket bindが`Operation not permitted`。
  今回のcorner関連退行は全体pytestでも検出されていない。
- 独立レビューで報告されたMeriken環境、restoring世代、停止要求、NetHack状態、catalog削除、
  diagnostics固定キーの6点を実装・テストで対応。CI、実機/E2Eは未実施。

### 変更ファイル一覧

新規:

- `src/docich/corner_catalog.py`
- `src/docich/corner_rotation.py`
- `src/docich/corner_adapters.py`
- `src/docich/corner_ownership.py`
- `tests/test_corner_rotation.py`
- `tests/test_corner_rotation_execution.py`
- `docs/corner-rotation.md`

更新:

- `src/docich/retro_corner.py`
- `src/docich/paper_corner.py`
- `src/docich/soren91_corner.py`
- `src/docich/nethack_corner.py`
- `src/docich/corner_boundary.py`
- `src/docich/corner_improve.py`
- `src/docich/__main__.py`
- `config/docich.soren-live.toml`
- `bin/docich`
- `scripts/systemd/docich-retro-corner.service`
- `ops/vm_actions/collect_diagnostics.py`
- `ops/vm_actions/tests/test_collect_diagnostics.py`
- `tests/test_hanjuku_retro_registration.py`
- `tests/test_retro_corner.py`
- `docs/retro-rolling-rotation.md`
- `docs/operations/runtime-diagnostics.md`

### 2026-09-22 名称移行 第1段階（docich-retro-corner → docich-corner-rotation）

- 目的: 実態が全corner共通rotationである旧unit名をcanonical名へ段階移行する。
  第1段階は互換準備（実装・テスト・自己レビューと互換deploy）まで。
  production timerの切替（旧timerのstop/alias化）は
  `corner_rotation_timer_migration_epoch`を追加する後続段階で行う。
- 基点: `origin/main = b5e98bc147905eed360b5cf53db7cb502fccd0b6`（#907の
  `corners.rotation_evidence`と#909の改善復旧修正を含む最新main。固定キーは
  維持したまま共存させている）。
- 追加: `scripts/systemd/docich-corner-rotation.service/timer`、
  `ops/vm_actions/migrate_corner_rotation_timer.sh`、
  `ops/vm_actions/rollback_corner_rotation_timer.sh`、
  `ops/vm_actions/authorize_corner_rotation.py`、
  `ops/vm_actions/restart_corner_rotation.sh`、
  `ops/vm_actions/recover_corner_rotation.sh`、
  `.github/workflows/corner-rotation-operator.yml`。
- 移行は`ops/vm_actions/corner_rotation_timer_migration_epoch`をreview済みで追加した
  deployだけがdeploy hook経由で実行する（第1段階では未追加。hookは旧timerのみを維持）。
- 検証: 最低限回帰 401 passed / 1 skipped / 92 subtests passed（17.16秒）。
  migration/rollback実行テスト21件（fake systemctl、state/lock/pause/receipt不変、
  二重timerなし、active service中断、disable中のtick復元、drift拒否、冪等、
  alias上書き拒否を含む）。関連広域 1961 passed / 223 subtests passed。1 failedは
  サブモジュール未初期化の環境依存で、未変更ベースでも同一。`systemd-analyze verify`は
  macOSで未利用のためskip（Linux CIで実行される）。
- 本段階のdeployでは旧timerを維持し、canonical unitはVMへ配置しない。実機での
  canonical切替（epoch追加後の後続段階）と24時間観測は未実施。

### 2026-09-22 名称移行 第2段階（canonical timer 切替・実測）

- PR #919でepoch `ops/vm_actions/corner_rotation_timer_migration_epoch` を追加し、
  merge後のpush deployでdeploy hook経由のmigrationを実行した。
- 基点/結果: main `f941164f1f18cea66e7e479c23b2c351d7c166c7`。VM operations run
  `35674462543`（deploy `uploaded`→`deployed`→`executed`、`Ensure corner rotation timer`
  success、radio worker再起動なし＝restart marker 0件）。
- 移行前 diagnostics（run `35674191799`）:
  - `corner_rotation_timer = {unit: "docich-retro-corner.timer", active: true, enabled: true, legacy_alias: false}`
  - `corner_rotation = {slot: 2, last_slot_at: 1790037296.195363, next_due_at: 1790048096.195363,
    eligible_count: 8, interval_seconds: 10800, pending: false, status: "waiting", reason: "not-due"}`
- 移行後 diagnostics（run `35674493731`）:
  - `corner_rotation_timer = {unit: "docich-corner-rotation.timer", active: true, enabled: true, legacy_alias: true}`
  - `corner_rotation` は slot / last_slot_at / next_due_at / eligible_count / interval_seconds /
    pending が移行前と同一。`last_seen_at` のみ毎分更新（timerが継続tickしている証跡）。
  - 総合 status ok、`tracked_drift.drift_detected=0` / `scan_complete=1`、workers 16 running、
    `required_down` / `required_stale` 空、`stale_locks=0`、`game_switch` は `ready`/`sorengame` のまま。
- `status` operation（run `35674552301`）: `configured` at `f941164f`。
- 旧timerのstop/disableとcanonical timerのenableのみで、共有配信・FFmpeg・音声・通知・
  `docich.service` の再起動は行っていない。state / lock / pause marker / game-switch receiptも
  変更していない（上記のstate一致）。
- 未実施: rollback操作の実機試験（rollback helperはowner-only `exec`で実行可能。固定operationへの
  配線は後続）、24時間観測、受入ゲート1〜5の残り。epochはmainに残っているため、rollbackを
  恒久化する場合はepochをrevertするPRが必要。

### 2026-09-22 名称移行 第3段階（rollback operation配線）

- canonical operator workflow `.github/workflows/corner-rotation-operator.yml` に固定operation
  `rollback-timer` を追加し、`ops/vm_actions/authorize_corner_rotation.py` の許可operationへ加えた。
  owner / actor ID / protected main / current-main一致 / production確認 / 新旧workflow path完全一致の
  既存契約は不変。legacy workflow（`retro-corner-operator.yml`）にはoperationを追加しない。
- rollbackはcanonical serviceがactiveならkillせず中断し（exit 33）、state / lock / pause marker /
  game-switch receiptを変更せず、共有配信・FFmpeg・音声・通知・`docich.service`をrestartしない。
- epochはmainに残っているため、rollback後も次のdeployで再migrationされる。恒久rollbackは
  epochのrevert PRが必要。
- 実機rollbackは未実施。受入は固定operationのreview/CIと、配線後のdeployでcanonical維持が
  継続することまで。
