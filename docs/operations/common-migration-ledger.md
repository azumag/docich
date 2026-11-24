# 共通配信・半熟英雄の docich 移行台帳（ADR 0006 第2〜4段階）

- 作成: 2026-11-09
- 正本: ADR 0006（リポジトリ境界）と ADR 0007（encoder の単一所有者）
- 目的: soviet_now を**ソ連ゲーム専用**にし、他ゲームの表示・配信共通基盤を docich へ移す。移行中の重複は期限付きで許容し、本番実測後に旧経路を削除する。

## 1. 最終形（soviet_now に残すもの）

soviet_now に残すのは、ソ連ゲームの盤面・ルール・戦略・ゲーム固有の入力と評価、およびそれらの公開インターフェースだけとする。

- 残す: `soren_loop.sh`、`strategy.py` / `strategy_runner.py`、`analyze_board.py`、`soviet_local.mjs` の Unity WebGL 入出力、`soren91/`（ソ連ゲーム91 本体）、ゲーム固有の観測・評価・改善ループ。
- 残さない: 半熟英雄・NetHack など他ゲームの表示と制御、配信共通の overlay service、音声・ラジオ・コメント・BGM、status 表示の renderer、旧互換の重複実装。
- game adapter は、ゲーム固有の値を docich の公開契約へ渡すだけの薄い層に限る。ゲーム名の分岐を共通コードへ持ち込まない。

## 2. 実測スナップショット（2026-11-09 18:00 JST）

| 項目 | 実測 |
| --- | --- |
| docich main / VM production HEAD | `57b0a87a` / 同時進行の作業ブランチ（デプロイ後に main へ正規化される） |
| soviet_now main / submodule pin | `68636c155` / 追随して更新 |
| encoder | `docich-stream.service` が active（2026-11-07 14:11:02 JST 起動）。`DOCICH_STREAM_OWNER=docich`、backend ffmpeg。FFmpeg は同 cgroup。旧 runner なし |
| 表示テンプレート | 実描画は `/home/ubuntu/docich/overlays/direct_broadcast_overlay.html`。左余白カードに docich 版だけが持つ「進軍数」表示を確認 |
| 表示 state | shared overlay の `/__soren_overlay/broadcast/state` は docich の builder が応答（`nextCorner` を含む） |
| shared overlay service | `soren-shared-overlay.service` が `/home/ubuntu/soren` のコードで稼働。未移行 |
| 音声・ラジオ・コメント・BGM | `soren-runtime.service` 配下の soviet_now コード。未移行 |
| 半熟の表示データ整形 | `lib/docich_corner_stats.py`（1,599 行）と `status_dashboard.py`（4,211 行）。未移行 |
| 現在のコーナー | `game_switch.json` phase=ready / hanjuku-hero / g1163 |

補足: 2026-11-09 は半熟の修正 PR が同時進行し、VM の HEAD と production checkout のブランチが短時間で動いた。作業ブランチは着手時と push 前に `origin/main` へ rebase する。

## 3. 部品別の状態

| 部品 | 現在 | 目標 | 分類 |
| --- | --- | --- | --- |
| 表示テンプレート（`overlays/direct_broadcast_overlay.html`） | docich 正典が実描画、soviet 側はフォールバック | soviet 側を削除 | 移行済み（第5段階で撤去） |
| 表示 state builder（`lib/direct_broadcast_overlay.mjs`） | docich の builder が応答、soviet 側は legacy | soviet 側を削除 | 移行済み（第5段階で撤去） |
| 半熟の表示データ整形（`lib/docich_corner_stats.py`、status の corner renderer） | soviet_now | docich の `soren_corner_stats` / `soren_corner_text` | 未着手 |
| shared overlay service（wrapper / Node runner / unit） | soviet_now | `docich-shared-overlay.service` | 未着手 |
| 音声 queue / fence（`outbound_queue.sh`、`runtime_audio_fence.py`） | soviet_now | docich の共通ライブラリ | 未着手 |
| TTS / say consumer（`say_enqueue.sh`、audio_worker） | soviet_now | docich の audio worker | 未着手 |
| ラジオ（`radio_worker.sh`、`radio_engine.sh`、`radio_state.sh`） | soviet_now | docich の radio service | 未着手 |
| コメント（`comment.sh`、`comment_lib.sh`、chat/comment worker） | soviet_now | docich の comment service | 未着手 |
| BGM（`bgm_worker.sh`、`soren-bgm.service`） | soviet_now | `docich-bgm.service` | 未着手 |
| `#826` の半熟限定 prepass バイパス | soviet_now（既定 OFF） | 外部コーナー汎用の判定（既定 ON） | 未着手 |
| encoder | docich（ADR 0007 段階 E 適用済み） | 旧 Soren 実装の撤去（段階 F） | 移行済み |
| `xvfb.service` / `soren-rtmp-relay.service` | ホスト unit | 今回は触らない | 保留 |

## 4. PR 順序

各 PR は「docich 実装・配備 → soviet_now を docich 優先へ → pin 更新 → 本番切替 → 実測」の順。旧経路の削除は同じフェーズの本番実測後、別 PR で行う。

