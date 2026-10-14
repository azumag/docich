# ゲームライフサイクル（game-only lifecycle）

「試合を完走させて結果を保存し、その後だけ旧ゲームの資源を止める」ための game-only
ハンドオーバー機構。[[ゲーム切替機構|Game-Switch]]（docich 側 coordinator）から見た
Soren 側の受け口と、Soren 側で完結する停止・復帰の契約をまとめる。

一次実装: soviet_now `lib/game_lifecycle.{py,mjs,sh}`（broker）、`soviet_local.mjs`
（bridge 停止経路）、`eloop.sh` / `soren_loop.sh`（park hook）、`soviet_watchdog.sh` /
`start_all.sh`（抑止）。2026-09-05 に本番反映済み（branch `codex/game-lifecycle`
`3dd82ad129`）。

## 責務分離（何がゲーム固有で何が共通か）

- **ゲーム固有（停止対象）**: ゲーム実行・描画・操作AI・改善ループ・ゲーム専用監視
  （bridge `soviet_local.mjs` / `soren_loop.sh` / `soviet_watchdog.sh` / BGM/SE 子プロセス / ゲーム Chromium）
- **共通（停止しない）**: 通知枠・ステータス枠・配信エンコーダ・共通音声・切替管理
  （`soren-shared-overlay.service` / `direct_stream` / ffmpeg / audio worker / radio・chat worker）

ゲームの停止・再起動が共通基盤へ波 spread しないことが検証条件。`soren-runtime.service`
全体の再起動は配信基盤変更時のみで、ゲーム切替のために行わない。

## 二相ハンドオーバー（boundary → 明示 stop）

1. **boundary（試合境界）**: 試合が完走し結果が保存されたら `request.json` に応答して
   `boundary` を ack し、**次ゲームの開始だけを保留する**。この時点では資源は止めない
   （bridge もゲームページも生存したまま）。loop は `exit 75` で park し、
   `tmp/state/soren_loop.paused` で supervisor が再起動を hold する。
2. **明示 stop**: coordinator（docich）が writer 側の quiesce を決めてから
   `control.json` に `action=stop` を発行する。ここまで来ない限り資源は閉じられない。
3. **claim-stop（不可逆フェンス）**: bridge が共有表示 readiness を確認した直後、
   Unity `Quit()` を呼ぶ**前に** `claim-stop` で原子的に停止権を取る。以後
   cancel/restore/期限切れでは取り消せない（`stopping` は fence）。

状態一覧: `accepted → waiting → boundary → stop_requested → stopping → stopped`
脇道: `cancelled`（stopping 前のみ）/ `failed` / `timeout` / `unsupported` /
`resumed`。`stopped / cancelled / failed / timeout / unsupported / resumed` が
terminal。terminal への再入力は競合（`cancelled` への cancel は冪等 OK、control は書き換えない）。

## 要求 identity 照合（古い応答が新しいゲームを止めない）

すべての永続レコード（ack/control/resource）は request から **5 項目の identity**
（`request_id` / `game` / `generation` / `deadline_epoch` / `deadline_at`）を複製し、
全操作で再照合する。遅延応答・期限切れ応答・世代違いの応答は新しい要求に影響しない。

- deadline は `boundary` / `stop` / `claim-stop` で判定し、期限切れなら `timeout`
  （rc=2）。**`stopping` は期限をまたいで継続する**（資源が既に死んでいるため、
  期限切れで「元に戻った」と偽るより fence を守る）。
- bridge は readiness 取得や音声 drain の待ち時間を挟むため、**terminal 書き込みの
  直前に必ず currency 再確認**する（待ちの間に要求が入れ替わっていたら書かない）。

## 資源停止シーケンス（bridge 側）

```
control(action=stop) 検出
  → 改善ジョブが生存していたら証跡のみ付与（停止は shell の担当）
  → 共有表示 readiness gate (GET :8092/healthz, ok/ready/browserReady/layoutReady/overlayReady)
     未ready → `unsupported` を書いて cancel 可能なまま旧ゲーム継続（fail-open）
  → claim-stop（原子フェンス、以後取消不可）
  → Unity.Quit() を呼ぶ（Promise は待たない。終わらない Promise があるビルドのため）
  → ゲーム専用 BGM/SE 子プロセス: SIGTERM → 2s 待ち → SIGKILL → 0.5s 待ち、exit を待ってから進む
  → browser close（context.close() と browser.close() の両方を必ず試行）
  → post-Quit 再プローブ: overlay が落ちていたら `failed` + overlay_died_mid_stop（fail-closed）
  → game_resource.json に stopped/failed を書き、loop 側の `finish` で ack を stopped に確定
```

`unsupported` は bridge がゲームページを触らない（cancel で安全に復帰できる）。
`failed` は browser を既に閉じているため復帰不能（watchdog は `stopping` から
deadline が切れるまで再起動しない）。

## park と再起動抑止

- `soren_loop.sh` は boundary/park で `exit 75`。supervisor は
  `tmp/state/soren_loop.paused`（汎用 pause ファイル）で hold する。
- watchdog / supervisor は `game_lifecycle_bridge_parked` 述語で bridge 再起動を抑止する:
  - park 対象: `stopping` / `stop_requested` / `resume_requested` / `stopped`
  - **非終端状態は deadline が生きている間だけ park**（期限切れで抑止を解除＝復旧できる）
  - **terminal `stopped` は期限と無関係に park 継続**
  - **`boundary` は park しない**（bridge は生存して明示 stop 待ち。死んだら通常復旧で良い）
