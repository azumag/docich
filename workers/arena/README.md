# docich-arena: 参加型ゲームアリーナ（オセロ MVP）

視聴者（同志）が Web ページを開いて AI と対戦できる参加型ゲームの Cloudflare Workers 実装。
設計の一次情報は [`docs/features/participatory-arena-othello-corner.md`](../../docs/features/participatory-arena-othello-corner.md)（docich 側）。

- issue: [azumag/docich#699](https://github.com/azumag/docich/issues/699)
- 構成: `workers/tsuitate-bot` と同じ Durable Object + SQLite + node:test の型。
  ルーム（`OthelloRoom`）とルーム上限管理（`ArenaLobby`）の 2 つの DO。
  DO 同士の呼び出しは持たない（lobby は TTL alarm でレコードを回収するだけ）。

## エンドポイント

| 種別 | パス | 内容 |
| --- | --- | --- |
| GET | `/` | ランディング（ゲーム一覧。未対応ゲームは「準備中」） |
| GET | `/othello` | 対戦クライアント（新規対戦を作成） |
| GET | `/othello/<roomId>` | 対戦クライアント（既存対戦に参加。席が空いていれば着席、なければ観戦） |
| GET | `/othello/view?room=<roomId>` | 観戦・配信用ビュー（960×540、snapshot を 1 秒轮番） |
| POST | `/api/othello/rooms` | 対戦を作成（body: `{"level":"easy"\|"normal"\|"hard"}`、省略時 normal） |
| GET | `/api/othello/rooms/<roomId>` | 公開スナップショット（観戦 API・docich 配信 view が消費する契約） |
| GET | `/api/othello/rooms/<roomId>/ws` | 対戦・観戦 WebSocket |

- roomId は 16 桁 hex。**ルーム一覧 API は存在しない**（URL を知る人だけが参加）。
- 席は先着 1 人（黒固定）。AI は白。手番・合法手・パス・終局はすべてサーバー権威。
- AI は決定論的（seed = roomId + 局数 + 手数）。同一局面なら同じ手を返す。

## 安全性と上限（設計 §5）

- ルーム作成は lobby が上限 16 を強制（超過 `429 room_cap`）。レコードは 30 分で回収。
- WebSocket 1 メッセージは 1024 文字まで。不正 JSON・未知 type は固定 error code、
  3 連続で切断。1 接続 20 msg/秒を超える送信も切断。
- 観戦者上限 100。DO ストレージは 1 ルーム 1 キー。30 分無活動で自消去
  （対戦中に着席者がいる間は維持、ただし最終更新から 60 分で強制消去）。
- 秘密値・認証・Origin 制限は未実装（MVP）。扱うデータはすべて公開可能な盤面のみ。

## ローカル検証

Node.js 22.18 以降が必要。Cloudflare アカウントには接続しない。

```sh
cd workers/arena
npm install
npm test               # engine / ai / protocol / room / worker の node:test
npm run test:workerd   # esbuild bundle + miniflare (workerd) 統合テスト
npm run build:check    # wrangler deploy --dry-run（配備しないビルド検証）
```

## 配備の境界

配備は Owner の明示的な操作でのみ行う。このリポジトリの CI はビルド検証までで配備しない。

```sh
# Owner が実行（wrangler の認証が必要）
npx wrangler deploy
```

- `wrangler.toml` の `compatibility_date` は `2026-09-08`（tsuitate-bot と同じ検証済み日付）。
- 配備後の URL 設定・docich 側配信 view（`arena-view`）・コーナーローテーション登録は
  後続 PR（設計 §6〜§7）で対応する。

## 既知の限定事項

- 視聴者同士の対戦（2 人席）は未対応。MVP は視聴者 vs AI のみ（オーナー確認済み 2026-10-18）。
- 色選択・ハンディキャップ・棋譜エクスポートは未対応。
- テトリス / ぷよぷよ / 衝立将棋 / FPS は後続 issue（設計 §2 のロードマップ）。
