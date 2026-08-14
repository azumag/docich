# アーキテクチャ

設計の一次情報は
[docs/architecture.md](https://github.com/azumag/docich/blob/main/docs/architecture.md)。
本ページはそのダイジェストである。正確な仕様・裏付け (【確認済】/【要検証】の区別を含む) は
必ず一次情報側で確認すること。

## 基本方針

1. **ディスプレイは常に生かし、ゲームだけを入れ替える**: Xvfb (`:98`, 1280x720) と
   PulseAudio null sink を常駐させ、ffmpeg はその画面+音声を配信し続ける。ゲーム切替は
   「ディスプレイ上のアプリの入れ替え」であるため、配信は途切れない。
2. **OBS は使わず ffmpeg 直結**: `x11grab + pulse → libx264 → RTMP` の単一 ffmpeg プロセスに
   簡素化する。オーバーレイが必要になれば drawtext から始め、OBS は将来の選択肢として残す。
3. **ゲームは「アダプタ」で抽象化する**: lifecycle (start/stop/alive) と AI I/O
   (observe/act) の 2 面の契約を実装する。配信・切替・エージェントループはアダプタの中身を
   知らない。
4. **brain (AI の頭脳) は外部コマンドとして差し替え可能にする**: 「観測 JSON を stdin に渡し、
   行動 JSON を stdout から受け取る」境界だけを定義する。`claude` CLI・自作スクリプト・
   ランダムテスト器を同じ口に差せる。
5. **依存は最小に**: オーケストレータは Python 3.11+ 標準ライブラリのみ (tomllib/argparse/
   subprocess/json)。外部コマンド (tmux/ffmpeg/Xvfb/pulseaudio/xdotool/retroarch/xterm) の
   みに依存し、pip 依存はゼロ。

## アダプタ契約

```python
class Adapter:
    def command(self) -> list[str]:          # ゲーム本体の起動コマンド
    def prepare(self) -> None:                # 起動前フック (cfg 生成・ROM 存在確認など)
    def cleanup(self) -> None:                # 停止後フック

    def observe(self) -> Observation:         # 現在の画面/状態を取得
    def act(self, action: Action) -> None:    # 行動を注入
```

現在 3 種類 (`retroarch` / `cli` / `browser`)。アダプタオブジェクトはステートレスに近く、
observe/act は都度 X11/tmux に問い合わせる。プロセスの生死は tmux window/セッションの生死で
判定する (pid ファイル管理をしない)。

### 観測 (Observation) JSON の最小例

`cli` アダプタ (NetHack) の例。CLI ゲームは画面がそのままテキストで観測できるのが利点:

```json
{
  "game": "nethack",
  "title": "NetHack",
  "adapter": "cli",
  "kind": "text",
  "text": "80x24 の端末画面ダンプ...",
  "meta": {}
}
```

`retroarch` / `browser` アダプタは `kind: "screenshot"` で `screenshot` に PNG のパスが入る
(brain 側がファイルを読んで解釈する)。

### 行動 (Action) JSON の最小例

```json
{"type": "pad", "buttons": ["down", "a"], "hold_ms": 150}
```

`pad` は SFC パッドの**意味ボタン**を同時押しする (`retroarch` アダプタのみ対応)。意味ボタン
→ 物理キーの解決はアダプタの責務であり、brain は RetroArch のキーバインドを知らずに
「SFC の A ボタン」とだけ言えばよい。これがゲーム差し替えの要になっている。他に
`key` / `text` / `special` / `mouse` / `wait` がある (型ごとの対応アダプタは
`docs/architecture.md` §3.2)。

## プロセスモデル

```
tmux セッション "docich"
  window: display   Xvfb :98
  window: audio     null sink 確保 ([audio] enabled=false なら省略)
  window: stream    ffmpeg ([stream] mode="null" なら省略)
  window: game      アダプタが起動するゲームプロセス
  window: agent     観測→brain→行動ループ ([agent] enabled のゲームのみ)
```

各 window は直接コマンドを実行するのではなく `docich run <component>` (監督ループ) を実行
する。子プロセスがクラッシュすると指数バックオフで再起動し、`run/logs/<component>.log` に
ログを残す。

`cli` アダプタのゲーム本体は、この `docich` セッションとは別の tmux セッション
`docich-game` の中で動く。`docich` セッション側の `game` window は、それを読み取り専用
(`tmux attach -r`) で映している xterm でしかない。ゲームの生死は `docich-game` セッションの
生死で判定するため、xterm 表示側が落ちても (映像が消えるだけで) ゲーム進行自体は失われない。

## マルチリポジトリ構成

docich を親 (配信基盤) とし、ゲーム固有の実装は別リポジトリを git submodule として `games/`
配下に取り込む構成になっている:

| リポジトリ | 役割 |
|---|---|
| **docich** (親、本リポジトリ) | 配信基盤: ディスプレイ/音声/ffmpeg 配信、ゲーム切替、アダプタ契約、エージェントループ、運用 CLI。ゲーム非依存の共通部品の置き場 |
| `games/soviet_now` (submodule) | sorengame (ソ連ゲー) 本体。現状は配信・チャット・ラジオ等も同居しており、段階的に共通部品化していく計画 |
| `games/hanjuku-sfc-speedrun` (submodule) | 半熟英雄の RTA チャート・ゲーム機構データ。Phase 2 brain の知識ベース |

コメント応答・ラジオ・オーバーレイ・TTS などは「参照利用 → 実証後に docich へ昇格」という
段階方式 (C0〜C4) で共通部品化する計画になっている (C0 = サブモジュール組み込みと main 監視
の開始。現在地はここ)。詳細は
[docs/multi_repo_plan.md](https://github.com/azumag/docich/blob/main/docs/multi_repo_plan.md)
を参照。

## soren 共存原則 (要約)

**この VM では soren (sorengame) が既に本番稼働中である。** docich は soren のリソースに
一切触れず、名前空間を分離して同居する:

| リソース | soren (稼働中) | docich | 分離方法 |
|---|---|---|---|
| X ディスプレイ | `:99` | `:98` (既定) | 別番号を既定にする |
| PulseAudio | デーモン + 既定 sink | 同一デーモンに `docich_sink` を追加するのみ | `set-default-sink` は実行しない (`set_default=false` が既定) |
| tmux セッション | soren 側のセッション | `docich` / `docich-game` | 名前分離。他セッションに触れない |
| 配信 | soren が配信中 | `stream.mode = "null"` が既定 | 明示設定なしでは配信しない (キー競合事故の防止) |

詳細・裏付け (【確認済】/【要検証】の別) は `docs/architecture.md` §0 を参照。

## 詳細はこちら

- [docs/architecture.md](https://github.com/azumag/docich/blob/main/docs/architecture.md) — 設計の一次情報 (アダプタ契約全文・配信コマンド構築・エージェントループ・設定スキーマ・リスクと落とし穴・フェーズ計画)
- [docs/multi_repo_plan.md](https://github.com/azumag/docich/blob/main/docs/multi_repo_plan.md) — マルチリポジトリ構成・共通部品化ロードマップ・soviet_now 監視の運用
- [[トラブルシューティング|Troubleshooting]] — 既知の落とし穴の逆引き
- [[対応ゲーム|Games]] — 各アダプタの実例
