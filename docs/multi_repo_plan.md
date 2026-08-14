# マルチリポジトリ構成計画 — docich を親、ゲーム実装をサブモジュールに

docich を「配信の基盤 (とりまとめ)」とし、各ゲームに固有の実装は**別リポジトリ (git submodule)** に置く構成の設計書。
共通部品化の対象と手順、および **soviet_now で並走中の Codex 作業との協調ルール**を定める。

## 表記ルール

- 【確認済】: 一次情報 (各リポジトリの実クローン・実コミット) で裏取りした事実
- 【要検証】: 実機・実運用での検証が必要な事項

---

## 1. 構成

```
docich/                            # 親: 配信基盤 (このリポジトリ)
├── src/docich/                    #   ゲーム非依存: アダプタ/配信/エージェント/CLI
├── config/games/*.toml            #   ゲーム定義 (サブモジュール内の実装を参照する)
├── games/
│   ├── roms/                      #   ROM 置き場 (gitignore)
│   ├── soviet_now/                # submodule → azumag/soviet_now (main 追跡)
│   └── hanjuku-sfc-speedrun/      # submodule → azumag/hanjuku-sfc-speedrun (main 追跡)
└── docs/ wiki/ scripts/ ...
```

| リポジトリ | 役割 |
|---|---|
| **docich** (親) | 配信基盤: ディスプレイ/音声/ffmpeg 配信、ゲーム切替、アダプタ契約、エージェントループ、運用 CLI。**ゲーム非依存の共通部品の置き場** (§3) |
| **soviet_now** | sorengame (ソ連ゲー) 本体: Unity WebGL 自動プレイ (soviet_local.mjs / strategy.py / soren91)、および現状は配信・チャット・ラジオ等の一式も同居 (§3 で段階的に共通部品化) |
| **hanjuku-sfc-speedrun** | 半熟英雄の RTA チャート・ゲーム機構データ (卵落ち判定・切り札ダメージ等) と分析ツール。【確認済】Phase 2 で作る半熟英雄 brain の**知識ベース** (プロンプトの grounding 素材) |

### 1.1 clone / 更新の作法

```bash
git clone --recurse-submodules https://github.com/azumag/docich.git
# 既存 clone に後から:
git submodule update --init

# サブモジュールを最新 main に進める (docich 側の明示的な操作でのみ行う):
git submodule update --remote games/soviet_now
git add games/soviet_now && git commit -m "Bump soviet_now to <sha> (<理由>)"
```

- **サブモジュールのポインタ更新は「意図を持った commit」**として docich に記録する (自動追従はしない。§4 の監視が更新の契機を検知し、内容を確認してから進める)。
- docich のセットアップ/CI/スクリプトがサブモジュール内を参照するときは `games/<name>/...` の相対パスを使う。

### 1.2 ゲーム定義との接続

- `config/games/sorengame.toml`: 形態 B (soren 本体で運転) の `launch_command` は `games/soviet_now/` 配下の起動スクリプトを指す (Codex の直結配信移行の完了後に実パスを確定する。§5)
- `config/games/hanjuku-hero.toml`: Phase 2 の brain (`[agent] command`) は `games/hanjuku-sfc-speedrun/` のチャート・データを読み込む実装をサブモジュール側 (または docich 側 brain スクリプト + サブモジュール参照) に置く

---

## 2. soviet_now の現状把握 (2026-08-14 時点)

【確認済】main 先端 = `8b210a5` "Merge pull request #97 from azumag/codex/soren-ffmpeg-direct" (2026-08-12)。
**Codex が OBS → ffmpeg 直結配信への移行を進行中** (`direct_stream.sh` / `cutover_direct_stream.sh` /
`configure_linux_stream_profile.sh` / `install_direct_stream_relay.sh` / soak・recovery テスト群)。
docich の設計 (ffmpeg 直結) と同一方向であり、共通部品化の合流先として好条件。

共通部品候補の所在 (【確認済】実クローンのファイル配置):

