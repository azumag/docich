# ADR: soviet_now をソ連ゲーム専用に縮小し、配信共通基盤を docich に移す

- 状態: **段階的移行を開始**（2026-11-06）。2026-11-09 実測: 第1段階（表示テンプレート）は本番適用済みで、配信は docich の HTML と state builder を使用。第2段階の表示データ整形、第3段階の shared overlay service、第4段階の音声・ラジオ・コメントは未移行。**最終形は soviet_now をソ連ゲーム専用にし、他ゲームの表示・配信共通コード・旧互換経路を残さない完全分離**とする（台帳: `docs/operations/common-migration-ledger.md`）。
- 所有者: 共通配信・音声・オーバーレイ・ゲーム切替・半熟英雄などは **docich**。ソ連ゲームの盤面、ルール、戦略、ゲーム固有の入力と評価のみ **soviet_now**。
- 原則: データ・契約の変更は生存するゲームと共通FFmpeg／音声を止めず、互換経路と回帰テストを伴う。独立の運用系に二重の「正典」を残さない。

## 責務と移行対象（現行のコードに基づく）

| 所有先 | コンポーネント例 | 移行 |
| --- | --- | --- |
| soviet_now | `soviet_local.mjs` の Unity WebGL入出力、`strategy.py` / `strategy_runner.py`、`analyze_board.py`、`soren_loop.sh`、`soren91/`、ソ連ゲーム専用観測／戦略 | 保持。ただし混在する配信部分を切り出す |
| docich | `overlays/direct_broadcast_overlay.html`（配信レール、半熟英雄速報／チャート） | **本PRで正典コピーを追加**。次のSoren互換PRでdocich版を優先して参照 |
| docich | `lib/direct_overlay.mjs`、`lib/direct_broadcast_overlay.mjs`、`lib/shared_overlay.mjs`、`shared_overlay.mjs`、`generate_*_overlay.*`、`overlay_notify.sh` | ゲーム固有stateを入力契約に分離してから移管 |
| docich | `lib/direct_stream.py`、`direct_stream.sh`、`start_shared_overlay_service.sh`、配信用 `deploy/`、OBS/TwiCa/RTMPS、監視と復旧 | 既存のdocich配信サービスへ統合。稼働中のencoderとserviceの所有権を一度に変更しない |
| docich | `broadcast/`、`workers/radio_worker.sh`、`say_enqueue.sh`、VOICEVOX/TTS、コメント・YouTube/Twitch連携、汎用リレー／通知 | producer/consumerの契約・フェンス・queue・永続化先を照合し段階的移管 |
| docich | 半熟英雄、NetHack、その他ゲーム固有の配信表示／制御 | ソ連ゲームrepoには追加しない |

## 移行の順序

1. **表示テンプレート**（本PR）: `docich/overlays/direct_broadcast_overlay.html` を追加し、`soviet_now` のHTMLと同一の既知良好版を採用。次のSoren側PRは `DOCICH_ROOT/overlays/direct_broadcast_overlay.html` を優先。明示パスは尊重し、docich未配備なら旧ファイルへfail-openする。
2. **表示データ／読み取り側**: `lib/direct_broadcast_overlay.mjs` の汎用state整形・半熟英雄表示をdocichへ。soviet_nowのgame adapterはゲーム固有の情報だけを配信契約へ提供する。Chromium合成／配置／state fencingを実フレームで検証。
3. **共有配信service**: FFmpeg、Xvfb、オーバーレイservice、外部ゲームのpresentation、TwiCa、OBS、監視をdocichに寄せる。service起動の所有権切替は冪等で、片系が動く状態で二重起動しない。
4. **音声・ラジオ・コメント**: worker、TTS、queue、radio、ニュース、コメント連携をdocichへ。queueロック、再生のruntime fence、ソース別dedupe、停電復旧を互換テストで守る。
5. **掃除と境界固定**: soviet_now側から不要になった共通コード／テスト／workflowを削除し、ソ連ゲーム固有の公開インターフェースだけを維持。docichのsubmoduleは引き続きソ連ゲームへの依存として使用。

## 現状の実測（2026-11-09 18:00 JST）

- 表示テンプレート: 実描画は `/home/ubuntu/docich/overlays/direct_broadcast_overlay.html`。soviet_now 側のコピーはフォールバック。
- 表示 state: shared overlay の `/__soren_overlay/broadcast/state` は docich の builder が応答する（`nextCorner` を含む）。
- encoder: `docich-stream.service` が唯一の所有者（ADR 0007 段階 E 適用済み）。
- shared overlay service: `soren-shared-overlay.service` が `/home/ubuntu/soren` のコードで稼働。未移行。
- 音声・ラジオ・コメント・BGM: `soren-runtime.service` 配下の soviet_now コード。未移行。
- 半熟の表示データ整形: `lib/docich_corner_stats.py` と `status_dashboard.py`。未移行。

## 暫定互換と完了条件

- 表示の実行経路はまだ `soviet_now/lib/direct_overlay.mjs` にある。**本PRのHTML追加のみでは本番表示は切り替わらない**。
- 同期順は docich（HTML配備）→ soviet_now（docich優先のresolver）→ docich（submodule更新）。既存 `SOREN_DIRECT_BROADCAST_OVERLAY_HTML_FILE` が明示されている場合は優先されるため、配備時に確認する。
- 本番切替前後に、Soren表示・半熟英雄の左余白・四面readiness・画面crop・ゲーム切替・VM現物SHAを照合。encoder、ゲーム、音声のPIDを保持。VMデプロイ・再起動はこのPRに含めない。
- 旧パスの撤去はdocich版の実配信確認後に別PRで行う。旧ファイルを一括削除するとソ連ゲーム自身の起動・テスト・リカバリーが壊れるため禁止。
- **最終完了**: soviet_nowがソ連ゲーム固有の実装のみを保持し、共通コードの正典、CI、配布・実行serviceをdocichが所有する。移行期間は限定的に重複を許容し、期限なく二重保守しない。他ゲームの表示（半熟・NetHack 等）、配信共通の overlay service・音声・ラジオ・コメント・BGM、status 表示の renderer は soviet_now に残さない。旧互換経路は docich 版の本番実測後に削除する。
