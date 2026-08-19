# 対応ゲーム

## 一覧

| ゲーム | アダプタ | 状態 | 関連サブモジュール |
|---|---|---|---|
| [[NetHack|Game-NetHack]] | `cli` | 標準例。テキスト観測、コンテナでの動作実証済み。既定で agent 無効 | なし |
| [[半熟英雄 (SFC)|Game-Hanjuku-Hero]] | `retroarch` | `dbus-run-session` での起動が必須。Phase 1 時点は `send` での単発操作確認まで、本物の brain は Phase 2 | `games/hanjuku-sfc-speedrun` (知識ベース) |
| [[soren game|Game-Sorengame]] | `browser` | viewer 専用。`http://127.0.0.1:8080` を表示するのみで agent 無効。本番は soviet_now が所有 | `games/soviet_now` |

各ゲームの定義は `config/games/<name>.toml` にある (1 ゲーム 1 ファイル)。有効なゲーム名と
アダプタは `bin/docich games` でも確認できる:

```bash
bin/docich games
```

## 新しいゲームを追加する

1. `config/games/<name>.toml` を書く: `[game]` (名前・タイトル・`adapter`) +
   アダプタ固有セクション (`[retroarch]` / `[cli]` / `[browser]`) + `[agent]` (brain の有無)。
   既存の 3 ファイルがそのままテンプレートになる。
2. アダプタを選ぶ: 端末で完結するゲームなら `cli`、SFC 等のレトロエミュレータなら
   `retroarch`、ブラウザゲームなら `browser`。
3. `bin/docich doctor` で、選んだアダプタが要求する依存コマンドが揃っているか確認する。
4. `bin/docich start <name>` で起動する。

`cli` アダプタは `[cli] command` を差し替えるだけで**任意の CLI/TUI ゲーム**が同じ仕組みで動く
(NetHack はその一例に過ぎない)。ゲームの生死は専用 tmux セッションの生死で判定されるため、
コマンドを差し替えるだけで新しいゲームの動作確認が始められる。

設計の詳細 (アダプタ契約・観測/行動 JSON のスキーマ) は [[アーキテクチャ|Architecture]] を参照。
