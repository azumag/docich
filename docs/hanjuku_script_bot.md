# 半熟英雄のscript bot

`config/games/hanjuku-hero.toml` は `brains/hanjuku/bot.py` を1.5秒間隔で起動する。
Python標準ライブラリだけで画像を判定し、決定的なルールでSNESのpad入力を返す。
Claude、OpenCode、API、認証情報を操作時に使用しない。旧`brain.py`は互換テスト用に残すが、
この設定からは実行されない。OpenCodeによる試合結果からの自動改善は後続作業とする。

## 操作と終了条件

- native画面を256×224に正規化し、タイトル、名前入力、会話、マップ、出撃メニュー、
  戦闘、月末メニュー、買い物を判定する。名前を決定し、最初の将軍を出撃させ、最初の敵城へ向かう。
- 戦闘中はA入力、会話やステージのイベントは確認入力で進める。戦闘終了をコーナー終了にしない。
  勝敗・ステージ番号の読み取りと全ステージ攻略は未実装。観測できない勝利をログに作らない。
- 固定20分の終了・強制セーブは行わない。名前入力とプレイを観測した後、実ROM由来のタイトル画像の
  特徴に3回以上・2秒以上一致した場合、`game_over`（根拠`title_return_after_gameplay`）とする。
  ゲームオーバー演出は確認入力で進め、タイトルへ戻った時点で入力を止める。起動時のタイトルや
  デモをゲームオーバーと扱わない。タイトルへ戻る前の敗北演出の種類までは判定しない。
- 正規化したゲーム画面のRGBが連続300秒変化しなければ`screen_stalled`とする。
  15秒超の観測欠落、一時停止、時計の巻き戻り、画面変化で計測をリセットする。
  背景アニメーションを含めて変化する画面は「無変化」ではない。
- 終了候補・終了確定時は実行境界でも入力を拒否する。runtime/generation/leaseが一致する終了証拠を
  `retroarch_boundary.json`へ引き継ぎ、既存coordinatorがゲーム側の子プロセスを停止して前のゲームへ戻す。
  他のRetroArchゲームの明示pause/save契約は変更しない。

## 改善用ログ

世代別の`run/runtimes/<runtime_id>/`へ保存する。ゲームを停止しても削除しない。

| ファイル | 内容 |
|---|---|
| `hanjuku_run.json` | bot版、世代、観測数、実入力数、戦闘開始/終了数、現在の判定、終了理由と根拠 |
| `hanjuku_events.jsonl` | 時刻、画面SHA-256、画面判定、実際に送信したボタンと押下時間、戦闘遷移、終了理由 |
| `hanjuku_events.previous.jsonl` | 4 MiBごとのローテート先。現行と合わせ最大約8 MiB |
| `hanjuku_frames/frame-*.png` | 画面判定の変化、60秒間隔、終了時の画像。120枚のリング |
| `hanjuku_bot.json` | botの現在の方策状態。別世代へ持ち越さない |

ログにLLMプロンプト、認証情報、ROM本体、セーブデータを含めない。画像と詳細イベントはVMローカルに保ち、
固定diagnosticsは許可されたphase、終了理由、入力数、戦闘回数、無変化秒数のみを公開する。
画像はリングで上書きされるため、イベントのSHAを使って対応を確認する。

## runtime変更の確認項目

- registry/manifest: 既存`retroarch` adapterと世代別agent/game manifestを使用。新worker/queue/providerなし。
- health: 既存agent修復とcoordinatorのownership/lease確認を使用。別runtimeの終了証拠では停止しない。
- telemetry/diagnostics: 上記JSONLと`collect_diagnostics._project_corner_state`の固定射影。
- regression: `tests/test_hanjuku_script.py`、登録、RetroArch境界、corner rotation、diagnosticsのテスト。
- deploy: docich protected mainの正規gateway経由。共有配信・音声を再起動せず、次回ゲーム起動から適用する。
- 実機受入: 新規ゲーム→入力→戦闘、終了後の復帰・子プロセス解放、配信PID維持を別々に検証する。
  ユニットテストや設定の有効化だけで実機受入の完了とはしない。
