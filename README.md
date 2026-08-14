# docich — マルチゲーム AI 配信基盤

docich は、VM 上で AI がゲームをプレイし、その画面と音声を ffmpeg で配信するための基盤である。
プレイするゲームは「アダプタ」で抽象化されており、`browser` (ブラウザゲーム) / `retroarch`
(レトロエミュレータ) / `cli` (端末ゲーム) を差し替えて同じ配信基盤の上で動かせる。

設計の一次情報は **`docs/architecture.md`**。本 README はそこへの入り口として、
クイックスタートと日常操作をまとめる。

## 表記ルール

- 【確認済】: 一次情報 (パッケージリポジトリ・`docs/architecture.md`・実機コンテナでの動作実証) で裏取りした事実
- 【要検証】: Oracle ARM 実機での検証が必要な事項

---

## 1. アーキテクチャ概要

ディスプレイ (Xvfb) と配信 (ffmpeg) を常駐させ続け、ゲームだけを入れ替える設計。
tmux セッション `docich` の中に、常駐コンポーネントごとの window が並ぶ:

```
tmux セッション "docich"
┌────────────────────────────────────────────────────┐
│ display  Xvfb :98 (1280x720)                        │
│ audio    null sink 確保 (既存 pulseaudio を再利用)   │
│ stream   ffmpeg (x11grab + pulse) → RTMP/file/null   │
│ game     アダプタが起動するゲーム本体                │
│           (retroarch / chromium / xterm+tmux)        │
│ agent    観測 → brain → 行動 のループ (ゲームごとに任意) │
└────────────────────────────────────────────────────┘
       docich CLI が各 window を生成/破棄する
       brain (外部コマンド) は stdin/stdout の JSON でつながる
```

- ゲーム切替 (`docich switch`) は game/agent window の作り直しだけで行われるため、
  **配信ストリームは切替中も途切れない**。
- ゲームの「頭脳」(brain) は外部コマンドとして差し替え可能。`claude` CLI・自作スクリプト・
  ランダムテスト器を同じ stdin/stdout JSON の口に差せる。

詳しい設計 (アダプタ契約・観測/行動 JSON スキーマ・配信コマンド構築・フェーズ計画など) は
**`docs/architecture.md`** を参照。上の図は簡略化してあるので、正確な構成は必ずそちらで確認すること。

---

## 2. soren との共存 (重要)

**この VM では、既に soren (sorengame) が本番稼働中である。** docich は soren のリソースに
一切触れず、名前空間を分離して同居する設計になっている (詳細: `docs/architecture.md` §0)。

| リソース | soren (稼働中) | docich |
|---|---|---|
| X ディスプレイ | `:99` | **`:98`** (既定。`:99` は使わない) |
| PulseAudio | 既存デーモン + 既定 sink | 同一デーモンに `docich_sink` を追加するのみ。**`set-default-sink` は実行しない** |
| tmux セッション | soren 側のセッション | `docich` / `docich-game` (名前分離) |
| 配信 | soren が配信中 | `stream.mode = "null"` が既定 (明示設定なしでは配信しない) |
| セットアップ | — | `scripts/setup_ubuntu_arm.sh` は apt install と mkdir のみ。既存サービス・設定は変更しない |

docich の導入・起動・停止が、稼働中の soren の配信・プロセスに影響を与えないことを設計上の
大前提としている。ただし実機上での最終確認は未実施の項目があるため、個々の挙動は
`docs/architecture.md` の 【要検証】表記に従うこと。

---

## 3. クイックスタート

対象は Ubuntu 24.04 (arm64/amd64)。

```bash
# 1. VM の依存パッケージを導入 (xvfb/tmux/ffmpeg/retroarch/nethack-console 等)
scripts/setup_ubuntu_arm.sh

# 2. 依存コマンド・環境の点検
bin/docich doctor

# 3. 基盤 (display/audio/stream) を起動
bin/docich up

# 4. ゲームを起動 (例: NetHack)
bin/docich start nethack

# 5. 状態確認・ゲーム切替・全停止
bin/docich status
bin/docich switch hanjuku-hero
bin/docich down
```

### ストリーム配信を有効にする

既定 (`config/docich.toml` の `[stream] mode = "null"`) では配信しない (事故防止。soren の
配信・キーと混同しないための既定でもある。§2 参照)。実配信する場合:

1. `config/docich.toml` の `[stream] mode` を `"rtmp"` に変更する。
2. ストリームキーは **環境変数のみ** で渡す (リポジトリ・設定ファイルには書かない):
   ```bash
   export DOCICH_STREAM_KEY="<配信キー>"
   ```
3. `bin/docich up` (または `down` → `up`) で反映する。

`mode = "file"` にすると `run/out.flv` へローカル保存でき、動作確認用に使える。

