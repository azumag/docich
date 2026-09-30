# 半熟英雄のチャネルポイント予想

## 視聴者向け仕様

半熟英雄の **1回の挑戦** を対象に「どこまでゲーム到達できるか？」を予想する。
進行中のチャネル予想がない場合だけ自動開始し、受付終了後の `LOCKED` も
未精算の予想として扱う。他コーナー・手動作成の予想を奪わない。
新規開始前の一覧取得は開始日時の降順で最新1件だけを確認する。
Twitchの同時実行上限は1件のため、進行中ならその最新1件が対象になる。
所有済みの予想の追跡・精算は引き続き予想IDで取得する。

尺度は「到達した話」ではなく **突破した話数**。第2話に入った場合は1話突破。
2026-09-30のowner確認に基づき、従来最高を1話突破として初期化する。

- 新記録目標 `T = 過去最高突破話数 + 1`。第6話突破が新記録ならT=6。
- 中間 `M = floor(T / 2)`。奇数は切り捨て、最低1。
- 的中は `突破数 >= T` / `M <= 突破数 < T` / `突破数 < M` の排他的な3区分。

現時点は **2話突破（新記録） / 1話突破 / 1話突破できず**。
T=6なら **6話突破（新記録） / 3〜5話突破 / 3話突破できず** と表示する。
目標と選択肢は予想作成時に固定し、挑戦途中に新記録が出ても変更しない。
次回の予想から更新する。全12話突破後は不可能な「13話突破」を作らない。

受付時間は **エンディング到達の受付時間 × 新記録目標話数 ÷ 全12話**
（整数秒で切り捨て、Twitchの最短30秒を下限）で決める。
エンディング到達は既定1800秒（30分）。2話なら300秒（5分）、
6話なら900秒（15分）、12話なら1800秒（30分）となる。
受付時間も作成前のintentへ保存し、既存予想や再試行の途中では変更しない。
導入前に開始した120秒の予想はそのまま追跡・精算する。
実行中に新記録達成が既に観測できたら受付をロックするが、
最終記録・精算は挑戦終了まで待つ。既に新記録が確定している途中参加では
結果既知の予想を作成しない。コーナーの時間制限や既存の終了判定は変更しない。

## 記録・終了・通信障害

`hanjuku_progress.json` はbotの単一writerが、既存の画面認識から確認した
最大突破数をruntime/generation/leaseとフレームハッシュ付きで保存する。
章の番号・次章の固有の城/将軍の根拠を利用し、年・月、チャートの予定手順、
本城の別名、未確認のボスHPを突破数へ変換しない。
第12話のみ、既存の戦闘結果分類による最終ボス「クーモン」の確認済み勝利を
12話突破とする。これは新しい画像認識器ではなく既存の認識精度に依存する。
同一runtimeで新しいゲームが検出された場合は複数挑戦を混ぜず判定不明にする。

自然終了では既存 `hanjuku_run.terminal()` を緩めず再検証する。
**game_over と screen_stalled の両方で同じ最大突破数を最終記録として採用**する。
膠着後のリセット・runtime削除・ログローテーションで0へ戻さない。
検証済み結果はruntimeの `hanjuku_prediction_result.json` と全体台帳の両方へ
先にfsyncしてからAPIを呼ぶ。台帳の結果はruntime retention後も精算に使える。
旧bot snapshotは完全なidentityと章の根拠がある場合だけ導入時に読める。
記録欠落を0話と決めつけず、最終数が不明な予想は返還する。

手動停止・別runtimeへの切替はゲームオーバーと偽らず、自分が作った予想だけ
キャンセルして返還する。切替中など所有者が未確定なら保留する。

Twitch通信はコーナー側の副処理で、**ゲーム入力用ロックの外**で行う。
通常30秒間隔、通信失敗は300秒後に再試行。新しい終了確定は通常間隔を待たず処理する。
各HTTP要求は5秒タイムアウト、応答256KiB上限、固定Helixホスト、redirect禁止。
ゲーム終了・復帰を通信成功の条件にしない。共通rotation/retro tickは、終了後も
自分の未精算予想を再確認するが、新規予想は作らない。