| ID | repo | 内容 | 依存 | 本番影響 |
| --- | --- | --- | --- | --- |
| 0 | docich | ADR 0006/0007 の状態を実測に更新し、本台帳を追加 | — | なし |
| 1 | soviet_now | `#826` を外部コーナー汎用・既定 ON へ。緊急停止スイッチだけ残す | — | 半熟コーナー中の prepass 停止（ラジオ内容が変わる） |
| 2 | docich | 半熟の表示データ整形を移設し、テストを移管 | — | なし |
| 3 | soviet_now | status_dashboard が docich を import（失敗時のみローカル） | 2 | なし |
| 4 | soviet_now | 旧 stats / 半熟 renderer を削除 | 3 + 実測 | なし |
| 5 | docich | overlay HTML / state の契約テストを移管し、soviet CI を縮小 | 2 | なし |
| 6 | docich | `docich-shared-overlay.service` と runner を追加（配置のみ） | — | なし |
| 7 | soviet_now | overlay service の所有者を docich へ切り替え | 6 | 5〜15 秒、枠・サイドバーが欠ける |
| 8 | soviet_now | 旧 overlay 実装を削除 | 7 + 実測 | なし |
| 9 | docich | queue / lock / fence / dedupe の契約と共通ライブラリを移設 | — | なし |
| 10 | soviet_now | queue / fence を docich 委譲 | 9 | 0 秒（次呼出から） |
| 11 | docich | TTS / say consumer と audio worker を移設 | 9,10 | 切替時に 2〜5 秒の無音 |
| 12 | soviet_now | audio worker を切り替え、旧実装を削除 | 11 | 同上 |
| 13 | docich | ラジオを移設 | 11 | 再起動で数秒ラジオ停止 |
| 14 | soviet_now | ラジオを切り替え、旧実装を削除 | 13 | 同上 |
| 15 | docich | コメントを移設 | 11 | 返信が数秒遅れる |
| 16 | soviet_now | コメントを切り替え、旧実装を削除 | 15 | 同上 |
| 17 | docich | BGM を移設 | — | 数秒無音 |
| 18 | soviet_now | BGM を切り替え、旧実装を削除 | 17 | 同上 |
| 19 | docich | status / event overlay の renderer を移設 | 3 | なし |
| 20 | 両方 | 第5段階: 残骸削除、`soren-runtime` の worker 縮小、ADR 更新、pin | all | なし |

## 5. 切替・中断・ロールバック

| フェーズ | 中断 | ロールバック |
| --- | --- | --- |
| 0〜5（表示データ） | なし。status は次 tick から反映 | 変更 PR を revert |
| 6〜8（overlay service） | 5〜15 秒、枠とサイドバーが欠ける。ゲーム窓・encoder は不変 | `soren-shared-overlay.service` を start、docich 側を stop |
| 9〜10（queue / fence） | なし | 変更 PR を revert |
| 11〜12（TTS / audio worker） | 2〜5 秒の無音。queue は保持 | soren 側 worker を再開 |
| 13〜16（ラジオ・コメント） | worker 再起動の数秒。映像は不変 | soren 側 worker を再開 |
| 17〜18（BGM） | 数秒無音 | `soren-bgm.service` を再開 |
| 19〜20 | なし | revert |

encoder（`docich-stream.service`）と Xvfb（`:99`）、RTMP relay は今回の移行では再起動しない。再起動が必要になった時点で、別の承認と作業窓を取る。

## 6. 本番での確認

- 実フレーム: `DISPLAY=:99 import -window root` で取得し、左余白カード・四面・crop・ゲーム切替を目視する。
- shared overlay: `/healthz` の `ready` / `browserReady` / `windowReady` / `layoutReady` / `overlayReady` と、`/__soren_overlay/broadcast/state` の内容。
- 音声: queue item の enqueue→played、fence 失効の拒否、source 別 dedupe、worker 再起動後の復帰。
- プロセス: encoder・ゲーム・音声の PID と起動時刻が、切替前後で意図せず変わっていないこと。
- 状態: `game_switch.json` の phase / active identity と、status カードの文言。

## 7. 未確認・リスク

- VM 再起動後の自動復帰、12:00 の正午監査、長時間配信の安定性は未確認。
- 複数 unit が systemd 上 "changed on disk"（daemon-reload 未実施）。unit を触る前に扱いを決める。
- 音声・ラジオ・コメントは合計 12,000 行超の shell。移設は挙動維持を優先し、Python への書き直しは別扱いにする。
- 同時進行の PR と衝突し得る。overlay HTML と state builder は特に、着手前に `origin/main` を再取得する。

## 8. 更新ルール

- 各 PR のマージ後、この台帳の「部品別の状態」と「PR 順序」を実測に合わせて更新する。
- 「移行済み」は、本番の実フレーム・state・PID のいずれかで確認できたものだけに使う。CI が緑、deploy が成功、だけでは移行済みとしない。
- 旧経路の削除は、同じ部品の docich 版が本番で動いている実測の後に行う。
