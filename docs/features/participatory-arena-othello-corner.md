# 参加型アリーナ（オセロ MVP）— 設計提案

- Status: **PR-1 実装済み（Worker コア）**。docich 側の配信 view 統合・Cloudflare 配備は未着手（§7）。
- 関連 Issue: [azumag/docich#699](https://github.com/azumag/docich/issues/699)（参加型　同志と対戦できるぷよぷよ、テトリス、オセロ、衝立将棋、FPS）
- 関連実装（既存の参加型・Worker 基盤）:
  - `workers/tsuitate-bot/`（Durable Object + SQLite + node:test + workerd 検証の先例）
  - `src/docich/tsuitate_view.py` / `tsuitate_corner.py`（配信 view 型コーナーの先例）
  - `docs/features/participatory-trpg-murder-mystery-corner.md`（参加型コーナー設計のテンプレート）
  - issue #698「ぷよぷよクローン」（別スレッドで進行中。落下型ゲームはそのコアと連携する）

## 0. 何を作るか

視聴者（同志）が **Web ページを開いて AI（docich 側）と直接対戦できる**参加型ゲーム基盤。
配信画面には観戦用ビューを映し、視聴者は配信に表示された URL から参加する。

- 対戦形態は **視聴者 vs AI**（オーナー確認済み、2026-10-18）。視聴者同士対戦は後続拡張。
- 最初のゲームは **オセロ**（オーナー確認済み）。完全情報・ターン制で、サーバー権威実装・
  観戦 API・配信 view の土台を最小構成で作り込む。
- 実行場所は **Cloudflare Workers**（オーナー確認済み）。`workers/tsuitate-bot` と同じ
  Durable Object + SQLite + node:test のパターンで、docich VM に常駐物を増やさない。
- ゲーム本体・配信 encoder・共通音声基盤は変更しない（AGENTS.md の原則）。

## 1. 全体アーキテクチャ

```
視聴者ブラシ／配信ブラウザ
   │  HTTPS / WebSocket
   ▼
Cloudflare Worker（docich-arena）
   ├─ GET  /                  ランディング（ゲーム一覧）
   ├─ GET  /othello           対戦クライアント（新規作成）
   ├─ GET  /othello/<roomId>  対戦クライアント（既存ルーム参加）
   ├─ GET  /othello/view      観戦・配信用ビュー（960×540、snapshot 輪番）
   ├─ POST /api/othello/rooms        ルーム作成（ArenaLobby が上限管理）
   ├─ GET  /api/othello/rooms/<id>   スナップショット JSON（観戦 API・公開契約）
   └─ GET  /api/othello/rooms/<id>/ws  対戦・観戦 WebSocket
        ▼
   OthelloRoom（Durable Object / SQLite）: 盤面・手番・席・履歴・勝敗を権威管理
   ArenaLobby（Durable Object / SQLite）: 活動ルーム数の上限とレコード回収
```

- オセロは完全情報ゲームなので、盤面・手番・勝敗はすべて公開データであり
  配信へ出してよい（衝立将棋と異なり隠し情報がない）。秘密値・token はこの PR では扱わない。
- サーバー（OthelloRoom）が唯一の権威であり、クライアント計算は信用しない。
  合法手判定・自動パス・終局・得点はすべてサーバー側エンジンで行う。

## 2. ゲームロードマップ（#699 の 5 ゲーム）

| ゲーム | 状態 | 方針 |
| --- | --- | --- |
| オセロ | **本 PR（コア）** | 同一 OthelloRoom 型。リアルタイム性が低く土台に最適 |
| テトリス / ぷよぷよ | 未着手 | issue #698 のクローンコアと連携。落下型の同期方式（操作ロック step 方式）を次 PR で設計 |
| 衝立将棋 | 未着手 | `workers/tsuitate-bot` の brain（`src/brain/`）をアリーナ席に再利用。観戦 view は既存の spectator-safe 契約を踏襲 |
| FPS | 未着手 | リアルタイム性・チート耐性が別次元。独立した設計 PR を先に立てる |

各ゲームは「ルーム DO 型 + 公開 snapshot 契約 + 観戦ビュー」という同じ型で追加する。

## 3. オセロ仕様（MVP）

- 8×8、初期配置は公式（d4/e5 白、e4/d5 黒）。**人間は黒（先手）固定、AI は白**。色選択は後続拡張。
- 合法手は盤面・手番からサーバーが計算する。打てる場所がなければ**自動パス**、
  両者打てなければ終局。反則手は存在しない（合法手以外は受理しない）。
- 座標表記は標準オセロ表記（`a1`〜`h8`）。列 a-h・行 1-8。
- 終局理由: `score`（通常終局）/ `resign`（投了）。
- 再戦: 対戦者だけが `rematch` を送れる。勝敗カウンタ（人間/AI/引分）はルーム内で保持し、
  配信ビューで連戦成績として表示できる。
- 難易度 3 段階（ルーム作成時に選択）:
  - `easy`: 合法手から seed 付き乱数で選択
  - `normal`: 位置重みテーブル + 移動可能数を 2 手先まで読む
  - `hard`: 位置重み + 移動可能数 + 境界評価で 4 手先の alpha-beta 探索
- AI は**決定論的**（seed = ルーム ID + 手数から導出）。同一局面・同一 seed なら同じ手を返し、
  対局の再現・テストが可能。LLM や外部モデルは使わない（モデル連携は将来拡張）。

## 4. データ契約（公開 snapshot）

`GET /api/othello/rooms/<id>` と WebSocket `state` は同じ形の JSON を返す。
PR-2 の docich 配信 view はこの契約だけに依存する。

```json
{
  "schemaVersion": 1,
  "kind": "arena_othello",
  "roomId": "0f3a9c1e77b24d6a",
  "status": "waiting|playing|ended",
  "board": "64文字（'.' / 'B' / 'W'）",
  "turn": "black|white|none",
  "moveNumber": 12,
  "lastMove": { "coord": "d3", "color": "black", "pass": false },
  "seats": {
    "black": { "kind": "human", "name": "しおん" },
    "white": { "kind": "ai", "level": "normal", "name": "DoCi AI" }
  },
  "score": { "black": 2, "white": 2 },
  "wins": { "human": 1, "ai": 0, "draw": 0 },
  "result": null,
  "updatedAt": "2026-10-18T00:00:00.000Z"
}
```

- `result` は終局時のみ: `{ "winner": "black|white|draw", "reason": "score|resign", "black": n, "white": n }`。
- ルーム ID は 16 桁 hex の推測不能値。**ルーム一覧 API は提供しない**（URL を知る人だけが参加）。
- 履歴（棋譜）は WebSocket `state` に含めず、ルーム内で直近 256 手に有界化して保持する。

## 5. 安全性と上限（fail-closed）

- ルーム作成は `ArenaLobby` が上限（既定 16）を強制し、超過は `429 room_cap`。
  Lobby は alarm で 30 分より古いレコードを回収する。**公開ルーム一覧は存在しない**。
- WebSocket 1 メッセージは 1 KiB まで。超過・不正 JSON・未知 type は固定 error code で拒否し、
  連続不正は切断する。1 接続あたり 20 msg/秒を超える送信も切断する。
- 対戦席はルーム 1 つにつき 1 人（先着）。URL を開いた 2 人目以降は自動で観戦者になり、
  対戦席が空いているときだけ `hello` で着席できる。観戦者上限 100（超過は `429` 相当の error 後切断）。
- DO ストレージは 1 ルーム 1 キー（< 8 KiB）。終局後 30 分無活動、または着席者不在のまま
  30 分経過したルームは alarm で自消去する（storage 削除 + lobby レコードは lobby 側 TTL で回収）。
- ルーム ID・座標・席以外の自由入力は表示名（≤ 20 文字、制御文字除去）のみ。ログに本文を出さない。
- この PR は秘密値・認証を持たない。Origin 制限も未実装であり（WebSocket は本質的に cross-origin 可）、
  公開データのみを扱う現仕様では許容する。運用で問題が出た場合は PR-2 以降で Owner 設定の
  Origin allowlist を追加する。

## 6. docich との関係（本 PR では変更しない）

- 配信への出し物は「OthelloRoom の公開 snapshot」であり、docich 側は PR-2 で
  `arena-view`（`tsuitate_view.py` と同型の loopback view / 960×540）を追加し、
  snapshot URL を轮番取得して表示する計画。view 標準（`tests/test_broadcast_view_standard.py`）
  はその PR で対象追加する。
- コーナーローテーション（`corner_catalog.py`）への登録も PR-2。本 PR は production の
  corner catalog を変更しない。
- 設定は `wrangler.toml`（tsuitate-bot の `cf` 設定と異なり、同一Worker内DOバインディングが標準記法で書ける）。配備は Owner が `npx wrangler deploy` で行う想定。本 PR はビルド検証までで配備しない。

## 7. 実装（PR-1 の範囲）

- `workers/arena/src/othello/engine.js`: 純粋なオセロ規則（合法手・反転・パス・終局・得点）
- `workers/arena/src/othello/ai.js`: 3 難易度の決定論的 AI（純粋関数）
- `workers/arena/src/protocol.js`: WS メッセージ検証（型・長さ・固定 error code）
- `workers/arena/src/arena/room.js` / `lobby.js`: Durable Object（SQLite、hibernation WebSocket）
- `workers/arena/src/pages.js` / `worker.js`: クライアント HTML（ja / notranslate 標準）とルーティング
- `workers/arena/test/`: engine / ai / protocol / room / worker の node:test と
  miniflare（workerd）統合テスト。Cloudflare アカウントには接続しない。
- `.github/workflows/arena-worker.yml`: `wrangler deploy --dry-run`（配備しないビルド検証）+ node:test + workerd 統合テスト

## 8. 検証と残作業

```sh
cd workers/arena
npm install
npm test               # engine / ai / protocol / room / worker の node:test
npm run test:workerd   # esbuild bundle + miniflare (workerd) 統合テスト
npm run build:check    # wrangler deploy --dry-run（配備しないビルド検証）
```

残作業（別 PR）:

1. docich 側 `arena-view` + corner catalog 登録 + `tests/test_broadcast_view_standard.py` 追加（PR-2）
2. Owner による Cloudflare 配備とURL設定
3. テトリス / ぷよぷよ（#698 連携）、衝立将棋（brain 再利用）、FPS の各設計・実装