| 領域 | soviet_now 内の実体 |
|---|---|
| コメント応答 | `broadcast/comment.sh`, `broadcast/comment_lib.sh`, `twitch_chat*.sh`, `youtube_chat.sh`, `viewer_chat_monitor.sh` |
| ラジオ | `broadcast/radio_engine.sh` ほか radio_* 一式 (persona/news/corners/factcheck/quality/state/themes/celebration), `fetch_radio_grounding.py`, `fetch_news.sh` |
| 配信レイアウト (オーバーレイ) | `generate_status_overlay.sh`, `generate_event_overlay.py`, `generate_improve_overlay.sh`, `generate_dashboard.sh`, `dashboard_data.py` ほか |
| 音声 (TTS/再生) | `say_enqueue.sh`, `google_tts.sh`, `coeiroink_tts.sh`, VOICEVOX 経路 |
| 配信本体 | `direct_stream.sh` 系 (Codex 移行中) |
| ゲーム固有 (共通化しない) | `soviet_local.mjs`, `strategy*.py`, `analyze_*.py`, `soren91/`, eloop 系 |

---

## 3. 共通部品化ロードマップ

**原則: 「移動」より先に「参照」。** docich からサブモジュール内のスクリプトを呼ぶ形で共通利用を始め、
ゲーム非依存であることが実証できた部品だけを docich 側へ昇格させる。昇格時は soviet_now 側を薄い
ラッパ (docich 呼び出し) に置換して二重管理を避ける — これは soviet_now 側の変更を伴うため
**Codex の作業完了とユーザー合意の後**に行う。

| 段階 | 内容 | 前提 |
|---|---|---|
| C0 (完了) | サブモジュール組み込み・main 監視の開始 | 本書 |
| C1 | Codex の直結配信移行の完了確認 → soviet_now の配信経路と docich `stream.py` の役割整理 (どちらを正とするか判断) | §4 の監視で main 更新を検知 |
| C2 | コメント応答・ラジオを docich から**参照実行**するアダプタ非依存の口を設計 (`docich chat` / `docich radio` 相当。ゲーム名を渡すだけで動く形) | C1 |
| C3 | オーバーレイ: docich の ffmpeg drawtext フック + `generate_*_overlay` の生成物を接続 | C1 |
| C4 | 実証済み部品の docich への昇格 (tts/ → chat/ → radio/ の順を想定)、soviet_now 側のラッパ化 | C2/C3 + ユーザー合意 |

---

## 4. 監視 — 「改善が入ったら動く」の仕組み

**soviet_now は現在 Codex が改善中のため、docich 側からは一切変更しない** (読み取り専用)。
毎時の Routine (Claude セッションへの定期トリガ) で以下を確認し、変化があったときだけ行動する:

1. **soviet_now main**: `git ls-remote` の main sha が docich のサブモジュールポインタと異なるか。
   - 異なれば: 新規コミットを `git log/diff` で確認 → 変更内容の要約と docich への影響 (配信経路・共通部品候補の変化) を評価 → 問題なければサブモジュールポインタを進める commit を作成 → C1 以降の段階に進める状態かを判断してユーザーへ報告。
2. **docich main**: origin/main が作業ブランチの基点より進んでいるか。
   - 進んでいれば: 変更内容を確認し、作業ブランチ (`claude/docich-game-switching-lsom1e`) を最新 main に追従 (再作成 or rebase) して継続作業の基点を保つ。
3. どちらも変化がなければ**何もしない** (報告もしない)。

---

## 5. 未決事項 (次の判断ポイント)

1. **配信経路の一本化**: soviet_now の `direct_stream.sh` (Codex) と docich の `stream.py` は同じ ffmpeg 直結。sorengame を docich 配下で流す際にどちらを配信の正とするか — Codex の移行完了形を見てから決める (C1)。【要検証】
2. **sorengame の `launch_command` 実パス**: 同上 (soviet_now の Linux 起動口が確定してから `config/games/sorengame.toml` に反映)。
3. **半熟英雄 brain の置き場**: brain スクリプト本体を hanjuku-sfc-speedrun 側に置くか、docich 側 (`brains/`) に置いてデータのみ参照するか — Phase 2 着手時に決定。チャート/データの参照パスは `games/hanjuku-sfc-speedrun/charts/`・`data/` (【確認済】卵落ちテーブル・キャラデータ・話数別チャートが存在)。