---

## 4. CLI コマンド一覧

`docs/architecture.md` §7 と同一の体系:

| コマンド | 説明 |
|---|---|
| `docich doctor` | 依存コマンド・環境の点検 (アダプタ別に OK/NG 表示) |
| `docich games` | `config/games/` の一覧と有効アダプタ |
| `docich up` / `down` | 基盤 (display/audio/stream) の起動・全停止 |
| `docich start <game>` | ゲーム起動 (`agent.enabled` なら agent も起動) |
| `docich stop` | 現在のゲーム停止 (基盤は残る) |
| `docich switch <game>` | `stop` + `start` (配信は継続) |
| `docich status` | 各コンポーネントの生死・現在のゲーム |
| `docich snap [-o out.png]` | 手動スクリーンショット |
| `docich obs [<game>]` | 観測 JSON を出力 (brain 開発用) |
| `docich send <game> '<json>'` | 行動を単発注入 (デバッグ用) |
| `docich ra-cmd <CMD>` | RetroArch へ UDP コマンド (`SAVE_STATE` 等) |
| `docich run <component> [...]` | (内部用) tmux window 内の supervise 実行 |

設定ファイルの探索順序: `--config` > `$DOCICH_CONFIG` > リポジトリの `config/docich.toml`。

---

## 5. 設定の要点

- **`config/docich.toml`**: グローバル設定 (`[display]` `[audio]` `[stream]` `[agent]` `[paths]`)。
  ディスプレイ番号・解像度、配信モード・ビットレート、agent の既定タイムアウトなどを定義する
  1 ファイル。`[display] number` の既定は `98`、`[audio] sink_name` の既定は `docich_sink`
  (§2 の共存ポリシーに対応した既定値)。
- **`config/games/*.toml`**: ゲーム定義。1 ゲーム 1 ファイルで、`[game]` (名前・アダプタ種別) +
  アダプタ固有セクション (`[retroarch]` / `[cli]` / `[browser]`) + `[agent]` (brain の有無・種類) を持つ。
  同梱例: `hanjuku-hero.toml` (retroarch) / `nethack.toml` (cli) / `sorengame.toml` (browser)。

各ゲームの個別セットアップ手順は `docs/games/` 以下を参照 (下記「関連ドキュメント」)。

---

## 6. ROM ポリシーとストリームキー

- **ROM**: 自己吸い出しした ROM のみを `games/roms/` に置く。このディレクトリの ROM 本体は
  git にコミットしない (README のみコミット対象)。docich は ROM の入手方法には一切関与しない。
  詳細は `games/roms/README.md` を参照。
- **ストリームキー**: `DOCICH_STREAM_KEY` などの環境変数からのみ読み込む。設定ファイルや
  リポジトリに直接書かない。ログ・`docich status` 等の表示ではキーをマスクする設計になっている
  (`docs/architecture.md` §5, §9-7)。

---

## 7. リポジトリ構成

```
docich/
├── README.md                   # 本ファイル
├── bin/docich                  # 起動ランチャ
├── src/docich/                 # Python パッケージ (標準ライブラリのみ)
│   ├── adapters/                #   retroarch / cli_game / browser
│   └── agent/                   #   loop + brains (command / random)
├── config/
│   ├── docich.toml              # グローバル設定
│   └── games/*.toml             # ゲーム定義 (1 ゲーム 1 ファイル)
├── games/roms/                  # ROM 置き場 (gitignore。README のみコミット)
├── scripts/
│   ├── setup_ubuntu_arm.sh      # VM 初期構築 (apt install + mkdir のみ)
│   └── smoke_cli.sh             # E2E スモーク
├── tests/                       # unittest
└── docs/
    ├── architecture.md          # 設計の一次情報 (共存原則 §0 含む)
    ├── oracle_arm_setup_guide.md
    ├── soren_linux_migration_plan.md
    └── games/                   # ゲーム別セットアップ手順
        ├── hanjuku-hero.md
        ├── nethack.md
        └── sorengame.md
```

---

## 8. 開発

```bash
# 単体テスト (pip 不要)
python3 -m unittest discover -s tests

# E2E スモーク (Xvfb + nethack + ffmpeg + xdotool)
scripts/smoke_cli.sh
```

---

## 関連ドキュメント

- 設計・用語の一次情報 (共存原則 §0 含む): `docs/architecture.md`
- ゲーム別セットアップ: `docs/games/hanjuku-hero.md` / `docs/games/nethack.md` / `docs/games/sorengame.md`
- soren game の Linux 移植計画: `docs/soren_linux_migration_plan.md`
- 実機 (Oracle Ampere A1) セットアップ: `docs/oracle_arm_setup_guide.md`