- `stopping` で crash した loop の再起動時は、startup hook（`game_lifecycle_resume_pending`）
  が同一 request の `finish` を完走させてから `exit 0` する（claim の取り残しを防ぐ）。

## 改善休止の所有権

- ハンドオーバーは `improve_daemon.paused` を作る前に既存 marker を確認し、
  `improvement_pause.json` に `improvement_marker_created`（自分が作ったか）を記録する。
  **operator が元から設定した marker は採用しない・削除しない**。
- 同一 request での再 pause は所有権を引き継ぐ（後発の restore で消し忘れない）。
- `_game_lifecycle_pause_improvements` は `broker.lock`（flock fd 9）で相互排他。
  取れない場合は fail-closed（marker を作らず abort、旧ゲーム継続）。
- `soren_loop.paused` も同様に `loop_pause.json` で所有権管理。

## ファイル構成（tmp/state/game_lifecycle/）

| ファイル | 役割 |
|---|---|
| `request.json` | 要求（identity + snapshot）。broker が書く |
| `ack.json` | broker の状態応答。全レコードの identity 元 |
| `control.json` | coordinator の操作（stop/cancel/resume）。**terminal 後も残る**（finish と resume-complete だけが消す） |
| `game_resource.json` | bridge の資源停止証跡（quit_called / audio_shutdown / browser_closed 等） |
| `broker.lock` | flock（broker と shell の相互排他） |
| `history/` | アーカイブされた要求一式 |

**教訓**: `control.json` が terminal 後も残るため、bridge は処理済み terminal
（`cancelled` / `resumed`）を毎反復再処理してはいけない。実バグとして
「cancel 後にページを再ロードし続け→試合が進まない→phantom 復旧が bridge を
再起動し続ける」カスケードが発生した（2026-09-05、回帰テスト化済み）。

## CLI ゲーム（Robots）の境界契約

- 自然終了プロンプト **`Another game?` のみ**が試合境界。`Really quit?`（q による
  確認）は境界と誤認しない—— **一切キーを送らず観測のみ**で、そのまま待つと
  deadline timeout で fail-closed（キーを送って答えると意図しないquit/再開になる）。
- スコア（`Score:` 最大値・null 許容）は ack の**前に** `runtime_dir/round_boundary_result.json`
  へ原子保存。書けない場合は ack しない。
- 待機中は入力を止めない（agent は fence+共有 lock で遊び続ける）、timeout でも
  強制終了しない（coordinator は cancel を発行して runtime を生存させる）。
- `require_round_boundary=false` のゲームでは adapter が capability を隠す
  （従来の即時切替経路を維持）。

## 隔離リハーサルの安全装置（必須 env）

実ループ/実 watchdog を本番 VM で動かすときは、復旧経路が本番を狙えないよう全て分離する:

| env | 意味 |
|---|---|
| `SOVIET_BRIDGE_PORT` | `_br_relaunch` の port 強制 kill 対象（既定 8080=本番） |
| `SOREN_CDP_PORT` | CDP ポート（既定 9222） |
| `SOREN_BRIDGE_TMUX_SESSION` | **tmux kill-session の対象セッション名（既定 soren_bridge=本番）** |
| `SOREN_GAME_LIFECYCLE_DIR` / `TMP_STATE_DIR` | lifecycle 状態の分離 |
| `SOREN_LOCAL_USER_DATA_DIR` | Chromium profile の分離 |

実事故（2026-09-05 17:13/17:14）: リハーサル内の phantom 復旧が
`tmux kill-session -t soren_bridge`（ハードコード）で**本番 bridge を kill**した
（watchdog が自動復旧・encoder は無傷）。→ `SOREN_BRIDGE_TMUX_SESSION` を追加。
また合成ゲームの試合長が短いと `turns=0` で phantom 復旧（bridge 再起動）が発火する
ため、試合長は runner の最初の決定（turns>=1）より長くする。

## 本番構成（2026-09-05 時点）

- `soren-shared-overlay.service`（systemd・独立）: `shared_overlay.mjs` を専用
  Xvfb(`:98`) + **xfwm4 必須**（WM がないと Chromium の outer が 1279x719 になり
  visible-window 契約が不通）で起動、`:8092/healthz` を公開。
  - twica proxy はゲーム bridge（18080）と衝突するため、サービスは
    `SOREN_DIRECT_TWICA_PROXY_PORT=18081` を強制。
  - systemd 環境では `HOME` / `XDG_RUNTIME_DIR` の補完が必要（無いと window 幾何が
    ずれて契約不通）。
- `.env` `SOREN_GAME_LIFECYCLE_SHARED_OVERLAY=1` で bridge の readiness gate を有効化。
- 配信映像は従来どおりゲームページ内描画（見た目不変）。映像面を共有オーバーレイに
  切替（ゲーム窓 960x540 実ウィンドウ化 + :99 重畳）は後続作業。

## worker 入替の手順（無停止）

1. 事前に PID 基線を採取（encoder/supervisor/全 worker）。
2. 対象ファイルの targeted backup（フル tar は chromium profile を含み肥大化するため禁止）。
3. **watchdog はいつでも** `kill -TERM`（trap が sleep 完了まで最大 INTERVAL 待つ点に注意）。
4. **loop と bridge は `(state==MOVE && strategy_runner.py 非存在)` の決定境界で**
   同時に TERM→再作成（1 回のゲームリセットに統一。bridge の再起動はページ再ロード=
   試合リセットが視聴者に見える）。
5. 後比較: encoder/supervisor/他 worker の PID 不変を必ず確認する。