作成要求前にランダム識別子入りのタイトルと選択肢を永続化する。POSTの応答を
失ったら、同じタイトル・同じ選択肢のremoteを照合してIDを回復する。
通信失敗を「予想なし」と扱わず、成功したか不明のPOSTを盲目的に再送しない。
確認できない場合は `create_unknown` として保留する。手動で無関係なremoteを
この台帳へコピーしたり、台帳だけ消して作り直したりしない。
Twitch側でキャンセル/返還済みなら再精算せず、その状態を尊重する。

## 配備順序と既存Sorenとの境界

**Soren側のremote予想ownership修正が必須の先行依存**。
旧 `twitch_predictions.sh` は種類を問わずACTIVE/LOCKEDをSoren予想として引き継ぎ、
半熟英雄を「建国なし」等で誤精算し得る。対応版はタイトルと4つのSoren専用選択肢を
確認した場合だけ引き継ぐ。`SOREN_REMOTE_PREDICTION_OWNERSHIP_V1` マーカーがない
runtimeでは本機能は `incompatible_soren` としてAPI操作を行わない。

先にSorenの修正をレビュー・main統合し、docichのsubmodule参照を統合後SHAへ合わせる。
次にdocichのレビュー・CIを通し、owner-only VM control planeで両方を反映する。
PR作成・ローカル試験だけでは配備済みとしない。既存Sorenの48試合ルール、
`tmp/state/current_prediction.json`、worker pause、ゲーム操作・配信基盤には触らない。
本番で次を確認するまではライブ動作は未検証とする。

1. diagnosticsの `programs.hanjuku_predictions` が `incompatible_soren` でない。
2. 新規の半熟英雄ランにてremoteが空の場合だけ3択を1件作る。
3. game_overまたはscreen_stalledで確認済み突破数に精算され、次回の目標に反映される。

設定は `config/games/hanjuku-hero.toml` の `[hanjuku.predictions]`。
`enabled` はboolean、`seed_best_cleared` は1〜12、
`ending_window_seconds` は30〜1800（エンディング到達の受付時間）。
OAuthは既存Soren `.env` / 環境の `TWITCH_PREDICTIONS_ENABLED`,
`TWITCH_PREDICTIONS_TOKEN`, `TWITCH_CLIENT_ID`, `TWITCH_BROADCASTER_ID` を使う。
`EXPLORE_MODE=1` または既存Twitch設定無効ならAPI操作しない。シェルをeval/sourceせず、
トークン・応答本文・HTTP headerを台帳/診断/ログに保存しない。
`run/hanjuku_predictions.paused` は新規開始だけを休止し、既存の精算は継続する。

## Runtime変更チェック

- registry / worker health: 新規常駐worker・PID・サービスは追加しない。
  既存corner/rotation tickを使用。Soren prediction_workerの規約は変更しない。
- queues: 新規キュー、音声、チャット投稿は追加しない。
- telemetry / diagnostics: `run/hanjuku_predictions.json` のmode/error、最高突破数、
  固定目標、中間、受付秒数、remote status、最終突破数、次回試行時刻だけを固定collectorで投影する。
  作成タイトル、outcome IDs、認証情報は診断に含めない。
- regression: `tests/test_hanjuku_predictions.py` と既存半熟英雄/retro/rotation/診断試験。
  CIで実Twitch・ROM・本番VMへ接続しない。
- deployment: Soren先行依存、protected main、owner-only gatewayを維持。

API仕様（一次情報、確認2026-09-30）:
https://dev.twitch.tv/docs/api/predictions/
https://dev.twitch.tv/docs/api/reference/#create-prediction
https://dev.twitch.tv/docs/api/reference/#end-prediction
