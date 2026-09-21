# 全cornerの24時間rolling rotation

## 構造とcatalog

`config/docich.soren-live.toml` の `[corner_rotation].corners` がproduction登録の正本。
各レトロゲーム・PAPER・メリケンを同列の項目として登録する。レトロという親枠はない。
既存設定から移した9件中、半熟英雄は引き続き無効。残る8件もadapter適格性を通った
項目だけを有効数Nに数える。ゲーム設定の無効化、実行ファイル不足、catalogの
`paused=true`、`state_dir/corners/<id>.paused` を反映し、Nを固定しない。

`adapter="game"`の項目には`target_matches`（整数1〜100）を任意指定できる。
省略時は従来の`retro_corner.target_matches`（既定3）を継承する。
productionの`nsnake`は1試合とし、開始以降のscorelog保存を確認して既存の終了・復帰へ進む。
時間上限、試合境界待ち、次slotの間隔とcooldownは維持する。試合数はNに加算しない。
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

## 時間と重複排除

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
正常な終了記録を待つ。retroは`corner_improve_<game>.json`、PAPERは既存
`trading/paper_improve_status.json`。失敗、SIGKILL後のrunning、終了記録欠落、所有権不明を
停止済み扱いにしない。安全なPID所有権証明なしにkillする代替経路は設けない。

手動startも共通lock/program slotを通し、同じrolling cooldownに使用を記録する。
独立manual stateと既存のduration等は維持する。自動pendingがあれば手動startを拒否する。
manual予約も`manual_pending`にrequest UUIDとowner state名を保存する。途中で切れた場合は、
同じmanual startで同じrequestを再開するか、既存のmanual stop/recoverおよびgame-switch
receiptを照合して復旧するまで次の自動枠を待たせる。暗黙の別cornerへの置換やキュー削除はしない。

## 移行と互換入口

`corner_rotation.enabled=true`のとき、旧retro/PAPER/メリケン/専用NetHackのtickは
共通tickへ委譲する。旧profileはそのままlegacy動作を維持する。
productionのretroゲーム一覧はcatalogから導出し、二重のリストを編集しない。
既存`docich-retro-corner.timer/service`は互換unit名を維持し、serviceが
`corner-rotation tick`を実行する。旧timerが複数残っても単一lock/stateを共有する。
`bin/docich`は全corner入口を既存trading Python環境で実行可能にする。

初回移行はretroの選択履歴とnext_dueを取り込む。legacy active/starting/pendingがあれば
先に旧実行の終了・復旧を要求する。壊れたJSONや未知schemaは空stateとして再作成しない。
既存PAPER/メリケン/manual stateの実使用もcooldownに取り込む。
失敗stateの削除、seed再生成、時計を進めてcooldownを回避する操作は移行手順ではない。

## 診断とローカル検証の引継ぎ

新しい常駐worker/外部queueは追加しない。既存timerとgame-switch FIFOを使用する。
`docich-retro-corner.timer`はdeploy時にreview済みunitを再配置してenableし、enable直後/boot後の
30秒tickと60秒間隔のmonotonic tickを持つ。read-only diagnosticsは`corners.corner_rotation`に
状態、待機理由、slot、next_due、last_seen、last_slot、interval、適格数、pending有無を固定投影し、
`corner_rotation_timer`にそのunitのactive/enabledだけを投影する。seed、prompt、生成文、adapter例外は
公開しない。
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
