# 参加型ゲームコーナー（MVP: オセロ）— 設計

- Status: **MVP 実装中（オセロ offline core + Worker）**。production の corner catalog は未変更（有効化は別途オーナー承認）。
- 関連 Issue: [azumag/docich#699](https://github.com/azumag/docich/issues/699)（参加型 同志と対戦できるぷよぷよ、テトリス、オセロ、衝立将棋、FPS）
- 仕様決定（2026-10-18 オーナー回答）:
  - **MVP はオセロから開始**。プラットフォーム土台（ルーム管理・観戦・配信view）をオセロで作り込み、
    ぷよぷよ・テトリス・衝立将棋・FPS は後続 issue で追加する。
  - **対戦形態は視聴者 vs AI**: 配信中の docich brain に視聴者が挑戦する。ルーム制で観戦もできる。
  - **サーバーは Cloudflare Workers + Durable Objects**（既存 `workers/tsuitate-bot` と同じパターン）。

## 1. MVP スコープ

### 1.1 オフラインコア（`src/docich/othello_corner.py`）

純Pythonの規則・状態機械・検証のみ。Twitch I/O、シェル実行、GameSwitch、production書込みは行わない
（`trpg_corner` と同じオフライン先例）。

- 標準8×8オセロ: 初期配置、合法手（8方向の挟み判定）、着手・裏返し、パス（自動付与）、終局、勝敗。
- ルーム状態機械: `IDLE → ROOM_OPEN → PLAYING → FINISHED`（＋ `FAILED` / `TIMEOUT`）。
  - 視聴者席は **黒**（先手）。AI 席は **白** の docich brain。
- docich brain: **位置評価の決定論的貪欲**（角=120、辺=20、C/X=負、他=小値）。
  同一局面で常に同じ手を出し、視聴者視点の再現性と Workers 側の同一性検証を可能にする。
- 席交代: 視聴者が着手すると AI が応手し、着手＋応手は 1 トランザクションで確定する
  （観戦者が「途中の手」を見ない）。
- パス: 相手に合法手がなければ手番を持つ側に戻し、両者とも打てなければ `FINISHED`。
  暗黙の強制終了はしない（AGENTS.md「試合終了待ち」原則）。
- fail-closed: 入力長・着手座標の範囲・最大手数（`MAX_MOVES`）、棋譜上限（`MAX_HISTORY_ENTRIES`
  / `MAX_BYTES`）、atomic write。文脈から取れる視聴者情報は表示名のみ。

### 1.2 Worker（`workers/othello-bot/`）

`workers/tsuitate-bot` と同じ Workers + Durable Objects 構成。

- `OthelloRoom` Durable Object: ルーム 1 つにつき 1 インスタンス。SQLite storage に
  `othello:state`（schema 1 の全 state）を保存する。
- 経路（`src/worker.js`、固定 allowlist、秘密・ログに出さない固定 error code）:
  - `GET  /room/snapshot?id=<roomId>` — 観戦者 safe projection（`spectatorSnapshot`）
  - `POST /room/open-room` — ルーム作成（viewer seat 名の検証、`ROOM_OPEN` へ）
  - `POST /room/start-match` — 標準配置で `PLAYING` へ
  - `POST /room/viewer-move` — 視聴者着手（AI 応手は同一トランザクション内）
  - `POST /room/fail` / `POST /room/timeout` — 明示的終了。無理な結果を捏造しない。
- 並行性: DO 操作は `tail` promise chain 直列化。ビューと着手の競合で半端な盤面は出ない。
- 制限: body 4 KiB / 各操作 2.5 s DO 予算 / リクエスト全体 7 s。worker は HTMLファイルを
  出さない（view は配信 HTML 標準に従う別プロセス）。
- `wrangler.toml`: `docich-othello-bot`、`OthelloRoom` SQLite migration v1。ローカル検証は
  `workers/othello-bot` で `npm test`（node --test）。実デプロイは tsuitate-bot と同じ
  検証済み経路（BUILDS.md）を別PRで行う。

### 1.3 配信 view（`src/docich/othello_view.py` + `adapters/othello_program.py`）

- 960×540 の読み取り専用合成ビュー。旧方式（weather / paper / tsuitate / doushi / yosou）と同じ
  共通標準: `--disable-translate` 等の chromium 引数、`<html lang="ja" translate="no">` ＋
  `<meta name="google" content="notranslate">` を view telemetry で検証
  （`tests/test_broadcast_view_standard.py` に `othello-view` を追加済み）。
- view は `/api/othello` で room Worker snapshot を 1 秒間隔で取り込み、盤面・手番・スコア・
  結果を再構築して表示する。情報は `spectatorSnapshot` の allowlist のみ（生 state は出さない）。
- ループバックのみで listen、認証・secret は view session 環境へは `DOCICH_OTHELLO_CONTROL_URL`
  の 1 項目だけを明示的に渡す（他 token は出さない）。URL は argv や state に出さない。
- オセロは隠し情報が存在しないので、衝立将棋のような fog 制約は無し。
  ただし生 state はprojection だけで出す（unavailable は fail-closed）。

## 2. 後続 issue に分離する部分

- ルーム一覧・観戦ルームの一覧化、複数ルーム同時実行、同時実行レースの排他（1部屋→N部屋）。
- トークン/チャット連携（視聴者着手の受け口はビューのチャット経路。本 PR では未接続）。
- 半熟英雄の `audio_enabled` / TTS 読み上げ・感想戦（trpg/tsuitate の narration パターン）。
- catalog への `{ id = "othello", adapter = "othello", game = "othello-view" }` 追加本番
  （有効化前提の GameSwitch 結合・rotation慎重性検証を含む。要オーナー承認）。
- 他ゲーム（ぷよぷよ・テトリス・衝立将棋・FPS）の `rules.js` 追加と同一回線への結線。

## 3. 検証

- `python3 -m pytest tests/test_othello_corner.py tests/test_othello_view.py` — オフラインコア
  （50）＋ view（7）。
- `python3 -m pytest tests/test_broadcast_view_standard.py` — 配信 HTML 標準（9）。
- `cd workers/othello-bot && npm test` — rules（9）＋ DO room（8）。

未定義事項・断念: 実 DO / secret / deploy / 実対局は本 PR では行わない
（`workers/tsuitate-bot/BUILDS.md` の同じ制約）。
